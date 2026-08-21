"""The importer has to land a catalogue that already obeys the code rule.

``(prefixo, codigo)`` is the piece's identity — it goes on the contract and on
the physical tag — and ``catalog_product_unique_active_code`` allows only one
live row per code.  ``bulk_create`` skips ``full_clean``, so anything the
importer gets wrong surfaces as a driver-level error in the middle of a cutover
window instead of as a message someone can act on.

Two legacy shapes need folding, and both are handled before the insert:

* a free code slot — BRcom never deleted a product row, it rewrote the
  description to ``NULO``; a blank Access description becomes ``PREFIXCODE``;
* two real rows sharing one code — 40 such codes exist in the dump, and nothing
  in it says which is the garment on the rack, because ``locado`` links by
  ``(prefixo, codigo)`` rather than by product id.
"""

import shutil
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.models import Count
from django.test import TestCase

from catalog.models import Product
from catalog.services import LEGACY_FREED_MARKER, is_free_code_slot
from core.management.commands.import_legacy_access import DUPLICATE_CODE_MARKER
from core.tests_importer import build_export_dir, minimal_data
from rentals.models import RentalItem


def product_rows(*rows):
    """``produtos`` rows with the importer's column names filled in."""
    built = []
    for row in rows:
        built.append({
            'id': row['id'],
            'prefixo': row.get('prefixo', 'VT'),
            'codigo': row.get('codigo', 1),
            'descrição': row.get('descricao', ''),
            'cor': row.get('cor', ''),
            'tamanho': row.get('tamanho', ''),
            'valor': row.get('valor', '0'),
            'obs': '',
        })
    return built


class ImporterCodeSlotTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_export(self, produtos, locado=None):
        data = minimal_data()
        data['produtos'] = produtos
        if locado is not None:
            data['locado'] = locado
        return build_export_dir(tempfile.mkdtemp(dir=self.tmp), data)

    def import_from(self, export_dir):
        call_command(
            'import_legacy_access',
            f'--export-dir={export_dir}',
            '--reset',
            '--confirm-reset',
            verbosity=0,
        )

    def run_import(self, produtos, locado=None):
        self.import_from(self.build_export(produtos, locado))

    def get(self, legacy_id):
        return Product.objects.get(pk=legacy_id)

    # -- free code slots ---------------------------------------------------

    def test_nulo_is_retired_and_marked(self):
        self.run_import(product_rows({'id': 9, 'codigo': 9, 'descricao': 'NULO'}))

        product = self.get(9)
        self.assertFalse(product.is_active)
        self.assertIn(LEGACY_FREED_MARKER, product.legacy_notes)

    def test_nulo_is_matched_regardless_of_case_and_padding(self):
        self.run_import(product_rows(
            {'id': 9, 'codigo': 9, 'descricao': 'nulo'},
            {'id': 10, 'codigo': 10, 'descricao': '  Nulo '},
        ))

        self.assertFalse(self.get(9).is_active)
        self.assertFalse(self.get(10).is_active)

    def test_blank_description_is_retired(self):
        """The importer turns it into ``PREFIXCODE``; same meaning."""
        self.run_import(product_rows({'id': 9, 'codigo': 9, 'descricao': ''}))

        product = self.get(9)
        self.assertEqual(product.description, 'VT9')
        self.assertFalse(product.is_active)

    def test_explicit_prefix_code_description_is_retired(self):
        self.run_import(product_rows({'id': 9, 'codigo': 9, 'descricao': 'VT9'}))

        self.assertFalse(self.get(9).is_active)

    def test_nula_is_a_real_description_and_stays_in_the_collection(self):
        """"VEST NULA MANGA" means sleeveless — 340 legacy rows use it.

        Without this, someone simplifies the rule to ``startswith('NUL')`` and
        silently retires hundreds of real garments.
        """
        self.run_import(product_rows(
            {'id': 9, 'codigo': 9, 'descricao': 'NULA'},
            {'id': 10, 'codigo': 10, 'descricao': 'VEST NULA MANGA S EVASE'},
        ))

        for legacy_id in (9, 10):
            product = self.get(legacy_id)
            self.assertTrue(product.is_active, legacy_id)
            self.assertNotIn(LEGACY_FREED_MARKER, product.legacy_notes)

    def test_a_real_description_is_untouched(self):
        self.run_import(product_rows(
            {'id': 9, 'codigo': 9, 'descricao': 'Vestido de renda'},
        ))

        product = self.get(9)
        self.assertTrue(product.is_active)
        self.assertEqual(product.legacy_notes, '')

    # -- duplicate codes ---------------------------------------------------

    def test_the_lowest_legacy_id_keeps_the_code(self):
        self.run_import(product_rows(
            {'id': 20, 'codigo': 5, 'descricao': 'SMOKING'},
            {'id': 9, 'codigo': 5, 'descricao': 'SMOKING FORM'},
        ))

        self.assertTrue(self.get(9).is_active)
        retired = self.get(20)
        self.assertFalse(retired.is_active)
        self.assertIn(DUPLICATE_CODE_MARKER.format(prefix='VT', code=5, kept=9), retired.legacy_notes)

    def test_three_rows_on_one_code_leave_exactly_one_live(self):
        self.run_import(product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'CAMISA TRA SLIM'},
            {'id': 20, 'codigo': 5, 'descricao': 'CAMISA'},
            {'id': 21, 'codigo': 5, 'descricao': 'CAMISA'},
        ))

        live = Product.objects.filter(code=5, is_active=True)
        self.assertEqual([p.pk for p in live], [9])
        self.assertEqual(Product.objects.filter(code=5).count(), 3)

    def test_a_free_slot_loses_to_the_real_piece_without_a_duplicate_marker(self):
        """The free-slot rule runs first, so this is not a triage decision."""
        self.run_import(product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'NULO'},
            {'id': 20, 'codigo': 5, 'descricao': 'CAMISA SLIM'},
        ))

        self.assertTrue(self.get(20).is_active)
        shell = self.get(9)
        self.assertFalse(shell.is_active)
        self.assertIn(LEGACY_FREED_MARKER, shell.legacy_notes)
        self.assertNotIn('tinha mais de um cadastro', shell.legacy_notes)

    def test_rental_items_bind_to_the_live_row(self):
        self.run_import(
            product_rows(
                {'id': 20, 'codigo': 5, 'descricao': 'SMOKING'},
                {'id': 9, 'codigo': 5, 'descricao': 'SMOKING FORM'},
            ),
            locado=[{
                'id': 1, 'locação': 1, 'cliente': 1, 'prefixo': 'VT', 'codigo': 5,
                'descrição': 'SMOKING', 'retirada': '2024-01-10',
                'dev_prevista': '2024-01-15', 'dev_efetiva': None, 'valor': '150.00',
                'multa': '0', 'devolvido': 0, 'retirado': 0, 'obs': '', 'usar': 0,
            }],
        )

        item = RentalItem.objects.get()
        self.assertEqual(item.product_id, 9)
        self.assertTrue(item.product.is_active)

    # -- invariants --------------------------------------------------------

    def test_no_code_ends_up_with_two_live_rows(self):
        self.run_import(product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'A'},
            {'id': 20, 'codigo': 5, 'descricao': 'B'},
            {'id': 21, 'codigo': 6, 'descricao': 'NULO'},
            {'id': 22, 'codigo': 6, 'descricao': 'C'},
            {'id': 23, 'codigo': 7, 'descricao': 'D'},
        ))

        clashes = (
            Product.objects.filter(is_active=True)
            .values('category_id', 'code')
            .annotate(rows=Count('pk'))
            .filter(rows__gt=1)
        )
        self.assertEqual(list(clashes), [])

    def test_the_import_leaves_nothing_for_migration_0009_to_archive(self):
        """Importer and migration must agree, or a fresh import drifts."""
        self.run_import(product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'NULO'},
            {'id': 20, 'codigo': 6, 'descricao': ''},
            {'id': 21, 'codigo': 7, 'descricao': 'Vestido de renda'},
        ))

        pending = [
            p for p in Product.objects.select_related('category')
            if p.is_active and not p.is_placeholder and is_free_code_slot(p)
        ]
        self.assertEqual(pending, [])

    def test_reimporting_the_same_export_does_not_fail(self):
        """The regression the constraint introduces; the runbook replays this."""
        export_dir = self.build_export(product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'SMOKING FORM'},
            {'id': 20, 'codigo': 5, 'descricao': 'SMOKING'},
            {'id': 21, 'codigo': 6, 'descricao': 'NULO'},
        ))
        self.import_from(export_dir)
        self.import_from(export_dir)

        self.assertEqual(Product.objects.filter(code=5, is_active=True).count(), 1)

    def test_audit_table_records_every_decision(self):
        self.run_import(product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'SMOKING FORM'},
            {'id': 20, 'codigo': 5, 'descricao': 'SMOKING'},
            {'id': 21, 'codigo': 6, 'descricao': 'NULO'},
        ))

        with connection.cursor() as cursor:
            cursor.execute('SELECT key, value FROM legacy_import_audit')
            audit = dict(cursor.fetchall())

        self.assertEqual(audit['free_code_slots_retired'], '1')
        self.assertEqual(audit['duplicate_products_retired'], '1')
        self.assertIn('VT5#20>9', audit['duplicate_products_detail'])

    def test_a_forged_export_that_beats_the_rule_aborts_in_portuguese(self):
        """``bulk_create`` skips ``full_clean``; the guard must speak first."""
        original = Product.save

        data = minimal_data()
        data['produtos'] = product_rows(
            {'id': 9, 'codigo': 5, 'descricao': 'A'},
            {'id': 20, 'codigo': 5, 'descricao': 'B'},
        )
        export_dir = build_export_dir(tempfile.mkdtemp(dir=self.tmp), data)

        from core.management.commands import import_legacy_access as module

        def keep_everything_live(self, product, marker):
            return None

        original_retire = module.Command._retire_product
        module.Command._retire_product = keep_everything_live
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'import_legacy_access',
                    f'--export-dir={export_dir}',
                    '--reset',
                    '--confirm-reset',
                    verbosity=0,
                )
        finally:
            module.Command._retire_product = original_retire
            Product.save = original

        self.assertIn('mais de um cadastro ativo no mesmo codigo', str(ctx.exception))
        self.assertIn('VT5', str(ctx.exception))
