"""Unify the two dialects for "this code is free" onto ``is_active``.

The legacy BRcom system never deleted a product row: retiring an item meant
editing it in place and writing ``NULO`` over the description, so the
``(prefixo, codigo)`` slot stayed in the table and could be revived later.  The
import carried those rows over verbatim, and they landed with
``is_active=True`` — which left the codebase with two competing definitions of
"out of the collection": ``is_active=False`` (honoured by the product list, the
availability lookup and both rental pickers) and ``description='NULO'``
(honoured only by the reusable-codes endpoint).

Every consumer except that one endpoint therefore treated the legacy shells as
live inventory.  Collapsing them onto ``is_active=False`` restores the single
meaning the legacy system had.

Two shapes mark a free slot, and both are folded in so the migration agrees
with ``dedupe_product_codes.is_free_marker``:

* ``NULO`` — the operator-typed retirement marker.
* ``PREFIXCODE`` (e.g. ``CAM24``) — what ``import_legacy_access`` writes when
  the Access row had a blank description, which meant the same thing.

``NULA`` is deliberately not matched: it is a real description word in this
catalogue ("VEST NULA MANGA" = sleeveless), used by 340 legacy rows.

Rows touched here are tagged in ``legacy_notes`` so the reverse only reactivates
what this migration archived, and not an item legitimately retired afterwards.
"""

import unicodedata

from django.db import migrations


MARKER = '[0009] código anulado liberado para reaproveitamento'


def normalize(text):
    value = unicodedata.normalize('NFKD', text or '')
    stripped = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(stripped.casefold().split())


def is_free_slot(product, prefix):
    description = normalize(product.description)
    if not description:
        return True
    if description == 'nulo':
        return True
    return description == normalize(f'{prefix}{product.code}')


def free_null_codes(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    # Placeholders are incomplete legacy records awaiting completion, not
    # retired items; archiving one would hide a rental that still needs it.
    candidates = (
        Product.objects.filter(is_active=True)
        .exclude(is_placeholder=True)
        .select_related('category')
    )
    touched = []
    for product in candidates.iterator():
        if not is_free_slot(product, product.category.prefix):
            continue
        notes = product.legacy_notes.strip()
        product.legacy_notes = f'{notes}\n{MARKER}'.strip() if notes else MARKER
        product.is_active = False
        touched.append(product)

    Product.objects.bulk_update(touched, ['is_active', 'legacy_notes'], batch_size=500)


def restore_null_codes(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    restored = []
    for product in Product.objects.filter(
        is_active=False, legacy_notes__contains=MARKER,
    ).iterator():
        lines = [
            line for line in product.legacy_notes.splitlines()
            if line.strip() != MARKER
        ]
        product.legacy_notes = '\n'.join(lines).strip()
        product.is_active = True
        restored.append(product)

    Product.objects.bulk_update(restored, ['is_active', 'legacy_notes'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_product_catalog_product_value_gte_0'),
    ]

    operations = [
        migrations.RunPython(free_null_codes, restore_null_codes),
    ]
