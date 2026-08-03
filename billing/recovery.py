"""Pure value objects used to plan a financial database recovery.

This module deliberately has no Django or ORM imports.  It turns source rows
into stable, hashable identities and validates the audited recovery totals
before a separate command is allowed to perform database writes.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Iterable, Mapping


CENT = Decimal('0.01')

RETROACTIVE_PAYMENT_GROUP = 'retroactive_payment'
ENTRY_PAYMENT_GROUP = 'entry_payment'

LEGACY_DEAD_CUTOFF_GROUP = 'legacy_dead_cutoff'
ACCESS_SETTLED_WITHOUT_VALUE_GROUP = 'access_settled_without_value'
LEGACY_OVERPAYMENT_GROUP = 'legacy_overpayment'


def _canonical_text(value: str, *, casefold: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError('text values must be strings')
    normalized = ' '.join(value.split())
    return normalized.casefold() if casefold else normalized


def _positive_integer(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} must be a positive integer')
    return value


def _non_negative_integer(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{field} must be a non-negative integer')
    return value


def _date(value: date, *, field: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f'{field} must be a date')
    return value


def _money(value: Decimal | str | int, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f'{field} must be a finite monetary amount') from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f'{field} must be a finite non-negative monetary amount')
    return amount


@dataclass(frozen=True, order=True)
class LegacyReceivableKey:
    """Natural identity of a receivable imported from the Access database."""

    legacy_source: str
    legacy_id: int

    def __post_init__(self):
        source = _canonical_text(self.legacy_source, casefold=True)
        if not source:
            raise ValueError('legacy_source must not be blank')
        object.__setattr__(self, 'legacy_source', source)
        object.__setattr__(
            self,
            'legacy_id',
            _positive_integer(self.legacy_id, field='legacy_id'),
        )


@dataclass(frozen=True, order=True)
class SourcePaymentIdentity:
    """Immutable identity of one Payment row in a named source database."""

    source_database: str
    source_payment_id: int

    def __post_init__(self):
        source_database = _canonical_text(self.source_database, casefold=True)
        if not source_database:
            raise ValueError('source_database must not be blank')
        object.__setattr__(self, 'source_database', source_database)
        object.__setattr__(
            self,
            'source_payment_id',
            _positive_integer(self.source_payment_id, field='source_payment_id'),
        )


def _payment_method(value: str) -> str:
    method = _canonical_text(value, casefold=True)
    if not method:
        raise ValueError('method must not be blank')
    return method


@dataclass(frozen=True, order=True)
class RetroactivePaymentFingerprint:
    """Auditable evidence for a payment recovered from a legacy title.

    ``identity`` is the idempotency key.  Association and financial fields are
    comparable evidence, while operator-editable notes are metadata only.
    """

    identity: SourcePaymentIdentity
    receivable: LegacyReceivableKey
    payment_date: date
    amount: Decimal
    method: str
    notes: str = field(default='', compare=False)

    def __post_init__(self):
        if not isinstance(self.identity, SourcePaymentIdentity):
            raise TypeError('identity must be a SourcePaymentIdentity')
        if not isinstance(self.receivable, LegacyReceivableKey):
            raise TypeError('receivable must be a LegacyReceivableKey')
        object.__setattr__(
            self,
            'payment_date',
            _date(self.payment_date, field='payment_date'),
        )
        object.__setattr__(self, 'amount', _money(self.amount, field='amount'))
        object.__setattr__(self, 'method', _payment_method(self.method))
        object.__setattr__(self, 'notes', _canonical_text(self.notes))


@dataclass(frozen=True, order=True)
class LocalReceivableKey:
    """Exact natural identity of one locally generated rental installment."""

    rental_number: int
    due_date: date
    amount: Decimal
    ordinal: int

    def __post_init__(self):
        object.__setattr__(
            self,
            'rental_number',
            _positive_integer(self.rental_number, field='rental_number'),
        )
        object.__setattr__(self, 'due_date', _date(self.due_date, field='due_date'))
        object.__setattr__(self, 'amount', _money(self.amount, field='amount'))
        object.__setattr__(
            self,
            'ordinal',
            _positive_integer(self.ordinal, field='ordinal'),
        )


@dataclass(frozen=True, order=True)
class EntryPaymentFingerprint:
    """Auditable evidence for an entry payment from the source database."""

    identity: SourcePaymentIdentity
    receivable: LocalReceivableKey
    payment_date: date
    amount: Decimal
    method: str
    notes: str = field(default='', compare=False)
    user_email: str = field(default='', compare=False)

    def __post_init__(self):
        if not isinstance(self.identity, SourcePaymentIdentity):
            raise TypeError('identity must be a SourcePaymentIdentity')
        if not isinstance(self.receivable, LocalReceivableKey):
            raise TypeError('receivable must be a LocalReceivableKey')
        object.__setattr__(
            self,
            'payment_date',
            _date(self.payment_date, field='payment_date'),
        )
        object.__setattr__(self, 'amount', _money(self.amount, field='amount'))
        object.__setattr__(self, 'method', _payment_method(self.method))
        object.__setattr__(self, 'notes', _canonical_text(self.notes))
        object.__setattr__(
            self,
            'user_email',
            _canonical_text(self.user_email, casefold=True),
        )

    @property
    def rental_number(self) -> int:
        return self.receivable.rental_number


@dataclass(frozen=True, order=True)
class ScheduleInstallment:
    """One canonical member of an ordered installment multiset."""

    due_date: date
    amount: Decimal
    ordinal: int

    def __post_init__(self):
        object.__setattr__(self, 'due_date', _date(self.due_date, field='due_date'))
        object.__setattr__(self, 'amount', _money(self.amount, field='amount'))
        object.__setattr__(
            self,
            'ordinal',
            _positive_integer(self.ordinal, field='ordinal'),
        )


@dataclass(frozen=True, order=True)
class LocalScheduleKey:
    """Natural key for one rental's complete local receivable schedule."""

    rental_number: int
    installments: tuple[ScheduleInstallment, ...]

    def __post_init__(self):
        object.__setattr__(
            self,
            'rental_number',
            _positive_integer(self.rental_number, field='rental_number'),
        )
        installments = tuple(self.installments)
        if not all(isinstance(item, ScheduleInstallment) for item in installments):
            raise TypeError('installments must contain ScheduleInstallment values')
        if installments != tuple(sorted(installments)):
            raise ValueError('installments must be in canonical order')
        expected_ordinals = tuple(range(1, len(installments) + 1))
        if tuple(item.ordinal for item in installments) != expected_ordinals:
            raise ValueError('installment ordinals must be contiguous from one')
        object.__setattr__(self, 'installments', installments)


