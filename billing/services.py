"""Single source of truth for late interest and installment generation.

Centralizing interest here addresses the incorrect-interest risk in PRD section 12.
Interest uses the company's monthly rate divided by 30 and applied for each overdue
day, with the legacy daily rate as a fallback when the monthly rate is zero.
"""

import hashlib
import json
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    DecimalField,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from company.models import Company
from core.models import AuditLog

from .models import (
    CashAccount,
    FinancialMovement,
    Payment,
    Receivable,
    Receipt,
    ReceiptAllocation,
    payment_principal_expression,
)


PENALTY_NOTE_PREFIX = 'penalty:'
MONEY_QUANTUM = Decimal('0.01')


def _quantize_money(value):
    """Round a Decimal monetary value using the application's money policy."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _normalize_money(value, label):
    """Convert a monetary input to finite cents using one rounding policy."""
    try:
        raw_amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'O {label} informado é inválido.') from exc
    if not raw_amount.is_finite():
        raise ValueError(f'O {label} informado é inválido.')
    if raw_amount < 0:
        raise ValueError(f'O {label} não pode ser negativo.')
    try:
        return _quantize_money(raw_amount)
    except InvalidOperation as exc:
        raise ValueError(f'O {label} informado é inválido.') from exc


class ReceiptServiceError(ValueError):
    """Raised when a real receipt would violate a financial invariant."""


class ReceiptIdempotencyConflict(ReceiptServiceError):
    """Raised when an idempotency key is reused with a different payload."""


def _normalize_idempotency_key(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReceiptServiceError('A chave de idempotência é inválida.') from exc


def _normalize_receipt_date(value, label='data do recebimento'):
    if type(value) is date_cls:
        normalized = value
    else:
        try:
            normalized = date_cls.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ReceiptServiceError(f'A {label} é inválida.') from exc
    if normalized > timezone.localdate():
        raise ReceiptServiceError(f'A {label} não pode estar no futuro.')
    return normalized


def _positive_integer(value, label):
    if isinstance(value, bool):
        raise ReceiptServiceError(f'O {label} é inválido.')
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ReceiptServiceError(f'O {label} é inválido.') from exc
    if normalized <= 0:
        raise ReceiptServiceError(f'O {label} é inválido.')
    return normalized


def _payload_hash(payload):
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _normalize_receipt_payload(payload):
    """Validate and canonicalize the public payload for ``register_receipt``."""
    if not isinstance(payload, dict):
        raise ReceiptServiceError('O conteúdo do recibo deve ser um objeto.')
    allowed_keys = {
        'rental_id',
        'cash_account_id',
        'received_on',
        'amount',
        'method',
        'notes',
        'allocations',
    }
    unknown_keys = set(payload) - allowed_keys
    if unknown_keys:
        raise ReceiptServiceError(
            'O conteúdo do recibo possui campos desconhecidos: '
            + ', '.join(sorted(unknown_keys))
            + '.',
        )

    rental_id = _positive_integer(payload.get('rental_id'), 'ID da locação')
    account_id = _positive_integer(
        payload.get('cash_account_id'),
        'ID da conta caixa',
    )
    received_on = _normalize_receipt_date(payload.get('received_on'))
    amount = _normalize_money(payload.get('amount'), 'valor do recibo')
    if amount <= 0:
        raise ReceiptServiceError('O valor do recibo deve ser maior que zero.')
    method = str(payload.get('method') or '').strip()
    if method not in Payment.Method.values:
        raise ReceiptServiceError('A forma de recebimento informada é inválida.')
    notes = str(payload.get('notes') or '').strip()

    raw_allocations = payload.get('allocations')
    if not isinstance(raw_allocations, list) or not raw_allocations:
        raise ReceiptServiceError('Informe ao menos uma alocação para o recibo.')

    allocations = []
    seen_receivables = set()
    for position, raw_allocation in enumerate(raw_allocations, start=1):
        if not isinstance(raw_allocation, dict):
            raise ReceiptServiceError(f'A alocação {position} é inválida.')
        unknown_allocation_keys = set(raw_allocation) - {
            'receivable_id',
            'cash_amount',
            'interest_amount',
            'discount_amount',
        }
        if unknown_allocation_keys:
            raise ReceiptServiceError(
                f'A alocação {position} possui campos desconhecidos: '
                + ', '.join(sorted(unknown_allocation_keys))
                + '.',
            )
        receivable_id = _positive_integer(
            raw_allocation.get('receivable_id'),
            f'ID do título da alocação {position}',
        )
        if receivable_id in seen_receivables:
            raise ReceiptServiceError(
                'Cada título pode ser alocado apenas uma vez no mesmo recibo.'
            )
        seen_receivables.add(receivable_id)
        cash_amount = _normalize_money(
            raw_allocation.get('cash_amount'),
            f'valor em caixa da alocação {position}',
        )
        interest_amount = _normalize_money(
            raw_allocation.get('interest_amount') or 0,
            f'valor dos juros da alocação {position}',
        )
        discount_amount = _normalize_money(
            raw_allocation.get('discount_amount') or 0,
            f'valor do desconto da alocação {position}',
        )
        principal_amount = cash_amount + discount_amount - interest_amount
        if principal_amount < 0:
            raise ReceiptServiceError(
                f'Os juros da alocação {position} superam o caixa somado ao desconto.'
            )
        allocations.append({
            'receivable_id': receivable_id,
            'cash_amount': cash_amount,
            'principal_amount': principal_amount,
            'interest_amount': interest_amount,
            'discount_amount': discount_amount,
        })

    allocations.sort(key=lambda item: item['receivable_id'])
    allocated_cash = sum(
        (item['cash_amount'] for item in allocations),
        Decimal('0'),
    )
    if amount != allocated_cash:
        raise ReceiptServiceError(
            'O valor do recibo deve ser igual à soma em caixa das alocações.'
        )

    canonical = {
        'operation': Receipt.Kind.INFLOW,
        'rental_id': rental_id,
        'cash_account_id': account_id,
        'received_on': received_on.isoformat(),
        'amount': f'{amount:.2f}',
        'method': method,
        'notes': notes,
        'allocations': [
            {
                'receivable_id': item['receivable_id'],
                'cash_amount': f'{item["cash_amount"]:.2f}',
                'principal_amount': f'{item["principal_amount"]:.2f}',
                'interest_amount': f'{item["interest_amount"]:.2f}',
                'discount_amount': f'{item["discount_amount"]:.2f}',
            }
            for item in allocations
        ],
    }
    return {
        'rental_id': rental_id,
        'account_id': account_id,
        'received_on': received_on,
        'amount': amount,
        'method': method,
        'notes': notes,
        'allocations': allocations,
        'payload_hash': _payload_hash(canonical),
    }


def _normalize_reversal_payload(receipt_id, payload):
    if not isinstance(payload, dict):
        raise ReceiptServiceError('O conteúdo do estorno deve ser um objeto.')
    unknown_keys = set(payload) - {'received_on', 'reason'}
    if unknown_keys:
        raise ReceiptServiceError(
            'O conteúdo do estorno possui campos desconhecidos: '
            + ', '.join(sorted(unknown_keys))
            + '.',
        )
    received_on = _normalize_receipt_date(
        payload.get('received_on'),
        label='data do estorno',
    )
    reason = str(payload.get('reason') or '').strip()
    if not reason:
        raise ReceiptServiceError('Informe o motivo do estorno.')
    canonical = {
        'operation': Receipt.Kind.REVERSAL,
        'receipt_id': receipt_id,
        'received_on': received_on.isoformat(),
        'reason': reason,
    }
    return {
        'received_on': received_on,
        'reason': reason,
        'payload_hash': _payload_hash(canonical),
    }


def _existing_receipt(idempotency_key, payload_hash):
    receipt = Receipt.objects.filter(idempotency_key=idempotency_key).first()
    if receipt is None:
        return None
    if receipt.payload_hash != payload_hash:
        raise ReceiptIdempotencyConflict(
            'A chave de idempotência já foi usada com outro conteúdo.'
        )
    return receipt


def _create_idempotent_receipt(*, idempotency_key, payload_hash, **fields):
    """Create under a savepoint and resolve a concurrent unique-key winner."""
    try:
        with transaction.atomic():
            receipt = Receipt.objects.create(
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                **fields,
            )
        return receipt, True
    except IntegrityError:
        existing = _existing_receipt(idempotency_key, payload_hash)
        if existing is not None:
            return existing, False
        raise


def register_receipt(*, idempotency_key, payload, user=None):
    """Register one cash inflow and allocate it across one rental's titles.

    ``payload`` contains ``rental_id``, ``cash_account_id``, ``received_on``,
    ``amount``, ``method``, optional ``notes`` and an ``allocations`` list. Each
    allocation contains ``receivable_id``, ``cash_amount`` and optional interest
    and discount amounts. The stable idempotency key makes safe retries return
    the original receipt without duplicating money.
    """
    from rentals.models import Rental

    key = _normalize_idempotency_key(idempotency_key)
    normalized = _normalize_receipt_payload(payload)
    existing = _existing_receipt(key, normalized['payload_hash'])
    if existing is not None:
        return existing

    with transaction.atomic():
        try:
            rental = (
                Rental.objects.select_for_update()
                .select_related('customer')
                .get(pk=normalized['rental_id'])
            )
        except Rental.DoesNotExist as exc:
            raise ReceiptServiceError('A locação informada não existe.') from exc

        existing = (
            Receipt.objects.select_for_update()
            .filter(idempotency_key=key)
            .first()
        )
        if existing is not None:
            if existing.payload_hash != normalized['payload_hash']:
                raise ReceiptIdempotencyConflict(
                    'A chave de idempotência já foi usada com outro conteúdo.'
                )
            return existing

        receivable_ids = [
            item['receivable_id'] for item in normalized['allocations']
        ]
        receivables = list(
            Receivable.objects.select_for_update(of=('self',))
            .filter(pk__in=receivable_ids, rental=rental)
            .order_by('pk')
        )
        if len(receivables) != len(receivable_ids):
            raise ReceiptServiceError(
                'Um ou mais títulos não pertencem à locação informada.'
            )
        receivable_by_id = {item.pk: item for item in receivables}

        try:
            account = (
                CashAccount.objects.select_for_update()
                .get(pk=normalized['account_id'])
            )
        except CashAccount.DoesNotExist as exc:
            raise ReceiptServiceError('A conta caixa informada não existe.') from exc
        if not account.active:
            raise ReceiptServiceError('A conta caixa informada está inativa.')

        for allocation in normalized['allocations']:
            receivable = receivable_by_id[allocation['receivable_id']]
            if receivable.written_off_at is not None:
                raise ReceiptServiceError(
                    f'O título #{receivable.pk} está baixado e não pode receber valor.'
                )
            if allocation['principal_amount'] > receivable.balance:
                raise ReceiptServiceError(
                    f'O principal aplicado ao título #{receivable.pk} supera seu saldo.'
                )

        receipt, created = _create_idempotent_receipt(
            idempotency_key=key,
            payload_hash=normalized['payload_hash'],
            kind=Receipt.Kind.INFLOW,
            customer=rental.customer,
            received_on=normalized['received_on'],
            amount=normalized['amount'],
            method=normalized['method'],
            notes=normalized['notes'],
            operator=user,
        )
        if not created:
            return receipt

        payments = []
        for allocation in normalized['allocations']:
            receivable = receivable_by_id[allocation['receivable_id']]
            payment = Payment.objects.create(
                receivable=receivable,
                customer=rental.customer,
                rental=rental,
                payment_date=normalized['received_on'],
                amount=allocation['cash_amount'],
                interest_amount=allocation['interest_amount'],
                discount_amount=allocation['discount_amount'],
                method=normalized['method'],
                notes=normalized['notes'],
                user=user,
            )
            ReceiptAllocation.objects.create(
                receipt=receipt,
                receivable=receivable,
                payment=payment,
                cash_amount=allocation['cash_amount'],
                principal_amount=allocation['principal_amount'],
                interest_amount=allocation['interest_amount'],
                discount_amount=allocation['discount_amount'],
            )
            receivable.recalculate_from_payments()
            payments.append(payment)

        movement = FinancialMovement.objects.create(
            date=normalized['received_on'],
            account=account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=normalized['amount'],
            description=f'Recibo — Locação #{rental.number}',
            source=FinancialMovement.Source.PAYMENT,
            customer=rental.customer,
            receivable=(receivables[0] if len(receivables) == 1 else None),
            payment=(payments[0] if len(payments) == 1 else None),
            rental=rental,
            created_by=user,
        )
        receipt.financial_movement = movement
        receipt.save(update_fields=['financial_movement', 'updated_at'])
        return receipt


def reverse_receipt(receipt, *, idempotency_key, payload, user=None):
    """Reverse an entire receipt atomically with one cash outflow."""
    from rentals.models import Rental

    receipt_id = getattr(receipt, 'pk', None)
    if not receipt_id:
        raise ReceiptServiceError('O recibo informado não existe.')
    key = _normalize_idempotency_key(idempotency_key)
    normalized = _normalize_reversal_payload(receipt_id, payload)
    existing = _existing_receipt(key, normalized['payload_hash'])
    if existing is not None:
        return existing

    with transaction.atomic():
        try:
            # ``financial_movement`` and ``customer`` are both nullable, so
            # select_related joins them as LEFT OUTER JOINs and Postgres refuses
            # to apply FOR UPDATE to the nullable side. Lock only the receipt
            # row: it is what serializes concurrent reversals of this receipt.
            original = (
                Receipt.objects.select_for_update(of=('self',))
                .select_related('financial_movement', 'customer')
                .get(pk=receipt_id)
            )
        except Receipt.DoesNotExist as exc:
            raise ReceiptServiceError('O recibo informado não existe.') from exc
        existing = (
            Receipt.objects.select_for_update()
            .filter(idempotency_key=key)
            .first()
        )
        if existing is not None:
            if existing.payload_hash != normalized['payload_hash']:
                raise ReceiptIdempotencyConflict(
                    'A chave de idempotência já foi usada com outro conteúdo.'
                )
            return existing
        if original.kind == Receipt.Kind.REVERSAL:
            raise ReceiptServiceError('Não é possível estornar um estorno.')
        if hasattr(original, 'reversal'):
            raise ReceiptServiceError('Este recibo já foi estornado.')
        if original.financial_movement_id is None:
            raise ReceiptServiceError(
                'O recibo não possui movimento financeiro para estorno.'
            )
        original_movement = original.financial_movement
        if (
            original_movement.direction != FinancialMovement.Direction.INFLOW
            or original_movement.amount != original.amount
        ):
            raise ReceiptServiceError(
                'O movimento financeiro diverge do recibo. Reconcilie antes de estornar.'
            )

        original_allocations = list(
            ReceiptAllocation.objects.select_for_update()
            .filter(receipt=original)
            .select_related('payment', 'receivable__rental')
            .order_by('receivable_id')
        )
        if not original_allocations:
            raise ReceiptServiceError('O recibo não possui alocações para estorno.')
        rental_ids = sorted({
            allocation.receivable.rental_id
            for allocation in original_allocations
        })
        list(
            Rental.objects.select_for_update()
            .filter(pk__in=rental_ids)
            .order_by('pk')
        )
        receivable_ids = sorted({
            allocation.receivable_id for allocation in original_allocations
        })
        locked_receivables = list(
            Receivable.objects.select_for_update(of=('self',))
            .filter(pk__in=receivable_ids)
            .order_by('pk')
        )
        receivable_by_id = {item.pk: item for item in locked_receivables}
        original_payment_ids = sorted(
            allocation.payment_id for allocation in original_allocations
        )
        original_payments = list(
            Payment.objects.select_for_update(of=('self',))
            .filter(pk__in=original_payment_ids)
            .order_by('pk')
        )
        payment_by_id = {item.pk: item for item in original_payments}
        if any(payment.reversed_by_id for payment in original_payments):
            raise ReceiptServiceError(
                'Uma ou mais alocações deste recibo já foram estornadas.'
            )
        account = CashAccount.objects.select_for_update().get(
            pk=original_movement.account_id,
        )

        reversal, created = _create_idempotent_receipt(
            idempotency_key=key,
            payload_hash=normalized['payload_hash'],
            kind=Receipt.Kind.REVERSAL,
            customer=original.customer,
            received_on=normalized['received_on'],
            amount=original.amount,
            method=original.method,
            notes=f'Estorno: {normalized["reason"]}',
            operator=user,
            reversal_of=original,
        )
        if not created:
            return reversal

        reversal_payments = []
        for allocation in original_allocations:
            receivable = receivable_by_id[allocation.receivable_id]
            original_payment = payment_by_id[allocation.payment_id]
            reversal_payment = Payment.objects.create(
                receivable=receivable,
                customer=original_payment.customer,
                rental=original_payment.rental,
                payment_date=normalized['received_on'],
                amount=-allocation.cash_amount,
                interest_amount=-allocation.interest_amount,
                discount_amount=-allocation.discount_amount,
                method=original.method,
                notes=f'Estorno: {normalized["reason"]}',
                user=user,
                is_reversal=True,
            )
            original_payment.reversed_by = reversal_payment
            original_payment.save(update_fields=['reversed_by', 'updated_at'])
            ReceiptAllocation.objects.create(
                receipt=reversal,
                receivable=receivable,
                payment=reversal_payment,
                cash_amount=allocation.cash_amount,
                principal_amount=allocation.principal_amount,
                interest_amount=allocation.interest_amount,
                discount_amount=allocation.discount_amount,
            )
            receivable.recalculate_from_payments()
            reversal_payments.append(reversal_payment)

        reversal_movement = FinancialMovement.objects.create(
            date=normalized['received_on'],
            account=account,
            direction=FinancialMovement.Direction.OUTFLOW,
            amount=original.amount,
            description=f'Estorno do recibo #{original.pk}',
            source=FinancialMovement.Source.REVERSAL,
            customer=original.customer,
            receivable=(
                locked_receivables[0] if len(locked_receivables) == 1 else None
            ),
            payment=(
                reversal_payments[0] if len(reversal_payments) == 1 else None
            ),
            rental=(
                locked_receivables[0].rental if len(rental_ids) == 1 else None
            ),
            created_by=user,
        )
        reversal.financial_movement = reversal_movement
        reversal.save(update_fields=['financial_movement', 'updated_at'])
        return reversal


def _percentage_amount(base_amount, rate):
    """Apply a Company percentage without imposing an artificial 100% ceiling."""
    base = Decimal(str(base_amount or 0))
    percentage = Decimal(str(rate or 0))
    return _quantize_money(base * percentage / Decimal('100'))


def days_overdue(receivable, on_date=None):
    """Whole days the receivable is past due (0 if paid or not yet due)."""
    if receivable.is_paid:
        return 0
    on_date = on_date or timezone.localdate()
    return max(0, (on_date - receivable.due_date).days)


def compute_interest(receivable, on_date=None, company=None):
    """Late interest using the monthly rate per 30 days, with daily fallback."""
    days = days_overdue(receivable, on_date)
    if days == 0:
        return Decimal('0.00')
    company = company or Company.load()
    monthly_rate = company.monthly_interest_rate or Decimal('0')
    daily_rate = (
        monthly_rate / Decimal('30')
        if monthly_rate
        else company.daily_interest_rate or Decimal('0')
    )
    interest = receivable.balance * (daily_rate / Decimal('100')) * days
    return _quantize_money(interest)


def total_with_interest(receivable, on_date=None, company=None):
    """Open balance plus accrued late interest."""
    return _quantize_money(
        receivable.balance + compute_interest(receivable, on_date, company=company)
    )


def interest_breakdown(receivable, on_date=None, company=None):
    """Return days, interest, and total using one company config lookup."""
    interest = compute_interest(receivable, on_date, company=company)
    return {
        'interest': interest,
        'total_with_interest': _quantize_money(receivable.balance + interest),
        'days_overdue': days_overdue(receivable, on_date),
    }


class PaymentPlanError(ValueError):
    """Raised when a rental payment plan would violate financial invariants."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def generate_for_rental(rental, installments=1, first_due_date=None, total_amount=None, last_due_date=None):
    """Create receivables splitting the rental total into N installments (RF-19/8.1.3).

    The total is divided evenly; any rounding remainder lands on the first
    installment so the sum matches the rental total exactly. Installments fall
    due monthly starting at ``first_due_date`` (defaults to the return date) or
    ending at ``last_due_date`` when specified.
    """
    installments = max(1, int(installments))
    total = (
        Decimal(str(total_amount))
        if total_amount is not None
        else Decimal(str(rental.final_value or 0))
    )
    if total < 0:
        raise PaymentPlanError('O valor das parcelas não pode ser negativo.')

    enforced_policy = (
        rental.financial_policy_version
        == rental.FinancialPolicy.ENFORCED_V1
    )
    if last_due_date:
        due_dates = [_add_months(last_due_date, -(installments - 1 - i)) for i in range(installments)]
    elif first_due_date:
        due_dates = [_add_months(first_due_date, i) for i in range(installments)]
    elif enforced_policy:
        due_dates = [
            _add_months(rental.pickup_date, -(installments - 1 - i))
            for i in range(installments)
        ]
    else:
        due_dates = [_add_months(rental.return_date, i) for i in range(installments)]

    if enforced_policy and max(due_dates) > rental.pickup_date:
        raise PaymentPlanError(
            'A última parcela deve vencer até a data de retirada.',
            field='first_due_date',
        )

    base = _quantize_money(total / installments)
    remainder = total - base * installments

    created = []
    for index, due in enumerate(due_dates):
        amount = base + (remainder if index == 0 else Decimal('0'))
        created.append(
            Receivable.objects.create(rental=rental, due_date=due, amount=amount)
        )
    return created


