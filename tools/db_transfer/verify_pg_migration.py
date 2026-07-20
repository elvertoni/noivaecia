"""Read-only verification of the SQLite -> PostgreSQL data migration.

Local ``db.sqlite3`` is the golden source. A separate transfer script copies
all business data + ``legacy_*`` audit tables into a PostgreSQL 16 database
that shares the same Django schema. This script never writes to either
database — it only compares them and prints a pt-BR pass/fail report.

Usage:
    .\\venv\\Scripts\\python.exe tools\\db_transfer\\verify_pg_migration.py \\
        --pg-url postgresql://user:pass@host:port/db

    # Validate the sqlite-side SQL and see local baseline numbers without a
    # reachable PostgreSQL instance:
    .\\venv\\Scripts\\python.exe tools\\db_transfer\\verify_pg_migration.py --self-test

Exit code: 0 = todas as verificacoes passaram, 1 = alguma verificacao falhou.
"""
import argparse
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SQLITE_PATH = BASE_DIR / 'db.sqlite3'

# Tables intentionally left out of the generic row-count comparison: pure
# framework/auth bookkeeping that is not business data and is not guaranteed
# (or required) to transfer 1:1.
EXCLUDED_TABLES = {
    'django_migrations',
    'django_session',
    'django_content_type',
    'auth_permission',
    'django_admin_log',
}

# Hardcoded expectations, valid after the 2026-07-19 data cleanup. If the
# source data changes again these constants must be updated deliberately.
EXPECTED_CATALOG_CATEGORY_COUNT = 68
EXPECTED_BILLING_PAYMENT_COUNT = 107
EXPECTED_RECEIVABLE_OVERPAID_COUNT = 1
EXPECTED_RECEIVABLE_OVERPAID_ID = 18

# (table, pk column) checked for "MAX(id) <= sequence current value" — this
# catches a transfer script that copied rows with explicit ids but forgot to
# bump the PostgreSQL sequence, which would break the next plain INSERT.
SEQUENCE_TABLES = [
    ('customers_customer', 'id'),
    ('catalog_product', 'id'),
    ('rentals_rental', 'id'),
    ('rentals_rentalitem', 'id'),
    ('billing_receivable', 'id'),
    ('billing_payment', 'id'),
]

# (table, column, pt-BR label) for exact financial checksums.
FINANCIAL_CHECKSUMS = [
    ('billing_receivable', 'amount', 'soma billing_receivable.amount'),
    ('billing_receivable', 'paid_amount', 'soma billing_receivable.paid_amount'),
    ('billing_payment', 'amount', 'soma billing_payment.amount'),
    ('billing_financialmovement', 'amount', 'soma billing_financialmovement.amount'),
    ('rentals_rental', 'total_value', 'soma rentals_rental.total_value'),
]

# (table, columns) for deterministic sample spot-checks.
SPOT_CHECK_TABLES = [
    ('customers_customer', ('id', 'name', 'city')),
    ('rentals_rental', ('id', 'number', 'status', 'total_value')),
    ('billing_receivable', ('id', 'amount', 'paid_amount')),
]
DECIMAL_COLUMNS = {'amount', 'paid_amount', 'total_value'}


class Report:
    """Accumulates (label, sqlite_value, pg_value, status) rows."""

    def __init__(self):
        self.rows = []
        self.failed = False

    def add(self, label, sqlite_value, pg_value, ok):
        status = 'OK' if ok else 'FALHA'
        if not ok:
            self.failed = True
        self.rows.append((label, str(sqlite_value), str(pg_value), status))

    def add_info(self, label, sqlite_value, pg_value='-'):
        """Row with no pass/fail semantics — informational baseline only."""
        self.rows.append((label, str(sqlite_value), str(pg_value), '-'))

    def print_summary(self):
        headers = ('Verificacao', 'SQLite', 'PostgreSQL', 'Status')
        max_widths = (68, 60, 60, 6)
        widths = [len(h) for h in headers]
        for row in self.rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], min(len(cell), max_widths[i]))

        def fmt_row(cells):
            parts = []
            for i, cell in enumerate(cells):
                if len(cell) > widths[i]:
                    cell = cell[:widths[i] - 1] + '…'
                parts.append(cell.ljust(widths[i]))
            return ' | '.join(parts)

        print(fmt_row(headers))
        print('-+-'.join('-' * w for w in widths))
        for row in self.rows:
            print(fmt_row(row))

        total = len(self.rows)
        ok_count = sum(1 for r in self.rows if r[3] == 'OK')
        fail_count = sum(1 for r in self.rows if r[3] == 'FALHA')
        info_count = total - ok_count - fail_count
        print()
        print(
            f'Total: {total} verificacoes | OK: {ok_count} | '
            f'FALHA: {fail_count} | informativas: {info_count}'
        )