def build_local_schedule_key(
    rental_number: int,
    installments: Iterable[tuple[date, Decimal | str | int]],
) -> LocalScheduleKey:
    """Build a deterministic key without mutating or trusting source row order."""

    canonical_pairs = sorted(
        (
            _date(due_date, field='due_date'),
            _money(amount, field='amount'),
        )
        for due_date, amount in installments
    )
    canonical_installments = tuple(
        ScheduleInstallment(due_date, amount, ordinal)
        for ordinal, (due_date, amount) in enumerate(canonical_pairs, start=1)
    )
    return LocalScheduleKey(rental_number, canonical_installments)


class ScheduleMatchStatus(StrEnum):
    MISSING = 'missing'
    EQUIVALENT = 'equivalent'
    AMBIGUOUS = 'ambiguous'


@dataclass(frozen=True)
class ScheduleMatch:
    status: ScheduleMatchStatus
    candidate_indexes: tuple[int, ...]

    @property
    def is_safe(self) -> bool:
        return self.status == ScheduleMatchStatus.EQUIVALENT


def detect_equivalent_schedule(
    expected: LocalScheduleKey,
    candidates: Iterable[LocalScheduleKey],
) -> ScheduleMatch:
    """Find one exact schedule match, rejecting zero or multiple candidates."""

    if not isinstance(expected, LocalScheduleKey):
        raise TypeError('expected must be a LocalScheduleKey')
    matching_indexes = tuple(
        index
        for index, candidate in enumerate(candidates)
        if candidate == expected
    )
    if not matching_indexes:
        status = ScheduleMatchStatus.MISSING
    elif len(matching_indexes) == 1:
        status = ScheduleMatchStatus.EQUIVALENT
    else:
        status = ScheduleMatchStatus.AMBIGUOUS
    return ScheduleMatch(status=status, candidate_indexes=matching_indexes)


