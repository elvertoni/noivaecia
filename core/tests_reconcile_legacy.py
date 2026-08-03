import copy
import json
import os
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import SimpleTestCase, override_settings

from core.legacy_reconciliation import (
    CURATED_ALIAS,
    DEPENDENT_ENTITIES,
    ENTITY_SPECS,
    TARGET_ALIAS,
    _cash_account_identity,
    _cash_account_key,
    _attach_cash_account_key,
    _attach_receivable_key,
    _financial_movement_key,
    _legacy_or_manual_key,
    _receivable_key,
    canonical_json,
    compare_snapshots,
    database_names_equal,
    read_only_transaction,
    reconciliation_aliases,
    reconciliation_readiness,
    manual_lineage_requirement,
    sha256_fingerprint,
    snapshot_rows,
    validate_database_names,
)
from core.management.commands.reconcile_legacy import (
    atomic_write_manifest,
    resolve_manifest_output,
)


class FingerprintTests(SimpleTestCase):
    def test_hash_is_deterministic_for_supported_database_values(self):
        left = {'amount': Decimal('10.50'), 'due': date(2026, 8, 2), 'active': True}
        right = {'active': True, 'due': date(2026, 8, 2), 'amount': Decimal('10.50')}

        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(sha256_fingerprint(left), sha256_fingerprint(right))
        self.assertEqual(len(sha256_fingerprint(left)), 64)

    def test_snapshot_retains_no_raw_pii_or_internal_source_pk(self):
        snapshot = snapshot_rows(
            [{
                '_source_pk': 99,
                'legacy_id': 9,
                'name': 'Nome Particular',
                'cpf': '123.456.789-00',
            }],
            lambda row: str(row['legacy_id']),
        )

        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn('Nome Particular', serialized)
        self.assertNotIn('123.456.789-00', serialized)
        self.assertNotIn('_source_pk', snapshot['_records']['9'][0]['field_sha256'])

    def test_snapshot_reports_null_and_duplicate_stable_keys(self):
        snapshot = snapshot_rows(
            [
                {'legacy_id': 1, 'value': 'a'},
                {'legacy_id': 1, 'value': 'b'},
                {'legacy_id': None, 'value': 'manual'},
            ],
            lambda row: None if row['legacy_id'] is None else str(row['legacy_id']),
        )

        self.assertEqual(snapshot['counts']['rows'], 3)
        self.assertEqual(snapshot['counts']['unkeyed_rows'], 1)
        self.assertEqual(snapshot['duplicate_keys'], ['1'])


