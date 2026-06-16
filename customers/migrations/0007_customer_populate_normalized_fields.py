import re
import unicodedata

from django.db import migrations


def _digits_only(value):
    return re.sub(r'\D', '', value or '')


def _normalize_name(value):
    if not value:
        return ''
    nfkd = unicodedata.normalize('NFKD', value)
    stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ' '.join(stripped.lower().split())


def populate_normalized_fields(apps, schema_editor):
    Customer = apps.get_model('customers', 'Customer')
    batch = []
    for customer in Customer.objects.all().iterator(chunk_size=500):
        customer.cpf_digits = _digits_only(customer.cpf)
        customer.rg_digits = _digits_only(customer.rg)
        customer.phone_home_digits = _digits_only(customer.phone_home)
        customer.phone_mobile_digits = _digits_only(customer.phone_mobile)
        customer.phone_work_digits = _digits_only(customer.phone_work)
        customer.name_search = _normalize_name(customer.name)
        batch.append(customer)
        if len(batch) >= 500:
            Customer.objects.bulk_update(
                batch,
                ['cpf_digits', 'rg_digits', 'phone_home_digits',
                 'phone_mobile_digits', 'phone_work_digits', 'name_search'],
            )
            batch.clear()
    if batch:
        Customer.objects.bulk_update(
            batch,
            ['cpf_digits', 'rg_digits', 'phone_home_digits',
             'phone_mobile_digits', 'phone_work_digits', 'name_search'],
        )


def reverse_populate(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0006_customer_normalized_lookup_fields'),
    ]

    operations = [
        migrations.RunPython(populate_normalized_fields, reverse_populate),
    ]
