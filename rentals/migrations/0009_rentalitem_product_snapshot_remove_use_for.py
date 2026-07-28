from django.db import migrations, models


USE_FOR_PREFIX = 'locado.usar: '
SNAPSHOT_FIELDS = [
    'product_prefix_snapshot',
    'product_code_snapshot',
    'product_description_snapshot',
    'product_color_snapshot',
    'product_size_snapshot',
    'product_snapshot_captured',
]


def preserve_use_for_and_snapshot_products(apps, schema_editor):
    database = schema_editor.connection.alias
    Rental = apps.get_model('rentals', 'Rental')
    RentalItem = apps.get_model('rentals', 'RentalItem')

    rentals_to_update = []
    rentals = (
        Rental.objects.using(database)
        .exclude(use_for='')
        .only('pk', 'use_for', 'legacy_notes')
    )
    for rental in rentals.iterator(chunk_size=1000):
        value = ' '.join((rental.use_for or '').split())
        if not value:
            continue
        marker = f'{USE_FOR_PREFIX}{value}'
        existing_lines = (rental.legacy_notes or '').splitlines()
        if marker in existing_lines:
            continue
        rental.legacy_notes = '\n'.join([
            part for part in ((rental.legacy_notes or '').rstrip(), marker) if part
        ])
        rentals_to_update.append(rental)
        if len(rentals_to_update) >= 1000:
            Rental.objects.using(database).bulk_update(
                rentals_to_update,
                ['legacy_notes'],
                batch_size=1000,
            )
            rentals_to_update.clear()
    if rentals_to_update:
        Rental.objects.using(database).bulk_update(
            rentals_to_update,
            ['legacy_notes'],
            batch_size=1000,
        )

    items_to_update = []
    items = (
        RentalItem.objects.using(database)
        .select_related('product__category')
        .all()
    )
    for item in items.iterator(chunk_size=1000):
        product = item.product
        item.product_prefix_snapshot = product.category.prefix
        item.product_code_snapshot = product.code
        item.product_description_snapshot = product.description
        item.product_color_snapshot = product.color
        item.product_size_snapshot = product.size
        item.product_snapshot_captured = True
        items_to_update.append(item)
        if len(items_to_update) >= 1000:
            RentalItem.objects.using(database).bulk_update(
                items_to_update,
                SNAPSHOT_FIELDS,
                batch_size=1000,
            )
            items_to_update.clear()
    if items_to_update:
        RentalItem.objects.using(database).bulk_update(
            items_to_update,
            SNAPSHOT_FIELDS,
            batch_size=1000,
        )


def restore_use_for(apps, schema_editor):
    database = schema_editor.connection.alias
    Rental = apps.get_model('rentals', 'Rental')
    rentals_to_update = []
    rentals = Rental.objects.using(database).exclude(legacy_notes='').only(
        'pk',
        'use_for',
        'legacy_notes',
    )
    for rental in rentals.iterator(chunk_size=1000):
        if rental.use_for:
            continue
        for line in (rental.legacy_notes or '').splitlines():
            if line.startswith(USE_FOR_PREFIX):
                rental.use_for = line.removeprefix(USE_FOR_PREFIX)[:200]
                rentals_to_update.append(rental)
                break
        if len(rentals_to_update) >= 1000:
            Rental.objects.using(database).bulk_update(
                rentals_to_update,
                ['use_for'],
                batch_size=1000,
            )
            rentals_to_update.clear()
    if rentals_to_update:
        Rental.objects.using(database).bulk_update(
            rentals_to_update,
            ['use_for'],
            batch_size=1000,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0007_product_is_active'),
        ('rentals', '0008_rentalitem_proof_photo_filefield'),
    ]

    operations = [
        migrations.AddField(
            model_name='rentalitem',
            name='product_prefix_snapshot',
            field=models.CharField(
                blank=True,
                max_length=10,
                verbose_name='prefixo da peça na locação',
            ),
        ),
        migrations.AddField(
            model_name='rentalitem',
            name='product_code_snapshot',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='código da peça na locação',
            ),
        ),
        migrations.AddField(
            model_name='rentalitem',
            name='product_description_snapshot',
            field=models.CharField(
                blank=True,
                max_length=200,
                verbose_name='descrição da peça na locação',
            ),
        ),
        migrations.AddField(
            model_name='rentalitem',
            name='product_color_snapshot',
            field=models.CharField(
                blank=True,
                max_length=50,
                verbose_name='cor da peça na locação',
            ),
        ),
        migrations.AddField(
            model_name='rentalitem',
            name='product_size_snapshot',
            field=models.CharField(
                blank=True,
                max_length=50,
                verbose_name='tamanho da peça na locação',
            ),
        ),
        migrations.AddField(
            model_name='rentalitem',
            name='product_snapshot_captured',
            field=models.BooleanField(
                default=False,
                verbose_name='dados da peça preservados',
            ),
        ),
        migrations.RunPython(
            preserve_use_for_and_snapshot_products,
            restore_use_for,
        ),
        migrations.RemoveField(
            model_name='rental',
            name='use_for',
        ),
    ]
