"""Single source of truth for late interest and installment generation.

Centralizing interest here addresses the incorrect-interest risk in PRD section 12.
Interest uses the company's monthly rate divided by 30 and applied for each overdue
day, with the legacy daily rate as a fallback when the monthly rate is zero.
"""

from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction
from django.db.models import (
    DecimalField,
    F,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from company.models import Company
from core.models import AuditLog

from .models import CashAccount, FinancialMovement, Payment, Receivable


PENALTY_NOTE_PREFIX = 'penalty:'


def _percentage_amount(base_amount, rate):
    """Apply a Company percentage without imposing an artificial 100% ceiling."""
    base = Decimal(str(base_amount or 0))
    percentage = Decimal(str(rate or 0))
    return (base * percentage / Decimal('100')).quantize(Decimal('0.01'))


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
    return interest.quantize(Decimal('0.01'))


def total_with_interest(receivable, on_date=None, company=None):
    """Open balance plus accrued late interest."""
    return (
        receivable.balance + compute_interest(receivable, on_date, company=company)
    ).quantize(Decimal('0.01'))


def interest_breakdown(receivable, on_date=None, company=None):
    """Return days, interest, and total using one company config lookup."""
    interest = compute_interest(receivable, on_date, company=company)
    return {
        'interest': interest,
        'total_with_interest': (receivable.balance + interest).quantize(Decimal('0.01')),
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

    if last_due_date:
        due_dates = [_add_months(last_due_date, -(installments - 1 - i)) for i in range(installments)]
    else:
        first_due = first_due_date or rental.return_date
        due_dates = [_add_months(first_due, i) for i in range(installments)]

    base = (total / installments).quantize(Decimal('0.01'))
    remainder = total - base * installments

    created = []
    for index, due in enumerate(due_dates):
        amount = base + (remainder if index == 0 else Decimal('0'))
        created.append(
            Receivable.objects.create(rental=rental, due_date=due, amount=amount)
        )
    return created


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
        protected = [
            receivable for receivable in existing
            if (
                receivable.paid_amount != 0
                or receivable.written_off_at is not None
                or len(receivable.payments.all()) > 0
            )
        ]
        replaceable_ids = [
            receivable.pk for receivable in existing if receivable not in protected
        ]
        adjusted_partial_ids = []
        for receivable in protected:
            if (
                receivable.written_off_at is None
                and receivable.paid_amount > 0
                and receivable.balance > 0
            ):
                # Legacy entries were recorded as a partial payment against one
                # title for the full contract. Convert its paid portion into a
                # closed historical title before scheduling the remaining balance.
                receivable.amount = receivable.paid_amount
                receivable.save(update_fields=['amount', 'balance', 'updated_at'])
                adjusted_partial_ids.append(receivable.pk)
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
        created = generate_for_rental(
            locked_rental,
            installments=installments,
            first_due_date=effective_due_date,
            total_amount=remaining,
        )
        AuditLog.record(
            user=user,
            action='reprocess_future_installments',
            obj=locked_rental,
            reason=reason,
            metadata={
                'previous_schedule': previous_schedule,
                'protected_receivable_ids': [item.pk for item in protected],
                'adjusted_partial_receivable_ids': adjusted_partial_ids,
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
    total = rental.final_value
    entry_amount = Decimal(str(down_payment_amount or 0))
    installments = int(installments or 0)

    if entry_amount < 0:
        raise PaymentPlanError(
            'O valor da entrada não pode ser negativo.', field='down_payment_amount',
        )
    if entry_amount > total:
        raise PaymentPlanError(
            'O valor da entrada não pode superar o total da locação.',
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
        if not first_due_date:
            raise PaymentPlanError(
                'Informe a data do próximo pagamento.', field='first_due_date',
            )
        if first_due_date <= down_payment_date:
            raise PaymentPlanError(
                'O próximo vencimento deve ser posterior à data da entrada.',
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
        if entry_amount > 0:
            account = (
                CashAccount.objects.select_for_update()
                .filter(active=True)
                .order_by('id')
                .first()
            )
            if account is None:
                raise PaymentPlanError(
                    'Ative uma conta de caixa antes de registrar a entrada.'
                )
            entry_receivable = Receivable.objects.create(
                rental=locked_rental,
                due_date=down_payment_date,
                amount=entry_amount,
            )
            entry_payment = register_payment(
                entry_receivable,
                amount=entry_amount,
                payment_date=down_payment_date,
                method=down_payment_method,
                notes='Entrada na criação da locação',
                user=user,
            )
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


def register_payment(receivable, amount, payment_date, method='cash',
                     interest_amount=None, discount_amount=None, notes='', user=None):
    """Create Payment, recalculate receivable balance, create FinancialMovement inflow (R5.06)."""
    amount = Decimal(str(amount))
    interest_amount = Decimal(str(interest_amount or 0))
    discount_amount = Decimal(str(discount_amount or 0))
    if amount <= 0:
        raise ValueError('O valor recebido deve ser maior que zero.')
    if method not in Payment.Method.values:
        raise ValueError('A forma de recebimento informada é inválida.')
    if payment_date > timezone.localdate():
        raise ValueError('A data do recebimento não pode estar no futuro.')

    with transaction.atomic():
        locked_receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental__customer')
            .get(pk=receivable.pk)
        )
        if locked_receivable.written_off_at is not None:
            raise ValueError('Não é possível receber um título baixado.')
        account = (
            CashAccount.objects.select_for_update()
            .filter(active=True)
            .order_by('id')
            .first()
        )
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
    return payment


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

    Returns the list of payments created, in allocation order.
    """
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError('O valor recebido deve ser maior que zero.')

    payments = []
    with transaction.atomic():
        target = (
            Receivable.objects.select_for_update()
            .select_related('rental')
            .get(pk=receivable.pk)
        )
        # Oldest first: a surplus should clear the debt that has been open the
        # longest, not the one the cashier happened to click.
        others = list(
            Receivable.objects.select_for_update()
            .filter(
                rental_id=target.rental_id,
                balance__gt=0,
                written_off_at__isnull=True,
            )
            .exclude(pk=target.pk)
            .order_by('due_date', 'pk')
        )

        # Principal first, across the whole schedule. Interest is settled last so
        # that a surplus pays down real debt instead of overshooting the clicked
        # title — only an overpayment beyond ``amount`` drives ``balance`` negative.
        target_interest = total_with_interest(target) - target.balance
        principal = [(target, target.balance)]
        principal += [(rec, rec.balance) for rec in others]

        capacity = sum((limit for _, limit in principal), Decimal('0')) + target_interest
        if amount > capacity:
            raise PaymentPlanError(
                'O valor informado é maior que o saldo total desta locação '
                f'(R$ {capacity:.2f}).',
                field='value',
            )

        remaining = amount
        for rec, limit in principal:
            if remaining <= 0:
                break
            if limit <= 0:
                continue
            pay_amount = min(remaining, limit)
            payments.append(register_payment(
                receivable=rec,
                amount=pay_amount,
                payment_date=payment_date,
                method=method,
                notes=notes,
                user=user,
            ))
            remaining -= pay_amount

        # Whatever is left once every installment is settled can only be the late
        # interest the cashier chose to charge on the title they opened.
        if remaining > 0 and target_interest > 0:
            payments.append(register_payment(
                receivable=target,
                amount=min(remaining, target_interest),
                payment_date=payment_date,
                method=method,
                interest_amount=min(remaining, target_interest),
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
        locked_payment = (
            Payment.objects.select_for_update()
            .select_related('receivable', 'customer', 'rental')
            .get(pk=payment.pk)
        )
        if locked_payment.is_reversal or locked_payment.reversed_by_id is not None:
            raise ValueError('Este recebimento já foi estornado.')

        receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental__customer')
            .get(pk=locked_payment.receivable_id)
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

        account = CashAccount.objects.select_for_update().filter(active=True).order_by('id').first()
        if account:
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
    return fee.quantize(Decimal('0.01'))


def compute_monthly_interest(receivable, on_date=None, company=None):
    """Compatibility alias for the canonical monthly interest calculation."""
    return compute_interest(receivable, on_date=on_date, company=company)


def compute_damage_penalty(item_value, company=None):
    """Damage penalty: Company.damage_penalty_rate % of item value (R6.09)."""
    company = company or Company.load()
    return _percentage_amount(item_value, company.damage_penalty_rate)


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
    amount = Decimal(str(amount or 0)).quantize(Decimal('0.01'))
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

    # Divergence 2: open receivables where paid_amount != sum(payments.amount)
    inconsistent_qs = (
        Receivable.objects.filter(balance__gt=0)
        .select_related('rental')
        .annotate(
            payment_sum=Coalesce(
                Sum('payments__amount'),
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

    # Divergence 3: payments without a corresponding FinancialMovement
    payments_without_movement = (
        Payment.objects.filter(is_reversal=False)
        .exclude(financial_movements__source=FinancialMovement.Source.PAYMENT)
        .distinct()
    )
    payments_without_movement_count = payments_without_movement.count()
    payments_without_movement_ids = list(
        payments_without_movement.values_list('pk', flat=True)[:200]
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
        'payments_without_movement_count': payments_without_movement_count,
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
        Payment.objects.filter(payment_date=today, is_reversal=False)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    received_month = (
        Payment.objects.filter(payment_date__gte=month_start, is_reversal=False)
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
