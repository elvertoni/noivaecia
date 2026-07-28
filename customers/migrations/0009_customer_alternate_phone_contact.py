from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0008_customer_name_trgm_idx'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customer',
            name='phone_home',
            field=models.CharField(
                blank=True,
                max_length=20,
                verbose_name='telefone alternativo',
            ),
        ),
        migrations.AlterField(
            model_name='customer',
            name='phone_home_digits',
            field=models.CharField(
                blank=True,
                max_length=20,
                verbose_name='tel. alternativo (só dígitos)',
            ),
        ),
        migrations.AddField(
            model_name='customer',
            name='alternate_phone_contact',
            field=models.CharField(
                blank=True,
                default='',
                max_length=100,
                verbose_name='identificação do telefone alternativo',
            ),
        ),
    ]
