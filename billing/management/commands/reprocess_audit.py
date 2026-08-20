"""Classify every schedule rewrite produced by ``reprocess_future_installments``.

Read-only. The action behind the old "Atualizar parcelas" button rewrote payment
plans whenever an operator read the label as "reload the screen", so each event
has to be classified before anyone decides what to restore. The ``AuditLog``
metadata carries the full ``previous_schedule``, which makes the comparison
possible without guessing.
"""

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import FinancialMovement, Receivable
from core.models import AuditLog


ACTION = 'reprocess_future_installments'

HEADERS = (
    'Log',
    'Quando',
    'Locação',
    'Classificação',
    'Detalhe',
)
CSV_HEADERS = (
    'audit_log_id',
    'occurred_at',
    'rental_id',
    'classification',
    'detail',
)

NO_OP = 'no-op'
DUE_DATE_MOVED = 'vencimento-alterado'
PARTIAL_REWRITTEN = 'valor-reescrito'
BOTH = 'vencimento-e-valor'


def _schedule_signature(entries):
    """Compare plans by what the customer agreed to, not by row identity.

    A rewrite that recreates the same amounts on the same dates only churns
    primary keys; that is not something anyone needs to restore.
    """
    return sorted(
        (entry['due_date'], Decimal(entry['amount'])) for entry in entries
    )


def _money_touched(log):
    """Report any deleted title that carried money — must always be empty.

    Three independent barriers should make this impossible: the rewrite skips
    titles with ``paid_amount``, skips titles with a ``Payment`` row, and the
    database PROTECTs any title referenced by a ``ReceiptAllocation``. This
    check exists to prove the barriers held, not because they are expected to
    fail — a non-empty result means a registered receipt was destroyed.
    """
    metadata = log.metadata or {}
    deleted = set(metadata.get('deleted_receivable_ids') or [])
    if not deleted:
        return []
    return [
        entry for entry in (metadata.get('previous_schedule') or [])
        if entry['id'] in deleted and Decimal(entry['paid_amount']) != 0
    ]


def _classify(log):
    metadata = log.metadata or {}
    previous = metadata.get('previous_schedule') or []
    adjusted = metadata.get('adjusted_partials') or []

    current = _schedule_signature([
        {'due_date': item.due_date.isoformat(), 'amount': str(item.amount)}
        for item in Receivable.objects.filter(rental_id=log.object_id)
    ])
    before = _schedule_signature(previous)

    schedule_changed = current != before
    if adjusted and schedule_changed:
        classification = BOTH
    elif adjusted:
        classification = PARTIAL_REWRITTEN
    elif schedule_changed:
        classification = DUE_DATE_MOVED
    else:
        classification = NO_OP

    details = []
    for item in adjusted:
        details.append(
            'título {due}: R$ {before} -> R$ {after}'.format(
                due=item['due_date'],
                before=item['previous_amount'],
                after=item['amount'],
            )
        )
    if schedule_changed:
        details.append(
            'plano anterior {before} · plano atual {current}'.format(
                before=[f'{due}=R$ {amount}' for due, amount in before],
                current=[f'{due}=R$ {amount}' for due, amount in current],
            )
        )
    return classification, '; '.join(details) or 'sem diferença'


class Command(BaseCommand):
    help = (
        'Classifica os reprocessamentos de parcelas registrados no AuditLog '
        '(no-op / vencimento alterado / valor reescrito). Somente leitura.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            dest='csv_path',
            help='Caminho opcional para exportar o resultado em CSV.',
        )
        parser.add_argument(
            '--only-changed',
            action='store_true',
            help='Omite os eventos classificados como no-op.',
        )

    def handle(self, *args, **options):
        logs = AuditLog.objects.filter(action=ACTION).order_by('created_at')

        rows = []
        tally = Counter()
        money_alerts = []
        affected_rentals = set()
        for log in logs:
            classification, detail = _classify(log)
            tally[classification] += 1
            affected_rentals.add(log.object_id)
            for entry in _money_touched(log):
                money_alerts.append(
                    f'#{log.pk} · locação {log.object_id} · título {entry["id"]} '
                    f'apagado com R$ {entry["paid_amount"]} recebido'
                )
            if options['only_changed'] and classification == NO_OP:
                continue
            rows.append((
                str(log.pk),
                timezone.localtime(log.created_at).isoformat(sep=' ', timespec='minutes'),
                str(log.object_id),
                classification,
                detail,
            ))

        for row in rows:
            self.stdout.write(
                f'#{row[0]} · {row[1]} · locação {row[2]} · {row[3]}\n    {row[4]}'
            )

        self.stdout.write('')
        self.stdout.write(f'Total de eventos: {sum(tally.values())}')
        for classification in (NO_OP, DUE_DATE_MOVED, PARTIAL_REWRITTEN, BOTH):
            self.stdout.write(f'  {classification}: {tally[classification]}')

        self.stdout.write('')
        self.stdout.write('Integridade dos recebimentos:')
        if money_alerts:
            self.stdout.write(self.style.ERROR(
                'ALERTA: título com valor recebido foi apagado. '
                'Investigue antes de qualquer outra ação.'
            ))
            for alert in money_alerts:
                self.stdout.write(self.style.ERROR(f'  {alert}'))
        else:
            self.stdout.write(self.style.SUCCESS(
                '  nenhum título com valor recebido foi apagado.'
            ))

        # ``FinancialMovement.receivable`` is SET_NULL, so a deleted title
        # detaches its cash movement instead of destroying it. The money
        # survives either way; this reports the lost traceability.
        orphans = FinancialMovement.objects.filter(
            rental_id__in=affected_rentals, receivable__isnull=True
        ).count()
        if orphans:
            self.stdout.write(self.style.WARNING(
                f'  {orphans} movimento(s) de caixa nas locações afetadas estão '
                'sem título vinculado (o valor permanece no caixa).'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                '  nenhum movimento de caixa ficou sem título vinculado.'
            ))

        needing_review = (
            tally[DUE_DATE_MOVED] + tally[PARTIAL_REWRITTEN] + tally[BOTH]
        )
        if needing_review:
            self.stdout.write(self.style.WARNING(
                f'{needing_review} evento(s) alteraram o plano combinado com a '
                'cliente. Nenhuma reversão é aplicada por este comando: confirme '
                'caso a caso antes de mexer.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Nenhum plano de parcelas foi alterado de forma relevante.'
            ))

        if options['csv_path']:
            csv_path = Path(options['csv_path'])
            with csv_path.open('w', encoding='utf-8', newline='') as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(CSV_HEADERS)
                writer.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f'CSV salvo em {csv_path}'))