class StableKeyTests(SimpleTestCase):
    def test_every_required_dependent_entity_is_covered(self):
        self.assertTrue(DEPENDENT_ENTITIES.issubset({spec.name for spec in ENTITY_SPECS}))

    def test_customer_and_product_keys_include_source_and_manual_namespace(self):
        legacy = {'legacy_source': 'clientes', 'legacy_id': 7, '_source_pk': 100}
        manual = {'legacy_source': '', 'legacy_id': None, '_source_pk': 100}

        self.assertEqual(_legacy_or_manual_key(legacy, 'target'), 'legacy:clientes:7')
        self.assertEqual(_legacy_or_manual_key(manual, 'target'), 'manual:target:100')
        self.assertEqual(_legacy_or_manual_key(manual, 'curated'), 'manual:curated:100')

    def test_cash_account_key_and_financial_fk_share_the_same_rule(self):
        legacy = {'legacy_code': 'CX-01', '_source_pk': 4}
        manual = {'legacy_code': '', '_source_pk': 4}

        self.assertEqual(_cash_account_key(legacy, 'target'), 'legacy:CX-01')
        self.assertEqual(_cash_account_key(manual, 'target'), 'manual:target:4')
        self.assertEqual(
            _cash_account_identity(
                source='target',
                legacy_code=manual['legacy_code'],
                source_pk=manual['_source_pk'],
            ),
            _cash_account_key(manual, 'target'),
        )
        movement = {'account_id': 4, 'account__legacy_code': 'CX-01'}
        _attach_cash_account_key(movement, 'target')
        self.assertEqual(movement, {'account_key': 'legacy:CX-01'})

    def test_receivable_without_legacy_id_uses_schedule_multiset_key(self):
        row = {
            'legacy_source': '',
            'legacy_id': None,
            'rental__number': 123,
            'due_date': '2026-08-05',
            'amount': '100.00',
            '_schedule_ordinal': 2,
        }

        self.assertEqual(
            _receivable_key(row, 'target'),
            'schedule:123:2026-08-05:100.00:2',
        )

    def test_payment_on_second_equal_local_installment_keeps_ordinal_two(self):
        receivables = [
            {
                '_source_pk': 51,
                'legacy_source': '',
                'legacy_id': None,
                'rental__number': 123,
                'due_date': '2026-08-05',
                'amount': '100.00',
                '_schedule_ordinal': 1,
            },
            {
                '_source_pk': 52,
                'legacy_source': '',
                'legacy_id': None,
                'rental__number': 123,
                'due_date': '2026-08-05',
                'amount': '100.00',
                '_schedule_ordinal': 2,
            },
        ]
        key_map = {
            row['_source_pk']: _receivable_key(row, 'target')
            for row in receivables
        }
        payment = {'receivable_id': 52, 'amount': '100.00'}

        _attach_receivable_key(payment, key_map)

        self.assertEqual(
            payment['receivable_key'],
            'schedule:123:2026-08-05:100.00:2',
        )
        correct = snapshot_rows([payment], lambda _row: 'db:target:1')
        tampered = snapshot_rows(
            [{
                **payment,
                'receivable_key': 'schedule:123:2026-08-05:100.00:1',
            }],
            lambda _row: 'db:target:1',
        )
        comparison = compare_snapshots(correct, tampered)
        self.assertEqual(
            comparison['conflicts'][0]['changed_fields'],
            ['receivable_key'],
        )

    def test_financial_movement_key_priority(self):
        self.assertEqual(
            _financial_movement_key({
                'legacy_id': 5,
                '_payment_source_pk': 20,
                '_source_pk': 30,
            }, 'target'),
            'legacy:5',
        )
        self.assertEqual(
            _financial_movement_key({
                'legacy_id': None,
                '_payment_source_pk': 20,
                '_source_pk': 30,
            }, 'target'),
            'payment:target:20',
        )
        self.assertEqual(
            _financial_movement_key({
                'legacy_id': None,
                '_payment_source_pk': None,
                '_source_pk': 30,
            }, 'target'),
            'db:target:30',
        )

    def test_all_dependent_key_getters_cover_representative_rows(self):
        specs = {spec.name: spec for spec in ENTITY_SPECS}
        samples = {
            'cash_accounts': {'legacy_code': '', '_source_pk': 5},
            'rental_items': {'rental__number': 10, '_source_pk': 20},
            'pickups': {'rental__number': 10},
            'returns': {'rental__number': 10},
            'receivables': {
                'legacy_source': '',
                'legacy_id': None,
                'rental__number': 10,
                'due_date': '2026-08-05',
                'amount': '50.00',
                '_schedule_ordinal': 1,
            },
            'payments': {'_source_pk': 30},
            'financial_movements': {
                'legacy_id': None,
                '_payment_source_pk': None,
                '_source_pk': 40,
            },
        }

        for name in DEPENDENT_ENTITIES:
            with self.subTest(entity=name):
                key = specs[name].key_getter(samples[name], 'target')
                self.assertIsNotNone(key)

        snapshot = snapshot_rows(
            [samples['receivables']],
            lambda row: specs['receivables'].key_getter(row, 'target'),
        )
        self.assertEqual(snapshot['counts']['unkeyed_rows'], 0)