def _classify_for_reprocess(existing):
    """Split a rental's receivables into protected / partial / replaceable.

    Single definition shared by ``reprocess_future_installments`` and
    ``preview_future_reprocess`` so the screen never promises an outcome the
    rewrite would not produce.
    """
    protected = [
        receivable for receivable in existing
        if (
            receivable.paid_amount != 0
            or receivable.written_off_at is not None
            or len(receivable.payments.all()) > 0
        )
    ]
    partial = [
        receivable for receivable in protected
        if (
            receivable.written_off_at is None
            and receivable.paid_amount > 0
            and receivable.balance > 0
        )
    ]
    replaceable_ids = [
        receivable.pk for receivable in existing if receivable not in protected
    ]
    return protected, partial, replaceable_ids


def preview_future_reprocess(rental):
    """Describe what reprocessing would do, without writing anything.

    Lets the screen disable the action when there is nothing to reschedule and
    state the real consequences before the operator confirms.  Read-only: no
    locks, no writes.
    """
    existing = list(
        Receivable.objects.filter(rental=rental)
        .prefetch_related('payments')
        .order_by('due_date', 'pk')
    )
    protected, partial, replaceable_ids = _classify_for_reprocess(existing)
    # Mirror the rewrite's order: a partially paid title is first closed at the
    # amount already received, and only then does the leftover feed the new
    # schedule. Summing the *current* amounts here would understate what gets
    # rescheduled and could even hide the panel on a rental that reprocesses
    # fine.
    partial_ids = {receivable.pk for receivable in partial}
    protected_total = sum(
        (
            receivable.paid_amount if receivable.pk in partial_ids
            else receivable.amount
            for receivable in protected
        ),
        Decimal('0'),
    )
    remaining = rental.final_value - protected_total
    return {
        'deletable_count': len(replaceable_ids),
        'partial_count': len(partial),
        'partial_released': sum(
            (receivable.balance for receivable in partial), Decimal('0')
        ),
        'remaining': remaining,
        # Mirrors the two ``PaymentPlanError`` guards in the rewrite itself.
        'can_reprocess': remaining > 0,
        'blocked_reason': (
            'Os títulos com histórico financeiro superam o valor da locação.'
            if remaining < 0
            else (
                'Não há saldo sem histórico financeiro para reorganizar.'
                if remaining == 0
                else ''
            )
        ),
    }


