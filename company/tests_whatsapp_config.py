from datetime import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from company.forms import CompanyForm
from company.models import Company

User = get_user_model()


class WhatsappReportFieldDefaultsTests(TestCase):
    def test_migration_applies_with_expected_defaults(self):
        company = Company.load()
        self.assertFalse(company.whatsapp_reports_enabled)
        self.assertEqual(company.whatsapp_report_number, '')
        self.assertEqual(company.whatsapp_report_time, time(7, 30))


class CompanyFormWhatsappNumberValidationTests(TestCase):
    def _base_data(self, **overrides):
        data = {
            'name': 'Noivas & Cia',
            'address': '',
            'city': '',
            'cnpj': '',
            'phones': '',
            'last_rental_number': 0,
            'daily_interest_rate': '0.00',
            'late_fee_rate': '2.00',
            'monthly_interest_rate': '1.00',
            'damage_penalty_rate': '50.00',
            'loss_penalty_rate': '100.00',
            'footer_message': '',
            'whatsapp_reports_enabled': False,
            'whatsapp_report_number': '',
            'whatsapp_report_time': '07:30',
        }
        data.update(overrides)
        return data

    def test_accepts_valid_number_with_ddi(self):
        form = CompanyForm(data=self._base_data(whatsapp_report_number='5543999998888'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['whatsapp_report_number'], '5543999998888')

    def test_accepts_multiple_valid_numbers(self):
        form = CompanyForm(data=self._base_data(
            whatsapp_report_number='5543999998888, +55 (43) 98888-7777',
        ))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['whatsapp_report_number'],
            '5543999998888\n5543988887777',
        )

    def test_accepts_formatted_numbers_separated_by_space(self):
        form = CompanyForm(data=self._base_data(
            whatsapp_report_number=(
                '+55 (43) 99999-8888 +55 (43) 98888-7777'
            ),
        ))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['whatsapp_report_number'],
            '5543999998888\n5543988887777',
        )

    def test_deduplicates_multiple_numbers(self):
        form = CompanyForm(data=self._base_data(
            whatsapp_report_number='5543999998888\n5543999998888',
        ))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['whatsapp_report_number'], '5543999998888')

    def test_accepts_valid_number_with_symbols(self):
        form = CompanyForm(data=self._base_data(whatsapp_report_number='+55 (43) 99999-8888'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['whatsapp_report_number'], '5543999998888')

    def test_rejects_too_short_number(self):
        form = CompanyForm(data=self._base_data(whatsapp_report_number='123'))
        self.assertFalse(form.is_valid())
        self.assertIn('whatsapp_report_number', form.errors)

    def test_rejects_invalid_number_entry(self):
        form = CompanyForm(data=self._base_data(whatsapp_report_number='5543999998888\nabc'))
        self.assertFalse(form.is_valid())
        self.assertIn('whatsapp_report_number', form.errors)

    def test_rejects_number_without_ddi_55(self):
        form = CompanyForm(data=self._base_data(whatsapp_report_number='43999998888'))
        self.assertFalse(form.is_valid())
        self.assertIn('whatsapp_report_number', form.errors)

    def test_blank_number_is_allowed(self):
        form = CompanyForm(data=self._base_data(whatsapp_report_number=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['whatsapp_report_number'], '')

    def test_enabled_report_requires_a_destination_number(self):
        form = CompanyForm(data=self._base_data(
            whatsapp_reports_enabled=True,
            whatsapp_report_number='',
        ))

        self.assertFalse(form.is_valid())
        self.assertIn('whatsapp_report_number', form.errors)

    def test_accepts_and_formats_valid_cnpj(self):
        form = CompanyForm(data=self._base_data(cnpj='11222333000181'))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['cnpj'], '11.222.333/0001-81')

    def test_rejects_invalid_cnpj(self):
        form = CompanyForm(data=self._base_data(cnpj='11.111.111/1111-11'))

        self.assertFalse(form.is_valid())
        self.assertIn('cnpj', form.errors)

    def test_toggle_defaults_to_false_when_absent(self):
        data = self._base_data()
        del data['whatsapp_reports_enabled']
        form = CompanyForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data['whatsapp_reports_enabled'])


CMD = 'notifications.management.commands.send_daily_whatsapp_report'


class ResendWhatsAppReportViewTests(TestCase):
    """The 'reenviar agora' button on the Empresa screen must force a resend
    regardless of the once-a-day-per-recipient lock the scheduler relies on
    (see whats.md — that lock exists to stop the 30s poll loop from spamming,
    not to block a deliberate manual request)."""

    def setUp(self):
        self.company = Company.load()
        self.company.whatsapp_reports_enabled = True
        self.company.whatsapp_report_number = '5543999998888'
        self.company.save()
        self.user = User.objects.create_user(email='ana@test.com', password='pass')
        ModulePermission.objects.create(user=self.user, module_key='company', allowed=True)
        self.client.force_login(self.user)
        self.url = reverse('company:resend_whatsapp_report')

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_user_without_company_module_gets_403(self):
        other = User.objects.create_user(email='other@test.com', password='pass')
        self.client.force_login(other)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_blocks_when_reports_disabled(self):
        self.company.whatsapp_reports_enabled = False
        self.company.save()
        with mock.patch(f'{CMD}.evolution.send_text') as send_text:
            response = self.client.post(self.url, follow=True)
        send_text.assert_not_called()
        self.assertContains(response, 'Ative o relatório diário')

    def test_resends_even_when_already_sent_today(self):
        from datetime import date as date_cls

        from core.models import AuditLog
        AuditLog.record(
            user=None,
            action='whatsapp_daily_report',
            obj=self.company,
            metadata={
                'reference_date': date_cls.today().isoformat(),
                'target': '5543999998888',
                'targets': ['5543999998888'],
                'message_id': 'OLDID',
            },
        )
        with mock.patch(f'{CMD}.evolution.send_text', return_value='NEWID') as send_text:
            response = self.client.post(self.url, follow=True)
        send_text.assert_called_once_with('5543999998888', mock.ANY)
        self.assertContains(response, 'Relatório reenviado')

    def test_shows_error_when_send_fails(self):
        from notifications import evolution
        with mock.patch(
            f'{CMD}.evolution.send_text', side_effect=evolution.EvolutionError('offline')
        ):
            response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'Falha ao reenviar')
