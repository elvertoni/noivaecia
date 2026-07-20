"""One-shot data transfer from the local SQLite golden source into a remote
PostgreSQL 16 database whose schema was already created by
``manage.py migrate`` at the exact same code commit (so Django table/column
shapes match on both sides).

Usage
-----
    .\\venv\\Scripts\\python.exe tools\\db_transfer\\sqlite_to_pg.py --pg-url postgresql://user:pass@host:port/db [--dry-run]
    .\\venv\\Scripts\\python.exe tools\\db_transfer\\sqlite_to_pg.py --self-test

Everything happens inside a single PostgreSQL transaction: one COMMIT at the
very end, or a ROLLBACK on the first error (or always, with --dry-run). No
partial writes are ever left behind.

Design notes / assumptions to review before running against production
------------------------------------------------------------------------
* Table selection: any table present in BOTH sqlite and the pg ``public``
  schema is transferred, except the framework tables in ``EXCLUDED_TABLES``
  (migrations/session/content-type/permission/admin-log — these must keep
  the *remote* install's own state; content-type and permission ids are
  never portable between two independently migrated databases). All sqlite
  ``legacy_%`` tables are transferred unconditionally and created on the fly
  with ``CREATE TABLE IF NOT EXISTS`` if pg does not have them yet.
* The two Django m2m tables that reference ``auth_permission``
  (``accounts_user_user_permissions`` and ``auth_group_permissions``) are
  always skipped, even though they otherwise qualify: permission ids are
  assigned independently by each install's own migration + auto-generated
  permissions, so copying these rows would silently point at unrelated (or
  missing) permissions on the target. Re-grant permissions manually on the
  target after transfer if needed.
* FK ordering: contrary to a common assumption, plain Django ``ForeignKey``
  columns are **not** created as ``DEFERRABLE`` in PostgreSQL by default —
  Django only marks a constraint deferrable when a model explicitly uses
  ``Meta.constraints`` with ``Deferrable.DEFERRED``. This project does not
  do that (checked accounts/company/customers/catalog/rentals/billing/
  movements/core models), so constraints are checked immediately at the end
  of each statement. We still issue ``SET CONSTRAINTS ALL DEFERRED`` at the
  top of the transaction as a harmless no-op safety net (it only affects
  constraints actually marked deferrable; non-deferrable ones are simply
  unaffected, it never errors). The real safety comes from ``PREFERRED_ORDER``
  below, which copies parents strictly before children, so every FK target
  row already exists by the time a referencing row's COPY statement runs.
* ``TRUNCATE ... CASCADE`` on a single statement listing every target table
  is used for the wipe step; CASCADE makes intra-list ordering irrelevant
  for the truncate itself.
* Datetime handling: this project runs with ``USE_TZ = True`` and
  ``TIME_ZONE = 'America/Sao_Paulo'``. Django's sqlite backend stores
  ``DateTimeField`` values as *naive* UTC strings (no offset). When such a
  value targets a PostgreSQL ``timestamp with time zone`` column, we attach
  ``timezone.utc`` explicitly before handing it to psycopg — otherwise
  psycopg would send a naive timestamp and PostgreSQL would interpret it
  using the *session* timezone instead of UTC, silently shifting every
  value. ``timestamp without time zone`` columns (none expected from Django
  DateTimeField, but handled defensively) are left naive.
* Only stdlib + psycopg (already a project dependency) are used — no new
  dependencies.
* This script never runs itself against a real PostgreSQL database as part
  of authoring/testing it; only ``--self-test`` (pure in-memory, no network)
  has been executed.
"""

import argparse
import decimal
import json
import sqlite3
import sys
import time
from datetime import date, datetime, time as dt_time, timezone

try:
    import psycopg
except ImportError:  # pragma: no cover - only --self-test can run without it
    psycopg = None


SQLITE_PATH = 'db.sqlite3'

# Framework tables that must keep the *target* install's own state and/or
# whose ids are not portable between independently migrated databases.
EXCLUDED_TABLES = {
    'django_migrations',
    'django_session',
    'django_content_type',
    'auth_permission',
    'django_admin_log',
    'sqlite_sequence',
}