def reprocess_future_installments(
    rental,
    installments=1,
    first_due_date=None,
    *,
    user=None,
    reason='Reorganização manual das parcelas futuras.',
):
    """Replace only receivables without payment history.

    Receivables with any payment, write-off, or imported paid amount are kept
    intact.  The remaining contract amount is split into a fresh schedule.  This
    allows the attendant to reorganize the future balance after an entry was
    recorded without deleting or duplicating financial history.

    A *partially* paid title is closed at the amount already received and its
    open balance joins the amount being rescheduled, under every financial
    policy.  That keeps the schedule reorganizable after the customer pays an
    arbitrary amount (the normal case), while every ``Payment`` stays attached to
    the title that received it.

    Returns a dict with ``protected``, ``created``, ``scheduled_amount`` and --
    added for the partial-title disclosure -- ``adjusted_partials`` (one entry
    per rewritten title, with its previous and new amount) and
    ``released_amount``.  Both are also written to the ``AuditLog`` entry.
    """
    installments = int(installments or 0)
    if installments < 1:
        raise PaymentPlanError('Informe ao menos uma parcela futura.')

    with transaction.atomic():
        locked_rental = rental.__class__.objects.select_for_update().get(pk=rental.pk)
        existing = list(
            Receivable.objects.select_for_update()
            .filter(rental=locked_rental)
            .prefetch_related('payments')
            .order_by('due_date', 'pk')
        )
        previous_schedule = [
            {
                'id': receivable.pk,
                'due_date': receivable.due_date.isoformat(),
                'amount': str(receivable.amount),
                'paid_amount': str(receivable.paid_amount),
                'balance': str(receivable.balance),
            }
            for receivable in existing
        ]
        protected, partial, replaceable_ids = _classify_for_reprocess(existing)
        enforced_policy = (
            locked_rental.financial_policy_version
            == locked_rental.FinancialPolicy.ENFORCED_V1
        )
        # A partially paid title is the normal outcome of the informal payment
        # flow: ``register_payment_with_carryover`` settles whatever the customer
        # handed over and leaves the last title it reaches with a residual
        # balance. Refusing to reorganize (the former ENFORCED_V1 behaviour)
        # froze the schedule of every rental that ever received an off-boundary
        # amount -- in practice, every rental. Both policies now take the same
        # path: the paid part is frozen as a closed historical title and only its
        # still-open balance returns to the pool that feeds the new schedule.
        # No Payment row is read, moved or rewritten, and the rental's
        # receivables keep summing to ``final_value`` because the amount removed
        # from the partial title is exactly the amount added to ``remaining``.
        # The rewrite is disclosed in the AuditLog and in the return value so the
        # operator is told which titles changed instead of finding out later.
        adjusted_partials = []
        for receivable in partial:
            previous_amount = receivable.amount
            released = receivable.balance
            receivable.amount = receivable.paid_amount
            receivable.save(update_fields=['amount', 'balance', 'updated_at'])
            adjusted_partials.append({
                'id': receivable.pk,
                'due_date': receivable.due_date.isoformat(),
                'previous_amount': str(previous_amount),
                'amount': str(receivable.amount),
                'released_amount': str(released),
            })
        adjusted_partial_ids = [item['id'] for item in adjusted_partials]
        released_total = sum(
            (Decimal(item['released_amount']) for item in adjusted_partials),
            Decimal('0'),
        )
        protected_total = sum(
            (receivable.amount for receivable in protected),
            Decimal('0'),
        )
        remaining = locked_rental.final_value - protected_total
        if remaining < 0:
            raise PaymentPlanError(
                'Os títulos com histórico financeiro superam o valor da locação. '
                'Revise os recebimentos antes de reprocessar as parcelas.'
            )
        if remaining == 0:
            raise PaymentPlanError(
                'Não há saldo sem histórico financeiro para gerar novas parcelas.'
            )
        if enforced_policy:
            if first_due_date:
                due_dates = [
                    _add_months(first_due_date, index)
                    for index in range(installments)
                ]
            else:
                due_dates = [
                    _add_months(
                        locked_rental.pickup_date,
                        -(installments - 1 - index),
                    )
                    for index in range(installments)
                ]
            if max(due_dates) > locked_rental.pickup_date:
                raise PaymentPlanError(
                    'A última parcela deve vencer até a data de retirada.',
                    field='first_due_date',
                )
            effective_due_date = due_dates[0]
        else:
            effective_due_date = first_due_date or locked_rental.return_date
        payment_dates = [
            receivable.last_payment_date
            for receivable in protected
            if receivable.last_payment_date
        ]
        if payment_dates and effective_due_date <= max(payment_dates):
            raise PaymentPlanError(
                'O primeiro vencimento futuro deve ser posterior ao último recebimento.'
            )

        Receivable.objects.filter(pk__in=replaceable_ids).delete()
        generate_kwargs = {
            'installments': installments,
            'total_amount': remaining,
        }
        if enforced_policy and first_due_date is None:
            generate_kwargs['last_due_date'] = locked_rental.pickup_date
        else:
            generate_kwargs['first_due_date'] = effective_due_date
        created = generate_for_rental(locked_rental, **generate_kwargs)
        AuditLog.record(
            user=user,
            action='reprocess_future_installments',
            obj=locked_rental,
            reason=reason,
            metadata={
                'previous_schedule': previous_schedule,
                'protected_receivable_ids': [item.pk for item in protected],
                'adjusted_partial_receivable_ids': adjusted_partial_ids,
                'adjusted_partials': adjusted_partials,
                'released_amount': str(released_total),
                'deleted_receivable_ids': replaceable_ids,
                'new_schedule': [
                    {
                        'id': item.pk,
                        'due_date': item.due_date.isoformat(),
                        'amount': str(item.amount),
                    }
                    for item in created
                ],
                'previous_contract_version': locked_rental.contract_version,
                'previous_contract_printed_at': (
                    locked_rental.contract_printed_at.isoformat()
                    if locked_rental.contract_printed_at
                    else None
                ),
            },
        )

    return {
        'protected': protected,
        'created': created,
        'scheduled_amount': remaining,
        # Additive keys: the caller may warn the operator that a partially paid
        # title was closed at the amount already received.
        'adjusted_partials': adjusted_partials,
        'released_amount': released_total,
        'deleted_count': len(replaceable_ids),
    }