def open_sqlite(path):
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise SystemExit(f'Banco sqlite nao encontrado: {resolved}')
    uri = resolved.as_uri() + '?mode=ro'
    return sqlite3.connect(uri, uri=True)


def open_postgres(pg_url):
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit(
            'O pacote psycopg nao esta instalado no ambiente atual.'
        ) from exc
    conn = psycopg.connect(pg_url, autocommit=True)
    with conn.cursor() as cur:
        # Extra safety net: this script must never write, even by accident.
        cur.execute('SET default_transaction_read_only = on')
    return conn


def sqlite_table_names(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row[0] for row in cur.fetchall()]


def pg_table_names(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        return [row[0] for row in cur.fetchall()]


def sqlite_count(conn, table):
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def pg_count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]


def sqlite_sum(conn, table, column):
    row = conn.execute(
        f'SELECT printf(\'%.2f\', COALESCE(SUM("{column}"), 0)) FROM "{table}"'
    ).fetchone()
    return Decimal(row[0])


def pg_sum(conn, table, column):
    with conn.cursor() as cur:
        cur.execute(f'SELECT COALESCE(SUM("{column}"), 0) FROM "{table}"')
        value = cur.fetchone()[0]
    return Decimal(value).quantize(Decimal('0.01'))


def normalize_value(value, is_decimal):
    if value is None:
        return None
    if is_decimal:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    return value


def fetch_spot_rows_sqlite(conn, table, columns, order):
    cols_sql = ', '.join(f'"{c}"' for c in columns)
    sql = f'SELECT {cols_sql} FROM "{table}" ORDER BY "id" {order} LIMIT 5'
    return conn.execute(sql).fetchall()


def fetch_spot_rows_pg(conn, table, columns, order):
    cols_sql = ', '.join(f'"{c}"' for c in columns)
    sql = f'SELECT {cols_sql} FROM "{table}" ORDER BY "id" {order} LIMIT 5'
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Individual check sections
# ---------------------------------------------------------------------------

def check_row_counts(sqlite_conn, pg_conn, report):
    sqlite_tables = set(sqlite_table_names(sqlite_conn)) - EXCLUDED_TABLES

    if pg_conn is None:
        for table in sorted(sqlite_tables):
            report.add_info(
                f'contagem: {table}', sqlite_count(sqlite_conn, table), 'N/A (self-test)'
            )
        return

    pg_tables = set(pg_table_names(pg_conn))
    common = sorted(sqlite_tables & pg_tables)
    missing_in_pg = sorted(sqlite_tables - pg_tables)

    for table in missing_in_pg:
        report.add(f'contagem: {table}', sqlite_count(sqlite_conn, table), 'TABELA AUSENTE', False)

    for table in common:
        s_count = sqlite_count(sqlite_conn, table)
        p_count = pg_count(pg_conn, table)
        report.add(f'contagem: {table}', s_count, p_count, s_count == p_count)


def check_financial_checksums(sqlite_conn, pg_conn, report):
    for table, column, label in FINANCIAL_CHECKSUMS:
        s_sum = sqlite_sum(sqlite_conn, table, column)
        if pg_conn is None:
            report.add_info(label, s_sum, 'N/A (self-test)')
            continue
        p_sum = pg_sum(pg_conn, table, column)
        report.add(label, s_sum, p_sum, s_sum == p_sum)


def check_company_singleton(pg_conn, report):
    label = 'invariante: company_company possui exatamente 1 registro'
    if pg_conn is None:
        report.add_info(label, 'N/A (checagem so faz sentido em pg)', 'N/A (self-test)')
        return
    count = pg_count(pg_conn, 'company_company')
    report.add(label, '-', count, count == 1)


