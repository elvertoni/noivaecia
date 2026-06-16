from django.db import migrations


def enable_trigram_and_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS customer_name_trgm_idx '
        'ON customers_customer USING gin (name_search gin_trgm_ops)'
    )


def remove_trigram_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('DROP INDEX IF EXISTS customer_name_trgm_idx')


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0007_customer_populate_normalized_fields'),
    ]

    operations = [
        migrations.RunPython(enable_trigram_and_index, remove_trigram_index),
    ]
