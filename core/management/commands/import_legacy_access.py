import json
import re
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from billing.models import Receivable
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental, RentalItem


TABLES = (
    'categoria',
    'clientes',
    'empresa',
    'libera',
    'locado',
    'movimento',
    'pagar',
    'produtos',
    'programas',
    'temp',
    'usuario',
)

ACCESS_TO_SQLITE = {
    2: 'INTEGER',
    3: 'INTEGER',
    4: 'INTEGER',
    5: 'REAL',
    6: 'NUMERIC',
    7: 'TEXT',
    11: 'INTEGER',
    17: 'INTEGER',
    130: 'TEXT',
    202: 'TEXT',
    203: 'TEXT',
}


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def slug_identifier(value):
    normalized = unicodedata.normalize('NFKD', value)
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^0-9A-Za-z]+', '_', ascii_value).strip('_').lower()
    return slug or 'table'


def raw_table_name(table_name):
    return f'legacy_{slug_identifier(table_name)}'


def clean_text(value, max_length=None, blank_markers=('*',)):
    if value is None:
        text = ''
    else:
        text = str(value).strip()
    if text in blank_markers:
        text = ''
    if max_length is not None:
        return text[:max_length]
    return text


def normalize_prefix(value):
    return clean_text(value, 10).upper()


def as_int(value, default=None):
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_decimal(value, default='0'):
    if value is None or value == '':
        return Decimal(default)
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def as_bool(value):
    if value is None or value == '':
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value != 0
    return str(value).strip().lower() in {'1', '-1', 'true', 'yes', 'sim'}


def as_date(value):
    if value is None or value == '':
        return None
    if hasattr(value, 'date'):
        return value.date()
    text = str(value).strip()
    formats = (
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y',
    )
    for date_format in formats:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError(f'Invalid legacy date: {value!r}')


def first_value(rows, key):
    for row in rows:
        value = row.get(key)
        if value not in (None, ''):
            return value
    return None


def unique_clean_values(rows, key, max_length=None):
    values = []
    seen = set()
    for row in rows:
        value = clean_text(row.get(key), max_length)
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