class ComparisonTests(SimpleTestCase):
    def test_public_conflicts_have_no_values_or_row_hashes(self):
        target = snapshot_rows(
            [
                {'legacy_id': 1, 'city': 'Cidade A', 'active': True},
                {'legacy_id': 2, 'city': 'Cidade B', 'active': True},
            ],
            lambda row: str(row['legacy_id']),
        )
        curated = snapshot_rows(
            [
                {'legacy_id': 1, 'city': 'Cidade Normalizada', 'active': True},
                {'legacy_id': 3, 'city': 'Cidade C', 'active': True},
            ],
            lambda row: str(row['legacy_id']),
        )

        comparison = compare_snapshots(target, curated)

        self.assertEqual(comparison['exclusive'], {'target': ['2'], 'curated': ['3']})
        self.assertEqual(
            comparison['conflicts'][0],
            {'key': '1', 'changed_fields': ['city']},
        )
        serialized = json.dumps(comparison, ensure_ascii=False)
        self.assertNotIn('Cidade A', serialized)
        self.assertNotIn('Cidade Normalizada', serialized)
        self.assertNotIn('target_sha256', serialized)
        self.assertNotIn('curated_sha256', serialized)

    def test_output_order_does_not_depend_on_input_order(self):
        rows = [
            {'legacy_id': 2, 'value': 'b'},
            {'legacy_id': 1, 'value': 'a'},
        ]
        first = snapshot_rows(rows, lambda row: str(row['legacy_id']))
        second = snapshot_rows(reversed(rows), lambda row: str(row['legacy_id']))
        self.assertEqual(first['sha256'], second['sha256'])


class ReadinessTests(SimpleTestCase):
    @staticmethod
    def _entity(*, keyed=True, unique=True):
        return {'expectations': {
            'all_rows_keyed': keyed,
            'duplicate_stable_keys_must_be_zero': unique,
        }}

    def _complete_entities(self):
        return {spec.name: self._entity() for spec in ENTITY_SPECS}

    def test_ready_requires_schema_keys_and_dependent_coverage(self):
        result = reconciliation_readiness(
            self._complete_entities(),
            schemas_match=True,
        )
        self.assertTrue(result['ready_for_human_reconciliation_plan'])

        for mutation in ('schema', 'unkeyed', 'duplicate', 'missing'):
            with self.subTest(mutation=mutation):
                entities = self._complete_entities()
                schemas_match = True
                if mutation == 'schema':
                    schemas_match = False
                elif mutation == 'unkeyed':
                    entities['receivables'] = self._entity(keyed=False)
                elif mutation == 'duplicate':
                    entities['customers'] = self._entity(unique=False)
                else:
                    entities.pop('payments')
                result = reconciliation_readiness(
                    entities,
                    schemas_match=schemas_match,
                )
                self.assertFalse(result['ready_for_human_reconciliation_plan'])

    def test_manual_lineage_count_is_exposed_by_comparison(self):
        target = snapshot_rows(
            [{'_source_pk': 1, 'name': 'Caixa atual'}],
            lambda row: f'manual:target:{row["_source_pk"]}',
        )
        curated = snapshot_rows(
            [{'_source_pk': 2, 'name': 'Caixa curado'}],
            lambda row: f'manual:curated:{row["_source_pk"]}',
        )

        comparison = compare_snapshots(target, curated)

        self.assertEqual(
            comparison['expectations']['manual_lineage_required'],
            1,
        )
        self.assertEqual(
            comparison['expectations']['manual_lineage_target_candidates'],
            1,
        )
        lineage = manual_lineage_requirement({'cash_accounts': comparison})
        self.assertEqual(lineage['curated_keys_requiring_mapping'], 1)
        self.assertEqual(lineage['target_key_candidates'], 1)
        self.assertTrue(lineage['required'])
        self.assertFalse(lineage['auto_match_by_name'])