# m2m tables referencing auth_permission (excluded) — copying them would
# create dangling / mismatched permission references on the target.
SKIPPED_M2M_TABLES = {
    'accounts_user_user_permissions',
    'auth_group_permissions',
}

LEGACY_PREFIX = 'legacy_'

# Topologically valid copy order — parents strictly before children — read
# directly off the FK columns declared in accounts/company/customers/catalog/
# rentals/billing/movements/core models:
#   catalog_product        -> catalog_category
#   rentals_rental          -> customers_customer, accounts_user (cancelled_by)
#   rentals_rentalitem      -> rentals_rental, catalog_product
#   billing_receivable      -> rentals_rental
#   billing_payment         -> billing_receivable, customers_customer,
#                              rentals_rental, accounts_user
#   billing_financialmovement -> billing_cashaccount, customers_customer,
#                                 billing_receivable, billing_payment,
#                                 rentals_rental, accounts_user
#   movements_pickup/return -> rentals_rental
#   core_auditlog           -> accounts_user
#   accounts_user_groups    -> accounts_user, auth_group
PREFERRED_ORDER = [
    'company_company',
    'accounts_user',
    'auth_group',
    'accounts_user_groups',
    'accounts_modulepermission',
    'accounts_actionpermission',
    'customers_customer',
    'catalog_category',
    'catalog_product',
    'rentals_rental',
    'rentals_rentalitem',
    'billing_cashaccount',
    'billing_receivable',
    'billing_payment',
    'billing_financialmovement',
    'movements_pickup',
    'movements_return',
    'core_auditlog',
]

# SQLite type affinity (from PRAGMA table_info) -> PostgreSQL column type,
# used only to CREATE the legacy_% tables that do not yet exist on target.
SQLITE_TO_PG_TYPE = {
    'INTEGER': 'bigint',
    'INT': 'bigint',
    'BIGINT': 'bigint',
    'SMALLINT': 'bigint',
    'REAL': 'double precision',
    'FLOAT': 'double precision',
    'DOUBLE': 'double precision',
    'TEXT': 'text',
    'CHAR': 'text',
    'VARCHAR': 'text',
    'CLOB': 'text',
    'BLOB': 'bytea',
    'NUMERIC': 'numeric',
    'DECIMAL': 'numeric',
    'BOOLEAN': 'boolean',
    'DATE': 'date',
    'DATETIME': 'timestamp',
    '': 'text',
}


# ---------------------------------------------------------------------------
# Pure helpers (covered by --self-test, no DB connection required)
# ---------------------------------------------------------------------------

def quote_ident(name):
    """Double-quote a SQL identifier, escaping embedded double quotes.

    Used for EVERY identifier in every generated statement (table and
    column names alike) because several legacy columns carry accents and
    spaces (e.g. ``locação``, ``valor pago``) that require quoting in both
    SQLite and PostgreSQL. Never build a bare, unquoted identifier anywhere
    in this script.
    """
    return '"' + str(name).replace('"', '""') + '"'


def sqlite_type_to_pg(sqlite_type):
    """Translate a SQLite column type affinity string into a PostgreSQL type."""
    base = (sqlite_type or '').strip().upper()
    base = base.split('(')[0].strip()  # drop any length modifier, e.g. VARCHAR(50)
    return SQLITE_TO_PG_TYPE.get(base, 'text')


def build_create_table_sql(table, columns):
    """Build a ``CREATE TABLE IF NOT EXISTS`` statement for a legacy table.

    ``columns`` is a sequence of ``(name, sqlite_type)`` pairs, in the order
    reported by ``PRAGMA table_info``.
    """
    col_defs = ', '.join(
        f'{quote_ident(name)} {sqlite_type_to_pg(sqtype)}' for name, sqtype in columns
    )
    return f'CREATE TABLE IF NOT EXISTS {quote_ident(table)} ({col_defs})'


def build_select_sql(table, columns):
    """Build a ``SELECT`` over explicit, quoted columns (works against sqlite
    too — SQLite also treats double quotes as identifier quoting)."""
    cols = ', '.join(quote_ident(c) for c in columns)
    return f'SELECT {cols} FROM {quote_ident(table)}'


