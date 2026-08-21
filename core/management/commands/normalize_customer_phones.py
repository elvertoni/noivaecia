"""Normalize legacy ``Customer.phone_mobile`` values into WhatsApp-ready numbers.

Most of the customer base came from the Access system, where the mobile phone
was typed free-form: ``9 96634657`` (no area code), ``88083861`` (no area code
and no post-2015 ninth digit), ``043 99185309`` (legacy trunk prefix). None of
those pass ``notifications.services.format_whatsapp_number``, so pickup and
return reminders never reach those customers.

This command rewrites only the shapes that can be repaired without guessing a
single digit, and leaves everything else byte-for-byte intact. It writes to
``phone_mobile`` and lets ``Customer.save()`` re-derive ``phone_mobile_digits``,
so the two fields can never drift apart.

Categories (each one reported separately):

``ok``                 already accepted as-is — untouched
``add_ddd``            9 digits starting with 9 -> default area code + number
``add_ddd_and_ninth``  8 digits starting with 8/9 -> default area code + 9 + number
``add_ninth``          10 digits, area code + 8/9 -> the Anatel ninth digit
                       (except a leading ``99``, which is ambiguous — see below)
``strip_zeros``        leading legacy trunk zeros over an otherwise valid number
``landline``           landline shape — untouched (landlines do not do WhatsApp)
``empty``              no mobile on record — untouched
``unknown``            anything else — untouched, reported with samples

Usage:
    python manage.py normalize_customer_phones                      # preview
    python manage.py normalize_customer_phones --apply              # write
    python manage.py normalize_customer_phones --ddd 41             # other area code
    python manage.py normalize_customer_phones --report var/tel.csv # audit trail
"""

import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from customers.models import Customer

DEFAULT_DDD = '43'

# How many rows are fetched/saved per batch during --apply.
BATCH_SIZE = 500

# How many examples are printed per reported category.
SAMPLE_SIZE = 10

CATEGORY_OK = 'ok'
CATEGORY_ADD_DDD = 'add_ddd'
CATEGORY_ADD_DDD_AND_NINTH = 'add_ddd_and_ninth'
CATEGORY_ADD_NINTH = 'add_ninth'
CATEGORY_STRIP_ZEROS = 'strip_zeros'
CATEGORY_LANDLINE = 'landline'
CATEGORY_EMPTY = 'empty'
CATEGORY_UNKNOWN = 'unknown'

# The only categories this command is allowed to write. Everything else is
# preserved exactly as imported — a wrong number is worse than a missing one.
TRANSFORM_CATEGORIES = (
    CATEGORY_ADD_DDD,
    CATEGORY_ADD_DDD_AND_NINTH,
    CATEGORY_ADD_NINTH,
    CATEGORY_STRIP_ZEROS,
)

# Categories that get printed examples: everything transformed, plus the
# unrecognized ones so a human can eyeball what the command refused to touch.
SAMPLED_CATEGORIES = TRANSFORM_CATEGORIES + (CATEGORY_UNKNOWN,)

CATEGORY_ORDER = (
    CATEGORY_OK,
    CATEGORY_ADD_DDD,
    CATEGORY_ADD_DDD_AND_NINTH,
    CATEGORY_ADD_NINTH,
    CATEGORY_STRIP_ZEROS,
    CATEGORY_LANDLINE,
    CATEGORY_EMPTY,
    CATEGORY_UNKNOWN,
)

CATEGORY_LABELS = {
    CATEGORY_OK: 'já válido para WhatsApp — mantido',
    CATEGORY_ADD_DDD: 'sem DDD (9 dígitos) — recebe o DDD padrão',
    CATEGORY_ADD_DDD_AND_NINTH: 'sem DDD e sem o nono dígito (8 dígitos) — recebe DDD padrão + 9',
    CATEGORY_ADD_NINTH: 'com DDD, sem o nono dígito (10 dígitos) — recebe o 9',
    CATEGORY_STRIP_ZEROS: 'zeros à esquerda (prefixo antigo) — zeros removidos',
    CATEGORY_LANDLINE: 'telefone fixo — mantido (fixo não recebe WhatsApp)',
    CATEGORY_EMPTY: 'sem celular cadastrado — mantido',
    CATEGORY_UNKNOWN: 'formato não reconhecido — mantido',
}