class Command(BaseCommand):
    help = 'Importa o banco Access legado exportado para JSONL.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--export-dir',
            default='var/legacy_export',
            help='Diretorio gerado por tools/legacy_migration/export_access.ps1.',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Limpa tabelas de negocio antes de carregar os dados legados.',
        )
        parser.add_argument(
            '--skip-raw',
            action='store_true',
            help='Nao recria as tabelas legacy_* com o dump bruto.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=2000,
            help='Tamanho dos lotes de bulk insert.',
        )

    def handle(self, *args, **options):
        self.export_dir = Path(options['export_dir'])
        self.batch_size = options['batch_size']
        self.now = timezone.now()
        self.summary = {}

        if not self.export_dir.exists():
            raise CommandError(f'Export dir not found: {self.export_dir}')

        self._validate_export()
        self._backup_sqlite()

        with transaction.atomic():
            if not options['skip_raw']:
                self._import_raw_tables()

            if options['reset']:
                self._reset_business_tables()
            else:
                self._ensure_empty_business_tables()

            tables = {table: list(self._read_rows(table)) for table in TABLES}
            self._import_normalized(tables)
            self._write_audit_rows()

        for key, value in self.summary.items():
            self.stdout.write(f'{key}: {value}')
        self.stdout.write(self.style.SUCCESS('Importacao legado concluida.'))

    def _validate_export(self):
        missing = []
        for table in TABLES:
            if not (self.export_dir / 'data' / f'{table}.jsonl').exists():
                missing.append(table)
            if not (self.export_dir / 'schema' / f'{table}.json').exists():
                missing.append(f'{table}.schema')
        if missing:
            raise CommandError(f'Missing export files: {", ".join(missing)}')

    def _backup_sqlite(self):
        engine = settings.DATABASES['default']['ENGINE']
        if engine != 'django.db.backends.sqlite3':
            self.stdout.write('Backup automatico ignorado: banco default nao e SQLite.')
            return

        database_name = settings.DATABASES['default']['NAME']
        if database_name == ':memory:':
            return
        source = Path(database_name)
        if not source.exists():
            return

        backup_dir = Path('var/backups')
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = timezone.localtime(self.now).strftime('%Y%m%d-%H%M%S')
        target = backup_dir / f'{source.stem}.before-legacy-{stamp}{source.suffix}'
        shutil.copy2(source, target)
        self.summary['backup'] = str(target)

    def _ensure_empty_business_tables(self):
        models = (
            Return,
            Pickup,
            Receivable,
            RentalItem,
            Rental,
            Product,
            Category,
            Customer,
            Company,
        )
        non_empty = [
            f'{model._meta.label}={model.objects.count()}'
            for model in models
            if model.objects.exists()
        ]
        if non_empty:
            raise CommandError(
                'Business tables are not empty. Use --reset to replace them: '
                + ', '.join(non_empty)
            )

    def _reset_business_tables(self):
        for model in (
            Return,
            Pickup,
            Receivable,
            RentalItem,
            Rental,
            Product,
            Category,
            Customer,
            Company,
        ):
            model.objects.all().delete()

    def _schema_for(self, table_name):
        path = self.export_dir / 'schema' / f'{table_name}.json'
        with path.open(encoding='utf-8-sig') as handle:
            return json.load(handle)

    def _read_rows(self, table_name):
        path = self.export_dir / 'data' / f'{table_name}.jsonl'
        with path.open(encoding='utf-8-sig') as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _import_raw_tables(self):
        with connection.cursor() as cursor:
            for table_name in TABLES:
                schema = self._schema_for(table_name)
                raw_name = raw_table_name(table_name)
                columns = schema['columns']

                cursor.execute(f'DROP TABLE IF EXISTS {quote_identifier(raw_name)}')
                column_sql = []
                for column in columns:
                    sqlite_type = ACCESS_TO_SQLITE.get(column['data_type'], 'TEXT')
                    column_sql.append(
                        f'{quote_identifier(column["name"])} {sqlite_type}'
                    )
                cursor.execute(
                    f'CREATE TABLE {quote_identifier(raw_name)} '
                    f'({", ".join(column_sql)})'
                )

                names = [column['name'] for column in columns]
                placeholders = ', '.join(['%s'] * len(names))
                insert_sql = (
                    f'INSERT INTO {quote_identifier(raw_name)} '
                    f'({", ".join(quote_identifier(name) for name in names)}) '
                    f'VALUES ({placeholders})'
                )

                batch = []
                inserted = 0
                for row in self._read_rows(table_name):
                    batch.append([row.get(name) for name in names])
                    if len(batch) >= self.batch_size:
                        cursor.executemany(insert_sql, batch)
                        inserted += len(batch)
                        batch = []
                if batch:
                    cursor.executemany(insert_sql, batch)
                    inserted += len(batch)
                self.summary[f'raw_{raw_name}'] = inserted

    def _bulk_create(self, model, objects):
        if not objects:
            return
        model.objects.bulk_create(objects, batch_size=self.batch_size)

    def _import_normalized(self, tables):
        customer_by_legacy = self._load_customers(tables)
        categories = self._load_categories(tables)
        product_by_key = self._load_products(tables, categories)
        rental_by_number = self._load_rentals(tables, customer_by_legacy)
        self._load_rental_items(tables, product_by_key)
        self._load_movements(tables, rental_by_number)
        self._load_receivables(tables)
        self._load_company(tables)

    def _load_customers(self, tables):
        customer_ids = {
            as_int(row.get('numero'))
            for row in tables['clientes']
            if as_int(row.get('numero')) is not None
        }
        referenced_ids = {
            as_int(row.get('cliente'))
            for row in tables['locado'] + tables['pagar']
            if as_int(row.get('cliente')) is not None
        }

        max_customer_id = max(customer_ids or {0})
        placeholder_map = {}
        next_placeholder_id = max_customer_id + 1
        for legacy_id in sorted(referenced_ids - customer_ids):
            pk = legacy_id if legacy_id and legacy_id > 0 else next_placeholder_id
            if pk == next_placeholder_id:
                next_placeholder_id += 1
            placeholder_map[legacy_id] = pk

        customers = []
        customer_by_legacy = {}
        for row in tables['clientes']:
            legacy_id = as_int(row.get('numero'))
            if legacy_id is None:
                continue
            name = clean_text(row.get('nome'), 150) or f'Cliente legado {legacy_id}'
            customer = Customer(
                id=legacy_id,
                name=name,
                address=clean_text(row.get('endereço'), 200),
                district=clean_text(row.get('bairro'), 100),
                city=clean_text(row.get('cidade'), 100),
                rg=clean_text(row.get('rg'), 20),
                cpf=clean_text(row.get('cpf'), 14),
                phone_home=clean_text(row.get('telefone'), 20),
                phone_mobile=clean_text(row.get('celular'), 20),
                phone_work=clean_text(row.get('fone_cial'), 20),
                notes=clean_text(row.get('obs')),
                created_at=self.now,
                updated_at=self.now,
            )
            customers.append(customer)
            customer_by_legacy[legacy_id] = customer

        for legacy_id, pk in placeholder_map.items():
            customer = Customer(
                id=pk,
                name=f'Cliente legado sem cadastro {legacy_id}',
                notes=f'Referenciado no legado com codigo de cliente {legacy_id}.',
                created_at=self.now,
                updated_at=self.now,
            )
            customers.append(customer)
            customer_by_legacy[legacy_id] = customer

        self._bulk_create(Customer, customers)
        self.summary['customers'] = len(customers)
        self.summary['placeholder_customers'] = len(placeholder_map)
        return customer_by_legacy

    def _load_categories(self, tables):
        categories_by_prefix = {}
        for row in tables['categoria']:
            prefix = normalize_prefix(row.get('prefixo'))
            if not prefix or prefix in categories_by_prefix:
                continue
            categories_by_prefix[prefix] = clean_text(row.get('categoria'), 100)

        referenced_prefixes = set()
        for table_name in ('produtos', 'locado'):
            for row in tables[table_name]:
                prefix = normalize_prefix(row.get('prefixo'))
                if prefix:
                    referenced_prefixes.add(prefix)

        missing_prefixes = referenced_prefixes - set(categories_by_prefix)
        for prefix in sorted(missing_prefixes):
            categories_by_prefix[prefix] = f'Legado {prefix}'

        categories = [
            Category(
                prefix=prefix,
                name=name or f'Legado {prefix}',
                created_at=self.now,
                updated_at=self.now,
            )
            for prefix, name in sorted(categories_by_prefix.items())
        ]
        self._bulk_create(Category, categories)
        self.summary['categories'] = len(categories)
        self.summary['placeholder_categories'] = len(missing_prefixes)
        return Category.objects.in_bulk(field_name='prefix')

    def _load_products(self, tables, categories):
        products = []
        product_by_key = {}
        duplicate_keys = defaultdict(list)
        max_product_id = 0

        for row in tables['produtos']:
            legacy_id = as_int(row.get('id'))
            code = as_int(row.get('codigo'))
            prefix = normalize_prefix(row.get('prefixo'))
            if legacy_id is None or code is None or not prefix:
                continue
            max_product_id = max(max_product_id, legacy_id)
            product = Product(
                id=legacy_id,
                category=categories[prefix],
                code=code,
                description=clean_text(row.get('descrição'), 200) or f'{prefix}{code}',
                color=clean_text(row.get('cor'), 50),
                size=clean_text(row.get('tamanho'), 50),
                value=as_decimal(row.get('valor')),
                notes=clean_text(row.get('obs')),
                created_at=self.now,
                updated_at=self.now,
            )
            products.append(product)

            key = (prefix, code)
            duplicate_keys[key].append(legacy_id)
            if key not in product_by_key or legacy_id < product_by_key[key].id:
                product_by_key[key] = product

        placeholder_count = 0
        missing_item_keys = {}
        for row in tables['locado']:
            prefix = normalize_prefix(row.get('prefixo'))
            code = as_int(row.get('codigo'))
            if not prefix or code is None or (prefix, code) in product_by_key:
                continue
            missing_item_keys.setdefault(
                (prefix, code),
                clean_text(row.get('descrição'), 200) or f'{prefix}{code}',
            )

        next_id = max_product_id + 1
        for (prefix, code), description in sorted(missing_item_keys.items()):
            product = Product(
                id=next_id,
                category=categories[prefix],
                code=code,
                description=description,
                notes='Criado automaticamente: item de locacao sem cadastro em produtos.',
                created_at=self.now,
                updated_at=self.now,
            )
            products.append(product)
            product_by_key[(prefix, code)] = product
            next_id += 1
            placeholder_count += 1

        duplicate_count = sum(1 for ids in duplicate_keys.values() if len(ids) > 1)
        self._bulk_create(Product, products)
        self.summary['products'] = len(products)
        self.summary['duplicate_product_keys'] = duplicate_count
        self.summary['placeholder_products'] = placeholder_count
        return product_by_key

    def _load_rentals(self, tables, customer_by_legacy):
        locado_groups = defaultdict(list)
        for row in tables['locado']:
            number = as_int(row.get('locação'))
            if number is not None:
                locado_groups[number].append(row)

        pagar_groups = defaultdict(list)
        for row in tables['pagar']:
            number = as_int(row.get('locação'))
            if number is not None:
                pagar_groups[number].append(row)

        rentals = []
        rental_by_number = {}

        for number, rows in sorted(locado_groups.items()):
            pickup_dates = [as_date(row.get('retirada')) for row in rows]
            return_dates = [as_date(row.get('dev_prevista')) for row in rows]
            customer_legacy_id = as_int(first_value(rows, 'cliente'))
            total_value = sum(as_decimal(row.get('valor')) for row in rows)
            penalty_value = max(as_decimal(row.get('multa')) for row in rows)

            if all(as_bool(row.get('devolvido')) for row in rows):
                status = Rental.Status.RETURNED
            elif any(as_bool(row.get('retirado')) for row in rows):
                status = Rental.Status.PICKED_UP
            else:
                status = Rental.Status.PENDING

            notes = self._rental_notes(rows)
            rental = Rental(
                id=number,
                number=number,
                customer=customer_by_legacy[customer_legacy_id],
                pickup_date=min(date for date in pickup_dates if date is not None),
                return_date=max(date for date in return_dates if date is not None),
                total_value=total_value,
                penalty_value=penalty_value,
                notes=notes,
                status=status,
                created_at=self.now,
                updated_at=self.now,
            )
            rentals.append(rental)
            rental_by_number[number] = rental

        pagar_only_count = 0
        for number, rows in sorted(pagar_groups.items()):
            if number in rental_by_number:
                continue
            due_dates = [as_date(row.get('vencimento')) for row in rows]
            customer_legacy_id = as_int(first_value(rows, 'cliente'))
            total_value = sum(as_decimal(row.get('valor')) for row in rows)
            is_open = any(as_bool(row.get('pago')) for row in rows)
            rental = Rental(
                id=number,
                number=number,
                customer=customer_by_legacy[customer_legacy_id],
                pickup_date=min(date for date in due_dates if date is not None),
                return_date=max(date for date in due_dates if date is not None),
                total_value=total_value,
                notes='Importado de pagar sem itens correspondentes em locado.',
                status=Rental.Status.PENDING if is_open else Rental.Status.RETURNED,
                created_at=self.now,
                updated_at=self.now,
            )
            rentals.append(rental)
            rental_by_number[number] = rental
            pagar_only_count += 1

        self._bulk_create(Rental, rentals)
        self.summary['rentals'] = len(rentals)
        self.summary['rentals_from_locado'] = len(locado_groups)
        self.summary['rentals_from_pagar_only'] = pagar_only_count
        return rental_by_number

    def _rental_notes(self, rows):
        notes = []
        uses = unique_clean_values(rows, 'usar', 80)
        row_notes = unique_clean_values(rows, 'obs')
        if uses:
            notes.append('Usar: ' + '; '.join(uses))
        if row_notes:
            notes.append('Obs: ' + '; '.join(row_notes))
        return '\n'.join(notes)

    def _load_rental_items(self, tables, product_by_key):
        items = []
        for row in tables['locado']:
            legacy_id = as_int(row.get('id'))
            rental_number = as_int(row.get('locação'))
            prefix = normalize_prefix(row.get('prefixo'))
            code = as_int(row.get('codigo'))
            if legacy_id is None or rental_number is None or not prefix or code is None:
                continue
            product = product_by_key[(prefix, code)]
            items.append(
                RentalItem(
                    id=legacy_id,
                    rental_id=rental_number,
                    product=product,
                    description=clean_text(row.get('descrição'), 200),
                    value=as_decimal(row.get('valor')),
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
        self._bulk_create(RentalItem, items)
        self.summary['rental_items'] = len(items)

    def _load_movements(self, tables, rental_by_number):
        pickups = []
        returns = []
        groups = defaultdict(list)
        for row in tables['locado']:
            number = as_int(row.get('locação'))
            if number is not None:
                groups[number].append(row)

        for number, rows in groups.items():
            rental = rental_by_number[number]
            if rental.status in {Rental.Status.PICKED_UP, Rental.Status.RETURNED}:
                pickup_dates = [as_date(row.get('retirada')) for row in rows]
                pickups.append(
                    Pickup(
                        rental=rental,
                        pickup_date=min(date for date in pickup_dates if date is not None),
                        created_at=self.now,
                        updated_at=self.now,
                    )
                )

            if rental.status == Rental.Status.RETURNED:
                actual_dates = [as_date(row.get('dev_efetiva')) for row in rows]
                actual_date = max(date for date in actual_dates if date is not None)
                days_late = max((actual_date - rental.return_date).days, 0)
                returns.append(
                    Return(
                        rental=rental,
                        return_date=actual_date,
                        days_late=days_late,
                        penalty_applied=rental.penalty_value if days_late > 0 else 0,
                        created_at=self.now,
                        updated_at=self.now,
                    )
                )

        self._bulk_create(Pickup, pickups)
        self._bulk_create(Return, returns)
        self.summary['pickups'] = len(pickups)
        self.summary['returns'] = len(returns)

    def _load_receivables(self, tables):
        receivables = []
        for row in tables['pagar']:
            legacy_id = as_int(row.get('id'))
            rental_number = as_int(row.get('locação'))
            due_date = as_date(row.get('vencimento'))
            if legacy_id is None or rental_number is None or due_date is None:
                continue

            amount = as_decimal(row.get('valor'))
            legacy_paid = as_decimal(row.get('valor_pago'))
            if as_bool(row.get('pago')):
                paid_amount = legacy_paid
            else:
                paid_amount = max(legacy_paid, amount)
            balance = amount - paid_amount

            receivables.append(
                Receivable(
                    id=legacy_id,
                    rental_id=rental_number,
                    due_date=due_date,
                    amount=amount,
                    paid_amount=paid_amount,
                    balance=balance,
                    last_payment_date=as_date(row.get('ult_pagto')),
                    created_at=self.now,
                    updated_at=self.now,
                )
            )

        self._bulk_create(Receivable, receivables)
        self.summary['receivables'] = len(receivables)

    def _load_company(self, tables):
        row = tables['empresa'][0] if tables['empresa'] else {}
        legacy_last = as_int(row.get('locação'), 0) or 0
        imported_last = Rental.objects.order_by('-number').values_list(
            'number', flat=True
        ).first() or 0
        company = Company.objects.create(
            name=clean_text(row.get('nome'), 150),
            address=clean_text(row.get('endereço'), 200),
            city=clean_text(row.get('cidade'), 100),
            cnpj=clean_text(row.get('cnpj'), 18),
            phones=clean_text(row.get('fones'), 150),
            last_rental_number=max(legacy_last, imported_last),
            daily_interest_rate=as_decimal(row.get('juros')),
            footer_message=clean_text(row.get('mensa'), 255),
        )
        self.summary['company'] = company.name or company.pk

    def _write_audit_rows(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'CREATE TABLE IF NOT EXISTS legacy_import_audit ('
                'id INTEGER PRIMARY KEY AUTOINCREMENT, '
                'imported_at TEXT NOT NULL, '
                'key TEXT NOT NULL, '
                'value TEXT NOT NULL)'
            )
            for key, value in self.summary.items():
                cursor.execute(
                    'INSERT INTO legacy_import_audit '
                    '(imported_at, key, value) VALUES (%s, %s, %s)',
                    [self.now.isoformat(), key, str(value)],
                )
