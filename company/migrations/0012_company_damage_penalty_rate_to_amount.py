from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('company', '0011_alter_company_cancellation_penalty_rate_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='company',
            name='company_damage_penalty_rate_gte_0',
        ),
        migrations.RenameField(
            model_name='company',
            old_name='damage_penalty_rate',
            new_name='damage_penalty_amount',
        ),
        migrations.AlterField(
            model_name='company',
            name='damage_penalty_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=50,
                help_text='Valor fixo, em reais, cobrado uma vez por locação em caso de dano.',
                max_digits=10,
                verbose_name='penalidade por dano (R$)',
            ),
        ),
        migrations.AddConstraint(
            model_name='company',
            constraint=models.CheckConstraint(
                condition=models.Q(('damage_penalty_amount__gte', 0)),
                name='company_damage_penalty_amount_gte_0',
            ),
        ),
    ]
