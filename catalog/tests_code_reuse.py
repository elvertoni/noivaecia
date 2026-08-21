"""One code, one row — the invariant the legacy BRcom system enforced.

Retiring an item there rewrote its row in place and reusing the code rewrote
that same row back into service, so a retired row and a live row never shared a
code.  These tests pin that behaviour on the Django port, where the reuse path
used to insert a second row instead.
"""

from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from catalog.forms import ProductForm
from catalog.management.commands.dedupe_product_codes import classify
from catalog.services import LEGACY_FREED_MARKER, is_free_code_slot
from catalog.tests_support import UNIQUE_ACTIVE_CODE, lift_unique_indexes
from catalog.models import Category, Product
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


def make_rental_item(product, *, number, value=300):
    rental = Rental.objects.create(
        number=number,
        customer=Customer.objects.create(name=f'Cliente {number}'),
        pickup_date=date(2026, 6, 10),
        return_date=date(2026, 6, 20),
    )
    return RentalItem.objects.create(rental=rental, product=product, value=value)


class ProductCodeUniquenessFormTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def payload(self, **overrides):
        data = {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido um ombro',
            'color': 'Preto',
            'size': 'P',
            'value': '250,00',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_code_already_in_the_collection_is_rejected(self):
        Product.objects.create(
            category=self.category, code=731, description='Vestido sereia', value=200,
        )

        form = ProductForm(data=self.payload())

        self.assertFalse(form.is_valid())
        self.assertIn('O código VF731 já está no acervo', form.errors['code'][0])
        self.assertIn('Vestido sereia', form.errors['code'][0])
        self.assertIsNone(form.reusable_product)

    def test_retired_code_is_routed_to_reuse_instead_of_rejected(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )

        form = ProductForm(data=self.payload())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.reusable_product, retired)

    def test_free_code_creates_normally(self):
        form = ProductForm(data=self.payload(code=999))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.reusable_product)

    def test_active_holder_is_reported_even_when_retired_rows_share_the_code(self):
        Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        Product.objects.create(
            category=self.category, code=731, description='Vestido sereia', value=200,
        )

        form = ProductForm(data=self.payload())

        self.assertFalse(form.is_valid())
        self.assertIn('Vestido sereia', form.errors['code'][0])

    def test_editing_a_product_cannot_take_over_a_retired_code(self):
        Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        editing = Product.objects.create(
            category=self.category, code=800, description='Vestido longo', value=300,
        )

        form = ProductForm(data=self.payload(), instance=editing)

        self.assertFalse(form.is_valid())
        self.assertIn('cadastre um produto novo com esse código', form.errors['code'][0])

    def test_editing_a_product_without_changing_its_own_code_is_allowed(self):
        editing = Product.objects.create(
            category=self.category, code=731, description='Vestido longo', value=300,
        )

        form = ProductForm(data=self.payload(), instance=editing)

        self.assertTrue(form.is_valid(), form.errors)


class ProductCodeReuseViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='reuse@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def payload(self, **overrides):
        data = {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido um ombro babado',
            'color': 'Preto',
            'size': 'P',
            'value': '250,00',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_reusing_a_retired_code_revives_the_row_instead_of_duplicating_it(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )

        response = self.client.post(reverse('catalog:product_create'), self.payload())

        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(Product.objects.filter(category=self.category, code=731).count(), 1)
        retired.refresh_from_db()
        self.assertTrue(retired.is_active)
        self.assertEqual(retired.description, 'Vestido um ombro babado')
        self.assertEqual(retired.color, 'Preto')
        self.assertEqual(retired.size, 'P')

    def test_reuse_is_audited_with_the_description_it_replaced(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )

        self.client.post(reverse('catalog:product_create'), self.payload())

        entry = AuditLog.objects.get(action='product_code_reuse', object_id=str(retired.pk))
        self.assertEqual(entry.metadata['description'], {
            'from': 'NULO',
            'to': 'Vestido um ombro babado',
        })
        self.assertEqual(entry.metadata['is_active'], {'from': False, 'to': True})

    def test_reuse_keeps_past_rental_history_and_its_frozen_snapshot(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='Vestido antigo', color='Rosa',
            value=100, is_active=False,
        )
        item = make_rental_item(retired, number=1)

        response = self.client.post(reverse('catalog:product_create'), self.payload())

        item.refresh_from_db()
        self.assertEqual(item.product_id, retired.pk)
        # The contract still prints the garment that was actually rented.
        self.assertEqual(item.display_description, 'Vestido antigo')
        self.assertEqual(item.display_color, 'Rosa')
        self.assertEqual(item.product_reference, 'VF731')
        self.assertIn(
            'As 1 locações antigas deste código seguem no histórico',
            ' '.join(str(message) for message in get_messages(response.wsgi_request)),
        )

    def test_reuse_clears_the_incomplete_import_flag(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0,
            is_active=False, is_placeholder=True,
        )

        self.client.post(reverse('catalog:product_create'), self.payload())

        retired.refresh_from_db()
        self.assertFalse(retired.is_placeholder)
        self.assertTrue(retired.is_active)

    def test_registering_a_code_already_in_the_collection_is_refused(self):
        Product.objects.create(
            category=self.category, code=731, description='Vestido sereia', value=200,
        )

        response = self.client.post(reverse('catalog:product_create'), self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Product.objects.filter(category=self.category, code=731).count(), 1)
        self.assertContains(response, 'já está no acervo')

    def test_race_on_the_code_is_refused_instead_of_overwriting_the_live_item(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        original_form_valid = ProductForm.clean

        def revive_behind_the_form(form):
            cleaned = original_form_valid(form)
            Product.objects.filter(pk=retired.pk).update(
                is_active=True, description='Reativado por outro usuário',
            )
            return cleaned

        with self.settings():
            ProductForm.clean = revive_behind_the_form
            try:
                response = self.client.post(reverse('catalog:product_create'), self.payload())
            finally:
                ProductForm.clean = original_form_valid

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'acabou de ser ocupado por outro usuário')
        retired.refresh_from_db()
        self.assertEqual(retired.description, 'Reativado por outro usuário')


class RetiredProductKeepsItsCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='retire@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='catalog.delete', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def test_retire_then_reuse_never_produces_a_second_row(self):
        product = Product.objects.create(
            category=self.category, code=731, description='Vestido antigo', value=100,
        )

        self.client.post(reverse('catalog:product_delete', args=[product.pk]))
        self.client.post(reverse('catalog:product_create'), {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido novo',
            'color': '',
            'size': '',
            'value': '250,00',
            'notes': '',
        })

        rows = Product.objects.filter(category=self.category, code=731)
        self.assertEqual(rows.count(), 1)
        revived = rows.get()
        self.assertEqual(revived.pk, product.pk)
        self.assertEqual(revived.description, 'Vestido novo')
        self.assertTrue(revived.is_active)


class FreeCodeMarkerTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def marker(self, description, code=731):
        return is_free_code_slot(
            Product(category=self.category, code=code, description=description),
        )

    def test_legacy_retirement_markers_are_recognised(self):
        self.assertTrue(self.marker('NULO'))
        self.assertTrue(self.marker('nulo'))
        self.assertTrue(self.marker('  '))
        # The importer's fallback for a blank Access description.
        self.assertTrue(self.marker('VF731'))

    def test_nula_is_a_real_description_word_not_a_marker(self):
        """"VEST NULA MANGA" means sleeveless — 340 rows use it in the legacy data."""
        self.assertFalse(self.marker('NULA'))
        self.assertFalse(self.marker('VEST NULA MANGA S EVASE'))


class DedupeProductCodesCommandTests(TestCase):
    def setUp(self):
        lift_unique_indexes(UNIQUE_ACTIVE_CODE)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def run_command(self, *args):
        out = StringIO()
        call_command('dedupe_product_codes', *args, stdout=out)
        return out.getvalue()

    def make_pair(self, code, first, second, **second_kwargs):
        older = Product.objects.create(
            category=self.category, code=code, description=first, value=100,
        )
        newer = Product.objects.create(
            category=self.category, code=code, description=second, value=200,
            **second_kwargs,
        )
        return older, newer

    def test_classifies_a_reoccupied_free_code_as_revival(self):
        older, newer = self.make_pair(731, 'NULO', 'Vestido um ombro')
        self.assertEqual(classify([older, newer]), 'revival')

    def test_classifies_matching_descriptions_as_identical(self):
        older, newer = self.make_pair(584, 'CAMISA SLIM', 'camisa  slim')
        self.assertEqual(classify([older, newer]), 'identical')

    def test_classifies_disagreeing_descriptions_as_divergent(self):
        older, newer = self.make_pair(109, 'SMOKING FORM', 'SMOKING')
        self.assertEqual(classify([older, newer]), 'divergent')

    def test_dry_run_is_the_default_and_changes_nothing(self):
        self.make_pair(731, 'NULO', 'Vestido um ombro')

        output = self.run_command()

        self.assertIn('DRY-RUN', output)
        self.assertEqual(Product.objects.filter(code=731).count(), 2)

    def test_apply_merges_a_revival_onto_the_original_slot(self):
        older, newer = self.make_pair(731, 'NULO', 'Vestido um ombro')
        old_item = make_rental_item(older, number=1)
        new_item = make_rental_item(newer, number=2)

        self.run_command('--apply')

        rows = Product.objects.filter(category=self.category, code=731)
        self.assertEqual(rows.count(), 1)
        survivor = rows.get()
        self.assertEqual(survivor.pk, older.pk)
        self.assertEqual(survivor.description, 'Vestido um ombro')
        self.assertTrue(survivor.is_active)

        old_item.refresh_from_db()
        new_item.refresh_from_db()
        self.assertEqual(old_item.product_id, survivor.pk)
        self.assertEqual(new_item.product_id, survivor.pk)
        # Snapshots stay frozen, so both contracts still print what was rented.
        self.assertEqual(old_item.display_description, 'NULO')
        self.assertEqual(new_item.display_description, 'Vestido um ombro')

    def test_apply_leaves_divergent_pairs_untouched(self):
        self.make_pair(109, 'SMOKING FORM', 'SMOKING')

        output = self.run_command('--apply')

        self.assertEqual(Product.objects.filter(code=109).count(), 2)
        self.assertIn('aguardam triagem da cliente', output)

    def test_explicit_pair_merges_a_divergent_case_after_triage(self):
        older, newer = self.make_pair(109, 'SMOKING FORM', 'SMOKING')

        self.run_command('--pair', 'VF109', '--apply')

        rows = Product.objects.filter(category=self.category, code=109)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().pk, older.pk)

    def test_explicit_pair_does_not_touch_other_duplicates(self):
        self.make_pair(109, 'SMOKING FORM', 'SMOKING')
        self.make_pair(731, 'NULO', 'Vestido um ombro')

        self.run_command('--pair', 'VF109', '--apply')

        self.assertEqual(Product.objects.filter(code=731).count(), 2)

    def test_merge_fills_blanks_from_the_discarded_row(self):
        older = Product.objects.create(
            category=self.category, code=731, description='NULO', color='Rosa',
            size='M', value=0,
        )
        Product.objects.create(
            category=self.category, code=731, description='Vestido um ombro', value=250,
        )

        self.run_command('--apply')

        survivor = Product.objects.get(category=self.category, code=731)
        self.assertEqual(survivor.pk, older.pk)
        self.assertEqual(survivor.description, 'Vestido um ombro')
        self.assertEqual(survivor.color, 'Rosa')
        self.assertEqual(survivor.size, 'M')
        self.assertEqual(survivor.value, 250)

    def test_merge_is_audited(self):
        older, newer = self.make_pair(731, 'NULO', 'Vestido um ombro')

        self.run_command('--apply')

        entry = AuditLog.objects.get(action='product_code_dedupe', object_id=str(older.pk))
        self.assertEqual(entry.metadata['kind'], 'revival')
        self.assertEqual(
            [row['pk'] for row in entry.metadata['discarded']],
            [newer.pk],
        )

    def test_invalid_pair_argument_is_rejected(self):
        with self.assertRaises(CommandError):
            self.run_command('--pair', 'nao-e-um-par')

    def test_reports_nothing_to_do_on_a_clean_catalogue(self):
        Product.objects.create(
            category=self.category, code=1, description='Vestido', value=100,
        )

        self.assertIn('Nenhum código duplicado encontrado.', self.run_command())


