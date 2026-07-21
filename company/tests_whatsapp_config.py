from datetime import time

from django.test import TestCase

from company.forms import CompanyForm
from company.models import Company


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
