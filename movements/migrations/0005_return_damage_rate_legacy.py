from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movements', '0004_return_damage_rate_return_damaged_items_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='return',
            old_name='damage_rate',
            new_name='damage_rate_legacy',
        ),
        migrations.AlterField(
            model_name='return',
            name='damage_rate_legacy',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Auditoria: percentual usado antes da multa virar valor fixo em R$.',
                max_digits=5,
                null=True,
                verbose_name='percentual aplicado por dano (legado)',
            ),
        ),
    ]
