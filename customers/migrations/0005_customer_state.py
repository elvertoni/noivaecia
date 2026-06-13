from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0004_customer_name_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='state',
            field=models.CharField(blank=True, default='PR', max_length=2, verbose_name='UF'),
        ),
        migrations.AlterField(
            model_name='customer',
            name='city',
            field=models.CharField(blank=True, default='Bandeirantes', max_length=100, verbose_name='cidade'),
        ),
    ]
