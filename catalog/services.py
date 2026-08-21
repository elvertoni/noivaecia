"""Shared catalogue rules around product code identity.

``(category, code)`` is the item's identity in this business — it is printed on
the contract and written on the physical tag.  The legacy BRcom system bound one
code to one row for good: retiring an item rewrote its description to ``NULO``
and reusing the code rewrote that same row back into service.

Deciding whether a row holds a *piece* or merely *a free slot* drives the reuse
flow, the deduplication command and the migration that folded the legacy shells
onto ``is_active``, so the rule lives here rather than being restated at each
call site.
"""

import unicodedata


#: Written into ``Product.legacy_notes`` by migration ``catalog.0009`` so its
#: reverse only reactivates rows that migration archived.  The migration keeps
#: its own copy of this literal — historical migrations must not depend on
#: application code that can change — and ``tests_code_reuse`` pins the two
#: together.
LEGACY_FREED_MARKER = '[0009] código anulado liberado para reaproveitamento'


def normalize_description(text):
    value = unicodedata.normalize('NFKD', text or '')
    stripped = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(stripped.casefold().split())


def is_free_code_slot(product, prefix=None):
    """True when the row holds a code slot rather than a real piece.

    ``NULO`` is the marker operators typed in the legacy system when an item
    left the collection.  A blank Access description was turned into
    ``PREFIXCODE`` by ``import_legacy_access``, so that shape means the same
    thing.

    ``NULA`` is deliberately excluded: it is a real description word in this
    catalogue ("VEST NULA MANGA" = sleeveless), used by 340 legacy rows.
    """
    description = normalize_description(product.description)
    if not description:
        return True
    if description == 'nulo':
        return True
    if prefix is None:
        prefix = product.category.prefix
    return description == normalize_description(f'{prefix}{product.code}')


def clear_legacy_freed_marker(product):
    """Drop the migration sentinel once the code is back in service.

    The row is no longer a freed legacy slot, so leaving the marker behind would
    let a later reverse of ``catalog.0009`` reactivate an item that was retired
    on purpose after the reuse.
    """
    if LEGACY_FREED_MARKER not in product.legacy_notes:
        return False
    remaining = [
        line for line in product.legacy_notes.splitlines()
        if line.strip() != LEGACY_FREED_MARKER
    ]
    product.legacy_notes = '\n'.join(remaining).strip()
    return True


def product_audit_snapshot(product):
    """Every field needed to reconstruct a row that is about to be discarded.

    This project keeps deleted records auditable rather than merely countable,
    so the log has to carry the legacy provenance too.
    """
    return {
        'pk': product.pk,
        'description': product.description,
        'color': product.color,
        'size': product.size,
        'value': str(product.value),
        'notes': product.notes,
        'is_active': product.is_active,
        'is_placeholder': product.is_placeholder,
        'legacy_id': product.legacy_id,
        'legacy_source': product.legacy_source,
        'legacy_notes': product.legacy_notes,
        'created_at': product.created_at.isoformat(),
    }


def active_code_holder(category_id, code, exclude_pk=None):
    """The live row occupying ``(category, code)``, if any.

    Used both for the optimistic check and for recovering from an
    ``IntegrityError``: re-querying is the only portable way to tell which
    constraint fired, since SQLite names the columns and PostgreSQL names the
    index.
    """
    from .models import Product

    queryset = Product.objects.select_related('category').filter(
        category_id=category_id, code=code, is_active=True,
    )
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.first()


def code_taken_message(prefix, code, holder):
    return (
        f'O código {prefix}{code} já está no acervo, usado por '
        f'"{holder.description}". Retire esse item do acervo antes de '
        'reaproveitar o código.'
    )
