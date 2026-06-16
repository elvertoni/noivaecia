from django.db import migrations


def add_trgm_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS product_desc_trgm_idx '
        'ON catalog_product USING gin (description_search gin_trgm_ops)'
    )


def remove_trgm_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('DROP INDEX IF EXISTS product_desc_trgm_idx')


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_product_description_search'),
        ('customers', '0008_customer_name_trgm_idx'),
    ]

    operations = [
        migrations.RunPython(add_trgm_index, remove_trgm_index),
    ]
