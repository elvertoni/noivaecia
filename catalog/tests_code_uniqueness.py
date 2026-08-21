"""The uniqueness rules, and the paths that used to walk around them.

``ProductForm`` refuses a code already in the collection, but form validation is
check-then-insert: two concurrent requests both pass it.  Worse, a
``UniqueConstraint`` whose ``condition`` names a field the form excludes is
skipped *silently* during validation (``constraints.py``:
``except FieldError: pass``), and ``ModelForm`` excludes everything outside
``Meta.fields`` — ``is_active`` included.  So the database is the only real
arbiter, and every write path has to survive it landing.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from catalog.admin import ProductAdmin
from catalog.forms import ProductForm
from catalog.models import Category, Product
from catalog.tests_support import UNIQUE_ACTIVE_CODE, lift_unique_indexes

User = get_user_model()


class ProductAdminGuardsTests(TestCase):
    """The admin bypasses ``ProductForm`` unless it is told not to."""

    def test_admin_uses_the_product_form(self):
        # Without it the admin would validate a duplicate code as clean and then
        # raise IntegrityError on save, as a 500.
        self.assertIs(ProductAdmin.form, ProductForm)

    def test_admin_cannot_delete_products(self):
        """Deleting frees the code with no trace — retiring is always in place."""
        admin = ProductAdmin(Product, None)

        self.assertFalse(admin.has_delete_permission(None))
        self.assertFalse(admin.has_delete_permission(None, obj=Product()))

    def test_the_form_the_admin_uses_does_not_expose_is_active(self):
        """Pins the reason ``form = ProductForm`` is required, not optional."""
        self.assertNotIn('is_active', ProductForm.Meta.fields)


class ReactivateUnderRaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='react@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='catalog.delete', allowed=True)
        self.client.force_login(self.user)
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')

    def test_reactivating_into_a_taken_code_reports_the_holder(self):
        lift_unique_indexes(UNIQUE_ACTIVE_CODE)
        retired = Product.objects.create(
            category=self.category, code=731, description='Vestido antigo', value=100,
            is_active=False,
        )
        Product.objects.create(
            category=self.category, code=731, description='Vestido atual', value=200,
        )

        response = self.client.post(
            reverse('catalog:product_reactivate', args=[retired.pk]),
        )

        retired.refresh_from_db()
        self.assertFalse(retired.is_active)
        self.assertIn(
            'já está no acervo, usado por "Vestido atual"',
            ' '.join(str(m) for m in get_messages(response.wsgi_request)),
        )

    def test_reactivating_a_free_code_still_works(self):
        retired = Product.objects.create(
            category=self.category, code=731, description='Vestido antigo', value=100,
            is_active=False,
        )

        self.client.post(reverse('catalog:product_reactivate', args=[retired.pk]))

        retired.refresh_from_db()
        self.assertTrue(retired.is_active)


class CategoryPrefixUniquenessTests(TestCase):
    """``VF`` beside ``vf`` prints one code twice — the reported bug itself."""

    def test_the_form_still_reports_it_in_portuguese(self):
        from catalog.forms import CategoryForm

        Category.objects.create(prefix='VF', name='Vestidos de festa')
        form = CategoryForm(data={'prefix': 'vf', 'name': 'Outra'})

        self.assertFalse(form.is_valid())
        self.assertIn('Já existe uma categoria', form.errors['prefix'][0])

    def test_the_admin_uses_the_category_form(self):
        from catalog.admin import CategoryAdmin
        from catalog.forms import CategoryForm

        self.assertIs(CategoryAdmin.form, CategoryForm)


class AvailabilityStillDisambiguatesTests(TestCase):
    """Kept as the fallback for a code that resolves to two live items."""

    def setUp(self):
        self.user = User.objects.create_user(email='avail@test.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.client.force_login(self.user)

    def test_two_live_items_behind_one_tag_still_ask_the_operator_to_choose(self):
        lift_unique_indexes(UNIQUE_ACTIVE_CODE)
        category = Category.objects.create(prefix='VF', name='Vestidos de festa')
        Product.objects.create(
            category=category, code=731, description='Vestido A', value=Decimal('100'),
        )
        Product.objects.create(
            category=category, code=731, description='Vestido B', value=Decimal('120'),
        )

        response = self.client.get(
            reverse('catalog:availability'), {'prefix': 'VF', 'code': '731'},
        )

        self.assertTrue(response.context.get('needs_disambiguation'))
        self.assertEqual(len(response.context['candidates']), 2)
