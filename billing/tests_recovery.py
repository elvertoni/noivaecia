from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from billing.recovery import (
    ACCESS_SETTLED_WITHOUT_VALUE_GROUP,
    ENTRY_PAYMENT_GROUP,
    EXPECTED_RECOVERY_PLAN,
    LEGACY_DEAD_CUTOFF_GROUP,
    LEGACY_OVERPAYMENT_GROUP,
    RETROACTIVE_PAYMENT_GROUP,
    EntryPaymentFingerprint,
    GroupedCounts,
    LocalReceivableKey,
    LegacyReceivableKey,
    RecoveryPlanSummary,
    RetroactivePaymentFingerprint,
    ScheduleMatchStatus,
    SourcePaymentIdentity,
    build_local_schedule_key,
    detect_equivalent_schedule,
    validate_recovery_plan,
)


def _local_receivable(
    rental_number=500,
    *,
    due_date=date(2026, 8, 1),
    amount=Decimal('100.00'),
    ordinal=1,
):
    return LocalReceivableKey(rental_number, due_date, amount, ordinal)


class RecoveryFingerprintTests(SimpleTestCase):
    def test_legacy_receivable_key_is_canonical_and_hashable(self):
        first = LegacyReceivableKey(' Pagar ', 42)
        second = LegacyReceivableKey('pagar', 42)

        self.assertEqual(first, second)
        self.assertEqual({first, second}, {LegacyReceivableKey('PAGAR', 42)})

    def test_retroactive_payment_fingerprint_is_idempotent(self):
        key = LegacyReceivableKey('pagar', 42)
        first = RetroactivePaymentFingerprint(
            SourcePaymentIdentity('recovery_backup', 10),
            key,
            date(2026, 7, 1),
            Decimal('100'),
            'cash',
            'Recebido   no legado',
        )
        second = RetroactivePaymentFingerprint(
            SourcePaymentIdentity('RECOVERY_BACKUP', 10),
            key,
            date(2026, 7, 1),
            Decimal('100.000'),
            'CASH',
            'Recebido no legado',
        )

        self.assertEqual(first, second)
        self.assertEqual(len({first, second}), 1)

    def test_retroactive_fingerprint_keeps_receivable_identity(self):
        first = RetroactivePaymentFingerprint(
            SourcePaymentIdentity('recovery_backup', 10),
            LegacyReceivableKey('pagar', 41),
            date(2026, 7, 1),
            Decimal('100.00'),
            'cash',
        )
        second = RetroactivePaymentFingerprint(
            SourcePaymentIdentity('recovery_backup', 10),
            LegacyReceivableKey('pagar', 42),
            date(2026, 7, 1),
            Decimal('100.00'),
            'cash',
        )

        self.assertNotEqual(first, second)

    def test_entry_fingerprint_normalizes_method_email_and_money(self):
        first = EntryPaymentFingerprint(
            identity=SourcePaymentIdentity('recovery_backup', 20),
            receivable=_local_receivable(
                56489,
                amount=Decimal('250.00'),
            ),
            payment_date=date(2026, 8, 1),
            amount=Decimal('250'),
            method=' PIX ',
            notes='Entrada   na reserva',
            user_email=' ADMIN@EXAMPLE.COM ',
        )
        second = EntryPaymentFingerprint(
            identity=SourcePaymentIdentity('RECOVERY_BACKUP', 20),
            receivable=_local_receivable(
                56489,
                amount=Decimal('250.000'),
            ),
            payment_date=date(2026, 8, 1),
            amount=Decimal('250.000'),
            method='pix',
            notes='Entrada na reserva',
            user_email='admin@example.com',
        )

        self.assertEqual(first, second)
        self.assertEqual(len({first, second}), 1)

    def test_notes_and_operator_email_are_not_payment_identity(self):
        identity = SourcePaymentIdentity('recovery_backup', 20)
        original = EntryPaymentFingerprint(
            identity=identity,
            receivable=_local_receivable(
                56489,
                amount=Decimal('250.00'),
            ),
            payment_date=date(2026, 8, 1),
            amount=Decimal('250.00'),
            method='pix',
            notes='Texto original',
            user_email='primeiro@example.com',
        )
        edited = EntryPaymentFingerprint(
            identity=identity,
            receivable=_local_receivable(
                56489,
                amount=Decimal('250.00'),
            ),
            payment_date=date(2026, 8, 1),
            amount=Decimal('250.00'),
            method='pix',
            notes='Texto editado',
            user_email='outro@example.com',
        )

        self.assertEqual(original, edited)
        self.assertEqual(original.identity, edited.identity)

    def test_identical_legitimate_payments_with_different_source_ids_are_distinct(self):
        shared = {
            'receivable': _local_receivable(56489),
            'payment_date': date(2026, 8, 1),
            'amount': Decimal('100.00'),
            'method': 'cash',
        }
        first = EntryPaymentFingerprint(
            identity=SourcePaymentIdentity('recovery_backup', 20),
            **shared,
        )
        second = EntryPaymentFingerprint(
            identity=SourcePaymentIdentity('recovery_backup', 21),
            **shared,
        )

        self.assertNotEqual(first, second)
        self.assertEqual(len({first, second}), 2)

    def test_invalid_natural_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            LegacyReceivableKey('', 1)
        with self.assertRaises(ValueError):
            LegacyReceivableKey('pagar', 0)
        with self.assertRaises(ValueError):
            LocalReceivableKey(
                rental_number=0,
                due_date=date(2026, 8, 1),
                amount=Decimal('1.00'),
                ordinal=1,
            )


