"""Bulk clean-up of dead legacy records after the go-live migration.

Two independent scopes, both selected by a cutoff date decided with the
client at delivery time:

* ``--pickup-before YYYY-MM-DD`` — pending rentals whose pickup date is
  older than the cutoff are cancelled in bulk (they were never picked up
  in the old system and never will be).
* ``--due-before YYYY-MM-DD`` — open receivables (balance > 0) due before
  the cutoff are written off (``written_off_at``): amount and paid_amount
  stay untouched for history, balance is forced to zero so the whole app
  stops treating them as collectible.

Dry-run is the default; nothing is written unless ``--apply`` is given.
The dry-run output includes a per-year breakdown so the cutoff date can
be chosen interactively with the client.

Picked-up rentals awaiting return are deliberately NOT covered: that list
is small and recent, and must be reviewed case by case in the UI.
"""

from collections import Counter
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import Receivable
from core.models import AuditLog
from rentals.models import Rental

DEFAULT_REASON = 'Ajuste pós-migração: registro legado morto (corte acordado com a cliente).'


class Command(BaseCommand):
    help = 'Bulk-cancel dead pending rentals and write off uncollectible legacy receivables.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pickup-before',
            help='Cancel pending rentals with pickup_date before this date (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--due-before',
            help='Write off open receivables with due_date before this date (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually write changes. Without this flag the command only reports (dry-run).',
        )
        parser.add_argument(
            '--reason',
            default=DEFAULT_REASON,
            help='Reason recorded on each cancelled rental / written-off receivable and in the audit log.',
        )

    def handle(self, *args, **options):
        pickup_before = self._parse_date(options.get('pickup_before'), '--pickup-before')
        due_before = self._parse_date(options.get('due_before'), '--due-before')
        apply_changes = options['apply']
        reason = options['reason']

        if not pickup_before and not due_before:
            raise CommandError('Nothing to do: pass --pickup-before and/or --due-before.')

        mode = 'APLICANDO' if apply_changes else 'DRY-RUN (nada será gravado; use --apply para executar)'
        self.stdout.write(self.style.MIGRATE_HEADING(f'legacy_reset — {mode}'))

        if pickup_before:
            self._handle_pending_rentals(pickup_before, apply_changes, reason)

        if due_before:
            self._handle_open_receivables(due_before, apply_changes, reason)

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                '\nDry-run concluído. Revise os números acima e repita com --apply para executar.'
            ))

    # ── helpers ────────────────────────────────────────────────────────────

    def _parse_date(self, raw, flag):
        if not raw:
            return None
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'{flag}: data inválida {raw!r}, use o formato YYYY-MM-DD.')

    def _year_breakdown(self, dates):
        counter = Counter(d.year for d in dates if d)
        lines = []
        for year in sorted(counter):
            lines.append(f'    {year}: {counter[year]}')
        return lines

    def _handle_pending_rentals(self, cutoff, apply_changes, reason):
        qs = Rental.objects.filter(status=Rental.Status.PENDING, pickup_date__lt=cutoff)
        count = qs.count()

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[1] Locações pendentes (a retirar) com retirada antes de {cutoff:%d/%m/%Y}'
        ))
        self.stdout.write(f'  Total a cancelar: {count}')
        if count:
            self.stdout.write('  Por ano de retirada:')
            for line in self._year_breakdown(qs.values_list('pickup_date', flat=True)):
                self.stdout.write(line)

        if not apply_changes or not count:
            return

        now = timezone.now()
        with transaction.atomic():
            updated = qs.update(
                status=Rental.Status.CANCELLED,
                cancelled_reason=reason,
                cancelled_at=now,
                updated_at=now,
            )
            AuditLog.objects.create(
                user=None,
                action='legacy_reset_cancel_rentals',
                model_name='Rental',
                object_id='bulk',
                object_repr=f'{updated} locações pendentes canceladas',
                reason=reason,
                metadata={
                    'cutoff_pickup_before': cutoff.isoformat(),
                    'cancelled_count': updated,
                    'executed_at': now.isoformat(),
                },
            )
        self.stdout.write(self.style.SUCCESS(f'  Canceladas: {updated}'))

    def _handle_open_receivables(self, cutoff, apply_changes, reason):
        qs = Receivable.objects.filter(
            balance__gt=0, due_date__lt=cutoff, written_off_at__isnull=True,
        )
        count = qs.count()
        total = (qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')).quantize(Decimal('0.01'))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[2] Recebimentos em aberto com vencimento antes de {cutoff:%d/%m/%Y}'
        ))
        self.stdout.write(f'  Total a baixar: {count} títulos, saldo R$ {total}')
        if count:
            self.stdout.write('  Por ano de vencimento:')
            for line in self._year_breakdown(qs.values_list('due_date', flat=True)):
                self.stdout.write(line)

        if not apply_changes or not count:
            return

        now = timezone.now()
        with transaction.atomic():
            # balance is forced to zero together with the write-off mark, the
            # same invariant Receivable.save() maintains from now on.
            updated = qs.update(
                written_off_at=now,
                written_off_reason=reason,
                balance=Decimal('0'),
                updated_at=now,
            )
            AuditLog.objects.create(
                user=None,
                action='legacy_reset_write_off',
                model_name='Receivable',
                object_id='bulk',
                object_repr=f'{updated} recebimentos baixados (R$ {total})',
                reason=reason,
                metadata={
                    'cutoff_due_before': cutoff.isoformat(),
                    'written_off_count': updated,
                    'written_off_balance': str(total),
                    'executed_at': now.isoformat(),
                },
            )
        self.stdout.write(self.style.SUCCESS(f'  Baixados: {updated} (saldo R$ {total} zerado)'))
