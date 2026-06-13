from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rentals', '0003_rental_cancelled_at_rental_cancelled_by_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='rental',
            name='contract_version',
            field=models.CharField(blank=True, max_length=50, verbose_name='versão do contrato'),
        ),
        migrations.AddField(
            model_name='rental',
            name='contract_printed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='contrato impresso em'),
        ),
    ]
