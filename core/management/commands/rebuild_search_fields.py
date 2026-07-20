import argparse

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Product
from customers.models import Customer, _digits_only, _normalize_name


CUSTOMER_SEARCH_FIELDS = (
    'cpf_digits',
    'rg_digits',
    'phone_home_digits',
    'phone_mobile_digits',
    'phone_work_digits',
    'name_search',
)


def _positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError('deve ser um numero inteiro') from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError('deve ser maior que zero')
    return parsed


def _rebuild_customers(batch_size):
    updated = 0
    pending = []
    queryset = Customer.objects.only(
        'id',
        'cpf',
        'rg',
        'phone_home',
        'phone_mobile',
        'phone_work',
        'name',
        *CUSTOMER_SEARCH_FIELDS,
    ).order_by('id')

    for customer in queryset.iterator(chunk_size=batch_size):
        expected = {
            'cpf_digits': _digits_only(customer.cpf),
            'rg_digits': _digits_only(customer.rg),
            'phone_home_digits': _digits_only(customer.phone_home),
            'phone_mobile_digits': _digits_only(customer.phone_mobile),
            'phone_work_digits': _digits_only(customer.phone_work),
            'name_search': _normalize_name(customer.name),
        }
        if all(getattr(customer, field) == value for field, value in expected.items()):
            continue

        for field, value in expected.items():
            setattr(customer, field, value)
        pending.append(customer)

        if len(pending) == batch_size:
            Customer.objects.bulk_update(
                pending,
                CUSTOMER_SEARCH_FIELDS,
                batch_size=batch_size,
            )
            updated += len(pending)
            pending.clear()

    if pending:
        Customer.objects.bulk_update(
            pending,
            CUSTOMER_SEARCH_FIELDS,
            batch_size=batch_size,
        )
        updated += len(pending)

    return updated


def _rebuild_products(batch_size):
    updated = 0
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

        product.description_search = expected
        pending.append(product)

        if len(pending) == batch_size:
            Product.objects.bulk_update(
                pending,
                ('description_search',),
                batch_size=batch_size,
            )
            updated += len(pending)
            pending.clear()

    if pending:
        Product.objects.bulk_update(
            pending,
            ('description_search',),
            batch_size=batch_size,
        )
        updated += len(pending)

    return updated


class Command(BaseCommand):
    help = 'Reconstrói os campos normalizados de busca de clientes e produtos.'

    def add_arguments(self, parser):
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

        customer_count = _rebuild_customers(batch_size)
        product_count = _rebuild_products(batch_size)

        self.stdout.write(
            self.style.SUCCESS(
                'Campos de busca reconstruidos: '
                f'{customer_count} cliente(s) e {product_count} produto(s) atualizados.'
            )
        )
