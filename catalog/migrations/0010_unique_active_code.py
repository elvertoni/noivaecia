"""Make "one live item per printed code" a rule of the database.

Form validation is not an invariant.  ``ProductForm`` runs check-then-insert, so
two concurrent requests both pass; and the admin, the administrative MCP tools
and ``import_legacy_access`` reach ``Product`` without going through a form at
all.  Worse, a ``UniqueConstraint`` whose ``condition`` names a field the form
excludes is skipped *silently* during validation
(``django/db/models/constraints.py``: ``except FieldError: pass``), and
``ModelForm`` excludes every field outside ``Meta.fields`` — ``is_active``
among them.  The index is the only thing that actually arbitrates.

Two constraints land together because either alone leaves the reported bug
open:

* ``catalog_product_unique_active_code`` — at most one live row per
  ``(category, code)``.  Retired rows may pile up on a code; that is how the
  legacy system parked a freed slot and how reuse revives it.
* ``catalog_category_prefix_unique_ci`` — the product rule is scoped to
  ``category_id``, but the availability screen looks a piece up by
  ``prefix__iexact``.  ``Category.prefix`` is unique case-sensitively, so ``VF``
  and ``vf`` are two categories that read as one, each free to hold its own live
  code 731 — printing "VF731" twice on the very screen this work exists to fix.

Deploy order matters.  Cleaning the existing duplicates cannot be folded in as a
data migration here: most of them are two genuinely different garments sharing a
code, and only the owner can say which one is on the rack.  Run
``dedupe_product_codes --apply --quarantine`` (plus ``--keep``/``--pair`` for
whatever it reports) *before* deploying this.  The pre-flight below refuses
loudly rather than letting the schema change abort mid-deploy with a raw
``IntegrityError`` that, on SQLite, does not even name the constraint.
"""

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Upper


def assert_one_live_item_per_code(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    clashes = (
        Product.objects.filter(is_active=True)
        .values('category_id', 'code')
        .annotate(rows=Count('pk'))
        .filter(rows__gt=1)
        .order_by('category_id', 'code')
    )
    total = clashes.count()
    if not total:
        return

    prefixes = dict(
        apps.get_model('catalog', 'Category').objects.values_list('pk', 'prefix')
    )
    sample = ', '.join(
        f'{prefixes.get(row["category_id"], "?")}{row["code"]}' for row in clashes[:20]
    )
    raise RuntimeError(
        f'{total} código(s) têm mais de um item no acervo e impedem esta migração. '
        f'Amostra: {sample}. '
        'Rode "manage.py dedupe_product_codes --apply --quarantine" e resolva com '
        'a cliente os códigos que o comando reportar (--keep PK ou --pair).'
    )


def assert_prefixes_differ_by_more_than_case(apps, schema_editor):
    Category = apps.get_model('catalog', 'Category')
    seen = {}
    clashes = []
    for pk, prefix in Category.objects.values_list('pk', 'prefix').order_by('pk'):
        key = (prefix or '').strip().upper()
        if key in seen:
            clashes.append(f'{seen[key]!r} vs {prefix!r}')
        else:
            seen[key] = prefix
    if clashes:
        raise RuntimeError(
            f'{len(clashes)} prefixo(s) de categoria diferem apenas por maiúsculas '
            f'e impedem esta migração: {", ".join(clashes[:20])}. '
            'Padronize os prefixos em maiúsculas antes do deploy.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0009_free_legacy_null_codes'),
    ]

    operations = [
        migrations.RunPython(
            assert_one_live_item_per_code, migrations.RunPython.noop,
        ),
        migrations.RunPython(
            assert_prefixes_differ_by_more_than_case, migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(
                fields=('category', 'code'),
                condition=models.Q(is_active=True),
                name='catalog_product_unique_active_code',
                violation_error_message=(
                    'Este código já está no acervo desta categoria. Retire o item '
                    'atual antes de reaproveitar o código.'
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(
                Upper('prefix'),
                name='catalog_category_prefix_unique_ci',
                violation_error_message='Já existe uma categoria com este prefixo.',
            ),
        ),
    ]
