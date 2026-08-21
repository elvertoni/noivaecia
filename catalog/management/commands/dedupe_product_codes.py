"""Collapse products that share a ``(category, code)`` slot.

The legacy BRcom system bound one code to one row for good: retiring an item
rewrote its description to ``NULO`` and reusing the code rewrote that same row
back into service.  The Django port lost that invariant — retiring left the row
in place but reusing the code inserted a *second* row — so the catalogue now
carries duplicate slots from two eras: pairs inherited from the Access import
and pairs the operators created since go-live.

Merging is safe because ``RentalItem`` freezes a full snapshot of the piece
(prefix, code, description, colour, size) when the line is created, and
``RentalItem.save`` only refreshes it on a deliberate product swap.  Repointing
a rental line at the surviving row therefore changes nothing a contract, report
or rental screen displays.

Groups are classified before anything is touched:

``revival``
    At most one row carries a real description; the others are free-code
    markers left by the legacy convention (``NULO``, blank, or the importer's
    ``PREFIXCODE`` fallback for a blank Access description).  The real piece
    wins the slot.  Unambiguous — merged automatically.

``identical``
    Every real row has the same normalised description.  Same physical piece
    registered twice.  Unambiguous — merged automatically.

``divergent``
    Real rows disagree on the description ("SMOKING FORM" vs "SMOKING").
    Deciding they are the same piece is a call only the owner can make, so
    these are reported and left alone unless ``--pair`` names one explicitly.

``--quarantine`` handles the divergent leftovers without answering that
question: it retires the registration nobody is renting, so each code ends up
with a single live item.  When the data cannot tell them apart — both in use, or
neither — the code is reported instead of guessed, and ``--keep`` records the
owner's answer.

Usage::

    python manage.py dedupe_product_codes                  # dry-run, full report
    python manage.py dedupe_product_codes --apply          # merge the automatic classes
    python manage.py dedupe_product_codes --pair VF1304 --apply   # after triage
    python manage.py dedupe_product_codes --apply --quarantine --keep 9849
"""

import re
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from catalog.models import Product
from catalog.services import (
    is_free_code_slot,
    normalize_description,
    product_audit_snapshot,
)
from core.models import AuditLog
from rentals.models import RentalItem


COPIED_FIELDS = ('description', 'color', 'size', 'value', 'notes')

AUTOMATIC_CLASSES = ('revival', 'identical')


def classify(group):
    real = [product for product in group if not is_free_code_slot(product)]
    if len(real) <= 1:
        return 'revival'
    if len({normalize_description(product.description) for product in real}) == 1:
        return 'identical'
    return 'divergent'


def pick_winner(group, kind):
    """Row whose catalogue data the surviving slot should end up carrying."""
    real = [product for product in group if not is_free_code_slot(product)]
    if kind == 'revival':
        return real[0] if real else min(group, key=lambda p: p.pk)
    # Identical descriptions: prefer the most completely filled row, then the
    # most recently created one.
    return max(
        real,
        key=lambda p: (sum(1 for f in ('color', 'size') if (getattr(p, f) or '').strip()), p.pk),
    )