def create_rental_payment_plan(
    rental,
    *,
    installments=0,
    first_due_date=None,
    down_payment_amount=None,
    down_payment_date=None,
    down_payment_method=None,
    user=None,
):
    """Create a contract plan with a distinct paid entry and future balance.

    The entry becomes its own fully paid receivable, so contracts and accounts
    receivable show it as a separate condition.  The remaining amount is split
    evenly into monthly future installments, with cent rounding applied to the
    first future installment.
    """
    try:
        total = _normalize_money(rental.final_value, 'valor total da locação')
        entry_amount = _normalize_money(
            down_payment_amount or 0,
            'valor da entrada',
        )
    except ValueError as exc:
        raise PaymentPlanError(str(exc), field='down_payment_amount') from exc
    installments = int(installments or 0)
    enforced_policy = (
        rental.financial_policy_version
        == rental.FinancialPolicy.ENFORCED_V1
    )

    if entry_amount < 0:
        raise PaymentPlanError(
            'O valor da entrada não pode ser negativo.', field='down_payment_amount',
        )
    if entry_amount > total:
        raise PaymentPlanError(
            'O valor da entrada não pode superar o total da locação.',
            field='down_payment_amount',
        )
    if enforced_policy and total > 0 and entry_amount <= 0:
        raise PaymentPlanError(
            'Informe uma entrada maior que zero para confirmar a locação.',
            field='down_payment_amount',
        )

    remaining = total - entry_amount
    if entry_amount > 0:
        if not down_payment_date:
            raise PaymentPlanError(
                'Informe a data em que a entrada foi recebida.', field='down_payment_date',
            )
        if down_payment_date > timezone.localdate():
            raise PaymentPlanError(
                'A data da entrada não pode estar no futuro.', field='down_payment_date',
            )
        if down_payment_method not in Payment.Method.values:
            raise PaymentPlanError(
                'Informe uma forma de recebimento válida para a entrada.',
                field='down_payment_method',
            )
    if remaining > 0 and installments < 1:
        raise PaymentPlanError(
            'Informe ao menos uma parcela futura para o saldo restante.',
            field='installment_count',
        )
    if entry_amount > 0 and remaining > 0:
        if first_due_date:
            due_dates = [
                _add_months(first_due_date, index)
                for index in range(installments)
            ]
        else:
            due_dates = [
                _add_months(rental.pickup_date, -(installments - 1 - index))
                for index in range(installments)
            ]
        if enforced_policy and max(due_dates) > rental.pickup_date:
            raise PaymentPlanError(
                'A última parcela deve vencer até a data de retirada.',
                field='first_due_date',
            )
        if min(due_dates) <= down_payment_date:
            raise PaymentPlanError(
                'As parcelas futuras devem vencer depois da data da entrada.',
                field='first_due_date',
            )

    with transaction.atomic():
        locked_rental = rental.__class__.objects.select_for_update().get(pk=rental.pk)
        if locked_rental.receivables.select_for_update().exists():
            raise PaymentPlanError(
                'As condições de pagamento desta locação já foram geradas.'
            )

        entry_receivable = None
        entry_payment = None
        entry_receipt = None
        if entry_amount > 0:
            # Fail fast with the plan-specific message; register_receipt also
            # locks and re-checks the selected account before posting cash.
            cash_account = _active_cash_account()
            if cash_account is None:
                raise PaymentPlanError(
                    'Ative uma conta de caixa antes de registrar a entrada.'
                )
            entry_receivable = Receivable.objects.create(
                rental=locked_rental,
                due_date=down_payment_date,
                amount=entry_amount,
            )
            try:
                entry_receipt = register_receipt(
                    idempotency_key=uuid4(),
                    payload={
                        'rental_id': locked_rental.pk,
                        'cash_account_id': cash_account.pk,
                        'received_on': down_payment_date,
                        'amount': entry_amount,
                        'method': down_payment_method,
                        'notes': 'Entrada na criação da locação',
                        'allocations': [{
                            'receivable_id': entry_receivable.pk,
                            'cash_amount': entry_amount,
                            'interest_amount': Decimal('0.00'),
                            'discount_amount': Decimal('0.00'),
                        }],
                    },
                    user=user,
                )
            except ReceiptServiceError as exc:
                raise PaymentPlanError(str(exc)) from exc
            entry_payment = entry_receipt.allocations.select_related(
                'payment',
            ).get().payment
            entry_receivable.refresh_from_db()

        future_receivables = []
        if remaining > 0:
            if first_due_date:
                future_receivables = generate_for_rental(
                    locked_rental,
                    installments=installments,
                    first_due_date=first_due_date,
                    total_amount=remaining,
                )
            else:
                # Golden rule (client request): with no explicit first due
                # date, the last installment must land on the pickup date.
                future_receivables = generate_for_rental(
                    locked_rental,
                    installments=installments,
                    last_due_date=locked_rental.pickup_date,
                    total_amount=remaining,
                )

    return {
        'entry_receivable': entry_receivable,
        'entry_payment': entry_payment,
        'entry_receipt': entry_receipt,
        'future_receivables': future_receivables,
    }


def _add_months(start, months):
    """Add ``months`` to a date, clamping the day to the target month length."""
    from calendar import monthrange

    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    last_day = monthrange(year, month)[1]
    return date_cls(year, month, min(start.day, last_day))


def _active_cash_account():
    """Pick the account that receives cash, without serializing the hot path.

    This selection used to be ``select_for_update()``. With a single active
    account -- the real production setup -- every ``register_payment`` in the
    system queued behind that one row until the whole transaction committed, so
    unrelated cashiers serialized against each other. ``no_key=True`` would not
    have helped: FOR NO KEY UPDATE conflicts with itself, so the queue would
    remain.

    What the lock actually bought was "do not post cash to an account that is
    being deactivated", and that is restored by ``_assert_cash_account_active``
    instead. Removing the lock also removes this row from the canonical
    Rental -> Receivable -> CashAccount ordering in ``register_payment``, so that
    path can no longer take part in a lock cycle at all.
    """
    return CashAccount.objects.filter(active=True).order_by('id').first()


