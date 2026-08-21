"""What only the database can enforce.

``ProductForm`` refuses a code already in the collection, but form validation is
check-then-insert: two concurrent requests both pass it.  And a
``UniqueConstraint`` whose ``condition`` names a field the form excludes is
skipped *silently* during validation (``constraints.py``:
``except FieldError: pass``), while ``ModelForm`` excludes everything outside
``Meta.fields`` — ``is_active`` included.

These tests therefore assert the database refuses what the form cannot, and they
only make sense once ``catalog.0010`` has landed.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from catalog.forms import ProductForm
from catalog.models import Category, Product

User = get_user_model()


class DatabaseRefusesDuplicateCodesTests(TestCase):
    def test_two_live_items_can_never_share_a_code(self):
        category = Category.objects.create(prefix='VN', name='Vestidos')
        Product.objects.create(category=category, code=1, description='A', value=10)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(category=category, code=1, description='B', value=20)

    def test_retired_rows_may_still_share_a_code(self):
        """Reuse revives a retired row, so the code has to stay with it."""
        category = Category.objects.create(prefix='VN', name='Vestidos')
        Product.objects.create(category=category, code=1, description='A', value=10)
        Product.objects.create(
            category=category, code=1, description='NULO', value=0, is_active=False,
        )
        Product.objects.create(
            category=category, code=1, description='VN1', value=0, is_active=False,
        )

        self.assertEqual(Product.objects.filter(category=category, code=1).count(), 3)

    def test_category_prefixes_cannot_differ_only_by_case(self):
        """``VF`` beside ``vf`` prints one code twice — the reported bug itself.

        The product rule is scoped to ``category_id``; the availability lookup
        matches ``prefix__iexact``.  Without this second guard the first one
        does not actually close the complaint.
        """
        Category.objects.create(prefix='VF', name='Vestidos de festa')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(prefix='vf', name='Outra')


class ConcurrentCreateTests(TestCase):
    """The loser of a race gets a field error, not a 500."""

    def setUp(self):
        self.user = User.objects.create_user(email='race@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def payload(self):
        return {
            'category': self.category.pk,
            'code': 731,
            'description': 'Vestido um ombro',
            'color': '',
            'size': '',
            'value': '250,00',
            'notes': '',
        }

    def post_with_the_code_taken_mid_flight(self):
        original_clean = ProductForm.clean

        def take_the_code_behind_the_form(form):
            cleaned = original_clean(form)
            # Simulates the other request committing in the gap the optimistic
            # check cannot cover.
            Product.objects.create(
                category=self.category, code=731, description='Vestido sereia',
                value=200,
            )
            return cleaned

        ProductForm.clean = take_the_code_behind_the_form
        try:
            return self.client.post(reverse('catalog:product_create'), self.payload())
        finally:
            ProductForm.clean = original_clean

    def test_a_code_taken_between_validation_and_insert_is_reported_on_the_field(self):
        response = self.post_with_the_code_taken_mid_flight()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já está no acervo')
        self.assertContains(response, 'Vestido sereia')
        self.assertEqual(Product.objects.filter(category=self.category, code=731).count(), 1)

    def test_the_transaction_survives_the_handled_violation(self):
        """A caught IntegrityError must not poison the connection.

        Without the inner savepoint the next query would raise
        ``TransactionManagementError`` instead of answering.
        """
        self.post_with_the_code_taken_mid_flight()

        self.assertEqual(Product.objects.count(), 1)


class ZzzUniquenessGuardsSurviveTheSuiteTests(TestCase):
    """Sentinel: the indexes must still exist after the whole suite ran.

    Two mechanisms remove them on purpose — the migration tests roll the app
    back and forward, and the dedupe tests drop the product index to build their
    input.  Both restore it; if either ever leaks, every later test silently
    stops enforcing the rule instead of failing.
    """

    def test_product_code_uniqueness_is_still_enforced(self):
        category = Category.objects.create(prefix='ZZP', name='Sentinela')
        Product.objects.create(category=category, code=1, description='A', value=1)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(category=category, code=1, description='B', value=1)

    def test_category_prefix_uniqueness_is_still_enforced(self):
        Category.objects.create(prefix='ZZC', name='Sentinela')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(prefix='zzc', name='Sentinela (outra)')
