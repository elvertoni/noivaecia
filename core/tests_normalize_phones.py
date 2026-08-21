"""Tests for the ``normalize_customer_phones`` management command.

The business goal is single: after ``--apply``, the repaired records must be
accepted by ``notifications.services.format_whatsapp_number``, which is what
gates every pickup/return reminder.
"""

import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from core.management.commands.normalize_customer_phones import (
    CATEGORY_ADD_DDD,
    CATEGORY_ADD_DDD_AND_NINTH,
    CATEGORY_ADD_NINTH,
    CATEGORY_EMPTY,
    CATEGORY_LANDLINE,
    CATEGORY_OK,
    CATEGORY_STRIP_ZEROS,
    CATEGORY_UNKNOWN,
    classify_phone,
)
from customers.models import Customer
from notifications.services import format_whatsapp_number


class ClassifyPhoneTests(TestCase):
    """Unit coverage for every classification branch."""

    def test_empty_values(self):
        for value in ('', None, '   ', 'sem telefone'):
            with self.subTest(value=value):
                self.assertEqual(classify_phone(value), (CATEGORY_EMPTY, ''))

    def test_already_valid_eleven_digits(self):
        self.assertEqual(classify_phone('(43) 99999-8888'), (CATEGORY_OK, ''))
        self.assertEqual(classify_phone('43999998888'), (CATEGORY_OK, ''))

    def test_already_valid_with_country_code(self):
        # 13 digits and 12 digits, both starting with 55.
        self.assertEqual(classify_phone('5543999998888'), (CATEGORY_OK, ''))
        self.assertEqual(classify_phone('554333334444'), (CATEGORY_OK, ''))

    def test_add_ddd(self):
        self.assertEqual(
            classify_phone('9 96634657'),
            (CATEGORY_ADD_DDD, '43996634657'),
        )

    def test_add_ddd_honors_custom_default(self):
        self.assertEqual(
            classify_phone('996634657', default_ddd='41'),
            (CATEGORY_ADD_DDD, '41996634657'),
        )

    def test_add_ddd_and_ninth(self):
        # Anatel 2015: an eight-digit mobile gained a leading 9.
        self.assertEqual(
            classify_phone('88083861'),
            (CATEGORY_ADD_DDD_AND_NINTH, '43988083861'),
        )
        self.assertEqual(
            classify_phone('91667660'),
            (CATEGORY_ADD_DDD_AND_NINTH, '43991667660'),
        )

    def test_add_ninth(self):
        self.assertEqual(
            classify_phone('43 8806-6815'),
            (CATEGORY_ADD_NINTH, '43988066815'),
        )
        self.assertEqual(
            classify_phone('4396475068'),
            (CATEGORY_ADD_NINTH, '43996475068'),
        )

    def test_ten_digits_starting_with_99_is_left_alone(self):
        """'9 984887097' could be a stray 9 or area code 99 — never guess.

        A stray leading digit before a complete 9-digit mobile produces exactly
        the same shape as area code 99 (Maranhão) plus a pre-2015 mobile.
        """
        for value in ('9 984887097', '9999683327', '9991439320'):
            with self.subTest(value=value):
                self.assertEqual(classify_phone(value), (CATEGORY_UNKNOWN, ''))

    def test_landline_is_reported_but_never_rewritten(self):
        self.assertEqual(classify_phone('3542-1317'), (CATEGORY_LANDLINE, ''))
        self.assertEqual(classify_phone('(43) 3542-1317'), (CATEGORY_LANDLINE, ''))

    def test_unknown_shapes(self):
        cases = (
            '8805-966',        # 7 digits
            '9',               # 1 digit
            '96',              # 2 digits
            '840857817',       # 9 digits not starting with 9
            '430996530819',    # 12 digits not starting with 55
            '43 43998046779',  # 13 digits not starting with 55
            '4374003256',      # 10 digits, third digit is neither mobile nor landline
            '43799998888',     # 11 digits without the ninth digit
            '1088083861',      # 10 digits with an impossible area code
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(classify_phone(value), (CATEGORY_UNKNOWN, ''))

    def test_leading_zeros_are_stripped_then_reclassified(self):
        self.assertEqual(
            classify_phone('043 99185309'),
            (CATEGORY_ADD_NINTH, '43999185309'),
        )
        self.assertEqual(
            classify_phone('0996634657'),
            (CATEGORY_ADD_DDD, '43996634657'),
        )

    def test_leading_zeros_over_a_valid_number_are_removed(self):
        # '038992530078' only fails format_whatsapp_number because of the zero.
        self.assertEqual(
            classify_phone('038992530078'),
            (CATEGORY_STRIP_ZEROS, '38992530078'),
        )

    def test_only_zeros_is_unknown_not_empty(self):
        # There *is* a value on record — it just means nothing.
        self.assertEqual(classify_phone('0'), (CATEGORY_UNKNOWN, ''))
        self.assertEqual(classify_phone('000'), (CATEGORY_UNKNOWN, ''))

    def test_transformed_numbers_are_stable(self):
        """A transformed value must classify as ``ok`` on the next run."""
        for value in ('9 96634657', '88083861', '43 8806-6815', '038992530078'):
            with self.subTest(value=value):
                _category, new_digits = classify_phone(value)
                self.assertEqual(classify_phone(new_digits), (CATEGORY_OK, ''))


class NormalizeCustomerPhonesCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.add_ddd = Customer.objects.create(
            name='Maria sem DDD', phone_mobile='9 96634657'
        )
        cls.add_ddd_and_ninth = Customer.objects.create(
            name='Joana oito digitos', phone_mobile='88083861'
        )
        cls.add_ninth = Customer.objects.create(
            name='Carla sem nono', phone_mobile='43 8806-6815'
        )
        cls.strip_zeros = Customer.objects.create(
            name='Bruna com zero', phone_mobile='038992530078'
        )
        cls.already_ok = Customer.objects.create(
            name='Ana valida', phone_mobile='(43) 99999-8888'
        )
        cls.landline = Customer.objects.create(
            name='Fixo da loja', phone_mobile='3542-1317'
        )
        cls.unknown = Customer.objects.create(
            name='Numero truncado', phone_mobile='8805-966'
        )
        cls.empty = Customer.objects.create(name='Sem celular', phone_mobile='')

    def run_command(self, *args):
        out = StringIO()
        call_command('normalize_customer_phones', *args, stdout=out, stderr=out)
        return out.getvalue()

    # ── preview ────────────────────────────────────────────────────────────
    def test_preview_does_not_write(self):
        output = self.run_command()

        self.assertIn('MODO PREVIEW', output)
        self.assertIn('nada foi gravado', output)
        self.add_ddd.refresh_from_db()
        self.add_ddd_and_ninth.refresh_from_db()
        self.add_ninth.refresh_from_db()
        self.strip_zeros.refresh_from_db()
        self.assertEqual(self.add_ddd.phone_mobile, '9 96634657')
        self.assertEqual(self.add_ddd_and_ninth.phone_mobile, '88083861')
        self.assertEqual(self.add_ninth.phone_mobile, '43 8806-6815')
        self.assertEqual(self.strip_zeros.phone_mobile, '038992530078')

    def test_preview_reports_every_category_and_the_total(self):
        output = self.run_command()

        self.assertIn('Total classificado: 8 cliente(s).', output)
        self.assertIn('Total a alterar: 4 cliente(s).', output)
        for category in (
            CATEGORY_OK,
            CATEGORY_ADD_DDD,
            CATEGORY_ADD_DDD_AND_NINTH,
            CATEGORY_ADD_NINTH,
            CATEGORY_STRIP_ZEROS,
            CATEGORY_LANDLINE,
            CATEGORY_EMPTY,
            CATEGORY_UNKNOWN,
        ):
            with self.subTest(category=category):
                self.assertIn(category, output)
        self.assertIn(f'Exemplos ({CATEGORY_ADD_DDD}):', output)
        self.assertIn("-> '43996634657'", output)
        self.assertIn(f'Exemplos ({CATEGORY_UNKNOWN}):', output)

    # ── apply ──────────────────────────────────────────────────────────────
    def test_apply_writes_the_repairable_numbers(self):
        output = self.run_command('--apply')

        self.assertIn('Concluído: 4 cliente(s) atualizado(s).', output)
        self.add_ddd.refresh_from_db()
        self.add_ddd_and_ninth.refresh_from_db()
        self.add_ninth.refresh_from_db()
        self.strip_zeros.refresh_from_db()
        self.assertEqual(self.add_ddd.phone_mobile, '43996634657')
        self.assertEqual(self.add_ddd_and_ninth.phone_mobile, '43988083861')
        self.assertEqual(self.add_ninth.phone_mobile, '43988066815')
        self.assertEqual(self.strip_zeros.phone_mobile, '38992530078')

    def test_apply_keeps_phone_mobile_and_digits_consistent(self):
        self.run_command('--apply')

        for customer in Customer.objects.all():
            with self.subTest(customer=customer.pk):
                self.assertEqual(
                    customer.phone_mobile_digits,
                    ''.join(c for c in customer.phone_mobile if c.isdigit()),
                )

    def test_apply_makes_numbers_acceptable_to_whatsapp(self):
        """The whole point: format_whatsapp_number must accept the result."""
        # Rejected outright today — these customers get no reminder at all.
        for customer in (self.add_ddd, self.add_ddd_and_ninth, self.strip_zeros):
            with self.subTest(customer=customer.pk):
                customer.refresh_from_db()
                self.assertIsNone(format_whatsapp_number(customer.phone_mobile_digits))
        # The 10-digit case is superficially accepted, but the E.164 number it
        # yields is the pre-2015 one, which no longer exists on WhatsApp.
        self.add_ninth.refresh_from_db()
        self.assertEqual(
            format_whatsapp_number(self.add_ninth.phone_mobile_digits),
            '554388066815',
        )

        self.run_command('--apply')

        repaired = (
            (self.add_ddd, '5543996634657'),
            (self.add_ddd_and_ninth, '5543988083861'),
            (self.add_ninth, '5543988066815'),
            (self.strip_zeros, '5538992530078'),
        )
        for customer, expected in repaired:
            with self.subTest(customer=customer.pk):
                customer.refresh_from_db()
                self.assertEqual(
                    format_whatsapp_number(customer.phone_mobile_digits),
                    expected,
                )

    def test_apply_honors_custom_ddd(self):
        self.run_command('--apply', '--ddd', '41')

        self.add_ddd.refresh_from_db()
        self.add_ddd_and_ninth.refresh_from_db()
        self.add_ninth.refresh_from_db()
        self.assertEqual(self.add_ddd.phone_mobile, '41996634657')
        self.assertEqual(self.add_ddd_and_ninth.phone_mobile, '41988083861')
        # An existing area code is never replaced by the default one.
        self.assertEqual(self.add_ninth.phone_mobile, '43988066815')

    def test_invalid_ddd_is_refused(self):
        for value in ('4', '430', '09', 'ab'):
            with self.subTest(value=value):
                with self.assertRaises(CommandError):
                    call_command(
                        'normalize_customer_phones',
                        '--ddd',
                        value,
                        stdout=StringIO(),
                    )

    # ── preservation ───────────────────────────────────────────────────────
    def test_landline_unknown_and_empty_stay_intact(self):
        self.run_command('--apply')

        self.landline.refresh_from_db()
        self.unknown.refresh_from_db()
        self.empty.refresh_from_db()
        self.already_ok.refresh_from_db()
        self.assertEqual(self.landline.phone_mobile, '3542-1317')
        self.assertEqual(self.unknown.phone_mobile, '8805-966')
        self.assertEqual(self.empty.phone_mobile, '')
        self.assertEqual(self.already_ok.phone_mobile, '(43) 99999-8888')

    def test_already_valid_record_is_not_touched(self):
        before = Customer.objects.get(pk=self.already_ok.pk).updated_at

        self.run_command('--apply')

        after = Customer.objects.get(pk=self.already_ok.pk).updated_at
        self.assertEqual(before, after)

    def test_stale_digits_are_reported_not_silently_fixed(self):
        # Legacy state: phone_mobile filled, derived column never populated.
        Customer.objects.filter(pk=self.already_ok.pk).update(phone_mobile_digits='')

        output = self.run_command('--apply')

        self.assertIn('phone_mobile_digits fora de sincronia', output)
        self.assertIn('rebuild_search_fields', output)
        self.already_ok.refresh_from_db()
        self.assertEqual(self.already_ok.phone_mobile_digits, '')

    # ── idempotence ────────────────────────────────────────────────────────
    def test_second_apply_changes_nothing(self):
        first = self.run_command('--apply')
        self.assertIn('Concluído: 4 cliente(s) atualizado(s).', first)
        snapshot = {
            pk: (phone, digits)
            for pk, phone, digits in Customer.objects.values_list(
                'pk', 'phone_mobile', 'phone_mobile_digits'
            )
        }

        second = self.run_command('--apply')

        self.assertIn('Total a alterar: 0 cliente(s).', second)
        self.assertIn('Nenhum telefone precisa de transformação.', second)
        self.assertNotIn('Concluído:', second)
        self.assertEqual(
            {
                pk: (phone, digits)
                for pk, phone, digits in Customer.objects.values_list(
                    'pk', 'phone_mobile', 'phone_mobile_digits'
                )
            },
            snapshot,
        )

    # ── audit trail ────────────────────────────────────────────────────────
    def test_report_csv_lists_every_classified_customer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'telefones.csv'

            output = self.run_command('--apply', '--report', str(path))

            self.assertIn('CSV salvo em', output)
            with path.open(encoding='utf-8', newline='') as csv_file:
                rows = list(csv.reader(csv_file))

        self.assertEqual(
            rows[0],
            [
                'customer_id',
                'name',
                'phone_mobile_antigo',
                'phone_mobile_novo',
                'categoria',
            ],
        )
        by_pk = {int(row[0]): row for row in rows[1:]}
        self.assertEqual(len(by_pk), Customer.objects.count())

        # Transformed rows carry the reversion pair (old -> new).
        self.assertEqual(
            by_pk[self.add_ddd.pk][2:],
            ['9 96634657', '43996634657', CATEGORY_ADD_DDD],
        )
        self.assertEqual(
            by_pk[self.add_ddd_and_ninth.pk][2:],
            ['88083861', '43988083861', CATEGORY_ADD_DDD_AND_NINTH],
        )
        self.assertEqual(
            by_pk[self.add_ninth.pk][2:],
            ['43 8806-6815', '43988066815', CATEGORY_ADD_NINTH],
        )
        self.assertEqual(
            by_pk[self.strip_zeros.pk][2:],
            ['038992530078', '38992530078', CATEGORY_STRIP_ZEROS],
        )
        # Untouched rows are listed too, with old == new.
        self.assertEqual(
            by_pk[self.landline.pk][2:],
            ['3542-1317', '3542-1317', CATEGORY_LANDLINE],
        )
        self.assertEqual(
            by_pk[self.unknown.pk][2:],
            ['8805-966', '8805-966', CATEGORY_UNKNOWN],
        )
        self.assertEqual(
            by_pk[self.empty.pk][2:], ['', '', CATEGORY_EMPTY]
        )
        self.assertEqual(
            by_pk[self.already_ok.pk][2:],
            ['(43) 99999-8888', '(43) 99999-8888', CATEGORY_OK],
        )
        # Names are carried for human review (Customer.save() uppercases them).
        self.assertEqual(by_pk[self.add_ddd.pk][1], 'MARIA SEM DDD')

    def test_report_csv_is_written_in_preview_mode_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plano.csv'

            self.run_command('--report', str(path))

            with path.open(encoding='utf-8', newline='') as csv_file:
                rows = list(csv.reader(csv_file))

        self.assertEqual(len(rows), Customer.objects.count() + 1)
        self.add_ddd.refresh_from_db()
        self.assertEqual(self.add_ddd.phone_mobile, '9 96634657')


class NormalizeCustomerPhonesEmptyBaseTests(TestCase):
    def test_no_customers_at_all(self):
        out = StringIO()
        call_command('normalize_customer_phones', stdout=out)
        output = out.getvalue()

        self.assertIn('Total classificado: 0 cliente(s).', output)
        self.assertIn('Nenhum telefone precisa de transformação.', output)