def check_company_rental_number(sqlite_conn, pg_conn, report):
    label = 'invariante: company.last_rental_number >= MAX(rentals_rental.number)'

    def read(conn, is_pg):
        if is_pg:
            with conn.cursor() as cur:
                cur.execute('SELECT last_rental_number FROM company_company LIMIT 1')
                row = cur.fetchone()
                last = row[0] if row else None
                cur.execute('SELECT MAX(number) FROM rentals_rental')
                max_number = cur.fetchone()[0]
        else:
            row = conn.execute('SELECT last_rental_number FROM company_company LIMIT 1').fetchone()
            last = row[0] if row else None
            max_number = conn.execute('SELECT MAX(number) FROM rentals_rental').fetchone()[0]
        return last, max_number

    s_last, s_max = read(sqlite_conn, False)
    sqlite_ok = s_last is not None and s_max is not None and s_last >= s_max
    sqlite_display = f'last_rental_number={s_last} max(number)={s_max}'

    if pg_conn is None:
        report.add(label, sqlite_display, 'N/A (self-test)', sqlite_ok)
        return

    p_last, p_max = read(pg_conn, True)
    pg_ok = p_last is not None and p_max is not None and p_last >= p_max
    pg_display = f'last_rental_number={p_last} max(number)={p_max}'
    report.add(label, sqlite_display, pg_display, pg_ok)


def check_sequences(pg_conn, report):
    if pg_conn is None:
        for table, column in SEQUENCE_TABLES:
            report.add_info(
                f'sequencia: MAX({column}) <= last_value ({table})',
                'N/A (checagem exclusiva de PostgreSQL)',
                'N/A (self-test)',
            )
        return

    with pg_conn.cursor() as cur:
        for table, column in SEQUENCE_TABLES:
            label = f'sequencia: MAX({column}) <= last_value ({table})'
            cur.execute(f'SELECT MAX("{column}") FROM "{table}"')
            max_id = cur.fetchone()[0] or 0
            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, column))
            seq_name = cur.fetchone()[0]
            if seq_name is None:
                report.add(label, '-', 'sequence nao encontrada', False)
                continue
            cur.execute(f'SELECT last_value FROM {seq_name}')
            last_value = cur.fetchone()[0]
            report.add(label, '-', f'max_id={max_id} last_value={last_value}', max_id <= last_value)


def check_overpaid_receivables(sqlite_conn, pg_conn, report):
    label = (
        f'billing_receivable com paid_amount > amount '
        f'(esperado {EXPECTED_RECEIVABLE_OVERPAID_COUNT}, caso legado id={EXPECTED_RECEIVABLE_OVERPAID_ID})'
    )
    s_count = sqlite_conn.execute(
        'SELECT COUNT(*) FROM billing_receivable WHERE paid_amount > amount'
    ).fetchone()[0]
    sqlite_ok = s_count == EXPECTED_RECEIVABLE_OVERPAID_COUNT

    if pg_conn is None:
        report.add(label, s_count, 'N/A (self-test)', sqlite_ok)
        return

    with pg_conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM billing_receivable WHERE paid_amount > amount')
        p_count = cur.fetchone()[0]
    ok = sqlite_ok and p_count == EXPECTED_RECEIVABLE_OVERPAID_COUNT
    report.add(label, s_count, p_count, ok)


def check_expected_counts(sqlite_conn, pg_conn, report):
    checks = [
        ('catalog_category', EXPECTED_CATALOG_CATEGORY_COUNT),
        ('billing_payment', EXPECTED_BILLING_PAYMENT_COUNT),
    ]
    for table, expected in checks:
        label = f'{table}: contagem = {expected} (esperado pos-limpeza 2026-07-19)'
        s_count = sqlite_count(sqlite_conn, table)
        if pg_conn is None:
            report.add(label, s_count, 'N/A (self-test)', s_count == expected)
            continue
        p_count = pg_count(pg_conn, table)
        report.add(label, s_count, p_count, s_count == expected and p_count == expected)


def check_legacy_audit(sqlite_conn, pg_conn, report):
    label = 'legacy_import_audit possui >= 1 linha'
    s_count = sqlite_count(sqlite_conn, 'legacy_import_audit')
    if pg_conn is None:
        report.add(label, s_count, 'N/A (self-test)', s_count >= 1)
        return
    p_count = pg_count(pg_conn, 'legacy_import_audit')
    report.add(label, s_count, p_count, p_count >= 1)