class ReuseAbsorptionBoundaryTests(TestCase):
    """Reuse consolidates empty slots — never another garment's history.

    Deciding that two descriptions name the same physical piece is the owner's
    call.  ``dedupe_product_codes`` asks it with a dry-run and a report; the
    create screen must not answer it silently.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='absorb@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def payload(self, **overrides):
        data = {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido um ombro babado',
            'color': 'Preto',
            'size': 'P',
            'value': '250,00',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_empty_slots_are_absorbed_into_the_revived_row(self):
        oldest = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        shell = Product.objects.create(
            category=self.category, code=731, description='VF731', value=0, is_active=False,
        )
        shell_item = make_rental_item(shell, number=1)

        self.client.post(reverse('catalog:product_create'), self.payload())

        self.assertEqual(Product.objects.filter(category=self.category, code=731).count(), 1)
        survivor = Product.objects.get(category=self.category, code=731)
        self.assertEqual(survivor.pk, oldest.pk)
        shell_item.refresh_from_db()
        self.assertEqual(shell_item.product_id, survivor.pk)
        # Frozen snapshot, so the old contract still prints what it printed.
        self.assertEqual(shell_item.display_description, 'VF731')

    def test_a_retired_garment_is_never_absorbed_silently(self):
        Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        other_piece = Product.objects.create(
            category=self.category, code=731, description='Vestido de renda',
            value=180, is_active=False,
        )
        other_item = make_rental_item(other_piece, number=2)

        response = self.client.post(reverse('catalog:product_create'), self.payload())

        other_piece.refresh_from_db()
        other_item.refresh_from_db()
        self.assertEqual(other_item.product_id, other_piece.pk)
        self.assertFalse(other_piece.is_active)
        warning = ' '.join(str(message) for message in get_messages(response.wsgi_request))
        self.assertIn('ainda tem 1 cadastro(s) anulado(s) com descrição própria', warning)
        self.assertIn('Vestido de renda', warning)
        # The default product list only flags codes held by two live items, so
        # the operator has to be sent to the filter that shows this pair.
        self.assertIn('Situação no acervo: Todos', warning)
        self.assertIn('Apenas duplicados', warning)

    def test_absorbed_rows_are_audited_with_their_legacy_identity(self):
        Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        shell = Product.objects.create(
            category=self.category, code=731, description='VF731', value=0, is_active=False,
            legacy_id=4242, legacy_source='produtos',
        )

        self.client.post(reverse('catalog:product_create'), self.payload())

        entry = AuditLog.objects.get(action='product_code_reuse')
        absorbed = {row['pk']: row for row in entry.metadata['absorbed']}
        self.assertEqual(absorbed[shell.pk]['legacy_id'], 4242)
        self.assertEqual(absorbed[shell.pk]['legacy_source'], 'produtos')
        # Everything needed to reconstruct the discarded row.
        for field in ('color', 'size', 'value', 'notes', 'legacy_notes', 'created_at'):
            self.assertIn(field, absorbed[shell.pk])


class LegacyFreedMarkerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='marker@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def test_migration_and_services_agree_on_the_sentinel(self):
        """The migration keeps its own copy; the two must not drift apart.

        A historical migration must not import application code that can
        change, so the literal is duplicated on purpose — and pinned here.
        """
        from importlib import import_module

        migration = import_module('catalog.migrations.0009_free_legacy_null_codes')
        self.assertEqual(migration.MARKER, LEGACY_FREED_MARKER)

    def test_reuse_clears_the_sentinel_so_a_reverse_cannot_reactivate_it(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0,
            is_active=False, legacy_notes=LEGACY_FREED_MARKER,
        )

        self.client.post(reverse('catalog:product_create'), {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido novo',
            'color': '',
            'size': '',
            'value': '250,00',
            'notes': '',
        })

        retired.refresh_from_db()
        self.assertTrue(retired.is_active)
        self.assertNotIn(LEGACY_FREED_MARKER, retired.legacy_notes)


class QuarantineCommandTests(TestCase):
    """Quarantine retires the registration nobody uses — it never guesses."""

    def setUp(self):
        lift_unique_indexes(UNIQUE_ACTIVE_CODE)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def run_command(self, *args):
        out = StringIO()
        call_command('dedupe_product_codes', *args, stdout=out)
        return out.getvalue()

    def make_divergent(self, code, first, second):
        older = Product.objects.create(
            category=self.category, code=code, description=first, value=100,
        )
        newer = Product.objects.create(
            category=self.category, code=code, description=second, value=200,
        )
        return older, newer

    def test_retires_the_registration_without_rentals(self):
        used, unused = self.make_divergent(109, 'SMOKING FORM', 'SMOKING')
        make_rental_item(used, number=1)

        self.run_command('--quarantine', '--apply')

        used.refresh_from_db()
        unused.refresh_from_db()
        self.assertTrue(used.is_active)
        self.assertFalse(unused.is_active)
        # Nothing was merged: both rows survive.
        self.assertEqual(Product.objects.filter(code=109).count(), 2)

    def test_refuses_when_both_registrations_are_in_use(self):
        first, second = self.make_divergent(13, 'CINTO MAS', 'cinto')
        make_rental_item(first, number=1)
        make_rental_item(second, number=2)

        output = self.run_command('--quarantine', '--apply')

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_active)
        self.assertTrue(second.is_active)
        self.assertIn('precisam da cliente', output)

    def test_refuses_when_neither_registration_has_been_used(self):
        """Row order is not evidence about which garment is on the rack."""
        first, second = self.make_divergent(35, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')

        output = self.run_command('--quarantine', '--apply')

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_active)
        self.assertTrue(second.is_active)
        self.assertIn('precisam da cliente', output)

    def test_quarantine_is_dry_run_by_default(self):
        used, unused = self.make_divergent(109, 'SMOKING FORM', 'SMOKING')
        make_rental_item(used, number=1)

        output = self.run_command('--quarantine')

        unused.refresh_from_db()
        self.assertTrue(unused.is_active)
        self.assertIn('DRY-RUN', output)

    def test_retirement_is_audited(self):
        used, unused = self.make_divergent(109, 'SMOKING FORM', 'SMOKING')
        make_rental_item(used, number=1)

        self.run_command('--quarantine', '--apply')

        entry = AuditLog.objects.get(
            action='product_code_quarantine', object_id=str(unused.pk),
        )
        self.assertEqual(entry.metadata['is_active'], {'from': True, 'to': False})
        self.assertEqual(entry.metadata['kept']['pk'], used.pk)


class ReuseSurvivorChoiceTests(TestCase):
    """Reuse rewrites the row it revives, so it must revive an empty slot.

    Picking merely the oldest retired row destroys a retired garment's
    catalogue identity whenever a legacy shell happens to have been created
    after it — the order the operator has no control over.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='survivor@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def payload(self):
        return {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido novo',
            'color': '',
            'size': '',
            'value': '250,00',
            'notes': '',
        }

    def test_empty_slot_wins_even_when_the_garment_row_is_older(self):
        garment = Product.objects.create(
            category=self.category, code=731, description='Vestido de renda',
            color='Rosa', value=180, is_active=False,
        )
        shell = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )

        form = ProductForm(data=self.payload())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.reusable_product, shell)
        self.assertNotEqual(form.reusable_product, garment)

    def test_the_older_garment_keeps_its_description_after_the_reuse(self):
        garment = Product.objects.create(
            category=self.category, code=731, description='Vestido de renda',
            color='Rosa', value=180, is_active=False,
        )
        item = make_rental_item(garment, number=1)
        Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )

        response = self.client.post(reverse('catalog:product_create'), self.payload())

        garment.refresh_from_db()
        self.assertEqual(garment.description, 'Vestido de renda')
        self.assertEqual(garment.color, 'Rosa')
        self.assertFalse(garment.is_active)
        item.refresh_from_db()
        self.assertEqual(item.product_id, garment.pk)
        self.assertIn(
            'ainda tem 1 cadastro(s) anulado(s) com descrição própria',
            ' '.join(str(m) for m in get_messages(response.wsgi_request)),
        )

    def test_a_lone_retired_garment_is_still_reusable(self):
        """Retire-then-reuse is the normal flow; the row is rewritten on purpose."""
        garment = Product.objects.create(
            category=self.category, code=731, description='Vestido de renda',
            value=180, is_active=False,
        )

        self.client.post(reverse('catalog:product_create'), self.payload())

        garment.refresh_from_db()
        self.assertTrue(garment.is_active)
        self.assertEqual(garment.description, 'Vestido novo')
        self.assertEqual(Product.objects.filter(category=self.category, code=731).count(), 1)
        entry = AuditLog.objects.get(action='product_code_reuse')
        self.assertEqual(entry.metadata['description']['from'], 'Vestido de renda')

    def test_the_oldest_empty_slot_wins_among_several(self):
        first_shell = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0, is_active=False,
        )
        Product.objects.create(
            category=self.category, code=731, description='VF731', value=0, is_active=False,
        )

        form = ProductForm(data=self.payload())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.reusable_product, first_shell)