def _assert_cash_account_active(account):
    """Re-check the account right after posting, replacing the old row lock.

    Called once the ``FinancialMovement`` insert already holds the implicit FOR
    KEY SHARE lock on the parent row, so the account can no longer be deleted
    underneath the receipt. Under READ COMMITTED this re-read sees any
    deactivation committed before this point and rolls the whole receipt back.
    A deactivation committing in the instant between this check and our own
    commit still wins the race; that is deliberate and harmless -- the money was
    genuinely received while the account was active -- and it is the price of
    not holding a global lock for the duration of every payment.
    """
    if not CashAccount.objects.filter(pk=account.pk, active=True).exists():
        raise ValueError(
            'A conta caixa foi desativada durante o recebimento. '
            'Refaça a operação em uma conta ativa.'
        )


def register_payment(receivable, amount, payment_date, method='cash',
                     interest_amount=None, discount_amount=None, notes='', user=None):
    """Create Payment, recalculate receivable balance, create FinancialMovement inflow (R5.06)."""
    amount = _normalize_money(amount, 'valor recebido')
    interest_amount = _normalize_money(interest_amount or 0, 'valor dos juros')
    discount_amount = _normalize_money(discount_amount or 0, 'valor do desconto')
    if amount <= 0:
        raise ValueError('O valor recebido deve ser maior que zero.')
    if method not in Payment.Method.values:
        raise ValueError('A forma de recebimento informada é inválida.')
    if payment_date > timezone.localdate():
        raise ValueError('A data do recebimento não pode estar no futuro.')

    with transaction.atomic():
        from rentals.models import Rental

        rental_id = (
            Receivable.objects.filter(pk=receivable.pk)
            .values_list('rental_id', flat=True)
            .get()
        )
        Rental.objects.select_for_update().only('pk').get(pk=rental_id)
        locked_receivable = (
            Receivable.objects.select_for_update(of=('self',))
            .select_related('rental__customer')
            .get(pk=receivable.pk)
        )
        if locked_receivable.written_off_at is not None:
            raise ValueError('Não é possível receber um título baixado.')
        principal_amount = amount - interest_amount + discount_amount
        if principal_amount < 0:
            raise ValueError(
                'O valor dos juros não pode superar o dinheiro recebido '
                'somado ao desconto.'
            )
        if principal_amount > locked_receivable.balance:
            raise ValueError(
                'O valor aplicado ao principal não pode superar o saldo do título.'
            )
        account = _active_cash_account()
        if account is None:
            raise ValueError(
                'Não é possível registrar recebimento sem uma conta caixa ativa. '
                'Configure uma conta em Financeiro > Contas.'
            )
        payment = Payment.objects.create(
            receivable=locked_receivable,
            customer=locked_receivable.rental.customer,
            rental=locked_receivable.rental,
            payment_date=payment_date,
            amount=amount,
            interest_amount=interest_amount,
            discount_amount=discount_amount,
            method=method,
            notes=notes,
            user=user,
        )
        locked_receivable.recalculate_from_payments()

        FinancialMovement.objects.create(
            date=payment_date,
            account=account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=amount,
            description=f'Recebimento — Locação #{locked_receivable.rental.number}',
            source=FinancialMovement.Source.PAYMENT,
            customer=locked_receivable.rental.customer,
            receivable=locked_receivable,
            payment=payment,
            rental=locked_receivable.rental,
            created_by=user,
        )
        _assert_cash_account_active(account)
    return payment


def register_recovered_legacy_payment(receivable, amount, payment_date,
                                      method='cash', notes='', user=None):
    """Materialize an audited Payment for principal already cached by Access.

    The approved one-time recovery starts with ``paid_amount`` populated but no
    Payment row. Clear that cache under the canonical locks, then delegate the
    actual financial write to ``register_payment`` so the final balance and cash
    movement are rebuilt from the recovered event exactly once.
    """
    amount = _normalize_money(amount, 'valor recebido')
    with transaction.atomic():
        from rentals.models import Rental

        rental_id = (
            Receivable.objects.filter(pk=receivable.pk)
            .values_list('rental_id', flat=True)
            .get()
        )
        Rental.objects.select_for_update().only('pk').get(pk=rental_id)
        locked_receivable = Receivable.objects.select_for_update(of=('self',)).get(
            pk=receivable.pk,
        )
        if locked_receivable.legacy_source != 'pagar':
            raise ValueError('A recuperação só aceita títulos importados do Access.')
        if locked_receivable.written_off_at is not None:
            raise ValueError('Não é possível recuperar pagamento de título baixado.')
        if locked_receivable.payments.exists():
            raise ValueError('O pagamento legado deste título já foi recuperado.')
        if locked_receivable.paid_amount != amount:
            raise ValueError('O valor legado diverge do pagamento a recuperar.')

        locked_receivable.paid_amount = Decimal('0')
        locked_receivable.last_payment_date = None
        locked_receivable.save(
            update_fields=['paid_amount', 'last_payment_date', 'updated_at'],
        )
        return register_payment(
            locked_receivable,
            amount=amount,
            payment_date=payment_date,
            method=method,
            notes=notes,
            user=user,
        )


def _new_allocation(receivable_id, cash_amount):
    return {
        'receivable_id': receivable_id,
        'cash_amount': cash_amount,
        'interest_amount': Decimal('0.00'),
        'discount_amount': Decimal('0.00'),
    }


def allocated_cash(allocations):
    """Total cash across ``allocations`` — the receipt ``amount`` to declare."""
    return sum(
        (item['cash_amount'] for item in allocations),
        Decimal('0.00'),
    )


def plan_carryover_allocations(*, target, schedule, amount, target_interest=0):
    """Spread ``amount`` over one rental's schedule, clicked title first.

    This is the single source of truth for carryover allocation policy: the
    clicked title is settled first, the surplus then clears the remaining open
    titles from oldest to newest, no title ever receives more than its own
    balance, and the late interest of the clicked title is settled last.
    ``register_receipt`` consumes the result directly, and
    ``register_payment_with_carryover`` materializes the same plan as one
    ``Payment`` per title.

    ``schedule`` must hold every receivable of the rental already ordered by
    ``(due_date, pk)`` and must contain ``target``. ``target_interest`` is the
    late interest the cashier may charge on the clicked title; it is only
    consumed once every open principal is settled. Nothing here touches the
    database — callers load and lock the rows, so the planner stays trivially
    testable and never changes the lock hierarchy.

    Interest rides inside the clicked title's own allocation
    (``cash = principal + interest``) because ``ReceiptAllocation`` is unique
    per ``(receipt, receivable)``.

    Raises ``PaymentPlanError`` (field ``value``) when the amount exceeds what
    the whole rental can absorb, and ``ValueError`` when the clicked title is
    not receivable at all.
    """
    amount = _normalize_money(amount, 'valor recebido')
    if amount <= 0:
        raise PaymentPlanError(
            'O valor recebido deve ser maior que zero.',
            field='value',
        )
    if target.written_off_at is not None:
        raise ValueError('Não é possível receber um título baixado.')
    if target.balance <= 0:
        raise ValueError(
            'Não é possível receber um título quitado ou com saldo inválido.'
        )

    target_interest = _normalize_money(target_interest or 0, 'valor dos juros')
    others = [
        item for item in schedule
        if (
            item.pk != target.pk
            and item.balance > 0
            and item.written_off_at is None
        )
    ]
    principal_order = [(target, target.balance)]
    principal_order += [(item, item.balance) for item in others]

    principal_total = sum((limit for _, limit in principal_order), Decimal('0'))
    capacity = principal_total + target_interest
    if amount > capacity:
        composition = f'R$ {principal_total:.2f} de saldo em aberto'
        if target_interest > 0:
            composition += f' e R$ {target_interest:.2f} de juros deste título'
        message = (
            'O valor informado é maior que o saldo total desta locação '
            f'(R$ {capacity:.2f} = {composition}).'
        )
        if any(days_overdue(item) > 0 for item, _ in principal_order[1:]):
            message += (
                ' Os demais títulos vencidos são quitados sem juros neste '
                'recebimento; para cobrar juros deles, receba cada título '
                'pela sua própria tela.'
            )
        raise PaymentPlanError(message, field='value')

    allocations = []
    remaining = amount
    for receivable, limit in principal_order:
        if remaining <= 0:
            break
        if limit <= 0:
            continue
        applied = min(remaining, limit)
        allocations.append(_new_allocation(receivable.pk, applied))
        remaining -= applied

    # Whatever is left once every installment's principal is settled can only be
    # the late interest the cashier chose to charge on the title they opened:
    # ``capacity`` above admits no other surplus.
    if remaining > 0 and target_interest > 0:
        charged_interest = min(remaining, target_interest)
        allocation = next(
            (item for item in allocations if item['receivable_id'] == target.pk),
            None,
        )
        if allocation is None:
            allocation = _new_allocation(target.pk, Decimal('0.00'))
            allocations.append(allocation)
        allocation['cash_amount'] += charged_interest
        allocation['interest_amount'] += charged_interest
        remaining -= charged_interest

    return allocations


def plan_selected_allocations(*, receivables, amount):
    """Spread ``amount`` over hand-picked titles, in the given order.

    Used by the multi-title screen, where the operator already chose which
    titles the cash settles. Only principal is allocated; no title receives more
    than its own balance, and titles that are closed or written off are skipped
    instead of driving a balance negative.
    """
    remaining = _normalize_money(amount, 'valor recebido')
    allocations = []
    for receivable in receivables:
        if remaining <= 0:
            break
        if receivable.balance <= 0 or receivable.written_off_at is not None:
            continue
        applied = min(remaining, receivable.balance)
        allocations.append(_new_allocation(receivable.pk, applied))
        remaining -= applied
    return allocations