class DatabaseSafetyTests(SimpleTestCase):
    def test_postgres_names_are_compared_case_insensitively(self):
        self.assertTrue(database_names_equal('Noivas_Cia', 'noivas_cia', engine='postgresql'))

    def test_rejects_default_database_as_either_input(self):
        for target, curated in [('production', 'curated'), ('target', 'production')]:
            with self.subTest(target=target, curated=curated):
                with self.assertRaisesRegex(ValueError, 'default/produção'):
                    validate_database_names(
                        default_name='production',
                        target_name=target,
                        curated_name=curated,
                        engine='django.db.backends.postgresql',
                    )

    def test_rejects_preexisting_reconciliation_alias(self):
        connections.databases[TARGET_ALIAS] = copy.deepcopy(
            connections.databases['default']
        )
        try:
            with self.assertRaisesRegex(ValueError, 'já registrado'):
                with reconciliation_aliases(
                    target_name='target',
                    curated_name='curated',
                ):
                    self.fail('Contexto não deveria ser aberto.')
        finally:
            connections.databases.pop(TARGET_ALIAS, None)

    def test_aliases_change_only_database_name_and_are_removed(self):
        default = copy.deepcopy(settings.DATABASES['default'])
        with reconciliation_aliases(
            target_name='target-clone',
            curated_name='curated-clone',
        ) as aliases:
            self.assertEqual(aliases, (TARGET_ALIAS, CURATED_ALIAS))
            for alias, expected_name in zip(
                aliases, ('target-clone', 'curated-clone'), strict=True,
            ):
                config = copy.deepcopy(connections.databases[alias])
                self.assertEqual(config.pop('NAME'), expected_name)
                default_without_name = copy.deepcopy(default)
                default_without_name.pop('NAME')
                self.assertEqual(config, default_without_name)
        self.assertNotIn(TARGET_ALIAS, connections.databases)
        self.assertNotIn(CURATED_ALIAS, connections.databases)

    def test_postgres_transaction_is_repeatable_read_and_read_only(self):
        statements = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql):
                statements.append(sql)

        class Connection:
            vendor = 'postgresql'
            connection = object()

            @staticmethod
            def cursor():
                return Cursor()

        class Handler:
            @staticmethod
            def __getitem__(_alias):
                return Connection()

        class Transactions:
            @staticmethod
            @contextmanager
            def atomic(using):
                self.assertEqual(using, 'candidate')
                yield

        with patch('core.legacy_reconciliation.connections', Handler()), patch(
            'core.legacy_reconciliation.transaction', Transactions(),
        ):
            with read_only_transaction('candidate'):
                pass

        self.assertEqual(
            statements,
            ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'],
        )


class ManifestOutputTests(SimpleTestCase):
    def test_relative_output_is_rooted_in_private_manifest_directory(self):
        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            root, output = resolve_manifest_output('audit/report.json')
            self.assertEqual(root, Path(directory) / 'recovery-manifests')
            self.assertEqual(output, root / 'audit' / 'report.json')

    def test_rejects_relative_escape_and_absolute_outside_root(self):
        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            for output in ('../outside.json', str(Path(directory) / 'outside.json')):
                with self.subTest(output=output):
                    with self.assertRaisesRegex(ValueError, 'deve ficar dentro'):
                        resolve_manifest_output(output)

    @skipUnless(hasattr(os, 'symlink'), 'Plataforma sem links simbólicos')
    def test_rejects_symlink_in_output_path(self):
        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            root, _output = resolve_manifest_output('seed/report.json')
            outside = Path(directory) / 'outside'
            outside.mkdir()
            link = root / 'linked'
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('Criação de symlink indisponível neste ambiente.')
            with self.assertRaisesRegex(ValueError, 'link simbólico'):
                resolve_manifest_output('linked/report.json')

    def test_atomic_writer_leaves_complete_file_and_no_temporary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'report.json'
            atomic_write_manifest(output, '{"ok":true}\n', root=root)
            self.assertEqual(output.read_text(encoding='utf-8'), '{"ok":true}\n')
            self.assertEqual(list(root.glob('*.tmp')), [])


class ReconcileLegacyCommandTests(SimpleTestCase):
    def test_apply_is_always_refused(self):
        with self.assertRaisesRegex(CommandError, 'estritamente dry-run'):
            call_command(
                'reconcile_legacy',
                target_db_name='target',
                curated_db_name='curated',
                output='unused.json',
                apply=True,
            )

    def test_writes_private_deterministic_json_report(self):
        report = {
            'report_version': 2,
            'mode': 'dry-run',
            'report_sha256': 'a' * 64,
        }

        @contextmanager
        def fake_aliases(**_kwargs):
            yield 'target_alias', 'curated_alias'

        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            stdout = StringIO()
            with patch(
                'core.management.commands.reconcile_legacy.reconciliation_aliases',
                fake_aliases,
            ), patch(
                'core.management.commands.reconcile_legacy.build_reconciliation_report',
                return_value=report,
            ):
                call_command(
                    'reconcile_legacy',
                    target_db_name='target',
                    curated_db_name='curated',
                    output='private/report.json',
                    stdout=stdout,
                )

            output = Path(directory) / 'recovery-manifests/private/report.json'
            self.assertEqual(json.loads(output.read_text(encoding='utf-8')), report)
            self.assertIn('Dry-run concluído', stdout.getvalue())
