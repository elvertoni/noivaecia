from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_customer_is_placeholder_customer_legacy_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='is_active',
            field=models.BooleanField(db_index=True, default=True, verbose_name='ativo'),
        ),
    ]
