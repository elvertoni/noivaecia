"""Database invariants for receipt events and their receivable allocations."""

import uuid
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from customers.models import Customer
from rentals.models import Rental

from .models import (
    CashAccount,
    FinancialMovement,
    Payment,
    Receivable,
    Receipt,
    ReceiptAllocation,
)


class ReceiptModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Cliente Recibo')
        self.account = CashAccount.objects.create(name='Caixa recibos')

    def _create_movement(self, amount=Decimal('100.00')):
        return FinancialMovement.objects.create(
            date=date(2026, 8, 10),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=amount,
            source=FinancialMovement.Source.PAYMENT,
        )

    def _create_receipt(self, **overrides):
        values = {
            'customer': self.customer,
            'received_on': date(2026, 8, 10),
            'amount': Decimal('100.00'),
            'method': Payment.Method.PIX,
            'payload_hash': 'a' * 64,
        }
        values.update(overrides)
        return Receipt.objects.create(**values)

    def test_creates_inflow_with_generated_unique_idempotency_key(self):
        receipt = self._create_receipt()

        self.assertEqual(receipt.kind, Receipt.Kind.INFLOW)
        self.assertIsInstance(receipt.idempotency_key, uuid.UUID)
        self.assertEqual(receipt.customer, self.customer)
        self.assertIsNotNone(receipt.created_at)

    def test_customer_is_optional_for_historical_compatibility(self):
        receipt = self._create_receipt(customer=None)

        self.assertIsNone(receipt.customer)

    def test_amount_must_be_strictly_positive_for_both_kinds(self):
        original = self._create_receipt()
        invalid_values = (
            (Receipt.Kind.INFLOW, None, Decimal('0')),
            (Receipt.Kind.INFLOW, None, Decimal('-0.01')),
            (Receipt.Kind.REVERSAL, original, Decimal('0')),
            (Receipt.Kind.REVERSAL, original, Decimal('-0.01')),
        )

        for kind, reversal_of, amount in invalid_values:
            with self.subTest(kind=kind, amount=amount):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self._create_receipt(
                        kind=kind,
                        reversal_of=reversal_of,
                        amount=amount,
                        idempotency_key=uuid.uuid4(),
                    )

    def test_idempotency_key_is_unique(self):
        key = uuid.uuid4()
        self._create_receipt(idempotency_key=key)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_receipt(idempotency_key=key)

    def test_reversal_kind_requires_original_receipt(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_receipt(kind=Receipt.Kind.REVERSAL)

    def test_inflow_kind_rejects_reversal_reference(self):
        original = self._create_receipt()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_receipt(
                reversal_of=original,
                idempotency_key=uuid.uuid4(),
            )

    def test_reversal_relation_is_one_to_one_and_protected(self):
        original = self._create_receipt()
        reversal = self._create_receipt(
            kind=Receipt.Kind.REVERSAL,
            reversal_of=original,
            idempotency_key=uuid.uuid4(),
        )

        self.assertEqual(original.reversal, reversal)
        with self.assertRaises(ProtectedError):
            original.delete()

    def test_financial_movement_relation_is_one_to_one_and_protected(self):
        movement = self._create_movement()
        receipt = self._create_receipt(financial_movement=movement)

        self.assertEqual(movement.receipt, receipt)
        with self.assertRaises(ProtectedError):
            movement.delete()


class ReceiptAllocationModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Cliente Alocação')
        self.rental = Rental.objects.create(
            number=88001,
            customer=self.customer,
            pickup_date=date(2026, 9, 1),
            return_date=date(2026, 9, 5),
            total_value=Decimal('300.00'),
        )
        self.receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 8, 10),
            amount=Decimal('100.00'),
        )
        self.payment = Payment.objects.create(
            receivable=self.receivable,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 8, 10),
            amount=Decimal('100.00'),
            method=Payment.Method.PIX,
        )
        self.receipt = Receipt.objects.create(
            customer=self.customer,
            received_on=date(2026, 8, 10),
            amount=Decimal('100.00'),
            method=Payment.Method.PIX,
            payload_hash='b' * 64,
        )

    def _create_allocation(self, **overrides):
        values = {
            'receipt': self.receipt,
            'receivable': self.receivable,
            'payment': self.payment,
            'cash_amount': Decimal('100.00'),
            'principal_amount': Decimal('95.00'),
            'interest_amount': Decimal('10.00'),
            'discount_amount': Decimal('5.00'),
        }
        values.update(overrides)
        return ReceiptAllocation.objects.create(**values)

    def _new_receivable_and_payment(self, number):
        receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 8, 10),
            amount=Decimal('100.00'),
        )
        payment = Payment.objects.create(
            receivable=receivable,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 8, 10),
            amount=Decimal('100.00'),
            notes=f'Pagamento {number}',
        )
        return receivable, payment

    def test_creates_allocation_and_exposes_reverse_relations(self):
        allocation = self._create_allocation()

        self.assertEqual(self.receipt.allocations.get(), allocation)
        self.assertEqual(self.receivable.receipt_allocations.get(), allocation)
        self.assertEqual(self.payment.receipt_allocation, allocation)
        self.assertEqual(allocation.cash_amount, Decimal('100.00'))
        self.assertEqual(allocation.discount_amount, Decimal('5.00'))

    def test_all_amount_components_must_be_nonnegative(self):
        fields = (
            'cash_amount',
            'principal_amount',
            'interest_amount',
            'discount_amount',
        )

        for field in fields:
            values = {field: Decimal('-0.01')}
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self._create_allocation(**values)

    def test_cash_plus_discount_is_principal_plus_interest(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_allocation(cash_amount=Decimal('99.99'))

    def test_discount_is_not_part_of_cash_composition(self):
        allocation = self._create_allocation(
            discount_amount=Decimal('25.00'),
            principal_amount=Decimal('115.00'),
        )

        self.assertEqual(
            allocation.cash_amount + allocation.discount_amount,
            allocation.principal_amount + allocation.interest_amount,
        )

    def test_receipt_and_receivable_pair_is_unique(self):
        self._create_allocation()
        _receivable, other_payment = self._new_receivable_and_payment(2)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_allocation(payment=other_payment)

    def test_payment_can_belong_to_only_one_allocation(self):
        self._create_allocation()
        other_receivable, _payment = self._new_receivable_and_payment(3)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ReceiptAllocation.objects.create(
                receipt=Receipt.objects.create(
                    customer=self.customer,
                    received_on=date(2026, 8, 10),
                    amount=Decimal('100.00'),
                    payload_hash='c' * 64,
                ),
                receivable=other_receivable,
                payment=self.payment,
                cash_amount=Decimal('100.00'),
                principal_amount=Decimal('100.00'),
                interest_amount=Decimal('0'),
                discount_amount=Decimal('0'),
            )

    def test_related_financial_records_are_protected(self):
        self._create_allocation()

        for obj in (self.receipt, self.receivable, self.payment):
            with self.subTest(model=obj._meta.label):
                with self.assertRaises(ProtectedError):
                    obj.delete()