def check_spot_samples(sqlite_conn, pg_conn, report):
    for table, columns in SPOT_CHECK_TABLES:
        for order_label, order in (('menores ids', 'ASC'), ('maiores ids', 'DESC')):
            label = f'spot-check {table} ({order_label})'
            s_rows = fetch_spot_rows_sqlite(sqlite_conn, table, columns, order)

            if pg_conn is None:
                report.add_info(label, f'{len(s_rows)} linhas — {s_rows}', 'N/A (self-test)')
                continue

            p_rows = fetch_spot_rows_pg(pg_conn, table, columns, order)
            ok = len(s_rows) == len(p_rows)
            if ok:
                for s_row, p_row in zip(s_rows, p_rows):
                    for col, s_val, p_val in zip(columns, s_row, p_row):
                        is_decimal = col in DECIMAL_COLUMNS
                        if normalize_value(s_val, is_decimal) != normalize_value(p_val, is_decimal):
                            ok = False
            report.add(label, f'{len(s_rows)} linhas', f'{len(p_rows)} linhas', ok)
            if not ok:
                print(f'  Diferenca detectada em {label}:')
                print(f'    sqlite: {s_rows}')
                print(f'    pg:     {p_rows}')


def setup_stdout_utf8():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(
        description='Verifica (somente leitura) a migracao SQLite -> PostgreSQL.'
    )
    parser.add_argument(
        '--pg-url', help='URL de conexao PostgreSQL (postgresql://user:pass@host:port/db)'
    )
    parser.add_argument(
        '--self-test',
        action='store_true',
        help=(
            'Executa apenas as consultas do lado SQLite, sem PostgreSQL. '
            'Valida o SQL e mostra os valores locais como baseline.'
        ),
    )
    parser.add_argument(
        '--sqlite-path',
        default=str(DEFAULT_SQLITE_PATH),
        help='Caminho para o db.sqlite3 (padrao: raiz do projeto).',
    )
    args = parser.parse_args()

    if not args.self_test and not args.pg_url:
        parser.error('informe --pg-url ou use --self-test')

    setup_stdout_utf8()

    sqlite_conn = open_sqlite(args.sqlite_path)
    pg_conn = None
    try:
        if not args.self_test:
            pg_conn = open_postgres(args.pg_url)

        report = Report()
        mode_label = ' (SELF-TEST — apenas SQLite)' if args.self_test else ''
        print('=' * 78)
        print(f'Verificacao de migracao SQLite -> PostgreSQL{mode_label}')
        print('=' * 78)

        print('\n[1/6] Contagem de linhas por tabela...')
        check_row_counts(sqlite_conn, pg_conn, report)

        print('[2/6] Checksums financeiros...')
        check_financial_checksums(sqlite_conn, pg_conn, report)

        print('[3/6] Invariantes de negocio...')
        check_company_singleton(pg_conn, report)
        check_company_rental_number(sqlite_conn, pg_conn, report)
        check_sequences(pg_conn, report)
        check_overpaid_receivables(sqlite_conn, pg_conn, report)
        check_expected_counts(sqlite_conn, pg_conn, report)

        print('[4/6] Auditoria de importacao legada...')
        check_legacy_audit(sqlite_conn, pg_conn, report)

        print('[5/6] Spot-checks de amostras deterministicas...')
        check_spot_samples(sqlite_conn, pg_conn, report)

        print('\n[6/6] Resumo final\n')
        report.print_summary()

        if args.self_test:
            print(
                '\nModo --self-test: linhas com status "-" sao apenas baseline '
                'local (nao ha PostgreSQL para comparar). As demais linhas ja '
                'refletem verificacoes reais contra os valores esperados.'
            )

        if report.failed:
            print('\nRESULTADO: FALHA — uma ou mais verificacoes nao passaram.')
            return 1
        print('\nRESULTADO: OK — todas as verificacoes passaram.')
        return 0
    finally:
        sqlite_conn.close()
        if pg_conn is not None:
            pg_conn.close()


if __name__ == '__main__':
    sys.exit(main())