@dataclass(frozen=True)
class GroupedCounts:
    """Hashable, deterministic representation of named count groups."""

    items: tuple[tuple[str, int], ...]

    def __post_init__(self):
        items = tuple(self.items)
        labels = [label for label, _count in items]
        if any(not isinstance(label, str) or not label for label in labels):
            raise ValueError('group labels must be non-empty strings')
        if len(labels) != len(set(labels)):
            raise ValueError('group labels must be unique')
        for label, count in items:
            _non_negative_integer(count, field=f'group {label!r}')
        if items != tuple(sorted(items)):
            raise ValueError('groups must be in canonical order')
        object.__setattr__(self, 'items', items)

    @classmethod
    def from_mapping(cls, groups: Mapping[str, int]) -> 'GroupedCounts':
        return cls(tuple(sorted(groups.items())))

    @property
    def total(self) -> int:
        return sum(count for _label, count in self.items)


@dataclass(frozen=True)
class RecoveryPlanSummary:
    payment_count: int
    payment_total: Decimal
    payment_groups: GroupedCounts
    write_off_count: int
    write_off_groups: GroupedCounts
    payment_manifest: tuple[
        RetroactivePaymentFingerprint | EntryPaymentFingerprint, ...
    ] | None = None
    write_off_manifest: tuple[LegacyReceivableKey, ...] | None = None

    def __post_init__(self):
        object.__setattr__(
            self,
            'payment_count',
            _non_negative_integer(self.payment_count, field='payment_count'),
        )
        object.__setattr__(
            self,
            'payment_total',
            _money(self.payment_total, field='payment_total'),
        )
        object.__setattr__(
            self,
            'write_off_count',
            _non_negative_integer(self.write_off_count, field='write_off_count'),
        )
        if not isinstance(self.payment_groups, GroupedCounts):
            raise TypeError('payment_groups must be GroupedCounts')
        if not isinstance(self.write_off_groups, GroupedCounts):
            raise TypeError('write_off_groups must be GroupedCounts')
        if self.payment_manifest is not None:
            payment_manifest = tuple(self.payment_manifest)
            if not all(
                isinstance(
                    payment,
                    (RetroactivePaymentFingerprint, EntryPaymentFingerprint),
                )
                for payment in payment_manifest
            ):
                raise TypeError('payment_manifest contains an invalid fingerprint')
            object.__setattr__(
                self,
                'payment_manifest',
                tuple(sorted(payment_manifest, key=_payment_manifest_sort_key)),
            )
        if self.write_off_manifest is not None:
            write_off_manifest = tuple(self.write_off_manifest)
            if not all(
                isinstance(receivable, LegacyReceivableKey)
                for receivable in write_off_manifest
            ):
                raise TypeError('write_off_manifest contains an invalid identity')
            object.__setattr__(
                self,
                'write_off_manifest',
                tuple(sorted(write_off_manifest)),
            )


def _payment_manifest_sort_key(
    payment: RetroactivePaymentFingerprint | EntryPaymentFingerprint,
) -> tuple:
    if isinstance(payment, RetroactivePaymentFingerprint):
        association = (
            RETROACTIVE_PAYMENT_GROUP,
            payment.receivable.legacy_source,
            str(payment.receivable.legacy_id),
            '',
            '',
            '',
        )
    else:
        association = (
            ENTRY_PAYMENT_GROUP,
            '',
            str(payment.receivable.rental_number),
            payment.receivable.due_date.isoformat(),
            format(payment.receivable.amount, '.2f'),
            str(payment.receivable.ordinal),
        )
    return (
        payment.identity.source_database,
        payment.identity.source_payment_id,
        association,
        payment.payment_date,
        payment.amount,
        payment.method,
    )


EXPECTED_RECOVERY_PLAN = RecoveryPlanSummary(
    payment_count=127,
    payment_total=Decimal('12655.00'),
    payment_groups=GroupedCounts.from_mapping({
        RETROACTIVE_PAYMENT_GROUP: 107,
        ENTRY_PAYMENT_GROUP: 20,
    }),
    write_off_count=25113,
    write_off_groups=GroupedCounts.from_mapping({
        LEGACY_DEAD_CUTOFF_GROUP: 25043,
        ACCESS_SETTLED_WITHOUT_VALUE_GROUP: 69,
        LEGACY_OVERPAYMENT_GROUP: 1,
    }),
)


