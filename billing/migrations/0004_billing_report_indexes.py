import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_financialmovement_fmv_source_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='financialmovement',
            name='payment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='financial_movements',
                to='billing.payment',
                verbose_name='pagamento',
            ),
        ),
        migrations.AddIndex(
            model_name='receivable',
            index=models.Index(
                fields=['balance', 'due_date'],
                name='rcv_balance_due_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='receivable',
            index=models.Index(
                fields=['rental', 'due_date'],
                name='rcv_rental_due_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='receivable',
            index=models.Index(
                condition=models.Q(('balance__gt', 0)),
                fields=['due_date'],
                name='rcv_open_due_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(
                fields=['customer', 'payment_date'],
                name='pmt_customer_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(
                fields=['is_reversal', '-payment_date', '-created_at'],
                name='pmt_reversal_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='financialmovement',
            index=models.Index(
                fields=['-date', '-created_at'],
                name='fmv_date_created_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='financialmovement',
            index=models.Index(
                fields=['direction', 'date'],
                name='fmv_direction_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='financialmovement',
            index=models.Index(
                fields=['source', 'direction', 'date'],
                name='fmv_source_direction_date_idx',
            ),
        ),
    ]