CSV_HEADERS = (
    'customer_id',
    'name',
    'phone_mobile_antigo',
    'phone_mobile_novo',
    'categoria',
)

SAVED_FIELDS = ('phone_mobile', 'phone_mobile_digits', 'updated_at')


def digits_of(value):
    return re.sub(r'\D', '', value or '')


def is_plausible_ddd(pair):
    """Brazilian area codes run from 11 to 99."""
    return len(pair) == 2 and pair.isdigit() and 11 <= int(pair) <= 99


def classify_phone(value, default_ddd=DEFAULT_DDD):
    """Classify a raw ``phone_mobile`` value.

    Returns ``(category, new_digits)``. ``new_digits`` is the replacement value
    for transform categories and ``''`` for every category that must be left
    untouched.
    """
    digits = digits_of(value)
    if not digits:
        return CATEGORY_EMPTY, ''

    if digits.startswith('0'):
        # Legacy trunk prefix, e.g. '043 99185309' or '011 72729146'.
        stripped = digits.lstrip('0')
        if not stripped:
            # '0', '000': there is a value on record, it just means nothing.
            return CATEGORY_UNKNOWN, ''
        category, new_digits = _classify_digits(stripped, default_ddd)
        if category == CATEGORY_OK:
            # Valid number wearing extra zeros: the zeros are the whole defect,
            # and while they stay, format_whatsapp_number rejects the record.
            return CATEGORY_STRIP_ZEROS, stripped
        return category, new_digits

    return _classify_digits(digits, default_ddd)


def _classify_digits(digits, default_ddd):
    size = len(digits)

    # Already accepted by notifications.services.format_whatsapp_number.
    if size == 11 and is_plausible_ddd(digits[:2]) and digits[2] == '9':
        return CATEGORY_OK, ''
    if size in (12, 13) and digits.startswith('55'):
        return CATEGORY_OK, ''

    # Repairable shapes.
    if size == 9 and digits[0] == '9':
        return CATEGORY_ADD_DDD, f'{default_ddd}{digits}'
    if size == 8 and digits[0] in '89':
        # Anatel's 2015 rule: eight-digit mobiles gained a leading 9.
        return CATEGORY_ADD_DDD_AND_NINTH, f'{default_ddd}9{digits}'
    if size == 10 and is_plausible_ddd(digits[:2]) and digits[2] in '89':
        if digits.startswith('99'):
            # Ambiguous on purpose. A stray leading '9' typed before a complete
            # 9-digit mobile ('9 984887097') is byte-for-byte indistinguishable
            # from area code 99 (Maranhão) plus a pre-2015 mobile. Reading it
            # either way is a guess, and every such record in this base belongs
            # to a customer in the store's own region in Paraná, so area code 99
            # would be flatly wrong. Report it and let a human decide.
            return CATEGORY_UNKNOWN, ''
        return CATEGORY_ADD_NINTH, f'{digits[:2]}9{digits[2:]}'

    # Landlines: reported, never rewritten.
    if size == 8 and digits[0] in '2345':
        return CATEGORY_LANDLINE, ''
    if size == 10 and digits[2] in '2345':
        return CATEGORY_LANDLINE, ''

    return CATEGORY_UNKNOWN, ''