class Command(BaseCommand):
    help = 'Relata e funde produtos que dividem o mesmo par (categoria, código).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Grava as fusões. Sem esta flag o comando só relata (dry-run).',
        )
        parser.add_argument(
            '--pair',
            action='append',
            default=[],
            metavar='PREFIXOCODIGO',
            help=(
                'Restringe a um par específico, ex.: --pair VF1304. Use depois da '
                'triagem para fundir um caso divergente aprovado pela cliente.'
            ),
        )
        parser.add_argument(
            '--quarantine',
            action='store_true',
            help=(
                'Nos pares divergentes, anula os cadastros que não estão em uso para '
                'deixar um único item vivo por código. Não funde nada e não decide se '
                'as peças são iguais; apenas tira do acervo o cadastro sem locação.'
            ),
        )
        parser.add_argument(
            '--keep',
            action='append',
            default=[],
            metavar='PK',
            help=(
                'Registra a decisão da cliente sobre qual peça fica no acervo, por PK do '
                'produto, ex.: --keep 9849. Os demais cadastros ativos daquele código são '
                'anulados pela quarentena. Use quando ela informar qual peça está na loja.'
            ),
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        selected = self._parse_pairs(options['pair'])
        keep_pks = self._parse_keep(options['keep'])

        groups = self._load_groups(selected)
        if not groups:
            self.stdout.write(self.style.SUCCESS('Nenhum código duplicado encontrado.'))
            return

        buckets = defaultdict(list)
        for group in groups:
            buckets[classify(group)].append(group)

        for kind in ('revival', 'identical', 'divergent'):
            self._report(kind, buckets[kind])

        # An explicit --pair is a triage decision that already happened, so it
        # overrides the "divergent needs a human" rule for that pair only.
        mergeable = [
            group
            for kind in ('revival', 'identical', 'divergent')
            for group in buckets[kind]
            if kind in AUTOMATIC_CLASSES or selected
        ]

        self.stdout.write('')
        if not mergeable:
            self.stdout.write('Nada a fundir automaticamente.')
        elif not apply_changes:
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: {len(mergeable)} par(es) seriam fundidos. '
                'Rode novamente com --apply para gravar.'
            ))
        else:
            merged, relinked, removed = self._merge_all(mergeable)
            self.stdout.write(self.style.SUCCESS(
                f'{merged} par(es) fundidos · {relinked} item(ns) de locação repontados · '
                f'{removed} cadastro(s) duplicado(s) removido(s).'
            ))

        if options['quarantine'] and not selected:
            self._quarantine(buckets['divergent'], apply_changes, keep_pks)
        elif buckets['divergent'] and not selected:
            self.stdout.write(self.style.WARNING(
                f'{len(buckets["divergent"])} par(es) divergentes aguardam triagem da cliente. '
                'Use --quarantine para deixar um item vivo por código sem fundir nada, '
                'ou --pair depois da decisão dela.'
            ))

    def _quarantine(self, groups, apply_changes, keep_pks=()):
        """Leave a single live item per code without merging anything.

        Merging asks "are these the same physical piece?", which only the owner
        can answer.  Retiring the registration nobody is using asks the much
        smaller question "which of these is in the collection today?", and it is
        reversible — so the uniqueness rule can be enforced without waiting on
        that triage.
        """
        self.stdout.write('')
        self.stdout.write('== QUARENTENA — um item vivo por código ==')
        retired = 0
        blocked = []
        for group in groups:
            live = [product for product in group if product.is_active]
            if len(live) < 2:
                continue
            label = f'{group[0].category.prefix}{group[0].code}'
            # An explicit --keep is the owner's triage decision and outranks
            # anything inferred from the data.
            chosen = [product for product in live if product.pk in keep_pks]
            if len(chosen) > 1:
                raise CommandError(
                    f'--keep aponta {len(chosen)} cadastros do mesmo código {label}. '
                    'Informe apenas o que fica no acervo.'
                )
            used = [product for product in live if product.past_rentals]
            if not chosen and len(used) != 1:
                # Two registrations in real use, or none at all: nothing in the
                # data says which one is the piece on the rack, and retiring the
                # wrong one takes a rentable garment out of the collection.
                # Choosing by row order would be a guess wearing a rule's
                # clothes.  Owner's call.
                blocked.append((label, used or live))
                continue
            keep = chosen[0] if chosen else used[0]
            for product in live:
                if product.pk == keep.pk:
                    continue
                self.stdout.write(
                    f'  {label}: anula #{product.pk} {product.description!r} '
                    f'(mantém #{keep.pk} {keep.description!r})'
                )
                if apply_changes:
                    self._archive(product, keep)
                retired += 1

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: {retired} cadastro(s) seriam anulados.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f'{retired} cadastro(s) anulados.'))

        if blocked:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR(
                f'{len(blocked)} código(s) precisam da cliente para saber qual peça fica:'
            ))
            for label, candidates in blocked:
                detail = ' vs '.join(
                    f'#{p.pk} {p.description!r} ({p.past_rentals} loc)' for p in candidates
                )
                self.stdout.write(f'  {label}: {detail}')
            self.stdout.write(
                'Pergunte qual peça está na loja hoje e informe a decisão dela com '
                '--keep PK, ou funda com --pair se forem a mesma peça.'
            )

    def _archive(self, product, keep):
        with transaction.atomic():
            locked = Product.objects.select_for_update().get(pk=product.pk)
            if not locked.is_active:
                return
            locked.is_active = False
            locked.save(update_fields=['is_active', 'updated_at'])
            AuditLog.objects.create(
                user=None,
                action='product_code_quarantine',
                model_name='Product',
                object_id=str(locked.pk),
                object_repr=str(locked)[:200],
                reason=(
                    f'Código {locked.category.prefix}{locked.code} tinha dois cadastros '
                    f'no acervo; este foi anulado para o código ficar com um item vivo.'
                ),
                metadata={
                    'is_active': {'from': True, 'to': False},
                    'kept': {'pk': keep.pk, 'description': keep.description},
                    'legacy_id': locked.legacy_id,
                    'legacy_source': locked.legacy_source,
                },
            )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _parse_keep(self, raw_keeps):
        """Product PKs the owner said are the ones in the collection."""
        keep_pks = set()
        for raw in raw_keeps:
            try:
                keep_pks.add(int(str(raw).strip()))
            except (TypeError, ValueError):
                raise CommandError(f'--keep inválido: {raw!r}. Informe o PK do produto.')
        missing = keep_pks - set(
            Product.objects.filter(pk__in=keep_pks).values_list('pk', flat=True)
        )
        if missing:
            raise CommandError(
                f'--keep aponta produto inexistente: {sorted(missing)}.'
            )
        return keep_pks

    def _parse_pairs(self, raw_pairs):
        selected = set()
        for raw in raw_pairs:
            match = re.fullmatch(r'\s*([A-Za-z]+)\s*0*(\d+)\s*', raw)
            if not match:
                raise CommandError(f'Par inválido: {raw!r}. Use o formato VF1304.')
            selected.add((match.group(1).upper(), int(match.group(2))))
        return selected

    def _load_groups(self, selected):
        duplicated = (
            Product.objects.values('category_id', 'code')
            .annotate(rows=Count('pk'))
            .filter(rows__gt=1)
        )
        groups = []
        for row in duplicated:
            group = list(
                Product.objects.select_related('category')
                .filter(category_id=row['category_id'], code=row['code'])
                .annotate(past_rentals=Count('rental_items'))
                .order_by('pk')
            )
            key = (group[0].category.prefix.upper(), group[0].code)
            if selected and key not in selected:
                continue
            groups.append(group)

        if selected:
            found = {(g[0].category.prefix.upper(), g[0].code) for g in groups}
            for missing in sorted(selected - found):
                self.stdout.write(self.style.WARNING(
                    f'Par {missing[0]}{missing[1]} não está duplicado; ignorado.'
                ))
        return sorted(groups, key=lambda g: (g[0].category.prefix, g[0].code))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _report(self, kind, groups):
        titles = {
            'revival': 'REAPROVEITAMENTO — código anulado reocupado (fusão automática)',
            'identical': 'IDÊNTICOS — mesma descrição cadastrada duas vezes (fusão automática)',
            'divergent': 'DIVERGENTES — descrições diferentes (precisa de triagem da cliente)',
        }
        self.stdout.write('')
        self.stdout.write(f'== {titles[kind]}: {len(groups)} ==')
        for group in groups:
            winner = pick_winner(group, kind)
            survivor = min(group, key=lambda p: p.pk)
            label = f'{group[0].category.prefix}{group[0].code}'
            self.stdout.write(f'  {label}')
            for product in group:
                marks = []
                if product.pk == survivor.pk:
                    marks.append('fica')
                if product.pk == winner.pk:
                    marks.append('descrição vence')
                if is_free_code_slot(product):
                    marks.append('código livre')
                if not product.is_active:
                    marks.append('anulado')
                suffix = f' [{", ".join(marks)}]' if marks else ''
                self.stdout.write(
                    f'    #{product.pk} · {product.description!r} · '
                    f'{product.past_rentals} locação(ões){suffix}'
                )

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def _merge_all(self, groups):
        merged = relinked = removed = 0
        for group in groups:
            with transaction.atomic():
                counts = self._merge(group)
            merged += 1
            relinked += counts[0]
            removed += counts[1]
        return merged, relinked, removed

    def _merge(self, group):
        pks = [product.pk for product in group]
        # Re-read under a lock: the report ran outside the transaction.
        locked = list(
            Product.objects.select_for_update()
            .select_related('category')
            .filter(pk__in=pks)
            .order_by('pk')
        )
        if len(locked) < 2:
            return 0, 0

        kind = classify(locked)
        survivor = locked[0]
        winner = pick_winner(locked, kind)
        losers = [product for product in locked if product.pk != survivor.pk]

        before = {
            'description': survivor.description,
            'is_active': survivor.is_active,
        }
        # Snapshot every row's data before touching the survivor, otherwise
        # overwriting it with the winner's blanks destroys the very values the
        # fill-in pass below is supposed to recover.
        fallbacks = [
            {field: getattr(product, field) for field in COPIED_FIELDS}
            for product in locked
        ]
        for field in COPIED_FIELDS:
            setattr(survivor, field, getattr(winner, field))
        # Fill anything the winner left blank from the other rows.
        for fallback in fallbacks:
            for field in ('color', 'size', 'notes'):
                if not (getattr(survivor, field) or '').strip():
                    setattr(survivor, field, fallback[field])
            if not survivor.value:
                survivor.value = fallback['value']
        survivor.is_active = any(product.is_active for product in locked)
        if not is_free_code_slot(winner):
            survivor.is_placeholder = False

        # Order matters, and the database enforces it once
        # ``catalog_product_unique_active_code`` exists: activating the survivor
        # while an active loser still holds the same code would put two live
        # items on it, if only for one statement.  Relink first (RentalItem
        # PROTECTs the row), then drop the losers, and only then bring the
        # survivor back into the collection.
        relinked = RentalItem.objects.filter(
            product_id__in=[product.pk for product in losers],
        ).update(product=survivor)

        # The discarded rows leave the catalogue for good, so the log has to
        # carry their full identity — legacy provenance included.  This project
        # keeps deleted records auditable rather than merely countable.
        discarded = [product_audit_snapshot(product) for product in losers]
        for product in losers:
            product.delete()
        survivor.save()

        AuditLog.objects.create(
            user=None,
            action='product_code_dedupe',
            model_name='Product',
            object_id=str(survivor.pk),
            object_repr=str(survivor)[:200],
            reason=(
                f'Fusão de cadastros duplicados no código '
                f'{survivor.category.prefix}{survivor.code} (classe: {kind}).'
            ),
            metadata={
                'kind': kind,
                'survivor_before': before,
                'discarded': discarded,
                'rental_items_relinked': relinked,
            },
        )
        return relinked, len(losers)
