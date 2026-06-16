from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0005_customer_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='cpf_digits',
            field=models.CharField(blank=True, db_index=True, max_length=14, verbose_name='CPF (só dígitos)'),
        ),
        migrations.AddField(
            model_name='customer',
            name='rg_digits',
            field=models.CharField(blank=True, db_index=True, max_length=20, verbose_name='RG (só dígitos)'),
        ),
        migrations.AddField(
            model_name='customer',
            name='phone_home_digits',
            field=models.CharField(blank=True, max_length=20, verbose_name='tel. residencial (só dígitos)'),
        ),
        migrations.AddField(
            model_name='customer',
            name='phone_mobile_digits',
            field=models.CharField(blank=True, db_index=True, max_length=20, verbose_name='celular (só dígitos)'),
        ),
        migrations.AddField(
            model_name='customer',
            name='phone_work_digits',
            field=models.CharField(blank=True, max_length=20, verbose_name='tel. comercial (só dígitos)'),
        ),
        migrations.AddField(
            model_name='customer',
            name='name_search',
            field=models.CharField(blank=True, max_length=180, verbose_name='nome normalizado'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['cpf_digits'], name='customer_cpf_digits_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['rg_digits'], name='customer_rg_digits_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['phone_mobile_digits'], name='customer_mobile_digits_idx'),
        ),
    ]
