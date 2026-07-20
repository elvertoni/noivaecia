"""Read-only report of customers sharing the same CPF (RF-11 follow-up).

Never merges or edits records — CPF/RG/phone are legacy data inherited from
the Access system, which had no uniqueness constraint. Fixing duplicates in
bulk risks corrupting valid, distinct customers (see db-migra.md, "não
consertar CPF/RG/telefone em massa sem regra clara"). This command only lists
the groups so a human can review and decide, case by case, whether to merge.

Usage:
    python manage.py cpf_duplicate_report [--output-dir DIR]
"""

from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min
from django.db.models.functions import Length

from customers.models import Customer


class Command(BaseCommand):
    help = 'Read-only report of customers sharing the same CPF; no writes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default='var/homologation',
            help='Diretório onde salvar o relatório em markdown.',
        )

    def handle(self, *args, **options):
        groups = (
            Customer.objects.annotate(cpf_len=Length('cpf_digits'))
            .filter(cpf_len=11)
            .values('cpf_digits')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
            .order_by('-n', 'cpf_digits')
        )
        groups = list(groups)

        total_customers = sum(g['n'] for g in groups)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'cpf_duplicate_report — {len(groups)} grupo(s), '
            f'{total_customers} cliente(s) envolvido(s)'
        ))

        lines = [
            '# Relatório de CPF duplicado',
            '',
            f'Gerado em {datetime.now().isoformat(timespec="seconds")}. '
            'Somente leitura — nenhuma alteração foi feita. Revisão humana '
            'necessária antes de qualquer mesclagem.',
            '',
            f'Total: {len(groups)} grupo(s) de CPF duplicado, '
            f'{total_customers} cliente(s) envolvido(s).',
            '',
        ]

        for group in groups:
            cpf = group['cpf_digits']
            customers = (
                Customer.objects.filter(cpf_digits=cpf)
                .annotate(
                    rental_count=Count('rentals'),
                    first_rental=Min('rentals__pickup_date'),
                    last_rental=Max('rentals__pickup_date'),
                )
                .order_by('-rental_count', 'id')
            )
            lines.append(f'## CPF {cpf} ({group["n"]} registros)')
            lines.append('')
            lines.append(
                '| id | legacy_id | nome | cidade | ativo | locações | '
                'primeira | última |'
            )
            lines.append('|---|---|---|---|---|---|---|---|')
            for customer in customers:
                lines.append(
                    f'| {customer.pk} | {customer.legacy_id or "-"} | '
                    f'{customer.name} | {customer.city} | '
                    f'{"sim" if customer.is_active else "não"} | '
                    f'{customer.rental_count} | '
                    f'{customer.first_rental or "-"} | '
                    f'{customer.last_rental or "-"} |'
                )
            lines.append('')

        output_dir = Path(options['output_dir'])
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        output_path = output_dir / f'{timestamp}-cpf-duplicates.md'
        output_path.write_text('\n'.join(lines), encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(f'Relatório salvo em {output_path}'))