def register_payment_with_carryover(receivable, amount, payment_date, method='cash',
                                    notes='', user=None):
    """Receive ``amount`` on ``receivable``, spilling the excess into the other
    open installments of the same rental (RF-21 follow-up).

    Customers pay whatever they can afford in a given month, so a R$ 200 payment
    against a R$ 60 installment must settle that one and keep going down the
    schedule instead of being refused. Each installment still gets its own
    ``Payment``, and no installment ever receives more than it asks for — paying
    a single title beyond its balance would drive ``balance`` negative, which is
    the exact corruption ``reconcile_negative_balances`` exists to undo.

    **Interest policy: only the title the cashier opened accrues late interest
    here.** The rental has two inviolable rules -- an entry at contract time and
    a debt fully settled by the pickup date -- so the due dates of the
    intermediate installments organize the expectation but are not an obligation
    the customer can be charged late interest for. Titles reached by the cascade
    are therefore settled at their plain balance. Charging them automatically
    would also push the debt above the ceiling the payment screen shows the
    operator (this title's total with interest plus the plain balance of the
    others), turning a mistyped amount into an interest charge instead of an
    error. To charge interest on another overdue title, receive that title
    directly. ``amount`` is capped at that same ceiling.

    Returns the list of payments created, in allocation order.
    """
    amount = _normalize_money(amount, 'valor recebido')
    if amount <= 0:
        raise ValueError('O valor recebido deve ser maior que zero.')

    payments = []
    with transaction.atomic():
        from rentals.models import Rental

        rental_id = (
            Receivable.objects.filter(pk=receivable.pk)
            .values_list('rental_id', flat=True)
            .get()
        )
        Rental.objects.select_for_update().only('pk').get(pk=rental_id)
        schedule = list(
            Receivable.objects.select_for_update(of=('self',))
            .filter(rental_id=rental_id)
            .select_related('rental')
            .order_by('due_date', 'pk')
        )
        target = next((item for item in schedule if item.pk == receivable.pk), None)
        if target is None:
            raise ValueError('O título informado não pertence mais a esta locação.')
        if target.written_off_at is not None:
            raise ValueError('Não é possível receber um título baixado.')
        if target.balance <= 0:
            raise ValueError('Não é possível receber um título quitado ou com saldo inválido.')

        # One allocation policy, two materializations: the plan below is the same
        # one ``register_receipt`` consumes. All rows were locked above in
        # canonical order, so the planner sees a stable schedule.
        by_id = {item.pk: item for item in schedule}
        allocations = plan_carryover_allocations(
            target=target,
            schedule=schedule,
            amount=amount,
            target_interest=total_with_interest(target) - target.balance,
        )

        # Principal first, across the whole schedule, in allocation order.
        # Interest is settled last as its own Payment: recalculate_from_payments
        # excludes it from principal, so it cannot push any title into a
        # negative balance.
        charged_interest = Decimal('0.00')
        for allocation in allocations:
            interest = allocation['interest_amount']
            charged_interest += interest
            principal_amount = allocation['cash_amount'] - interest
            if principal_amount <= 0:
                continue
            payments.append(register_payment(
                receivable=by_id[allocation['receivable_id']],
                amount=principal_amount,
                payment_date=payment_date,
                method=method,
                notes=notes,
                user=user,
            ))

        if charged_interest > 0:
            payments.append(register_payment(
                receivable=target,
                amount=charged_interest,
                payment_date=payment_date,
                method=method,
                interest_amount=charged_interest,
                notes=notes,
                user=user,
            ))
    return payments


def reverse_payment(payment, reason, user=None):
    """Create reversal Payment (negative amount) and FinancialMovement outflow (R5.09)."""
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Informe o motivo do estorno.')
    today = timezone.localdate()
    with transaction.atomic():
        from rentals.models import Rental

        rental_id = (
            Payment.objects.filter(pk=payment.pk)
            .values_list('receivable__rental_id', flat=True)
            .get()
        )
        Rental.objects.select_for_update().only('pk').get(pk=rental_id)
        locked_payment = (
            Payment.objects.select_for_update(of=('self',))
            .select_related('receivable', 'customer', 'rental')
            .get(pk=payment.pk)
        )
        if locked_payment.is_reversal or locked_payment.reversed_by_id is not None:
            raise ValueError('Este recebimento já foi estornado.')

        receivable = (
            Receivable.objects.select_for_update(of=('self',))
            .select_related('rental__customer')
            .get(pk=locked_payment.receivable_id)
        )
        original_movements = list(
            FinancialMovement.objects.select_for_update()
            .filter(
                payment=locked_payment,
                direction=FinancialMovement.Direction.INFLOW,
                source=FinancialMovement.Source.PAYMENT,
            )
            .order_by('pk')[:2]
        )
        if len(original_movements) != 1:
            raise ValueError(
                'O recebimento precisa ter exatamente um movimento de entrada '
                'para ser estornado com segurança.'
            )
        original_movement = original_movements[0]
        if (
            original_movement.amount != locked_payment.amount
            or original_movement.receivable_id != locked_payment.receivable_id
            or original_movement.rental_id != locked_payment.rental_id
        ):
            raise ValueError(
                'O movimento de entrada diverge do recebimento. Execute a '
                'reconciliação financeira antes de estornar.'
            )
        account = (
            CashAccount.objects.select_for_update()
            .get(pk=original_movement.account_id)
        )
        reversal = Payment.objects.create(
            receivable=receivable,
            customer=locked_payment.customer,
            rental=locked_payment.rental,
            payment_date=today,
            amount=-locked_payment.amount,
            interest_amount=-locked_payment.interest_amount,
            discount_amount=-locked_payment.discount_amount,
            method=locked_payment.method,
            notes=f'Estorno: {reason}',
            user=user,
            is_reversal=True,
        )
        locked_payment.reversed_by = reversal
        locked_payment.save(update_fields=['reversed_by', 'updated_at'])
        receivable.recalculate_from_payments()

        FinancialMovement.objects.create(
            date=today,
            account=account,
            direction=FinancialMovement.Direction.OUTFLOW,
            amount=locked_payment.amount,
            description=(
                f'Estorno pgto #{locked_payment.pk} — '
                f'Locação #{locked_payment.rental.number if locked_payment.rental else "?"}'
            ),
            source=FinancialMovement.Source.REVERSAL,
            customer=locked_payment.customer,
            receivable=receivable,
            payment=reversal,
            rental=locked_payment.rental,
            created_by=user,
        )
    return reversal


def write_off_receivable(receivable, reason, user=None):
    """Close one positive outstanding balance while preserving its history."""
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('A reconciliation reason is required.')

    with transaction.atomic():
        locked_receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental')
            .get(pk=receivable.pk)
        )
        if (
            locked_receivable.written_off_at is not None
            or locked_receivable.balance <= 0
        ):
            return False

        previous_balance = locked_receivable.balance
        written_off_at = timezone.now()
        locked_receivable.written_off_at = written_off_at
        locked_receivable.written_off_reason = reason
        locked_receivable.save(update_fields=[
            'written_off_at',
            'written_off_reason',
            'balance',
            'updated_at',
        ])
        AuditLog.record(
            user=user,
            action='write_off_receivable',
            obj=locked_receivable,
            reason=reason,
            metadata={
                'amount': str(locked_receivable.amount),
                'paid_amount': str(locked_receivable.paid_amount),
                'previous_balance': str(previous_balance),
                'written_off_at': written_off_at.isoformat(),
            },
        )

    return True


def write_off_receivables(receivables, reason, user=None):
    """Write off an audited batch of positive balances in one transaction."""
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('A reconciliation reason is required.')

    receivable_ids = sorted({receivable.pk for receivable in receivables})
    if not receivable_ids:
        return 0

    with transaction.atomic():
        locked_receivables = list(
            Receivable.objects.select_for_update().filter(
                pk__in=receivable_ids,
                written_off_at__isnull=True,
                balance__gt=0,
            )
        )
        if not locked_receivables:
            return 0

        written_off_at = timezone.now()
        for receivable in locked_receivables:
            previous_balance = receivable.balance
            receivable.written_off_at = written_off_at
            receivable.written_off_reason = reason
            receivable.balance = Decimal('0')
            receivable.updated_at = written_off_at
            receivable._recovery_previous_balance = previous_balance

        Receivable.objects.bulk_update(
            locked_receivables,
            ['written_off_at', 'written_off_reason', 'balance', 'updated_at'],
            batch_size=1000,
        )
        AuditLog.objects.bulk_create([
            AuditLog(
                user=user,
                action='write_off_receivable',
                model_name='Receivable',
                object_id=str(receivable.pk),
                object_repr=str(receivable)[:200],
                reason=reason,
                metadata={
                    'amount': str(receivable.amount),
                    'paid_amount': str(receivable.paid_amount),
                    'previous_balance': str(
                        receivable._recovery_previous_balance,
                    ),
                    'written_off_at': written_off_at.isoformat(),
                },
            )
            for receivable in locked_receivables
        ], batch_size=1000)

    return len(locked_receivables)


