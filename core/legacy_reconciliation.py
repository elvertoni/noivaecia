"""Read-only comparison primitives for a legacy database reconciliation.

Raw business values exist only while an entity is being compared. Public
reports contain stable keys, aggregate hashes and changed field names, never
row values or row hashes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import platform
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple

import django
from django.conf import settings
from django.db import connections, transaction
from django.db.migrations.recorder import MigrationRecorder

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from catalog.models import Category, Product
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental, RentalItem


REPORT_VERSION = 2
TARGET_ALIAS = 'legacy_reconcile_target'
CURATED_ALIAS = 'legacy_reconcile_curated'
TARGET_SOURCE = 'target'
CURATED_SOURCE = 'curated'
DEPENDENT_ENTITIES = frozenset({
    'cash_accounts',
    'rental_items',
    'pickups',
    'returns',
    'receivables',
    'payments',
    'financial_movements',
})


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_value(item) for item in value]
        return sorted(converted, key=canonical_json)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )


def sha256_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def database_names_equal(left: Any, right: Any, *, engine: str) -> bool:
    left_text = str(left or '').strip()
    right_text = str(right or '').strip()
    if 'sqlite3' in engine:
        return Path(left_text).expanduser().resolve() == Path(right_text).expanduser().resolve()
    return left_text.casefold() == right_text.casefold()


def validate_database_names(
    *,
    default_name: Any,
    target_name: Any,
    curated_name: Any,
    engine: str,
) -> None:
    if not str(target_name or '').strip() or not str(curated_name or '').strip():
        raise ValueError('Os nomes dos bancos alvo e curado são obrigatórios.')
    if database_names_equal(target_name, curated_name, engine=engine):
        raise ValueError('Os bancos alvo e curado devem ser diferentes.')
    if database_names_equal(target_name, default_name, engine=engine):
        raise ValueError('O banco alvo não pode ser o banco default/produção.')
    if database_names_equal(curated_name, default_name, engine=engine):
        raise ValueError('O banco curado não pode ser o banco default/produção.')


@contextmanager
def reconciliation_aliases(*, target_name: str, curated_name: str):
    """Register isolated aliases by changing only ``NAME`` from default."""
    preexisting = [
        alias for alias in (TARGET_ALIAS, CURATED_ALIAS)
        if alias in connections.databases
    ]
    if preexisting:
        raise ValueError(
            'Alias de reconciliação já registrado: ' + ', '.join(preexisting)
        )

    default_config = copy.deepcopy(settings.DATABASES['default'])
    validate_database_names(
        default_name=default_config.get('NAME'),
        target_name=target_name,
        curated_name=curated_name,
        engine=default_config.get('ENGINE', ''),
    )
    aliases = {TARGET_ALIAS: target_name, CURATED_ALIAS: curated_name}
    registered = []
    try:
        for alias, name in aliases.items():
            config = copy.deepcopy(default_config)
            config['NAME'] = name
            connections.databases[alias] = config
            registered.append(alias)
        yield TARGET_ALIAS, CURATED_ALIAS
    finally:
        initialized = {
            connection.alias: connection
            for connection in connections.all(initialized_only=True)
        }
        for alias in reversed(registered):
            connection = initialized.get(alias)
            if connection is not None:
                connection.close()
                del connections[alias]
            connections.databases.pop(alias, None)


@contextmanager
def read_only_transaction(alias: str):
    """Hold one consistent, database-enforced read-only snapshot."""
    connection = connections[alias]
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA query_only = ON')
    try:
        with transaction.atomic(using=alias):
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
                    )
            yield
    finally:
        if connection.vendor == 'sqlite' and connection.connection is not None:
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA query_only = OFF')


def _key_text(*parts: Any) -> str | None:
    if any(part is None or part == '' for part in parts):
        return None
    return ':'.join(str(part) for part in parts)


def _legacy_or_manual_key(
    row: Mapping[str, Any], source: str,
) -> str | None:
    legacy_id = row['legacy_id']
    if legacy_id is None:
        return _key_text('manual', source, row['_source_pk'])
    if not row['legacy_source']:
        return None
    return _key_text('legacy', row['legacy_source'], legacy_id)


def _related_legacy_key(
    *, source: str, legacy_source: Any, legacy_id: Any, source_pk: Any,
) -> str:
    if legacy_id is not None and legacy_source:
        return _key_text('legacy', legacy_source, legacy_id)
    return _key_text('manual', source, source_pk)


def _cash_account_identity(
    *, source: str, legacy_code: Any, source_pk: Any,
) -> str:
    normalized_code = str(legacy_code or '').strip()
    if normalized_code:
        return _key_text('legacy', normalized_code)
    return _key_text('manual', source, source_pk)


def _cash_account_key(row: Mapping[str, Any], source: str) -> str:
    return _cash_account_identity(
        source=source,
        legacy_code=row['legacy_code'],
        source_pk=row['_source_pk'],
    )


def _attach_cash_account_key(row: dict[str, Any], source: str) -> None:
    row['account_key'] = _cash_account_identity(
        source=source,
        legacy_code=row.pop('account__legacy_code'),
        source_pk=row.pop('account_id'),
    )


def _category_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    return Category.objects.using(alias).order_by('prefix').values(
        'prefix', 'name', 'legacy_id', 'legacy_source', 'legacy_notes',
        'is_placeholder',
    ).iterator(chunk_size=2000)


def _cash_account_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    return (
        {'_source_pk': row.pop('id'), **row}
        for row in CashAccount.objects.using(alias).order_by('id').values(
            'id', 'name', 'active', 'legacy_code',
        ).iterator(chunk_size=2000)
    )


def _customer_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    return (
        {'_source_pk': row.pop('id'), **row}
        for row in Customer.objects.using(alias).order_by('legacy_id', 'id').values(
            'id', 'legacy_id', 'legacy_source', 'name', 'address', 'district',
            'state', 'city', 'rg', 'cpf', 'cnpj', 'phone_home',
            'alternate_phone_contact', 'phone_mobile', 'phone_work', 'cpf_digits',
            'cnpj_digits', 'rg_digits', 'phone_home_digits', 'phone_mobile_digits',
            'phone_work_digits', 'name_search', 'notes', 'legacy_notes',
            'is_placeholder', 'is_active',
        ).iterator(chunk_size=2000)
    )


def _product_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    return (
        {'_source_pk': row.pop('id'), **row}
        for row in Product.objects.using(alias).order_by('legacy_id', 'id').values(
            'id', 'legacy_id', 'legacy_source', 'category__prefix', 'code',
            'description', 'description_search', 'color', 'size', 'value',
            'notes', 'legacy_notes', 'is_placeholder', 'is_active',
        ).iterator(chunk_size=2000)
    )


def _rental_rows(alias: str, source: str) -> Iterable[dict[str, Any]]:
    for row in Rental.objects.using(alias).order_by('number').values(
        'number', 'customer_id', 'customer__legacy_id',
        'customer__legacy_source', 'pickup_date', 'return_date', 'total_value',
        'penalty_value', 'wearer_name', 'cash_discount', 'cash_discount_percent',
        'cash_discount_amount', 'notes', 'status', 'cancelled_reason',
        'cancelled_at', 'contract_version', 'contract_printed_at', 'legacy_notes',
    ).iterator(chunk_size=2000):
        row['customer_key'] = _related_legacy_key(
            source=source,
            legacy_source=row.pop('customer__legacy_source'),
            legacy_id=row.pop('customer__legacy_id'),
            source_pk=row.pop('customer_id'),
        )
        yield row


def _rental_item_rows(alias: str, source: str) -> Iterable[dict[str, Any]]:
    for row in RentalItem.objects.using(alias).order_by(
        'rental__number', 'id',
    ).values(
        'id', 'rental__number', 'product_id', 'product__legacy_id',
        'product__legacy_source', 'description', 'value',
        'product_prefix_snapshot', 'product_code_snapshot',
        'product_description_snapshot', 'product_color_snapshot',
        'product_size_snapshot', 'product_snapshot_captured', 'proof_photo',
        'proof_photo_content_type', 'proof_photo_filename', 'proof_photo_size',
        'proof_photo_width', 'proof_photo_height', 'wearer_name',
    ).iterator(chunk_size=2000):
        row['_source_pk'] = row.pop('id')
        row['product_key'] = _related_legacy_key(
            source=source,
            legacy_source=row.pop('product__legacy_source'),
            legacy_id=row.pop('product__legacy_id'),
            source_pk=row.pop('product_id'),
        )
        yield row


def _pickup_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    return Pickup.objects.using(alias).order_by('rental__number').values(
        'rental__number', 'pickup_date',
    ).iterator(chunk_size=2000)


def _return_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    return Return.objects.using(alias).order_by('rental__number').values(
        'rental__number', 'return_date', 'days_late', 'penalty_applied',
        'damage_amount', 'damage_notes',
    ).iterator(chunk_size=2000)


def _receivable_rows(alias: str, _source: str) -> Iterable[dict[str, Any]]:
    duplicate_ordinals: defaultdict[tuple[Any, ...], int] = defaultdict(int)
    rows = Receivable.objects.using(alias).order_by(
        'rental__number', 'due_date', 'amount', 'id',
    ).values(
        'id', 'legacy_source', 'legacy_id', 'rental__number', 'due_date',
        'amount', 'paid_amount', 'balance', 'last_payment_date', 'written_off_at',
        'written_off_reason', 'legacy_notes',
    ).iterator(chunk_size=2000)
    for row in rows:
        schedule = (row['rental__number'], row['due_date'], row['amount'])
        duplicate_ordinals[schedule] += 1
        row['_source_pk'] = row.pop('id')
        row['_schedule_ordinal'] = duplicate_ordinals[schedule]
        yield row


def _receivable_key_map(alias: str) -> dict[Any, str]:
    """Index every receivable, so local ordinals never depend on paid subset."""
    return {
        row['_source_pk']: _receivable_key(row, '')
        for row in _receivable_rows(alias, '')
    }


def _attach_receivable_key(
    row: dict[str, Any], key_map: Mapping[Any, str],
) -> None:
    receivable_id = row.pop('receivable_id')
    row['receivable_key'] = (
        key_map.get(receivable_id) if receivable_id is not None else None
    )


def _payment_rows(alias: str, source: str) -> Iterable[dict[str, Any]]:
    receivable_keys = _receivable_key_map(alias)
    for row in Payment.objects.using(alias).order_by('id').values(
        'id', 'receivable_id', 'customer__legacy_source', 'customer__legacy_id',
        'customer_id', 'rental__number', 'payment_date', 'amount',
        'interest_amount', 'discount_amount', 'method', 'notes', 'user_id',
        'legacy_movement_id', 'is_reversal', 'reversed_by_id',
    ).iterator(chunk_size=2000):
        row['_source_pk'] = row.pop('id')
        _attach_receivable_key(row, receivable_keys)
        if row['customer_id'] is not None:
            row['customer_key'] = _related_legacy_key(
                source=source,
                legacy_source=row.pop('customer__legacy_source'),
                legacy_id=row.pop('customer__legacy_id'),
                source_pk=row.pop('customer_id'),
            )
        else:
            row.pop('customer__legacy_source')
            row.pop('customer__legacy_id')
            row.pop('customer_id')
            row['customer_key'] = None
        row['operator_present'] = row.pop('user_id') is not None
        reversed_by_id = row.pop('reversed_by_id')
        row['reversed_by_key'] = (
            _key_text('db', source, reversed_by_id) if reversed_by_id else None
        )
        yield row


def _financial_movement_rows(alias: str, source: str) -> Iterable[dict[str, Any]]:
    receivable_keys = _receivable_key_map(alias)
    for row in FinancialMovement.objects.using(alias).order_by('id').values(
        'id', 'legacy_id', 'date', 'account_id', 'account__legacy_code',
        'direction', 'amount', 'description', 'source', 'customer_id',
        'customer__legacy_source', 'customer__legacy_id',
        'receivable_id', 'payment_id', 'payment__legacy_movement_id',
        'rental__number', 'created_by_id',
    ).iterator(chunk_size=2000):
        row['_source_pk'] = row.pop('id')
        _attach_cash_account_key(row, source)
        _attach_receivable_key(row, receivable_keys)
        if row['customer_id'] is not None:
            row['customer_key'] = _related_legacy_key(
                source=source,
                legacy_source=row.pop('customer__legacy_source'),
                legacy_id=row.pop('customer__legacy_id'),
                source_pk=row.pop('customer_id'),
            )
        else:
            row.pop('customer__legacy_source')
            row.pop('customer__legacy_id')
            row.pop('customer_id')
            row['customer_key'] = None
        payment_id = row['payment_id']
        payment_legacy_id = row.pop('payment__legacy_movement_id')
        row['_payment_source_pk'] = payment_id
        row['payment_reference'] = (
            _key_text('legacy-movement', payment_legacy_id)
            if payment_legacy_id is not None
            else (_key_text('db', source, payment_id) if payment_id else None)
        )
        row.pop('payment_id')
        row['creator_present'] = row.pop('created_by_id') is not None
        yield row


def _receivable_key(row: Mapping[str, Any], _source: str) -> str | None:
    if row['legacy_id'] is not None:
        return _key_text('legacy', row['legacy_source'], row['legacy_id'])
    return _key_text(
        'schedule', row['rental__number'], row['due_date'], row['amount'],
        row['_schedule_ordinal'],
    )


def _financial_movement_key(row: Mapping[str, Any], source: str) -> str:
    if row['legacy_id'] is not None:
        return _key_text('legacy', row['legacy_id'])
    if row['_payment_source_pk'] is not None:
        return _key_text('payment', source, row['_payment_source_pk'])
    return _key_text('db', source, row['_source_pk'])


class EntitySpec(NamedTuple):
    name: str
    stable_key: str
    row_loader: Callable[[str, str], Iterable[dict[str, Any]]]
    key_getter: Callable[[Mapping[str, Any], str], str | None]


ENTITY_SPECS = (
    EntitySpec('categories', 'Category.prefix', _category_rows,
               lambda row, _source: _key_text(row['prefix'])),
    EntitySpec('cash_accounts', 'CashAccount.legacy_code; manual:<db>:<pk>',
               _cash_account_rows, _cash_account_key),
    EntitySpec('customers', 'Customer.(legacy_source,legacy_id); manual:<db>:<pk>',
               _customer_rows,
               lambda row, source: _legacy_or_manual_key(row, source)),
    EntitySpec('products', 'Product.(legacy_source,legacy_id); manual:<db>:<pk>',
               _product_rows,
               lambda row, source: _legacy_or_manual_key(row, source)),
    EntitySpec('rentals', 'Rental.number', _rental_rows,
               lambda row, _source: _key_text(row['number'])),
    EntitySpec('rental_items', 'RentalItem.(rental.number,id)', _rental_item_rows,
               lambda row, _source: _key_text(row['rental__number'], row['_source_pk'])),
    EntitySpec('pickups', 'Pickup.rental.number', _pickup_rows,
               lambda row, _source: _key_text(row['rental__number'])),
    EntitySpec('returns', 'Return.rental.number', _return_rows,
               lambda row, _source: _key_text(row['rental__number'])),
    EntitySpec(
        'receivables',
        'legacy:(source,id); schedule:(rental.number,due_date,amount,ordinal)',
        _receivable_rows,
        _receivable_key,
    ),
    EntitySpec('payments', 'Payment.<source-db>.<pk>', _payment_rows,
               lambda row, source: _key_text('db', source, row['_source_pk'])),
    EntitySpec(
        'financial_movements',
        'legacy_id; payment source PK; source DB/PK',
        _financial_movement_rows,
        _financial_movement_key,
    ),
)


def snapshot_rows(
    rows: Iterable[Mapping[str, Any]],
    key_getter: Callable[[Mapping[str, Any]], str | None],
) -> dict[str, Any]:
    """Hash one entity; underscore-prefixed values are internal identity only."""
    keyed: dict[str, list[dict[str, Any]]] = {}
    unkeyed_hashes = []
    total = 0
    for source_row in rows:
        total += 1
        normalized = {
            str(key): _json_value(value) for key, value in source_row.items()
        }
        key = key_getter(normalized)
        business_row = {
            field: value for field, value in normalized.items()
            if not field.startswith('_')
        }
        record = {
            'sha256': sha256_fingerprint(business_row),
            'field_sha256': {
                field: sha256_fingerprint(value)
                for field, value in sorted(business_row.items())
            },
        }
        if key is None:
            unkeyed_hashes.append(record['sha256'])
        else:
            keyed.setdefault(key, []).append(record)

    for records in keyed.values():
        records.sort(key=lambda item: item['sha256'])
    unkeyed_hashes.sort()
    duplicate_keys = sorted(key for key, records in keyed.items() if len(records) > 1)
    manual_keys = sorted(key for key in keyed if key.startswith('manual:'))
    fingerprint_material = {
        'keyed': [
            [key, [record['sha256'] for record in keyed[key]]]
            for key in sorted(keyed)
        ],
        'unkeyed': unkeyed_hashes,
    }
    return {
        'counts': {
            'rows': total,
            'keyed_rows': sum(len(records) for records in keyed.values()),
            'unkeyed_rows': len(unkeyed_hashes),
            'unique_keys': len(keyed),
            'duplicate_keys': len(duplicate_keys),
            'manual_keys': len(manual_keys),
        },
        'sha256': sha256_fingerprint(fingerprint_material),
        'duplicate_keys': duplicate_keys,
        '_records': keyed,
    }


def compare_snapshots(target: Mapping[str, Any], curated: Mapping[str, Any]) -> dict[str, Any]:
    target_records = target['_records']
    curated_records = curated['_records']
    target_keys = set(target_records)
    curated_keys = set(curated_records)
    common_keys = sorted(target_keys & curated_keys)
    conflicts = []
    duplicate_common_keys = []

    for key in common_keys:
        target_values = target_records[key]
        curated_values = curated_records[key]
        if len(target_values) != 1 or len(curated_values) != 1:
            duplicate_common_keys.append(key)
            continue
        target_record = target_values[0]
        curated_record = curated_values[0]
        if target_record['sha256'] == curated_record['sha256']:
            continue
        all_fields = sorted(
            set(target_record['field_sha256']) | set(curated_record['field_sha256'])
        )
        conflicts.append({
            'key': key,
            'changed_fields': [
                field for field in all_fields
                if target_record['field_sha256'].get(field)
                != curated_record['field_sha256'].get(field)
            ],
        })

    public = lambda snapshot: {
        key: value for key, value in snapshot.items() if key != '_records'
    }
    return {
        'target': public(target),
        'curated': public(curated),
        'exclusive': {
            'target': sorted(target_keys - curated_keys),
            'curated': sorted(curated_keys - target_keys),
        },
        'common_keys': len(common_keys),
        'identical_keys': len(common_keys) - len(conflicts) - len(duplicate_common_keys),
        'unresolved_duplicate_keys': duplicate_common_keys,
        'conflicts': conflicts,
        'expectations': {
            'target_exclusives_must_be_preserved': len(target_keys - curated_keys),
            'curated_exclusives_are_restore_candidates': len(curated_keys - target_keys),
            'conflicts_require_explicit_policy': len(conflicts),
            'all_rows_keyed': (
                target['counts']['unkeyed_rows'] == 0
                and curated['counts']['unkeyed_rows'] == 0
            ),
            'duplicate_stable_keys_must_be_zero': (
                target['counts']['duplicate_keys'] == 0
                and curated['counts']['duplicate_keys'] == 0
            ),
            'manual_lineage_required': curated['counts']['manual_keys'],
            'manual_lineage_target_candidates': target['counts']['manual_keys'],
        },
    }


def _migration_version(alias: str) -> dict[str, Any]:
    applied = sorted(
        f'{app}.{name}'
        for app, name in MigrationRecorder(connections[alias]).applied_migrations()
    )
    return {
        'applied_count': len(applied),
        'applied_sha256': sha256_fingerprint(applied),
        'latest_by_app': {
            app: max(name for candidate_app, name in (
                item.split('.', 1) for item in applied
            ) if candidate_app == app)
            for app in sorted({item.split('.', 1)[0] for item in applied})
        },
    }


def reconciliation_readiness(
    entities: Mapping[str, Mapping[str, Any]], *, schemas_match: bool,
) -> dict[str, bool]:
    dependent_entities_covered = DEPENDENT_ENTITIES.issubset(entities)
    all_rows_keyed = all(
        entity['expectations']['all_rows_keyed'] for entity in entities.values()
    )
    stable_keys_unique = all(
        entity['expectations']['duplicate_stable_keys_must_be_zero']
        for entity in entities.values()
    )
    return {
        'schemas_match': schemas_match,
        'dependent_entities_covered': dependent_entities_covered,
        'all_rows_keyed': all_rows_keyed,
        'stable_keys_unique': stable_keys_unique,
        'ready_for_human_reconciliation_plan': (
            schemas_match
            and dependent_entities_covered
            and all_rows_keyed
            and stable_keys_unique
        ),
    }


def manual_lineage_requirement(
    entities: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    curated_keys = sum(
        entity['curated']['counts']['manual_keys'] for entity in entities.values()
    )
    target_keys = sum(
        entity['target']['counts']['manual_keys'] for entity in entities.values()
    )
    return {
        'required': curated_keys > 0,
        'curated_keys_requiring_mapping': curated_keys,
        'target_key_candidates': target_keys,
        'mapping': 'curated-key→target-key',
        'auto_match_by_name': False,
    }


def build_reconciliation_report(target_alias: str, curated_alias: str) -> dict[str, Any]:
    """Compare one entity at a time inside two consistent DB snapshots."""
    entities = {}
    target_hashes = {}
    curated_hashes = {}
    with ExitStack() as stack:
        stack.enter_context(read_only_transaction(target_alias))
        stack.enter_context(read_only_transaction(curated_alias))
        target_version = _migration_version(target_alias)
        curated_version = _migration_version(curated_alias)

        for spec in ENTITY_SPECS:
            target = snapshot_rows(
                spec.row_loader(target_alias, TARGET_SOURCE),
                lambda row, getter=spec.key_getter: getter(row, TARGET_SOURCE),
            )
            curated = snapshot_rows(
                spec.row_loader(curated_alias, CURATED_SOURCE),
                lambda row, getter=spec.key_getter: getter(row, CURATED_SOURCE),
            )
            target_hashes[spec.name] = target['sha256']
            curated_hashes[spec.name] = curated['sha256']
            comparison = compare_snapshots(target, curated)
            comparison['stable_key'] = spec.stable_key
            entities[spec.name] = comparison
            del target, curated

    target_database_hash = sha256_fingerprint({
        'schema': target_version['applied_sha256'],
        'entities': target_hashes,
    })
    curated_database_hash = sha256_fingerprint({
        'schema': curated_version['applied_sha256'],
        'entities': curated_hashes,
    })
    schemas_match = (
        target_version['applied_sha256'] == curated_version['applied_sha256']
    )
    readiness = reconciliation_readiness(entities, schemas_match=schemas_match)
    report = {
        'report_version': REPORT_VERSION,
        'mode': 'dry-run',
        'hash_algorithm': 'SHA-256',
        'versions': {
            'python': platform.python_version(),
            'django': django.get_version(),
            'target_schema': target_version,
            'curated_schema': curated_version,
        },
        'fingerprints': {
            'target_database_sha256': target_database_hash,
            'curated_database_sha256': curated_database_hash,
        },
        'entities': entities,
        'expectations': {
            'no_database_writes_performed': True,
            'apply_supported': False,
            'manual_lineage_required': manual_lineage_requirement(entities),
            **readiness,
        },
    }
    report['report_sha256'] = sha256_fingerprint(report)
    return report