def build_copy_sql(table, columns):
    """Build a ``COPY ... FROM STDIN`` statement with explicit column list."""
    cols = ', '.join(quote_ident(c) for c in columns)
    return f'COPY {quote_ident(table)} ({cols}) FROM STDIN'


def build_truncate_sql(tables):
    """Build a single multi-table ``TRUNCATE ... CASCADE`` statement."""
    idents = ', '.join(quote_ident(t) for t in tables)
    return f'TRUNCATE TABLE {idents} CASCADE'


def parse_datetime_value(value):
    """Parse a sqlite-stored datetime string/object into a ``datetime``."""
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    text = text.replace('T', ' ')
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Fall back for odd trailing 'Z' or other minor deviations.
        return datetime.fromisoformat(text.rstrip('Z'))


def parse_date_value(value):
    """Parse a sqlite-stored date string/object into a ``date``."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()[:10]
    return date.fromisoformat(text)


def parse_time_value(value):
    """Parse a sqlite-stored time string/object into a ``time``."""
    if isinstance(value, dt_time):
        return value
    return dt_time.fromisoformat(str(value).strip())


def convert_value(value, pg_type):
    """Convert a raw sqlite value into the Python type psycopg expects for
    the destination PostgreSQL column type.

    ``pg_type`` is the lowercase ``information_schema.columns.data_type``
    string for the target column. NULLs pass through untouched.
    """
    if value is None:
        return None

    pg_type = (pg_type or '').lower()

    if pg_type == 'boolean':
        return bool(int(value)) if not isinstance(value, bool) else value

    if pg_type in ('integer', 'bigint', 'smallint'):
        return int(value)

    if pg_type == 'numeric':
        return decimal.Decimal(str(value))

    if pg_type in ('double precision', 'real'):
        return float(value)

    if pg_type in ('timestamp with time zone', 'timestamp without time zone'):
        parsed = parse_datetime_value(value)
        if pg_type == 'timestamp with time zone' and parsed.tzinfo is None:
            # Django's sqlite backend stores naive UTC strings when
            # USE_TZ=True; attach UTC explicitly so PostgreSQL does not
            # reinterpret the naive value using the session timezone.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    if pg_type == 'date':
        return parse_date_value(value)

    if pg_type in ('time with time zone', 'time without time zone'):
        return parse_time_value(value)

    if pg_type == 'bytea':
        return bytes(value) if not isinstance(value, (bytes, bytearray)) else bytes(value)

    if pg_type in ('json', 'jsonb'):
        parsed = json.loads(value) if isinstance(value, str) else value
        if psycopg is not None:
            from psycopg.types.json import Jsonb
            return Jsonb(parsed)
        return parsed

    # text, character varying, uuid, etc. — pass through as-is.
    return value


# ---------------------------------------------------------------------------
# Introspection helpers (require live connections)
# ---------------------------------------------------------------------------

def get_sqlite_tables(sqlite_conn):
    cur = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in cur.fetchall()}


def get_sqlite_columns(sqlite_conn, table):
    """Return an ordered list of ``(name, sqlite_type)`` for ``table``."""
    cur = sqlite_conn.execute(f'PRAGMA table_info({quote_ident(table)})')
    return [(row[1], row[2]) for row in cur.fetchall()]


def get_pg_tables(pg_conn):
    cur = pg_conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    return {row[0] for row in cur.fetchall()}


def get_pg_columns(pg_conn, table):
    """Return an ordered dict-like list of ``(name, data_type)`` for ``table``
    in the pg ``public`` schema, as a plain dict name -> data_type."""
    cur = pg_conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position",
        (table,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Transfer orchestration
# ---------------------------------------------------------------------------

def order_tables(tables):
    """Order transfer tables: PREFERRED_ORDER entries first (in that order),
    then any remaining non-legacy tables alphabetically, then legacy_% tables
    last (alphabetically)."""
    remaining = set(tables)
    ordered = [t for t in PREFERRED_ORDER if t in remaining]
    remaining -= set(ordered)

    legacy = sorted(t for t in remaining if t.startswith(LEGACY_PREFIX))
    remaining -= set(legacy)

    rest = sorted(remaining)

    return ordered + rest + legacy


def determine_transfer_tables(sqlite_tables, pg_tables):
    """Apply the table-selection rules and return the final ordered list,
    plus any warnings collected along the way."""
    warnings = []

    intersection = (sqlite_tables & pg_tables) - EXCLUDED_TABLES

    skipped_present = intersection & SKIPPED_M2M_TABLES
    if skipped_present:
        warnings.append(
            'Skipping m2m tables referencing auth_permission (ids are not '
            f'portable between installs): {sorted(skipped_present)}'
        )
    intersection -= SKIPPED_M2M_TABLES

    legacy_tables = {t for t in sqlite_tables if t.startswith(LEGACY_PREFIX)}

    transfer_tables = intersection | legacy_tables
    return order_tables(transfer_tables), warnings


def resolve_common_columns(table, sqlite_columns, pg_columns, is_legacy):
    """Return the list of column names (in sqlite order) to transfer, after
    validating that sqlite and pg agree on the column set.

    For non-legacy (Django-managed) tables a mismatch is fatal — the whole
    point is that pg schema was migrated from the same commit, so sqlite and
    pg columns must match exactly. For legacy tables (freshly created from
    the sqlite schema, or pre-existing with possible drift) a mismatch is
    only a warning.
    """
    sqlite_names = [name for name, _ in sqlite_columns]
    sqlite_set = set(sqlite_names)
    pg_set = set(pg_columns.keys())

    only_sqlite = sqlite_set - pg_set
    only_pg = pg_set - sqlite_set

    if only_sqlite or only_pg:
        message = (
            f'Column mismatch for table "{table}": only in sqlite={sorted(only_sqlite)}, '
            f'only in pg={sorted(only_pg)}'
        )
        if not is_legacy:
            raise RuntimeError(message)
        print(f'WARNING: {message}')

    return [name for name in sqlite_names if name in pg_set]


def transfer_table(sqlite_conn, pg_conn, table, is_legacy):
    """Copy one table's rows from sqlite into pg. Returns (rows_read, rows_written)."""
    sqlite_columns = get_sqlite_columns(sqlite_conn, table)
    pg_columns = get_pg_columns(pg_conn, table)

    common_columns = resolve_common_columns(table, sqlite_columns, pg_columns, is_legacy)
    if not common_columns:
        print(f'  {table}: no common columns, skipping')
        return 0, 0

    select_sql = build_select_sql(table, common_columns)
    copy_sql = build_copy_sql(table, common_columns)
    pg_types = [pg_columns[name] for name in common_columns]

    sqlite_cur = sqlite_conn.execute(select_sql)

    rows_read = 0
    with pg_conn.cursor() as pg_cur:
        with pg_cur.copy(copy_sql) as copy:
            for row in sqlite_cur:
                converted = tuple(
                    convert_value(value, pg_type)
                    for value, pg_type in zip(row, pg_types)
                )
                copy.write_row(converted)
                rows_read += 1

        pg_cur.execute(f'SELECT count(*) FROM {quote_ident(table)}')
        rows_in_pg = pg_cur.fetchone()[0]

    if rows_in_pg != rows_read:
        raise RuntimeError(
            f'Row count mismatch for table "{table}": read {rows_read} from sqlite, '
            f'found {rows_in_pg} in pg after copy'
        )

    return rows_read, rows_in_pg


