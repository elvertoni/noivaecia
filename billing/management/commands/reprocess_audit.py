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

from billing.models import FinancialMovement
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
ADDED = 'parcela-adicionada'
DUE_DATE_MOVED = 'vencimento-alterado'
PARTIAL_REWRITTEN = 'valor-reescrito'
BOTH = 'vencimento-e-valor'

CLASSIFICATIONS = (NO_OP, ADDED, DUE_DATE_MOVED, PARTIAL_REWRITTEN, BOTH)

# Only these mean the plan agreed with the customer stopped holding.
NEEDS_REVIEW = (DUE_DATE_MOVED, PARTIAL_REWRITTEN, BOTH)


def _schedule_signature(entries):
    """Compare plans by what the customer agreed to, not by row identity.

    A rewrite that recreates the same amounts on the same dates only churns
    primary keys; that is not something anyone needs to restore.
    """
    return sorted(
        (entry['due_date'], Decimal(entry['amount'])) for entry in entries
    )


def _resulting_schedule(metadata):
    """Rebuild the plan as it stood right after *this* event.

    Comparing against the rental's current receivables would be wrong: a rental
    reprocessed more than once would have every earlier event judged against
    the outcome of the later ones. The metadata is self-contained — protected
    titles (with any adjusted amount applied) plus the titles just created.
    """
    protected_ids = set(metadata.get('protected_receivable_ids') or [])
    adjusted = {
        item['id']: item['amount']
        for item in (metadata.get('adjusted_partials') or [])
    }
    kept = [
        {
            'due_date': entry['due_date'],
            'amount': adjusted.get(entry['id'], entry['amount']),
        }
        for entry in (metadata.get('previous_schedule') or [])
        if entry['id'] in protected_ids
    ]
    created = [
        {'due_date': entry['due_date'], 'amount': entry['amount']}
        for entry in (metadata.get('new_schedule') or [])
    ]
    return kept + created


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
    adjusted = metadata.get('adjusted_partials') or []

    before = _schedule_signature(metadata.get('previous_schedule') or [])
    after = _schedule_signature(_resulting_schedule(metadata))

    # A commitment the customer already had must still be there afterwards.
    # Extra rows on top of that are balance that simply had not been scheduled
    # yet — the panel doing its job, not a plan being rewritten.
    surviving = list(after)
    lost = []
    for commitment in before:
        if commitment in surviving:
            surviving.remove(commitment)
        else:
            lost.append(commitment)

    if adjusted and lost:
        classification = BOTH
    elif adjusted:
        classification = PARTIAL_REWRITTEN
    elif lost:
        classification = DUE_DATE_MOVED
    elif surviving:
        classification = ADDED
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
    if lost:
        details.append(
            'deixou de existir {lost} · passou a ser {after}'.format(
                lost=[f'{due}=R$ {amount}' for due, amount in lost],
                after=[f'{due}=R$ {amount}' for due, amount in after],
            )
        )
    elif surviving:
        details.append(
            'acrescentou {extra} sem mexer no que já existia'.format(
                extra=[f'{due}=R$ {amount}' for due, amount in surviving],
            )
        )
    return classification, '; '.join(details) or 'sem diferença'


class Command(BaseCommand):
    help = (
        'Classifica os reprocessamentos de parcelas registrados no AuditLog: '
        'no-op, parcela adicionada, vencimento alterado, valor reescrito. '
        'Somente leitura — nada é revertido.'
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
            help=(
                'Mostra apenas os eventos que desfizeram um compromisso já '
                'existente (omite no-op e parcela-adicionada).'
            ),
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
            if options['only_changed'] and classification not in NEEDS_REVIEW:
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
        for classification in CLASSIFICATIONS:
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

        needing_review = sum(tally[name] for name in NEEDS_REVIEW)
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
