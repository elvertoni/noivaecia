"""Helpers for exercising code paths that the uniqueness rules make unreachable.

Two guards keep one live item behind one printed code:
``catalog_product_unique_active_code`` and ``catalog_category_prefix_unique_ci``.
Some code exists precisely for the state they forbid — ``dedupe_product_codes``
cleans up duplicates that predate them and runs *before* the migration, and the
availability disambiguation screen is the fallback for a code that resolves to
two live items.  Neither can be tested while the guard is in place.

Dropping the index for the duration of a test is the honest way to build that
input.  Django compiles a ``UniqueConstraint`` into a plain index on every
backend, so the constraint name *is* the index name in both SQLite and
PostgreSQL, and ``TestCase`` rolls the DDL back with the rows it allowed.
"""

from django.db import connection


UNIQUE_ACTIVE_CODE = 'catalog_product_unique_active_code'
UNIQUE_CATEGORY_PREFIX = 'catalog_category_prefix_unique_ci'


def lift_unique_indexes(*names):
    """Drop the named unique indexes for the remainder of the transaction."""
    with connection.cursor() as cursor:
        for name in names:
            cursor.execute(f'DROP INDEX IF EXISTS {name}')