class QuarantineExplicitKeepTests(TestCase):
    """``--keep`` records the owner's triage so quarantine stops guessing."""

    def setUp(self):
        lift_unique_indexes(UNIQUE_ACTIVE_CODE)
        self.category = Category.objects.create(prefix='VEQ', name='Vestidos curtos')

    def run_command(self, *args):
        out = StringIO()
        call_command('dedupe_product_codes', *args, stdout=out)
        return out.getvalue()

    def make_divergent(self, code, first, second):
        return (
            Product.objects.create(
                category=self.category, code=code, description=first, value=100,
            ),
            Product.objects.create(
                category=self.category, code=code, description=second, value=200,
            ),
        )

    def test_keep_resolves_a_code_neither_registration_ever_used(self):
        keep, drop = self.make_divergent(35, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')

        output = self.run_command('--quarantine', '--keep', str(keep.pk), '--apply')

        keep.refresh_from_db()
        drop.refresh_from_db()
        self.assertTrue(keep.is_active)
        self.assertFalse(drop.is_active)
        self.assertNotIn('precisam da cliente', output)

    def test_keep_outranks_the_rental_history_heuristic(self):
        """The owner knows which garment is on the rack; the data only guesses."""
        keep, used = self.make_divergent(36, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')
        make_rental_item(used, number=1)

        self.run_command('--quarantine', '--keep', str(keep.pk), '--apply')

        keep.refresh_from_db()
        used.refresh_from_db()
        self.assertTrue(keep.is_active)
        self.assertFalse(used.is_active)

    def test_keep_is_audited_with_the_row_it_preserved(self):
        keep, drop = self.make_divergent(35, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')

        self.run_command('--quarantine', '--keep', str(keep.pk), '--apply')

        entry = AuditLog.objects.get(
            action='product_code_quarantine', object_id=str(drop.pk),
        )
        self.assertEqual(entry.metadata['kept']['pk'], keep.pk)

    def test_keep_naming_two_rows_of_one_code_is_refused(self):
        first, second = self.make_divergent(35, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')

        with self.assertRaises(CommandError):
            self.run_command(
                '--quarantine', '--keep', str(first.pk), '--keep', str(second.pk), '--apply',
            )

    def test_keep_pointing_at_a_missing_product_is_refused(self):
        self.make_divergent(35, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')

        with self.assertRaises(CommandError):
            self.run_command('--quarantine', '--keep', '999999', '--apply')

    def test_keep_must_be_a_number(self):
        self.make_divergent(35, 'VEST CURTO CO BORDADO', 'VEST CURTO BORD')

        with self.assertRaises(CommandError):
            self.run_command('--quarantine', '--keep', 'VEQ35', '--apply')


class MergeOrderingTests(TestCase):
    """The survivor may only go live after the losers are gone.

    ``catalog_product_unique_active_code`` forbids two live items on one code,
    so activating the survivor before deleting an active loser would fail — on
    every run after the constraint lands, including a ``--pair`` merge the owner
    approved.
    """

    def setUp(self):
        lift_unique_indexes(UNIQUE_ACTIVE_CODE)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def run_command(self, *args):
        out = StringIO()
        call_command('dedupe_product_codes', *args, stdout=out)
        return out.getvalue()

    def test_merges_when_the_survivor_is_retired_and_the_loser_is_live(self):
        retired_survivor = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0,
            is_active=False,
        )
        live_loser = Product.objects.create(
            category=self.category, code=731, description='Vestido um ombro', value=250,
        )
        item = make_rental_item(live_loser, number=1)

        self.run_command('--apply')

        rows = Product.objects.filter(category=self.category, code=731)
        self.assertEqual(rows.count(), 1)
        survivor = rows.get()
        self.assertEqual(survivor.pk, retired_survivor.pk)
        self.assertTrue(survivor.is_active)
        self.assertEqual(survivor.description, 'Vestido um ombro')
        item.refresh_from_db()
        self.assertEqual(item.product_id, survivor.pk)
        self.assertFalse(Product.objects.filter(pk=live_loser.pk).exists())

    def test_merge_keeps_the_code_retired_when_no_row_was_live(self):
        older = Product.objects.create(
            category=self.category, code=731, description='NULO', value=0,
            is_active=False,
        )
        Product.objects.create(
            category=self.category, code=731, description='VF731', value=0,
            is_active=False,
        )

        self.run_command('--apply')

        survivor = Product.objects.get(category=self.category, code=731)
        self.assertEqual(survivor.pk, older.pk)
        self.assertFalse(survivor.is_active)
