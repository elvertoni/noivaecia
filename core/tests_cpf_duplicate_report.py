import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from customers.models import Customer


class CpfDuplicateReportTests(TestCase):
    def call_command(self, output_dir):
        stdout = StringIO()
        call_command('cpf_duplicate_report', output_dir=str(output_dir), stdout=stdout)
        return stdout.getvalue()

    def test_groups_customers_sharing_cpf(self):
        Customer.objects.create(name='Maria Silva', cpf='111.111.111-11')
        Customer.objects.create(name='Maria S. Silva', cpf='11111111111')
        Customer.objects.create(name='Outro Cliente', cpf='222.222.222-22')

        output = self.call_command('/tmp/does-not-matter')

        self.assertIn('1 grupo(s)', output)
        self.assertIn('2 cliente(s)', output)

    def test_writes_report_file(self):
        Customer.objects.create(name='Maria Silva', cpf='111.111.111-11')
        Customer.objects.create(name='Maria S. Silva', cpf='11111111111')

        with tempfile.TemporaryDirectory() as tmp:
            self.call_command(tmp)
            files = list(Path(tmp).glob('*-cpf-duplicates.md'))
            self.assertEqual(len(files), 1)
            content = files[0].read_text(encoding='utf-8')
            self.assertIn('CPF 11111111111', content)
            self.assertIn('Maria Silva', content)
            self.assertIn('Maria S. Silva', content)

    def test_ignores_customers_without_duplicate_cpf(self):
        Customer.objects.create(name='Único', cpf='333.333.333-33')

        output = self.call_command('/tmp/does-not-matter')

        self.assertIn('0 grupo(s)', output)
