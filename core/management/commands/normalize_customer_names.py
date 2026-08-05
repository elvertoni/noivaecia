"""
Uppercase every Customer.name in bulk (one-off backfill).

New/edited records are already forced to uppercase by Customer.save() —
this command only catches records written before that change existed.

Usage:
    python manage.py normalize_customer_names             # preview (safe)
    python manage.py normalize_customer_names --apply      # apply
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from customers.models import Customer


class Command(BaseCommand):
    help = (
        'Uppercase every Customer.name. By default (no flags), previews '
        'changes without saving. Use --apply to save.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply changes to the database. Without this flag, only preview is shown.',
        )

    def handle(self, *args, **options):
        should_apply = options['apply']

        if not should_apply:
            self.stdout.write(self.style.WARNING('PREVIEW MODE — no changes will be saved.\n'))

        planned = []
        for pk, name in Customer.objects.values_list('pk', 'name'):
            new_name = (name or '').strip().upper()
            if new_name != name:
                planned.append((pk, name, new_name))

        if not planned:
            self.stdout.write(self.style.SUCCESS('Nenhum nome precisa de normalização.'))
            return

        for pk, old, new in planned[:20]:
            self.stdout.write(f'  #{pk}  {old!r} -> {new!r}')
        if len(planned) > 20:
            self.stdout.write(f'  ... e mais {len(planned) - 20} registro(s).')

        self.stdout.write(f'\nTotal: {len(planned)} cliente(s) a normalizar.\n')

        if not should_apply:
            self.stdout.write(self.style.WARNING('PREVIEW — rode com --apply para salvar.'))
            return

        with transaction.atomic():
            for pk, _old, new in planned:
                Customer.objects.filter(pk=pk).update(name=new)

        self.stdout.write(self.style.SUCCESS(f'\nConcluído: {len(planned)} cliente(s) atualizado(s).'))
