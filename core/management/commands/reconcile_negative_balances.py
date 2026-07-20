"""Reconcile receivables whose stored balance is negative."""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from billing.models import Receivable
from billing.services import reconcile_overpayment


DEFAULT_REASON = (
    'Ajuste pós-migração: saldo credor legado não reconciliado, '
    'baixado com preservação dos valores históricos.'
)


class Command(BaseCommand):
    help = 'Reconcile negative receivable balances; dry-run unless --apply is passed.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Grava os ajustes. Sem esta opção, apenas exibe a prévia.',
        )
        parser.add_argument(
            '--reason',
            default=DEFAULT_REASON,
            help='Motivo gravado no recebimento e no log de auditoria.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        reason = (options['reason'] or '').strip()
        if not reason:
            raise CommandError('--reason não pode ficar vazio.')

        receivables = Receivable.objects.filter(balance__lt=0).order_by('pk')
        count = receivables.count()
        negative_total = (
            receivables.aggregate(value=Sum('balance'))['value'] or Decimal('0')
        )
        overpayment_total = abs(negative_total).quantize(Decimal('0.01'))

        mode = 'APLICANDO' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'reconcile_negative_balances — {mode}'
        ))
        self.stdout.write(
            f'Total a reconciliar: {count} recebimento(s), '
            f'saldo credor R$ {overpayment_total}'
        )

        for receivable in receivables.select_related('rental'):
            self.stdout.write(
                f'  ID {receivable.pk} · Locação #{receivable.rental.number} · '
                f'valor R$ {receivable.amount} · recebido R$ {receivable.paid_amount} · '
                f'saldo R$ {receivable.balance}'
            )

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                'Dry-run concluído; use --apply para gravar os ajustes.'
            ))
            return

        reconciled_count = 0
        with transaction.atomic():
            for receivable in receivables:
                if reconcile_overpayment(receivable, reason=reason):
                    reconciled_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Reconciliados: {reconciled_count} recebimento(s).'
        ))
