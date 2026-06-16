from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rentals', '0007_rental_report_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rentalitem',
            name='proof_photo',
            field=models.FileField(
                blank=True,
                upload_to='rentals/proof_photos/%Y/%m/',
                verbose_name='foto de comprovação',
            ),
        ),
    ]