class LocalScheduleMatchingTests(SimpleTestCase):
    def test_schedule_key_is_deterministic_and_preserves_duplicates(self):
        source = [
            (date(2026, 10, 1), Decimal('50')),
            (date(2026, 9, 1), Decimal('100.00')),
            (date(2026, 9, 1), Decimal('100')),
        ]

        first = build_local_schedule_key(100, source)
        second = build_local_schedule_key(100, reversed(source))

        self.assertEqual(first, second)
        self.assertEqual([item.ordinal for item in first.installments], [1, 2, 3])
        self.assertEqual(
            [(item.due_date, item.amount) for item in first.installments],
            [
                (date(2026, 9, 1), Decimal('100.00')),
                (date(2026, 9, 1), Decimal('100.00')),
                (date(2026, 10, 1), Decimal('50.00')),
            ],
        )
        self.assertEqual(source[0][0], date(2026, 10, 1))

    def test_one_equivalent_schedule_is_safe(self):
        expected = build_local_schedule_key(
            100,
            [(date(2026, 9, 1), Decimal('100.00'))],
        )
        other_rental = build_local_schedule_key(
            101,
            [(date(2026, 9, 1), Decimal('100.00'))],
        )

        result = detect_equivalent_schedule(expected, [other_rental, expected])

        self.assertEqual(result.status, ScheduleMatchStatus.EQUIVALENT)
        self.assertEqual(result.candidate_indexes, (1,))
        self.assertTrue(result.is_safe)

    def test_duplicate_equivalent_schedules_are_ambiguous(self):
        expected = build_local_schedule_key(
            100,
            [(date(2026, 9, 1), Decimal('100.00'))],
        )

        result = detect_equivalent_schedule(expected, [expected, expected])

        self.assertEqual(result.status, ScheduleMatchStatus.AMBIGUOUS)
        self.assertEqual(result.candidate_indexes, (0, 1))
        self.assertFalse(result.is_safe)

    def test_missing_schedule_is_not_safe(self):
        expected = build_local_schedule_key(
            100,
            [(date(2026, 9, 1), Decimal('100.00'))],
        )
        different = build_local_schedule_key(
            100,
            [(date(2026, 9, 1), Decimal('99.00'))],
        )

        result = detect_equivalent_schedule(expected, [different])

        self.assertEqual(result.status, ScheduleMatchStatus.MISSING)
        self.assertEqual(result.candidate_indexes, ())