@dataclass(frozen=True)
class RecoveryPlanValidation:
    errors: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_recovery_plan(
    actual: RecoveryPlanSummary,
    expected: RecoveryPlanSummary = EXPECTED_RECOVERY_PLAN,
    *,
    require_manifests: bool = True,
) -> RecoveryPlanValidation:
    """Validate aggregate invariants and, by default, exact GO manifests.

    ``require_manifests=False`` is suitable only for preliminary aggregate
    auditing.  A GO decision must compare the complete payment evidence and the
    exact set of receivables to write off.
    """

    if not isinstance(actual, RecoveryPlanSummary):
        raise TypeError('actual must be a RecoveryPlanSummary')
    if not isinstance(expected, RecoveryPlanSummary):
        raise TypeError('expected must be a RecoveryPlanSummary')
    if not isinstance(require_manifests, bool):
        raise TypeError('require_manifests must be a bool')

    errors: list[str] = []
    if actual.payment_groups.total != actual.payment_count:
        errors.append(
            'payment_count does not equal the sum of payment_groups '
            f'({actual.payment_count} != {actual.payment_groups.total})'
        )
    if actual.write_off_groups.total != actual.write_off_count:
        errors.append(
            'write_off_count does not equal the sum of write_off_groups '
            f'({actual.write_off_count} != {actual.write_off_groups.total})'
        )

    comparisons = (
        ('payment_count', actual.payment_count, expected.payment_count),
        ('payment_total', actual.payment_total, expected.payment_total),
        ('payment_groups', actual.payment_groups, expected.payment_groups),
        ('write_off_count', actual.write_off_count, expected.write_off_count),
        ('write_off_groups', actual.write_off_groups, expected.write_off_groups),
    )
    for field, actual_value, expected_value in comparisons:
        if actual_value != expected_value:
            errors.append(
                f'{field} differs from the audited expectation '
                f'({actual_value!r} != {expected_value!r})'
            )

    _validate_manifest_invariants(actual, 'actual', errors)
    _validate_manifest_invariants(expected, 'expected', errors)

    manifest_comparisons = (
        ('payment_manifest', actual.payment_manifest, expected.payment_manifest),
        ('write_off_manifest', actual.write_off_manifest, expected.write_off_manifest),
    )
    for field, actual_value, expected_value in manifest_comparisons:
        if require_manifests and actual_value is None:
            errors.append(f'actual {field} is required for GO validation')
        if require_manifests and expected_value is None:
            errors.append(f'expected {field} is required for GO validation')
        if actual_value is not None and expected_value is not None:
            if actual_value != expected_value:
                errors.append(f'{field} differs from the expected identity set')

    return RecoveryPlanValidation(errors=tuple(errors))


def _validate_manifest_invariants(
    summary: RecoveryPlanSummary,
    label: str,
    errors: list[str],
) -> None:
    payment_manifest = summary.payment_manifest
    if payment_manifest is not None:
        if len(payment_manifest) != summary.payment_count:
            errors.append(
                f'{label} payment_manifest count differs from payment_count '
                f'({len(payment_manifest)} != {summary.payment_count})'
            )
        manifest_total = sum(
            (payment.amount for payment in payment_manifest),
            Decimal('0.00'),
        )
        if manifest_total != summary.payment_total:
            errors.append(
                f'{label} payment_manifest total differs from payment_total '
                f'({manifest_total} != {summary.payment_total})'
            )
        manifest_groups = GroupedCounts.from_mapping({
            RETROACTIVE_PAYMENT_GROUP: sum(
                isinstance(payment, RetroactivePaymentFingerprint)
                for payment in payment_manifest
            ),
            ENTRY_PAYMENT_GROUP: sum(
                isinstance(payment, EntryPaymentFingerprint)
                for payment in payment_manifest
            ),
        })
        if manifest_groups != summary.payment_groups:
            errors.append(f'{label} payment_manifest groups differ from payment_groups')

        identities = [payment.identity for payment in payment_manifest]
        if len(identities) != len(set(identities)):
            errors.append(f'{label} payment_manifest contains duplicate source identities')

    write_off_manifest = summary.write_off_manifest
    if write_off_manifest is not None:
        if len(write_off_manifest) != summary.write_off_count:
            errors.append(
                f'{label} write_off_manifest count differs from write_off_count '
                f'({len(write_off_manifest)} != {summary.write_off_count})'
            )
        if len(write_off_manifest) != len(set(write_off_manifest)):
            errors.append(f'{label} write_off_manifest contains duplicate identities')
