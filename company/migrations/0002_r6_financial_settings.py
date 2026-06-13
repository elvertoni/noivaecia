from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('company', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='late_fee_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=2,
                help_text='Percentual de multa moratória aplicado sobre o valor do título.',
                max_digits=5,
                verbose_name='multa moratória (%)',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='monthly_interest_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=1,
                help_text='Juros de mora mensais; dividido por 30 para cálculo diário.',
                max_digits=5,
                verbose_name='juros ao mês (%)',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='damage_penalty_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=50,
                help_text='Percentual do valor do item cobrado em caso de dano.',
                max_digits=5,
                verbose_name='penalidade por dano (%)',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='loss_penalty_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=100,
                help_text='Percentual do valor do item cobrado em caso de perda ou não devolução.',
                max_digits=5,
                verbose_name='penalidade por perda/não devolução (%)',
            ),
        ),
    ]
