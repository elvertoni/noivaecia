"""Generate a post-import homologation report for Sprint R14.

Covers:
  R14.02 — counts comparison (Access vs Django)
  R14.03 — placeholder inventory
  R14.04 — suspicious dates in raw legacy tables
  R14.05 — financial reconciliation

Usage:
    python manage.py homologation_report [--output-dir DIR] [--export-dir DIR]
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from billing.services import reconcile_financial
from catalog.models import Category, Product
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental, RentalItem


_ENTITY_MAP = {
    'clientes': (Customer, 'clientes'),
    'categoria': (Category, 'categoria'),
    'produtos': (Product, 'produtos'),
    'locado_itens': (RentalItem, 'locado'),
    'locado_locacoes': (Rental, 'locado'),
    'pagar': (Receivable, 'pagar'),
    'movimento': (FinancialMovement, 'movimento'),
    'pickups': (Pickup, None),
    'returns': (Return, None),
}

_SUSPICIOUS_MIN_YEAR = 1900
_SUSPICIOUS_MAX_YEAR = 2035
_SUSPICIOUS_RESULT_LIMIT = 500
_DATE_SCAN_BATCH_SIZE = 1000


def _brl(value):
    """Format Decimal/float as Brazilian currency string."""
    if value is None:
        value = Decimal('0')
    return f'R$ {value:,.2f}'


def _load_manifest(export_dir: Path):
    manifest_path = export_dir / 'manifest.json'
    if not manifest_path.exists():
        return None, f'manifest.json não encontrado em {export_dir}'
    try:
        with manifest_path.open(encoding='utf-8') as fh:
            return json.load(fh), None
    except Exception as exc:
        return None, f'Erro ao ler manifest.json: {exc}'


def _access_counts(manifest):
    """Return dict {table_name: row_count} from manifest tables list."""
    counts = {}
    for entry in manifest.get('tables', []):
        counts[entry['table']] = entry.get('row_count', 0)
    return counts


def _django_counts():
    """Return dict {entity_key: django_count}."""
    return {
        'clientes': Customer.objects.count(),
        'categoria': Category.objects.count(),
        'produtos': Product.objects.count(),
        'locado_itens': RentalItem.objects.count(),
        'locado_locacoes': Rental.objects.count(),
        'pagar': Receivable.objects.count(),
        'movimento': FinancialMovement.objects.count(),
        'pickups': Pickup.objects.count(),
        'returns': Return.objects.count(),
    }


# (entity_key, access_table, label, note)
_COUNT_ROWS = [
    ('clientes', 'clientes', 'clientes → Customer', ''),
    ('categoria', 'categoria', 'categoria → Category', ''),
    ('produtos', 'produtos', 'produtos → Product', ''),
    ('locado_itens', 'locado', 'locado → RentalItem (itens)', '71k itens = 1:1 com locado'),
    ('locado_locacoes', 'locado', 'locado → Rental (locações agrupadas)', 'agrupado por número'),
    ('pagar', 'pagar', 'pagar → Receivable', ''),
    ('movimento', 'movimento', 'movimento → FinancialMovement', ''),
    ('pickups', None, 'Pickups (retiradas)', 'sem equivalente direto em Access'),
    ('returns', None, 'Returns (devoluções)', 'sem equivalente direto em Access'),
]


def _build_counts_table(access_counts, django_counts):
    """Return list of (label, access_n, django_n, diff, note) tuples."""
    rows = []
    for key, access_table, label, note in _COUNT_ROWS:
        acc = access_counts.get(access_table, '-') if access_table else '-'
        dj = django_counts.get(key, 0)
        diff = (dj - acc) if isinstance(acc, int) else '-'
        rows.append((label, acc, dj, diff, note))
    return rows


def _placeholder_customers():
    return list(
        Customer.objects.filter(is_placeholder=True)
        .values('id', 'name', 'legacy_notes')
        .order_by('id')
    )


def _placeholder_categories():
    return list(
        Category.objects.filter(is_placeholder=True)
        .values('id', 'prefix', 'name')
        .order_by('prefix')
    )


def _placeholder_products():
    return list(
        Product.objects.filter(is_placeholder=True)
        .select_related('category')
        .values('id', 'description', 'category__prefix', 'code')
        .order_by('category__prefix', 'code')
    )


def _check_table_exists(cursor, table_name):
    return table_name in connection.introspection.table_names(cursor)


def _date_year(value):
    if value in (None, ''):
        return None

    year = getattr(value, 'year', None)
    if isinstance(year, int):
        return year

    text = str(value).strip()
    if not text:
        return None

    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).year
    except ValueError:
        pass

    for date_format in ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y'):
        try:
            return datetime.strptime(text, date_format).year
        except ValueError:
            continue
    return None


def _is_suspicious_date(value):
    year = _date_year(value)
    return year is not None and not (
        _SUSPICIOUS_MIN_YEAR <= year <= _SUSPICIOUS_MAX_YEAR
    )


def _find_suspicious_dates(cursor, table_name, selected_columns, date_columns):
    if not _check_table_exists(cursor, table_name):
        return None, 'tabelas raw não encontradas'

    quote_name = connection.ops.quote_name
    selected_sql = ', '.join(quote_name(column) for column in selected_columns)
    present_date_sql = ' OR '.join(
        f'{quote_name(column)} IS NOT NULL' for column in date_columns
    )
    try:
        cursor.execute(
            f'SELECT {selected_sql} FROM {quote_name(table_name)} '
            f'WHERE {present_date_sql} ORDER BY {quote_name("id")}'
        )
        columns = [description[0] for description in cursor.description]
        rows = []
        while len(rows) < _SUSPICIOUS_RESULT_LIMIT:
            batch = cursor.fetchmany(_DATE_SCAN_BATCH_SIZE)
            if not batch:
                break
            for values in batch:
                row = dict(zip(columns, values))
                if any(_is_suspicious_date(row[column]) for column in date_columns):
                    rows.append(row)
                    if len(rows) == _SUSPICIOUS_RESULT_LIMIT:
                        break
        return rows, None
    except Exception as exc:
        return None, str(exc)


def _suspicious_locado(cursor):
    """Return (rows, error_msg). rows is a list of dicts or None on error."""
    return _find_suspicious_dates(
        cursor,
        'legacy_locado',
        ('id', 'retirada', 'dev_prevista'),
        ('retirada', 'dev_prevista'),
    )


def _suspicious_pagar(cursor):
    """Return (rows, error_msg)."""
    return _find_suspicious_dates(
        cursor,
        'legacy_pagar',
        ('id', 'vencimento'),
        ('vencimento',),
    )


def _section_counts(count_rows):
    lines = [
        '## 1. Contagens: Access vs Django\n',
        '| Entidade (Access → Django) | Access | Django | Diferença | Nota |',
        '|---|---:|---:|---:|---|',
    ]
    for label, acc, dj, diff, note in count_rows:
        acc_str = f'{acc:,}' if isinstance(acc, int) else str(acc)
        dj_str = f'{dj:,}'
        diff_str = ('+' if isinstance(diff, int) and diff >= 0 else '') + (f'{diff:,}' if isinstance(diff, int) else str(diff))
        lines.append(f'| {label} | {acc_str} | {dj_str} | {diff_str} | {note} |')
    lines.append('')
    lines.append(
        'Legenda: Django pode ter mais registros que Access por placeholders '
        'criados automaticamente. Locado agrupado em Rentals.'
    )
    return '\n'.join(lines)


def _section_placeholders(ph_customers, ph_categories, ph_products):
    lines = ['## 2. Placeholders\n']

    lines.append(f'### Clientes placeholder ({len(ph_customers)})')
    if ph_customers:
        for c in ph_customers:
            notes = c['legacy_notes'] or ''
            lines.append(f"- ID {c['id']}: {c['name']}, {notes}")
    else:
        lines.append('Nenhum.')
    lines.append('')

    lines.append(f'### Categorias placeholder ({len(ph_categories)})')
    if ph_categories:
        for c in ph_categories:
            lines.append(f"- prefixo {c['prefix']}: {c['name']}")
    else:
        lines.append('Nenhuma.')
    lines.append('')

    lines.append(f'### Produtos placeholder ({len(ph_products)})')
    if ph_products:
        for p in ph_products:
            ref = f"{p['category__prefix']}{p['code']}"
            lines.append(f"- ID {p['id']}: {p['description']} ({ref})")
    else:
        lines.append('Nenhum.')
    return '\n'.join(lines)


def _section_dates(locado_rows, locado_error, pagar_rows, pagar_error):
    lines = ['## 3. Datas suspeitas\n']

    lines.append(
        f'### locado — datas fora do intervalo ({_SUSPICIOUS_MIN_YEAR}–{_SUSPICIOUS_MAX_YEAR})'
    )
    if locado_error:
        lines.append(locado_error)
    elif not locado_rows:
        lines.append('Nenhuma data suspeita encontrada.')
    else:
        lines.append('| id | retirada | dev_prevista |')
        lines.append('|---|---|---|')
        for row in locado_rows:
            lines.append(
                f"| {row.get('id', '')} | {row.get('retirada', '')} "
                f"| {row.get('dev_prevista', '')} |"
            )
    lines.append('')

    lines.append('### pagar — vencimentos fora do intervalo')
    if pagar_error:
        lines.append(pagar_error)
    elif not pagar_rows:
        lines.append('Nenhuma data suspeita encontrada.')
    else:
        lines.append('| id | vencimento |')
        lines.append('|---|---|')
        for row in pagar_rows:
            lines.append(f"| {row.get('id', '')} | {row.get('vencimento', '')} |")
    return '\n'.join(lines)


def _section_reconciliation(rec):
    lines = [
        '## 4. Reconciliação financeira\n',
        '| Indicador | Valor |',
        '|---|---:|',
        f"| Total recebíveis (amount) | {_brl(rec['total_receivable_amount'])} |",
        f"| Saldo em aberto | {_brl(rec['total_open_balance'])} |",
        f"| Total pagamentos | {_brl(rec['total_payments'])} |",
        f"| Total estornos | {_brl(rec['total_reversals'])} |",
        f"| Pagamentos líquidos | {_brl(rec['net_payments'])} |",
        f"| Total entradas (movimentos) | {_brl(rec['total_inflow'])} |",
        f"| Total saídas (movimentos) | {_brl(rec['total_outflow'])} |",
        f"| Saldo líquido (movimentos) | {_brl(rec['net_movements'])} |",
        f"| Recebíveis quitados sem Payment (legacy) | "
        f"{rec['paid_no_payments_count']:,} ({_brl(rec['paid_no_payments_sum'])}) |",
        f"| Inconsistências de saldo | {rec['inconsistent_count']:,} |",
        f"| Pagamentos sem movimento | {rec['payments_without_movement_count']:,} |",
    ]
    return '\n'.join(lines)


def _build_report(timestamp_str, count_rows, ph_customers, ph_categories, ph_products,
                  locado_rows, locado_error, pagar_rows, pagar_error, reconciliation):
    sections = [
        f'# Relatório de Homologação — {timestamp_str}\n',
        _section_counts(count_rows),
        '',
        _section_placeholders(ph_customers, ph_categories, ph_products),
        '',
        _section_dates(locado_rows, locado_error, pagar_rows, pagar_error),
        '',
        _section_reconciliation(reconciliation),
        '',
    ]
    return '\n'.join(sections)


class Command(BaseCommand):
    help = 'Gera relatório de homologação pós-importação (R14.02–R14.05).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default='var/homologation',
            help='Diretório de saída do relatório (default: var/homologation)',
        )
        parser.add_argument(
            '--export-dir',
            default='var/legacy_export',
            help='Diretório do export legado (default: var/legacy_export)',
        )

    def handle(self, *args, **options):
        export_dir = Path(options['export_dir'])
        output_dir = Path(options['output_dir'])
        output_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        timestamp_str = now.strftime('%Y-%m-%d %H:%M:%S')
        filename_ts = now.strftime('%Y-%m-%d-%H-%M-%S')
        report_path = output_dir / f'{filename_ts}-report.md'

        # 1. Load manifest
        manifest, manifest_error = _load_manifest(export_dir)
        if manifest_error:
            self.stderr.write(self.style.WARNING(f'Aviso: {manifest_error}'))
            access_counts = {}
        else:
            access_counts = _access_counts(manifest)

        # 2. Django counts
        django_counts = _django_counts()
        count_rows = _build_counts_table(access_counts, django_counts)

        # 3. Placeholders
        ph_customers = _placeholder_customers()
        ph_categories = _placeholder_categories()
        ph_products = _placeholder_products()

        # 4. Suspicious dates via raw SQL
        with connection.cursor() as cursor:
            locado_rows, locado_error = _suspicious_locado(cursor)
            pagar_rows, pagar_error = _suspicious_pagar(cursor)

        locado_count = len(locado_rows) if locado_rows else 0
        pagar_count = len(pagar_rows) if pagar_rows else 0

        # 5. Financial reconciliation
        try:
            reconciliation = reconcile_financial()
            recon_error = None
        except Exception as exc:
            reconciliation = {
                'total_receivable_amount': Decimal('0'),
                'total_open_balance': Decimal('0'),
                'total_payments': Decimal('0'),
                'total_reversals': Decimal('0'),
                'net_payments': Decimal('0'),
                'total_inflow': Decimal('0'),
                'total_outflow': Decimal('0'),
                'net_movements': Decimal('0'),
                'paid_no_payments_count': 0,
                'paid_no_payments_sum': Decimal('0'),
                'inconsistent_balances': [],
                'inconsistent_count': 0,
                'payments_without_movement_count': 0,
            }
            recon_error = str(exc)

        # 6. Build report
        report_md = _build_report(
            timestamp_str,
            count_rows,
            ph_customers,
            ph_categories,
            ph_products,
            locado_rows,
            locado_error,
            pagar_rows,
            pagar_error,
            reconciliation,
        )

        if recon_error:
            report_md += f'\n> Erro ao executar reconcile_financial(): {recon_error}\n'

        # 7. Write file
        report_path.write_text(report_md, encoding='utf-8')

        # 8. Print summary to stdout
        dj = django_counts
        acc = access_counts
        self.stdout.write(f'Relatório gerado: {report_path}')
        self.stdout.write(
            f"Contagens: clientes {dj['clientes']}/{acc.get('clientes', '?')}, "
            f"categorias {dj['categoria']}/{acc.get('categoria', '?')}, "
            f"produtos {dj['produtos']}/{acc.get('produtos', '?')}, "
            f"locações {dj['locado_locacoes']}/{acc.get('locado', '?')}, "
            f"recebíveis {dj['pagar']}/{acc.get('pagar', '?')}"
        )
        self.stdout.write(
            f'Placeholders: {len(ph_customers)} clientes, '
            f'{len(ph_categories)} categorias, '
            f'{len(ph_products)} produtos'
        )
        self.stdout.write(
            f'Datas suspeitas: {locado_count} em locado, {pagar_count} em pagar'
        )
        self.stdout.write(
            f"Reconciliação: {_brl(reconciliation['total_open_balance'])} aberto, "
            f"{reconciliation['inconsistent_count']} inconsistências"
        )