def reset_sequence(pg_conn, table, pg_columns):
    """Reset the table's ``id`` sequence to MAX(id), if it has one."""
    if 'id' not in pg_columns:
        return None

    with pg_conn.cursor() as cur:
        cur.execute('SELECT pg_get_serial_sequence(%s, %s)', (table, 'id'))
        sequence_name = cur.fetchone()[0]
        if sequence_name is None:
            return None  # e.g. legacy tables have no serial/identity column

        cur.execute(
            f'SELECT setval(%s, COALESCE((SELECT MAX({quote_ident("id")}) '
            f'FROM {quote_ident(table)}), 1))',
            (sequence_name,),
        )
        return cur.fetchone()[0]


def run_transfer(pg_url, dry_run):
    sys.stdout.reconfigure(encoding='utf-8')

    sqlite_conn = sqlite3.connect(f'file:{SQLITE_PATH}?mode=ro', uri=True)
    sqlite_conn.text_factory = str

    pg_conn = psycopg.connect(pg_url, autocommit=False)

    try:
        sqlite_tables = get_sqlite_tables(sqlite_conn)
        pg_tables_before = get_pg_tables(pg_conn)

        transfer_tables, warnings = determine_transfer_tables(sqlite_tables, pg_tables_before)
        for warning in warnings:
            print(f'WARNING: {warning}')

        print(f'Tables to transfer ({len(transfer_tables)}): {transfer_tables}')

        # Harmless safety net — see module docstring on FK deferrability.
        pg_conn.execute('SET CONSTRAINTS ALL DEFERRED')

        truncate_targets = [t for t in transfer_tables if t in pg_tables_before]
        if truncate_targets:
            print(f'Truncating {len(truncate_targets)} existing tables...')
            pg_conn.execute(build_truncate_sql(truncate_targets))

        for table in transfer_tables:
            if table.startswith(LEGACY_PREFIX) and table not in pg_tables_before:
                columns = get_sqlite_columns(sqlite_conn, table)
                create_sql = build_create_table_sql(table, columns)
                pg_conn.execute(create_sql)
                print(f'Created legacy table "{table}"')

        summary = []
        for table in transfer_tables:
            is_legacy = table.startswith(LEGACY_PREFIX)
            start = time.perf_counter()
            rows_read, rows_written = transfer_table(sqlite_conn, pg_conn, table, is_legacy)
            elapsed = time.perf_counter() - start
            print(f'  {table}: read {rows_read}, written {rows_written} ({elapsed:.2f}s)')
            summary.append((table, rows_read, rows_written, elapsed))

        print('Resetting sequences...')
        for table in transfer_tables:
            pg_columns = get_pg_columns(pg_conn, table)
            seq = reset_sequence(pg_conn, table, pg_columns)
            if seq:
                print(f'  {table}: sequence "{seq}" reset')

        print('\n--- Summary ---')
        total_rows = 0
        total_time = 0.0
        for table, rows_read, rows_written, elapsed in summary:
            print(f'{table:40s} read={rows_read:>8d} written={rows_written:>8d} {elapsed:>6.2f}s')
            total_rows += rows_written
            total_time += elapsed
        print(f'{"TOTAL":40s} {"":>13s} written={total_rows:>8d} {total_time:>6.2f}s')

        if dry_run:
            pg_conn.rollback()
            print('\nDRY RUN: transaction rolled back, no changes were committed.')
        else:
            pg_conn.commit()
            print('\nCOMMIT: transaction committed successfully.')

    except Exception:
        pg_conn.rollback()
        print('\nERROR: transaction rolled back, no changes were made.', file=sys.stderr)
        raise
    finally:
        pg_conn.close()
        sqlite_conn.close()