class Command(BaseCommand):
    help = (
        'Normaliza Customer.phone_mobile para o formato aceito pelo WhatsApp. '
        'Sem flags, apenas mostra o que seria alterado. Use --apply para gravar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Grava as alterações. Sem esta flag, apenas o preview é exibido.',
        )
        parser.add_argument(
            '--ddd',
            default=DEFAULT_DDD,
            help=f'DDD padrão para números sem DDD (padrão: {DEFAULT_DDD}).',
        )
        parser.add_argument(
            '--report',
            dest='report_path',
            help=(
                'Caminho do CSV de auditoria com todos os clientes classificados '
                '(inclusive os não alterados). É a trilha de reversão.'
            ),
        )

    def handle(self, *args, **options):
        should_apply = options['apply']
        default_ddd = str(options['ddd']).strip()
        if not is_plausible_ddd(default_ddd):
            raise CommandError('--ddd deve ter 2 dígitos entre 11 e 99.')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'normalize_customer_phones — DDD padrão {default_ddd}'
        ))
        if not should_apply:
            self.stdout.write(self.style.WARNING(
                'MODO PREVIEW — nada será gravado.'
            ))

        counts = {category: 0 for category in CATEGORY_ORDER}
        samples = {category: [] for category in CATEGORY_ORDER}
        rows = []
        pending = []
        stale_digits = 0

        queryset = (
            Customer.objects
            .order_by('pk')
            .values_list('pk', 'name', 'phone_mobile', 'phone_mobile_digits')
        )
        for pk, name, phone_mobile, phone_mobile_digits in queryset.iterator(
            chunk_size=BATCH_SIZE
        ):
            old_value = phone_mobile or ''
            category, new_digits = classify_phone(old_value, default_ddd)
            counts[category] += 1
            transforms = category in TRANSFORM_CATEGORIES
            new_value = new_digits if transforms else old_value

            if transforms:
                pending.append((pk, new_digits))
            elif (phone_mobile_digits or '') != digits_of(old_value):
                # Left untouched here, but its derived column is out of sync
                # (legacy import wrote phone_mobile without the digits field).
                stale_digits += 1

            rows.append((pk, name or '', old_value, new_value, category))
            if len(samples[category]) < SAMPLE_SIZE:
                samples[category].append((pk, old_value, new_value))

        total = len(rows)
        self.stdout.write('\nResumo por categoria:')
        width = max(len(category) for category in CATEGORY_ORDER)
        for category in CATEGORY_ORDER:
            self.stdout.write(
                f'  {category.ljust(width)}  {counts[category]:>7}  '
                f'{CATEGORY_LABELS[category]}'
            )
        self.stdout.write(f'\nTotal classificado: {total} cliente(s).')
        self.stdout.write(f'Total a alterar: {len(pending)} cliente(s).')

        for category in SAMPLED_CATEGORIES:
            if not samples[category]:
                continue
            self.stdout.write(f'\nExemplos ({category}):')
            for pk, old_value, new_value in samples[category]:
                if category in TRANSFORM_CATEGORIES:
                    self.stdout.write(f'  #{pk}  {old_value!r} -> {new_value!r}')
                else:
                    self.stdout.write(f'  #{pk}  {old_value!r}')
            remaining = counts[category] - len(samples[category])
            if remaining > 0:
                self.stdout.write(f'  ... e mais {remaining} registro(s).')

        if options['report_path']:
            self._write_report(Path(options['report_path']), rows)

        if stale_digits:
            self.stdout.write(self.style.WARNING(
                f'\nAviso: {stale_digits} cliente(s) não alterados têm '
                'phone_mobile_digits fora de sincronia com phone_mobile. '
                'Este comando não mexe neles; rode '
                '"manage.py rebuild_search_fields" para ressincronizar.'
            ))

        if not pending:
            self.stdout.write(self.style.SUCCESS(
                '\nNenhum telefone precisa de transformação.'
            ))
            return

        if not should_apply:
            self.stdout.write(self.style.WARNING(
                '\nPREVIEW — nada foi gravado. Rode com --apply para salvar.'
            ))
            return

        updated = self._apply(pending)
        self.stdout.write(self.style.SUCCESS(
            f'\nConcluído: {updated} cliente(s) atualizado(s).'
        ))

    def _apply(self, pending):
        """Save each new number through ``Customer.save()``.

        Never ``.update()``: ``phone_mobile_digits`` is derived from
        ``phone_mobile`` inside ``save()``, and a bulk update would leave the
        two columns contradicting each other.
        """
        updated = 0
        with transaction.atomic():
            for start in range(0, len(pending), BATCH_SIZE):
                chunk = dict(pending[start:start + BATCH_SIZE])
                for customer in Customer.objects.filter(pk__in=chunk):
                    customer.phone_mobile = chunk[customer.pk]
                    customer.save(update_fields=SAVED_FIELDS)
                    updated += 1
        return updated

    def _write_report(self, path, rows):
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8', newline='') as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(CSV_HEADERS)
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f'\nCSV salvo em {path}'))
