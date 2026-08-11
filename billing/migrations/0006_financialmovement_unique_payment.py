from django.db import migrations, models
from django.db.models import Count, Q


def validate_unique_payment_movements(apps, schema_editor):
    FinancialMovement = apps.get_model('billing', 'FinancialMovement')
    duplicates = (
        FinancialMovement.objects.exclude(payment_id__isnull=True)
        .values('payment_id')
        .annotate(movement_count=Count('id'))
        .filter(movement_count__gt=1)
        .order_by('payment_id')
    )
    duplicate_count = duplicates.count()
    if duplicate_count:
        sample = list(duplicates.values_list('payment_id', flat=True)[:20])
        raise RuntimeError(
            'Existem pagamentos ligados a múltiplos movimentos financeiros. '
            f'Corrija {duplicate_count} pagamento(s) antes desta migração. '
            f'Amostra de IDs: {sample}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_receivable_written_off_at_and_more'),
    ]

    operations = [
        migrations.RunPython(validate_unique_payment_movements, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='financialmovement',
            constraint=models.UniqueConstraint(
                fields=('payment',),
                condition=Q(payment__isnull=False),
                name='fmv_unique_payment_nonnull',
            ),
        ),
    ]