# ---------------------------------------------------------------------------
# Self-test (pure, no network / no pg connection)
# ---------------------------------------------------------------------------

def run_self_test():
    """Exercise the pure helper functions (identifier quoting, type
    translation, value conversion, SQL builders) against an in-memory
    sqlite database. Never touches PostgreSQL."""
    sys.stdout.reconfigure(encoding='utf-8')
    failures = []

    def check(label, condition):
        status = 'OK' if condition else 'FAIL'
        print(f'[{status}] {label}')
        if not condition:
            failures.append(label)

    # --- quote_ident -------------------------------------------------
    check('quote_ident: plain name', quote_ident('cliente') == '"cliente"')
    check(
        'quote_ident: accented name',
        quote_ident('locação') == '"locação"',
    )
    check(
        'quote_ident: name with space',
        quote_ident('valor pago') == '"valor pago"',
    )
    check(
        'quote_ident: embedded double quote is escaped',
        quote_ident('wei"rd') == '"wei""rd"',
    )

    # --- sqlite_type_to_pg --------------------------------------------
    check('type map: INTEGER -> bigint', sqlite_type_to_pg('INTEGER') == 'bigint')
    check('type map: REAL -> double precision', sqlite_type_to_pg('REAL') == 'double precision')
    check('type map: TEXT -> text', sqlite_type_to_pg('TEXT') == 'text')
    check('type map: BLOB -> bytea', sqlite_type_to_pg('BLOB') == 'bytea')
    check('type map: NUMERIC -> numeric', sqlite_type_to_pg('NUMERIC') == 'numeric')
    check('type map: VARCHAR(50) -> text', sqlite_type_to_pg('VARCHAR(50)') == 'text')
    check('type map: empty/unknown -> text', sqlite_type_to_pg('') == 'text')

    # --- in-memory sqlite with 2 tiny tables, one with tricky names ----
    conn = sqlite3.connect(':memory:')
    conn.execute(
        'CREATE TABLE regular_test ('
        'id INTEGER PRIMARY KEY, name TEXT, active INTEGER, '
        'price NUMERIC, created DATETIME)'
    )
    conn.execute(
        'INSERT INTO regular_test VALUES (1, \'Ana\', 1, \'123.45\', \'2026-07-19 10:30:00\')'
    )

    conn.execute(
        f'CREATE TABLE legacy_test ({quote_ident("id")} INTEGER, '
        f'{quote_ident("locação")} TEXT, {quote_ident("valor total")} REAL)'
    )
    conn.execute(
        f'INSERT INTO legacy_test ({quote_ident("id")}, {quote_ident("locação")}, '
        f'{quote_ident("valor total")}) VALUES (1, \'evento\', 99.5)'
    )
    conn.commit()

    regular_columns = get_sqlite_columns(conn, 'regular_test')
    check(
        'get_sqlite_columns: regular_test column names/order',
        [c for c, _ in regular_columns] == ['id', 'name', 'active', 'price', 'created'],
    )

    legacy_columns = get_sqlite_columns(conn, 'legacy_test')
    check(
        'get_sqlite_columns: legacy_test preserves accented/space names',
        [c for c, _ in legacy_columns] == ['id', 'locação', 'valor total'],
    )

    create_sql = build_create_table_sql('legacy_test', legacy_columns)
    check(
        'build_create_table_sql: quotes every identifier',
        '"legacy_test"' in create_sql
        and '"locação"' in create_sql
        and '"valor total"' in create_sql
        and 'CREATE TABLE IF NOT EXISTS' in create_sql,
    )
    print(f'  generated SQL: {create_sql}')

    select_sql = build_select_sql('legacy_test', [c for c, _ in legacy_columns])
    check(
        'build_select_sql: quotes columns and table',
        select_sql == 'SELECT "id", "locação", "valor total" FROM "legacy_test"',
    )

    row = conn.execute(select_sql).fetchone()
    check('sqlite round-trip: row read back with tricky columns', row == (1, 'evento', 99.5))

    copy_sql = build_copy_sql('regular_test', ['id', 'name'])
    check(
        'build_copy_sql: well-formed COPY statement',
        copy_sql == 'COPY "regular_test" ("id", "name") FROM STDIN',
    )

    truncate_sql = build_truncate_sql(['a', 'b', 'legacy_x'])
    check(
        'build_truncate_sql: single statement, all tables, CASCADE',
        truncate_sql == 'TRUNCATE TABLE "a", "b", "legacy_x" CASCADE',
    )

    # --- convert_value ---------------------------------------------------
    check('convert_value: None passes through', convert_value(None, 'integer') is None)
    check('convert_value: integer', convert_value('42', 'integer') == 42 and isinstance(convert_value('42', 'integer'), int))
    check('convert_value: boolean from 1/0', convert_value(1, 'boolean') is True and convert_value(0, 'boolean') is False)
    check(
        'convert_value: numeric -> Decimal',
        convert_value('123.45', 'numeric') == decimal.Decimal('123.45'),
    )
    check('convert_value: double precision -> float', convert_value('1.5', 'double precision') == 1.5)
    check(
        'convert_value: date',
        convert_value('2026-07-19', 'date') == date(2026, 7, 19),
    )
    naive_dt = convert_value('2026-07-19 10:30:00', 'timestamp without time zone')
    check(
        'convert_value: naive timestamp stays naive',
        naive_dt == datetime(2026, 7, 19, 10, 30, 0) and naive_dt.tzinfo is None,
    )
    tz_dt = convert_value('2026-07-19 10:30:00.123456', 'timestamp with time zone')
    check(
        'convert_value: timestamptz gets UTC attached',
        tz_dt.tzinfo == timezone.utc
        and tz_dt.replace(tzinfo=None) == datetime(2026, 7, 19, 10, 30, 0, 123456),
    )
    check(
        'convert_value: bytea',
        convert_value(b'\x01\x02', 'bytea') == b'\x01\x02',
    )
    check(
        'convert_value: text passthrough',
        convert_value('hello', 'character varying') == 'hello',
    )
    jsonb_value = convert_value('{"a": 1}', 'jsonb')
    if psycopg is not None:
        from psycopg.types.json import Jsonb
        check('convert_value: jsonb wrapped with Jsonb', isinstance(jsonb_value, Jsonb))
    else:
        check('convert_value: jsonb parsed as dict (no psycopg)', jsonb_value == {'a': 1})

    # --- table selection / ordering logic (no live pg connection) --------
    sqlite_tables = {
        'accounts_user', 'auth_group', 'auth_permission', 'auth_group_permissions',
        'accounts_user_user_permissions', 'accounts_user_groups', 'django_migrations',
        'django_session', 'django_content_type', 'django_admin_log', 'sqlite_sequence',
        'company_company', 'customers_customer', 'catalog_category', 'catalog_product',
        'rentals_rental', 'rentals_rentalitem', 'billing_cashaccount', 'billing_receivable',
        'legacy_clientes', 'legacy_locado',
    }
    pg_tables = sqlite_tables - {'legacy_clientes', 'legacy_locado', 'sqlite_sequence'}

    ordered, table_warnings = determine_transfer_tables(sqlite_tables, pg_tables)
    check(
        'determine_transfer_tables: excludes framework tables',
        not ({'django_migrations', 'django_session', 'django_content_type',
              'auth_permission', 'django_admin_log', 'sqlite_sequence'} & set(ordered)),
    )
    check(
        'determine_transfer_tables: skips permission m2m tables',
        not ({'accounts_user_user_permissions', 'auth_group_permissions'} & set(ordered)),
    )
    check('determine_transfer_tables: warns about skipped m2m tables', len(table_warnings) == 1)
    check(
        'determine_transfer_tables: includes legacy tables not present in pg',
        {'legacy_clientes', 'legacy_locado'} <= set(ordered),
    )
    check(
        'order_tables: legacy tables always come last',
        ordered.index('legacy_clientes') > max(
            ordered.index(t) for t in ordered if not t.startswith('legacy_')
        ),
    )
    check(
        'order_tables: parents precede children (company before user, category before product)',
        ordered.index('company_company') < ordered.index('accounts_user')
        and ordered.index('catalog_category') < ordered.index('catalog_product'),
    )

    conn.close()

    print()
    if failures:
        print(f'SELF-TEST FAILED ({len(failures)} failure(s)):')
        for label in failures:
            print(f'  - {label}')
        sys.exit(1)
    else:
        print('SELF-TEST PASSED')


def main():
    parser = argparse.ArgumentParser(
        description='One-shot data transfer from db.sqlite3 to a remote PostgreSQL 16 database.'
    )
    parser.add_argument('--pg-url', help='PostgreSQL connection URL (postgresql://user:pass@host:port/db)')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Run the full transfer inside a transaction, then ROLLBACK instead of COMMIT.'
    )
    parser.add_argument(
        '--self-test', action='store_true',
        help='Run pure unit checks on helper functions only; no DB connection is made.'
    )
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    if not args.pg_url:
        parser.error('--pg-url is required unless --self-test is given')

    if psycopg is None:
        parser.error('psycopg is not installed; cannot run a real transfer')

    run_transfer(args.pg_url, args.dry_run)


if __name__ == '__main__':
    main()
