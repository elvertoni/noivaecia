from datetime import date, datetime
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from core.management.commands.homologation_report import (
    _check_table_exists,
    _find_suspicious_dates,
    _suspicious_locado,
    _suspicious_pagar,
)


class TableExistenceTests(SimpleTestCase):
    def test_uses_django_database_introspection(self):
        cursor = object()

        with patch.object(
            connection.introspection,
            'table_names',
            return_value=['legacy_locado'],
        ) as table_names:
            self.assertTrue(_check_table_exists(cursor, 'legacy_locado'))

        table_names.assert_called_once_with(cursor)


class SuspiciousDateSQLiteTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute(
                'CREATE TABLE legacy_locado ('
                'id INTEGER PRIMARY KEY, retirada TEXT, dev_prevista TEXT)'
            )
            cursor.executemany(
                'INSERT INTO legacy_locado (id, retirada, dev_prevista) '
                'VALUES (%s, %s, %s)',
                [
                    (1, '1899-12-31', '2024-01-01'),
                    (2, '2024-01-01', '2035-12-31'),
                    (3, '2036-01-01', None),
                    (4, None, '01/01/1800'),
                    (5, 'invalid', None),
                ],
            )
            cursor.execute(
                'CREATE TABLE legacy_pagar ('
                'id INTEGER PRIMARY KEY, vencimento TEXT)'
            )
            cursor.executemany(
                'INSERT INTO legacy_pagar (id, vencimento) VALUES (%s, %s)',
                [
                    (1, '1900-01-01'),
                    (2, '2036-01-01 10:30:00'),
                    (3, None),
                ],
            )

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE legacy_locado')
            cursor.execute('DROP TABLE legacy_pagar')
        super().tearDown()

    def test_finds_out_of_range_locado_dates(self):
        with connection.cursor() as cursor:
            rows, error = _suspicious_locado(cursor)

        self.assertIsNone(error)
        self.assertEqual([row['id'] for row in rows], [1, 3, 4])

    def test_finds_out_of_range_pagar_dates(self):
        with connection.cursor() as cursor:
            rows, error = _suspicious_pagar(cursor)

        self.assertIsNone(error)
        self.assertEqual([row['id'] for row in rows], [2])


class DriverValueCompatibilityTests(SimpleTestCase):
    class Cursor:
        description = (('id',), ('vencimento',))

        def __init__(self):
            self.sql = ''
            self.batches = [[(1, date(1899, 1, 1)),
                             (2, datetime(2024, 1, 1, 12, 0))], []]

        def execute(self, sql):
            self.sql = sql

        def fetchmany(self, size):
            return self.batches.pop(0)

    def test_accepts_date_objects_without_vendor_specific_sql(self):
        cursor = self.Cursor()

        with patch(
            'core.management.commands.homologation_report._check_table_exists',
            return_value=True,
        ):
            rows, error = _find_suspicious_dates(
                cursor,
                'legacy_pagar',
                ('id', 'vencimento'),
                ('vencimento',),
            )

        self.assertIsNone(error)
        self.assertEqual([row['id'] for row in rows], [1])
        self.assertNotIn('sqlite_master', cursor.sql)
        self.assertNotIn('strftime', cursor.sql)
