"""List legacy migration write-offs that still hide a residual balance."""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.services import legacy_writeoff_review_queryset


HEADERS = (
    'Locação',
    'Cliente',
    'Vencimento',
    'Valor',
    'Valor pago',
    'Saldo oculto',
    'Baixado em',
)
CSV_HEADERS = (
    'rental_number',
    'customer_name',
    'due_date',
    'amount',
    'paid_amount',
    'hidden_balance',
    'written_off_at',
)


def _row(receivable):
    written_off_at = timezone.localtime(receivable.written_off_at)
    return (
        str(receivable.rental.number),
        receivable.rental.customer.name,
        receivable.due_date.isoformat(),
        str(receivable.amount),
        str(receivable.paid_amount),
        str(receivable.hidden_balance),
        written_off_at.isoformat(sep=' ', timespec='seconds'),
    )


class Command(BaseCommand):
    help = 'Lista baixas da migração legada com saldo real residual; somente leitura.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            dest='csv_path',
            help='Caminho opcional para exportar o resultado em CSV.',
        )

    def handle(self, *args, **options):
        rows = [_row(receivable) for receivable in legacy_writeoff_review_queryset()]
        widths = [
            max(len(header), *(len(row[index]) for row in rows)) if rows else len(header)
            for index, header in enumerate(HEADERS)
        ]
        self.stdout.write(' | '.join(
            header.ljust(widths[index])
            for index, header in enumerate(HEADERS)
        ))
        self.stdout.write('-+-'.join('-' * width for width in widths))
        for row in rows:
            self.stdout.write(' | '.join(
                value.ljust(widths[index])
                for index, value in enumerate(row)
            ))
        self.stdout.write(f'Total: {len(rows)} recebível(is).')

        if options['csv_path']:
            csv_path = Path(options['csv_path'])
            with csv_path.open('w', encoding='utf-8', newline='') as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(CSV_HEADERS)
                writer.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f'CSV salvo em {csv_path}'))
