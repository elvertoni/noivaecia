import argparse

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from catalog.models import Product
from core.management.commands.normalize_cities import normalize as normalize_city
from core.management.commands.rebuild_search_fields import CUSTOMER_SEARCH_FIELDS
from customers.models import Customer, _digits_only, _normalize_name


def _positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError('deve ser um numero inteiro') from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError('deve ser maior que zero')
    return parsed


def _city_plan():
    plan = []
    total = 0
    city_counts = (
        Customer.objects.values('city')
        .annotate(total=Count('id'))
        .order_by('city')
    )
    for entry in city_counts:
        canonical = normalize_city(entry['city'])
        if canonical is None:
            continue
        plan.append((entry['city'], canonical))
        total += entry['total']
    return plan, total


def _expected_customer_fields(customer):
    return {
        'cpf_digits': _digits_only(customer.cpf),
        'cnpj_digits': _digits_only(customer.cnpj),
        'rg_digits': _digits_only(customer.rg),
        'phone_home_digits': _digits_only(customer.phone_home),
        'phone_mobile_digits': _digits_only(customer.phone_mobile),
        'phone_work_digits': _digits_only(customer.phone_work),
        'name_search': _normalize_name(customer.name),
    }


def _rebuild_customer_search(batch_size, *, apply):
    changed = 0
    pending = []
    queryset = Customer.objects.only(
        'id',
        'cpf',
        'cnpj',
        'rg',
        'phone_home',
        'phone_mobile',
        'phone_work',
        'name',
        *CUSTOMER_SEARCH_FIELDS,
    ).order_by('id')

    for customer in queryset.iterator(chunk_size=batch_size):
        expected = _expected_customer_fields(customer)
        if all(getattr(customer, field) == value for field, value in expected.items()):
            continue
        changed += 1
        if not apply:
            continue
        for field, value in expected.items():
            setattr(customer, field, value)
        pending.append(customer)
        if len(pending) == batch_size:
            Customer.objects.bulk_update(pending, CUSTOMER_SEARCH_FIELDS, batch_size=batch_size)
            pending.clear()

    if pending:
        Customer.objects.bulk_update(pending, CUSTOMER_SEARCH_FIELDS, batch_size=batch_size)
    return changed


def _rebuild_product_search(batch_size, *, apply):
    changed = 0
    pending = []
    queryset = Product.objects.only(
        'id',
        'description',
        'description_search',
    ).order_by('id')

    for product in queryset.iterator(chunk_size=batch_size):
        expected = _normalize_name(product.description)
        if product.description_search == expected:
            continue
        changed += 1
        if not apply:
            continue
        product.description_search = expected
        pending.append(product)
        if len(pending) == batch_size:
            Product.objects.bulk_update(
                pending,
                ('description_search',),
                batch_size=batch_size,
            )
            pending.clear()

    if pending:
        Product.objects.bulk_update(
            pending,
            ('description_search',),
            batch_size=batch_size,
        )
    return changed


class Command(BaseCommand):
    help = (
        'Executa o pos-processamento idempotente da importacao legada: '
        'normaliza cidades, reconstrói campos de busca e zera valores de produtos.'
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            '--apply',
            action='store_true',
            help='Aplica as correcoes. Sem esta flag o comando apenas simula.',
        )
        mode.add_argument(
            '--dry-run',
            action='store_true',
            help='Explicita o modo de simulacao (padrao), sem gravar no banco.',
        )
        parser.add_argument(
            '--batch-size',
            type=_positive_int,
            default=1000,
            help='Quantidade positiva de registros processados por lote (padrao: 1000).',
        )

    def handle(self, *args, **options):
        try:
            batch_size = _positive_int(options['batch_size'])
        except argparse.ArgumentTypeError as exc:
            raise CommandError(f'--batch-size {exc}') from exc

        apply = options['apply']
        with transaction.atomic():
            if apply:
                # This command runs in a maintenance window. Row locks also
                # prevent concurrent edits from producing a partial report.
                list(
                    Customer.objects.select_for_update().values_list('pk', flat=True)
                )
                list(Product.objects.select_for_update().values_list('pk', flat=True))

            city_plan, city_count = _city_plan()
            positive_value_count = Product.objects.filter(value__gt=0).count()

            if apply:
                for original, canonical in city_plan:
                    Customer.objects.filter(city=original).update(city=canonical)

            customer_search_count = _rebuild_customer_search(
                batch_size,
                apply=apply,
            )
            product_search_count = _rebuild_product_search(
                batch_size,
                apply=apply,
            )
            if apply and positive_value_count:
                Product.objects.filter(value__gt=0).update(value=0)

        mode = 'APLICADO' if apply else 'SIMULACAO'
        self.stdout.write(f'Modo: {mode}')
        self.stdout.write(f'Cidades a normalizar: {city_count}')
        self.stdout.write(f'Clientes com busca a reconstruir: {customer_search_count}')
        self.stdout.write(f'Produtos com busca a reconstruir: {product_search_count}')
        self.stdout.write(f'Produtos com valor a zerar: {positive_value_count}')
        self.stdout.write(
            self.style.SUCCESS(
                'Pos-importacao concluida sem alterar a configuracao da empresa.'
            )
        )