class RecoveryPlanValidationTests(SimpleTestCase):
    def test_audited_aggregate_plan_is_valid_only_outside_go_mode(self):
        validation = validate_recovery_plan(
            EXPECTED_RECOVERY_PLAN,
            require_manifests=False,
        )

        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.errors, ())
        self.assertEqual(EXPECTED_RECOVERY_PLAN.payment_count, 110)
        self.assertEqual(EXPECTED_RECOVERY_PLAN.payment_total, Decimal('7670.00'))
        self.assertEqual(
            EXPECTED_RECOVERY_PLAN.payment_groups,
            GroupedCounts.from_mapping({
                RETROACTIVE_PAYMENT_GROUP: 107,
                ENTRY_PAYMENT_GROUP: 3,
            }),
        )

        go_validation = validate_recovery_plan(EXPECTED_RECOVERY_PLAN)
        self.assertFalse(go_validation.is_valid)
        self.assertTrue(any('required for GO' in item for item in go_validation.errors))
        self.assertEqual(
            EXPECTED_RECOVERY_PLAN.write_off_groups,
            GroupedCounts.from_mapping({
                LEGACY_DEAD_CUTOFF_GROUP: 25059,
                ACCESS_SETTLED_WITHOUT_VALUE_GROUP: 40,
                LEGACY_OVERPAYMENT_GROUP: 1,
            }),
        )

    def test_validation_is_deterministic(self):
        first = validate_recovery_plan(
            EXPECTED_RECOVERY_PLAN,
            require_manifests=False,
        )
        second = validate_recovery_plan(
            EXPECTED_RECOVERY_PLAN,
            require_manifests=False,
        )

        self.assertEqual(first, second)

    def test_grouped_counts_are_immutable_and_hashable(self):
        source = [(ENTRY_PAYMENT_GROUP, 3), (RETROACTIVE_PAYMENT_GROUP, 107)]
        groups = GroupedCounts(tuple(sorted(source)))

        self.assertEqual(groups.total, 110)
        self.assertEqual(len({groups, EXPECTED_RECOVERY_PLAN.payment_groups}), 1)

    def test_payment_group_invariant_and_expected_values_are_enforced(self):
        actual = RecoveryPlanSummary(
            payment_count=110,
            payment_total=Decimal('7669.99'),
            payment_groups=GroupedCounts.from_mapping({
                RETROACTIVE_PAYMENT_GROUP: 106,
                ENTRY_PAYMENT_GROUP: 3,
            }),
            write_off_count=25100,
            write_off_groups=EXPECTED_RECOVERY_PLAN.write_off_groups,
        )

        validation = validate_recovery_plan(actual)

        self.assertFalse(validation.is_valid)
        self.assertTrue(any('sum of payment_groups' in item for item in validation.errors))
        self.assertTrue(any('payment_total differs' in item for item in validation.errors))
        self.assertTrue(any('payment_groups differs' in item for item in validation.errors))

    def test_write_off_group_invariant_is_enforced(self):
        actual = RecoveryPlanSummary(
            payment_count=110,
            payment_total=Decimal('7670.00'),
            payment_groups=EXPECTED_RECOVERY_PLAN.payment_groups,
            write_off_count=25100,
            write_off_groups=GroupedCounts.from_mapping({
                LEGACY_DEAD_CUTOFF_GROUP: 25059,
                ACCESS_SETTLED_WITHOUT_VALUE_GROUP: 39,
                LEGACY_OVERPAYMENT_GROUP: 1,
            }),
        )

        validation = validate_recovery_plan(actual)

        self.assertFalse(validation.is_valid)
        self.assertTrue(any('sum of write_off_groups' in item for item in validation.errors))
        self.assertTrue(any('write_off_groups differs' in item for item in validation.errors))

    def test_go_validation_detects_association_tampering_with_same_totals(self):
        source_identity = SourcePaymentIdentity('recovery_backup', 1)
        expected_payment = RetroactivePaymentFingerprint(
            identity=source_identity,
            receivable=LegacyReceivableKey('pagar', 100),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='cash',
        )
        tampered_payment = RetroactivePaymentFingerprint(
            identity=source_identity,
            receivable=LegacyReceivableKey('pagar', 101),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='cash',
        )
        write_off = LegacyReceivableKey('pagar', 200)
        expected = self._summary((expected_payment,), (write_off,))
        actual = self._summary((tampered_payment,), (write_off,))

        validation = validate_recovery_plan(actual, expected)

        self.assertFalse(validation.is_valid)
        self.assertIn(
            'payment_manifest differs from the expected identity set',
            validation.errors,
        )

    def test_go_validation_detects_entry_installment_swap_same_rental(self):
        identity = SourcePaymentIdentity('recovery_backup', 1)
        expected_payment = EntryPaymentFingerprint(
            identity=identity,
            receivable=_local_receivable(500, ordinal=1),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='pix',
        )
        wrong_installment = EntryPaymentFingerprint(
            identity=identity,
            receivable=_local_receivable(500, ordinal=2),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='pix',
        )
        write_off = LegacyReceivableKey('pagar', 200)
        expected = self._summary((expected_payment,), (write_off,))
        actual = self._summary((wrong_installment,), (write_off,))

        validation = validate_recovery_plan(actual, expected)

        self.assertEqual(expected_payment.rental_number, 500)
        self.assertFalse(validation.is_valid)
        self.assertIn(
            'payment_manifest differs from the expected identity set',
            validation.errors,
        )

    def test_metadata_edits_do_not_change_go_manifest_identity(self):
        identity = SourcePaymentIdentity('recovery_backup', 1)
        expected_payment = EntryPaymentFingerprint(
            identity=identity,
            receivable=_local_receivable(),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='pix',
            notes='Antes',
            user_email='antes@example.com',
        )
        actual_payment = EntryPaymentFingerprint(
            identity=identity,
            receivable=_local_receivable(),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='pix',
            notes='Depois',
            user_email='depois@example.com',
        )
        write_off = LegacyReceivableKey('pagar', 200)
        expected = self._summary((expected_payment,), (write_off,))
        actual = self._summary((actual_payment,), (write_off,))

        self.assertTrue(validate_recovery_plan(actual, expected).is_valid)

    def test_go_manifest_preserves_identical_payments_with_different_source_ids(self):
        payments = tuple(
            EntryPaymentFingerprint(
                identity=SourcePaymentIdentity('recovery_backup', source_id),
                receivable=_local_receivable(),
                payment_date=date(2026, 7, 1),
                amount=Decimal('100.00'),
                method='cash',
            )
            for source_id in (1, 2)
        )
        summary = self._summary(
            payments,
            (LegacyReceivableKey('pagar', 200),),
        )

        validation = validate_recovery_plan(summary, summary)

        self.assertTrue(validation.is_valid)
        self.assertEqual(len(summary.payment_manifest), 2)
        self.assertNotEqual(payments[0].identity, payments[1].identity)

    def test_go_validation_detects_write_off_identity_swap(self):
        payment = EntryPaymentFingerprint(
            identity=SourcePaymentIdentity('recovery_backup', 1),
            receivable=_local_receivable(),
            payment_date=date(2026, 7, 1),
            amount=Decimal('100.00'),
            method='cash',
        )
        expected = self._summary(
            (payment,),
            (LegacyReceivableKey('pagar', 200),),
        )
        actual = self._summary(
            (payment,),
            (LegacyReceivableKey('pagar', 201),),
        )

        validation = validate_recovery_plan(actual, expected)

        self.assertFalse(validation.is_valid)
        self.assertIn(
            'write_off_manifest differs from the expected identity set',
            validation.errors,
        )

    def test_duplicate_source_identity_is_never_go_safe(self):
        identity = SourcePaymentIdentity('recovery_backup', 1)
        payments = (
            EntryPaymentFingerprint(
                identity=identity,
                receivable=_local_receivable(500),
                payment_date=date(2026, 7, 1),
                amount=Decimal('100.00'),
                method='cash',
            ),
            EntryPaymentFingerprint(
                identity=identity,
                receivable=_local_receivable(501),
                payment_date=date(2026, 7, 1),
                amount=Decimal('100.00'),
                method='cash',
            ),
        )
        summary = self._summary(
            payments,
            (LegacyReceivableKey('pagar', 200),),
        )

        validation = validate_recovery_plan(summary, summary)

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any('duplicate source identities' in item for item in validation.errors)
        )

    def test_duplicate_source_identity_across_payment_kinds_is_rejected(self):
        identity = SourcePaymentIdentity('recovery_backup', 1)
        payments = (
            RetroactivePaymentFingerprint(
                identity=identity,
                receivable=LegacyReceivableKey('pagar', 100),
                payment_date=date(2026, 7, 1),
                amount=Decimal('100.00'),
                method='cash',
            ),
            EntryPaymentFingerprint(
                identity=identity,
                receivable=_local_receivable(),
                payment_date=date(2026, 7, 1),
                amount=Decimal('100.00'),
                method='cash',
            ),
        )
        summary = self._summary(
            payments,
            (LegacyReceivableKey('pagar', 200),),
        )

        validation = validate_recovery_plan(summary, summary)

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any('duplicate source identities' in item for item in validation.errors)
        )

    def _summary(self, payments, write_offs):
        payments = tuple(payments)
        write_offs = tuple(write_offs)
        return RecoveryPlanSummary(
            payment_count=len(payments),
            payment_total=sum(
                (payment.amount for payment in payments),
                Decimal('0.00'),
            ),
            payment_groups=GroupedCounts.from_mapping({
                RETROACTIVE_PAYMENT_GROUP: sum(
                    isinstance(payment, RetroactivePaymentFingerprint)
                    for payment in payments
                ),
                ENTRY_PAYMENT_GROUP: sum(
                    isinstance(payment, EntryPaymentFingerprint)
                    for payment in payments
                ),
            }),
            write_off_count=len(write_offs),
            write_off_groups=GroupedCounts.from_mapping({
                LEGACY_DEAD_CUTOFF_GROUP: len(write_offs),
                ACCESS_SETTLED_WITHOUT_VALUE_GROUP: 0,
                LEGACY_OVERPAYMENT_GROUP: 0,
            }),
            payment_manifest=payments,
            write_off_manifest=write_offs,
        )