def legacy_writeoff_review_queryset():
    """Find migration write-offs hiding real debt, as seen in rental #56410.

    ``core.management.commands.legacy_reset`` writes off receivables in bulk
    via a single queryset ``.update()`` and logs one aggregate ``AuditLog``
    row with ``object_id='bulk'`` — there is no per-receivable audit trail to
    join against. The only per-row marker left by that command is the
    ``written_off_reason`` text it stamps on each ``Receivable`` (its
    ``--reason``, default ``legacy_reset.DEFAULT_REASON``), so that field is
    the correlator used here instead of ``AuditLog``.
    """
    legacy_reasons = set(
        AuditLog.objects.filter(
            action='legacy_reset_write_off',
            model_name='Receivable',
        ).values_list('reason', flat=True)
    )
    if not legacy_reasons:
        return Receivable.objects.none()
    return (
        Receivable.objects.filter(
            written_off_at__isnull=False,
            written_off_reason__in=legacy_reasons,
        )
        .annotate(hidden_balance=F('amount') - F('paid_amount'))
        .filter(hidden_balance__gt=0)
        .select_related('rental', 'rental__customer')
        .order_by('due_date')
    )


def revert_write_off_receivable(receivable, user=None, reason=''):
    """Reopen a previously written-off receivable."""
    with transaction.atomic():
        locked_receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental')
            .get(pk=receivable.pk)
        )
        if locked_receivable.written_off_at is None:
            raise ValueError('Este recebimento não está baixado.')

        previous_reason = locked_receivable.written_off_reason
        locked_receivable.written_off_at = None
        locked_receivable.written_off_reason = ''
        locked_receivable.save(update_fields=[
            'written_off_at',
            'written_off_reason',
            'balance',
            'updated_at',
        ])
        AuditLog.record(
            user=user,
            action='revert_write_off_receivable',
            obj=locked_receivable,
            reason=reason or 'Reversão de baixa efetuada pelo operador',
            metadata={
                'amount': str(locked_receivable.amount),
                'paid_amount': str(locked_receivable.paid_amount),
                'new_balance': str(locked_receivable.balance),
                'previous_written_off_reason': previous_reason,
            },
        )
        return True


