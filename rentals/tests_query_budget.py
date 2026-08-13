"""Query-count guards for the rental edit screen (RF-17).

Rendering this screen used to cost 42 queries for a four-item rental: the
formset override returned a fresh clone of the queryset on every call, so every
internal ``len(self.get_queryset())`` and ``self.get_queryset()[i]`` hit the
database again. These tests keep the regression from creeping back and prove the
memoisation does not change what the formset actually sees.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from billing.models import CashAccount, Payment, Receivable
from catalog.models import Category, Product
from customers.models import Customer
from rentals.forms import RentalItemEditFormSet
from rentals.models import Rental, RentalItem


User = get_user_model()


def build_rental(number, item_count=4):
    from company.models import Company
    Company.load()
    customer = Customer.objects.create(name=f'Cliente {number}')
    category = Category.objects.create(prefix=f'P{number}', name=f'Categoria {number}')
    rental = Rental.objects.create(
        customer=customer,
        number=number,
        pickup_date='2026-09-01',
        return_date='2026-09-05',
        status=Rental.Status.PENDING,
    )
    for index in range(item_count):
        product = Product.objects.create(
            category=category,
            code=index + 1,
            description=f'PECA {index + 1}',
            value=Decimal('100.00'),
        )
        RentalItem.objects.create(rental=rental, product=product, value=Decimal('100.00'))
    rental.recalculate_total()
    return rental


class RentalItemFormSetQuerysetTests(TestCase):
    def setUp(self):
        self.rental = build_rental(7001)

    def test_get_queryset_returns_the_same_object_every_call(self):
        """Django reuses the result cache through the queryset's identity.

        Returning a new `.filter()` clone per call is what made the screen
        re-query the database a dozen times over.
        """
        formset = RentalItemEditFormSet(instance=self.rental)

        self.assertIs(formset.get_queryset(), formset.get_queryset())

    def test_building_the_formset_reads_the_items_once(self):
        formset = RentalItemEditFormSet(instance=self.rental)

        with self.assertNumQueries(1):
            list(formset.forms)
            len(formset.get_queryset())
            formset.get_queryset()[0]

    def test_memoised_queryset_eager_loads_product_and_category(self):
        """Rendering product labels must not add one lookup per item."""
        formset = RentalItemEditFormSet(instance=self.rental)

        with self.assertNumQueries(1):
            labels = [str(item.product) for item in formset.get_queryset()]

        self.assertEqual(len(labels), 4)

    def test_each_formset_instance_gets_its_own_queryset(self):
        """The cache must not leak between requests."""
        primeiro = RentalItemEditFormSet(instance=self.rental)
        list(primeiro.forms)
        RentalItem.objects.filter(rental=self.rental).first().delete()

        segundo = RentalItemEditFormSet(instance=self.rental)

        self.assertEqual(len(segundo.get_queryset()), 3)


class RentalUpdateViewQueryTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='query-budget@noivasecia.test', password='Senha12345',
        )
        ModulePermission.objects.create(user=user, module_key='rentals', allowed=True)
        self.client.force_login(user)
        self.rental = build_rental(7002)

    def test_edit_screen_stays_within_its_query_budget(self):
        url = reverse('rentals:update', args=[self.rental.pk])

        # Was 42 before the formset memoisation and the prefetch.
        with self.assertNumQueries(11):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

    def test_query_count_does_not_grow_with_the_number_of_items(self):
        """The old defect scaled with the item count; the fix must not."""
        pequena = build_rental(7003, item_count=2)
        grande = build_rental(7004, item_count=10)

        with self.assertNumQueries(11):
            self.client.get(reverse('rentals:update', args=[pequena.pk]))
        with self.assertNumQueries(11):
            self.client.get(reverse('rentals:update', args=[grande.pk]))


class RentalUpdatePaymentLockTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='payment-lock@noivasecia.test', password='Senha12345',
        )
        ModulePermission.objects.create(user=user, module_key='rentals', allowed=True)
        self.client.force_login(user)
        self.rental = build_rental(7005, item_count=1)
        CashAccount.objects.create(name='Caixa teste')
        receivable = Receivable.objects.create(
            rental=self.rental,
            amount=Decimal('100.00'),
            balance=Decimal('100.00'),
            due_date='2026-09-01',
        )
        Payment.objects.create(
            receivable=receivable,
            amount=Decimal('50.00'),
            payment_date='2026-09-01',
            method=Payment.Method.CASH,
        )

    def test_paid_rental_locks_the_commercial_terms(self):
        response = self.client.get(reverse('rentals:update', args=[self.rental.pk]))

        self.assertTrue(response.context['has_payments'])
        form = response.context['form']
        self.assertTrue(form.fields['customer'].disabled, 'customer deveria estar bloqueado após um recebimento.')
        self.assertFalse(form.fields['pickup_date'].disabled, 'pickup_date deve estar editável mesmo com recebimentos.')
        self.assertFalse(form.fields['return_date'].disabled, 'return_date deve estar editável mesmo com recebimentos.')

    def test_payment_check_runs_once_per_request(self):
        """`get_form` and `get_context_data` both need it; it must be memoised."""
        response = self.client.get(reverse('rentals:update', args=[self.rental.pk]))
        view = response.context['view']

        self.assertTrue(hasattr(view, '_has_payments'))
        with self.assertNumQueries(0):
            view.has_payments()