def reconcile_overpayment(receivable, reason, user=None):
    """Write off a negative receivable balance and record the adjustment.

    The amount and paid amount remain unchanged as historical evidence. The
    receivable write-off fields make the derived balance zero, matching the
    existing write-off invariant. A locked recheck makes repeated calls safe.
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('A reconciliation reason is required.')

    with transaction.atomic():
        locked_receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental')
            .get(pk=receivable.pk)
        )
        if locked_receivable.balance >= 0:
            return False

        previous_balance = locked_receivable.balance
        reconciled_at = timezone.now()
        locked_receivable.written_off_at = reconciled_at
        locked_receivable.written_off_reason = reason
        locked_receivable.save(update_fields=[
            'written_off_at',
            'written_off_reason',
            'balance',
            'updated_at',
        ])
        AuditLog.record(
            user=user,
            action='reconcile_overpayment',
            obj=locked_receivable,
            reason=reason,
            metadata={
                'amount': str(locked_receivable.amount),
                'paid_amount': str(locked_receivable.paid_amount),
                'previous_balance': str(previous_balance),
                'overpayment_amount': str(abs(previous_balance)),
                'reconciled_at': reconciled_at.isoformat(),
            },
        )

    return True


def compute_moratoria(receivable, on_date=None, company=None):
    """Late moratoria fee (multa moratória) on the open balance using Company.late_fee_rate (R6.09).

    Applied once when the receivable becomes overdue (not per day).
    Returns zero if paid or not overdue.
    """
    if receivable.is_paid:
        return Decimal('0.00')
    on_date = on_date or timezone.localdate()
    if on_date <= receivable.due_date:
        return Decimal('0.00')
    company = company or Company.load()
    rate = company.late_fee_rate or Decimal('0')
    fee = receivable.balance * (rate / Decimal('100'))
    return _quantize_money(fee)


def compute_monthly_interest(receivable, on_date=None, company=None):
    """Compatibility alias for the canonical monthly interest calculation."""
    return compute_interest(receivable, on_date=on_date, company=company)


def compute_damage_penalty(company=None, amount=None):
    """Damage penalty: a flat R$ ``amount`` charged once per rental (R6.09).

    Falls back to ``Company.damage_penalty_amount`` when no ``amount`` is
    given. Rentals may stipulate their own amount
    (``Rental.effective_damage_penalty_amount``) instead of the company
    default — the fine is a value fixed per contract, not a percentage of
    the item's rental value, and not multiplied by how many items are
    marked damaged (most rentals are single-item anyway).
    """
    if amount is None:
        company = company or Company.load()
        amount = company.damage_penalty_amount
    return _quantize_money(Decimal(str(amount)))


def compute_loss_penalty(item_value, company=None):
    """Loss/non-return penalty: Company.loss_penalty_rate % of item value (R6.09)."""
    company = company or Company.load()
    return _percentage_amount(item_value, company.loss_penalty_rate)


def compute_cancellation_penalty(rental, company=None):
    """Cancellation total: Company.cancellation_penalty_rate % of rental value."""
    company = company or Company.load()
    return _percentage_amount(rental.final_value, company.cancellation_penalty_rate)


def create_penalty_receivable(
    rental,
    *,
    amount,
    due_date,
    kind,
    user=None,
    details='',
):
    """Create an auditable receivable generated by an operational penalty."""
    amount = _quantize_money(Decimal(str(amount or 0)))
    if amount <= 0:
        return None
    notes = f'{PENALTY_NOTE_PREFIX}{kind}'
    if details:
        notes = f'{notes}; {details}'
    receivable = Receivable.objects.create(
        rental=rental,
        due_date=due_date,
        amount=amount,
        legacy_notes=notes,
    )
    AuditLog.record(
        user=user,
        action='create_penalty_receivable',
        obj=receivable,
        reason=f'Penalidade de {kind} aplicada à locação #{rental.number}.',
        metadata={
            'kind': kind,
            'amount': str(amount),
            'due_date': due_date.isoformat(),
            'details': details,
        },
    )
    return receivable


def apply_cancellation_penalty(rental, *, user=None, due_date=None):
    """Adjust the active balance to the Company cancellation percentage.

    A normal rental already has receivables for 100% of its value.  This keeps
    100% cancellation neutral, creates an adjustment above that amount (for
    example, 200%), and reduces unpaid balances when the configured percentage
    is lower.  Amounts already paid are never rewritten or refunded.
    """
    due_date = due_date or timezone.localdate()
    with transaction.atomic():
        locked_rental = rental.__class__.objects.select_for_update().get(pk=rental.pk)
        company = Company.load()
        target_amount = compute_cancellation_penalty(locked_rental, company=company)
        receivables = list(
            Receivable.objects.select_for_update()
            .filter(rental=locked_rental)
            .order_by('due_date', 'pk')
        )
        paid_total = sum(
            (receivable.paid_amount for receivable in receivables),
            Decimal('0'),
        )
        open_receivables = [
            receivable
            for receivable in receivables
            if receivable.written_off_at is None and receivable.balance > 0
        ]
        current_open = sum(
            (receivable.balance for receivable in open_receivables),
            Decimal('0'),
        )
        desired_open = max(target_amount - paid_total, Decimal('0'))
        adjustment = desired_open - current_open
        reduced = []
        added_receivable = None

        if adjustment < 0:
            remaining_reduction = -adjustment
            for receivable in reversed(open_receivables):
                reduction = min(remaining_reduction, receivable.balance)
                if reduction <= 0:
                    continue
                previous_amount = receivable.amount
                receivable.amount -= reduction
                receivable.save(update_fields=['amount', 'balance', 'updated_at'])
                reduced.append({
                    'id': receivable.pk,
                    'previous_amount': str(previous_amount),
                    'new_amount': str(receivable.amount),
                })
                remaining_reduction -= reduction
                if remaining_reduction <= 0:
                    break
        elif adjustment > 0:
            added_receivable = create_penalty_receivable(
                locked_rental,
                amount=adjustment,
                due_date=due_date,
                kind='cancellation',
                user=user,
                details=(
                    f'Percentual {company.cancellation_penalty_rate}% '
                    f'sobre R$ {locked_rental.final_value:.2f}'
                ),
            )

        locked_rental.cancellation_penalty_rate = company.cancellation_penalty_rate
        locked_rental.cancellation_penalty_amount = target_amount
        locked_rental.save(update_fields=[
            'cancellation_penalty_rate',
            'cancellation_penalty_amount',
            'updated_at',
        ])
        AuditLog.record(
            user=user,
            action='apply_cancellation_penalty',
            obj=locked_rental,
            reason='Penalidade de desistência/rescisão calculada pela configuração da Empresa.',
            metadata={
                'rate': str(company.cancellation_penalty_rate),
                'target_amount': str(target_amount),
                'paid_total': str(paid_total),
                'current_open': str(current_open),
                'adjustment': str(adjustment),
                'reduced_receivables': reduced,
                'added_receivable_id': (
                    added_receivable.pk if added_receivable else None
                ),
            },
        )
    return {
        'amount': target_amount,
        'rate': company.cancellation_penalty_rate,
        'adjustment': adjustment,
        'paid_exceeds_amount': paid_total > target_amount,
    }


def reconcile_financial():
    """Compare receivables, payments, balances and movements. Return dict of aggregates (R6.05)."""
    from .models import FinancialMovement, Payment, Receivable

    total_receivable_amount = (
        Receivable.objects.aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_open_balance = (
        Receivable.objects.filter(balance__gt=0).aggregate(v=Sum('balance'))['v'] or Decimal('0')
    )
    total_payments = (
        Payment.objects.filter(is_reversal=False).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_reversals = (
        Payment.objects.filter(is_reversal=True).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_inflow = (
        FinancialMovement.objects.filter(direction=FinancialMovement.Direction.INFLOW)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_outflow = (
        FinancialMovement.objects.filter(direction=FinancialMovement.Direction.OUTFLOW)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )

    # Divergence 1: paid receivables (balance <= 0) with no Payment records
    # These are legacy-imported receivables forced closed by pago=0 logic.
    # Written-off receivables also have balance forced to zero without any
    # Payment — exclude them so the divergence keeps its original meaning.
    paid_no_payments = (
        Receivable.objects.filter(balance__lte=0, written_off_at__isnull=True)
        .exclude(payments__isnull=False)
    )
    paid_no_payments_count = paid_no_payments.count()
    paid_no_payments_sum = (
        paid_no_payments.aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )

    # Divergence 2: open receivables where stored settled principal differs from
    # cash minus interest plus discounts, including negative reversal rows.
    inconsistent_qs = (
        Receivable.objects.filter(balance__gt=0)
        .select_related('rental')
        .annotate(
            payment_sum=Coalesce(
                Sum(payment_principal_expression('payments__')),
                Value(Decimal('0')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        .exclude(paid_amount=F('payment_sum'))
    )
    inconsistent_count = inconsistent_qs.count()
    inconsistent_balances = []
    for rec in inconsistent_qs[:100]:
        inconsistent_balances.append({
            'id': rec.pk,
            'rental_number': rec.rental.number if rec.rental_id else None,
            'due_date': rec.due_date,
            'amount': rec.amount,
            'paid_amount_stored': rec.paid_amount,
            'payment_sum': rec.payment_sum,
            'diff': rec.paid_amount - rec.payment_sum,
        })

    # Divergence 3: a legacy Payment has one direct movement; a real Receipt has
    # one grouped movement reached through ReceiptAllocation. In the grouped
    # case every allocation must match its Payment cash, while the receipt total
    # must match both the allocation sum and its single movement.
    valid_inflow_receipts = (
        Receipt.objects.filter(
            kind=Receipt.Kind.INFLOW,
            financial_movement__source=FinancialMovement.Source.PAYMENT,
            financial_movement__direction=FinancialMovement.Direction.INFLOW,
        )
        .annotate(allocated_cash=Sum('allocations__cash_amount'))
        .filter(
            amount=F('allocated_cash'),
            financial_movement__amount=F('amount'),
        )
        .values('pk')
    )
    active_payments = Payment.objects.filter(is_reversal=False)
    valid_direct_payments = (
        active_payments
        .annotate(
            movement_count=Count('financial_movements'),
            expected_movement_count=Count(
                'financial_movements',
                filter=Q(
                    financial_movements__source=FinancialMovement.Source.PAYMENT,
                    financial_movements__direction=FinancialMovement.Direction.INFLOW,
                    financial_movements__amount=F('amount'),
                    financial_movements__receivable_id=F('receivable_id'),
                ),
            ),
        )
        .filter(movement_count=1, expected_movement_count=1)
        .values('pk')
    )
    valid_grouped_payments = (
        active_payments.filter(
            financial_movements__isnull=True,
            receipt_allocation__cash_amount=F('amount'),
            receipt_allocation__interest_amount=F('interest_amount'),
            receipt_allocation__discount_amount=F('discount_amount'),
            receipt_allocation__receivable_id=F('receivable_id'),
            receipt_allocation__receipt_id__in=valid_inflow_receipts,
        )
        .values('pk')
    )
    payments_with_movement_issue = (
        active_payments
        .exclude(pk__in=valid_direct_payments)
        .exclude(pk__in=valid_grouped_payments)
    )
    payments_with_movement_issue_count = payments_with_movement_issue.count()
    payments_with_movement_issue_ids = list(
        payments_with_movement_issue.values_list('pk', flat=True)[:200]
    )
    valid_reversal_receipts = (
        Receipt.objects.filter(
            kind=Receipt.Kind.REVERSAL,
            financial_movement__source=FinancialMovement.Source.REVERSAL,
            financial_movement__direction=FinancialMovement.Direction.OUTFLOW,
        )
        .annotate(allocated_cash=Sum('allocations__cash_amount'))
        .filter(
            amount=F('allocated_cash'),
            financial_movement__amount=F('amount'),
        )
        .values('pk')
    )
    reversal_payments = Payment.objects.filter(is_reversal=True)
    valid_direct_reversals = (
        reversal_payments
        .annotate(
            movement_count=Count('financial_movements'),
            expected_movement_count=Count(
                'financial_movements',
                filter=Q(
                    financial_movements__source=FinancialMovement.Source.REVERSAL,
                    financial_movements__direction=FinancialMovement.Direction.OUTFLOW,
                    financial_movements__amount=-F('amount'),
                    financial_movements__receivable_id=F('receivable_id'),
                ),
            ),
        )
        .filter(movement_count=1, expected_movement_count=1)
        .values('pk')
    )
    valid_grouped_reversals = (
        reversal_payments.filter(
            financial_movements__isnull=True,
            receipt_allocation__cash_amount=-F('amount'),
            receipt_allocation__interest_amount=-F('interest_amount'),
            receipt_allocation__discount_amount=-F('discount_amount'),
            receipt_allocation__receivable_id=F('receivable_id'),
            receipt_allocation__receipt_id__in=valid_reversal_receipts,
        )
        .values('pk')
    )
    reversals_with_movement_issue = (
        reversal_payments
        .exclude(pk__in=valid_direct_reversals)
        .exclude(pk__in=valid_grouped_reversals)
    )
    reversals_with_movement_issue_count = reversals_with_movement_issue.count()
    reversals_with_movement_issue_ids = list(
        reversals_with_movement_issue.values_list('pk', flat=True)[:200]
    )

    return {
        'total_receivable_amount': total_receivable_amount,
        'total_open_balance': total_open_balance,
        'total_payments': total_payments,
        'total_reversals': abs(total_reversals),
        'net_payments': total_payments + total_reversals,
        'total_inflow': total_inflow,
        'total_outflow': total_outflow,
        'net_movements': total_inflow - total_outflow,
        'paid_no_payments_count': paid_no_payments_count,
        'paid_no_payments_sum': paid_no_payments_sum,
        'inconsistent_balances': inconsistent_balances,
        'inconsistent_count': inconsistent_count,
        'payments_without_movement_count': payments_with_movement_issue_count,
        'payments_without_movement_ids': payments_with_movement_issue_ids,
        'payments_with_movement_issue_count': payments_with_movement_issue_count,
        'payments_with_movement_issue_ids': payments_with_movement_issue_ids,
        'reversals_without_movement_count': reversals_with_movement_issue_count,
        'reversals_without_movement_ids': reversals_with_movement_issue_ids,
        'reversals_with_movement_issue_count': reversals_with_movement_issue_count,
        'reversals_with_movement_issue_ids': reversals_with_movement_issue_ids,
    }


def financial_kpis(today=None):
    """KPIs for the billing dashboard (R5.02)."""
    from datetime import date as date_cls, timedelta
    from django.db.models import Sum
    from .models import FinancialMovement, Payment, Receivable

    today = today or timezone.localdate()
    week_end = today + timedelta(days=7)
    month_start = today.replace(day=1)

    open_qs = Receivable.objects.filter(balance__gt=0)
    overdue_qs = open_qs.filter(due_date__lt=today)
    due_today_qs = open_qs.filter(due_date=today)
    due_week_qs = open_qs.filter(due_date__gt=today, due_date__lte=week_end)

    open_balance = open_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')
    overdue_balance = overdue_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')
    due_today_balance = due_today_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')
    due_week_balance = due_week_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')

    received_today = (
        Payment.objects.filter(
            payment_date=today,
            is_reversal=False,
            reversed_by__isnull=True,
        )
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    received_month = (
        Payment.objects.filter(
            payment_date__gte=month_start,
            is_reversal=False,
            reversed_by__isnull=True,
        )
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )

    recent_movements = (
        FinancialMovement.objects.select_related('account', 'customer')
        .order_by('-date', '-created_at')[:10]
    )

    return {
        'open_balance': open_balance,
        'open_count': open_qs.count(),
        'overdue_balance': overdue_balance,
        'overdue_count': overdue_qs.count(),
        'due_today_balance': due_today_balance,
        'due_today_count': due_today_qs.count(),
        'due_week_balance': due_week_balance,
        'due_week_count': due_week_qs.count(),
        'received_today': received_today,
        'received_month': received_month,
        'recent_movements': recent_movements,
        'today': today,
    }
