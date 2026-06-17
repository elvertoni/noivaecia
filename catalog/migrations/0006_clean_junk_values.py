from django.db import migrations

# Legacy placeholder tokens that mean "no value". Matched case-insensitively
# against the *whole* trimmed field. Conservative on purpose: only obvious junk.
JUNK_TOKENS = {'nulo', 'nula', 'null', 'n/a', 'na', '-', '--', '---', '.', '..', '...'}


def _clean(value):
    if value is None:
        return ''
    collapsed = ' '.join(value.split())
    if collapsed.lower() in JUNK_TOKENS:
        return ''
    return collapsed


def clean_junk(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    changed = []
    qs = Product.objects.all().only('id', 'description', 'color', 'size', 'description_search')
    for product in qs.iterator(chunk_size=2000):
        new_desc = _clean(product.description)
        new_color = _clean(product.color)
        new_size = _clean(product.size)
        if (new_desc, new_color, new_size) == (
            product.description, product.color, product.size
        ):
            continue
        product.description = new_desc
        product.color = new_color
        product.size = new_size
        # Keep the normalized search field consistent (mirrors Product.save()).
        import unicodedata
        nfkd = unicodedata.normalize('NFKD', new_desc)
        stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
        product.description_search = ' '.join(stripped.lower().split())
        changed.append(product)
    if changed:
        Product.objects.bulk_update(
            changed, ['description', 'color', 'size', 'description_search'], batch_size=1000
        )


def noop(apps, schema_editor):
    # Irreversible data normalization: original junk values are not restored.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_product_desc_trgm_idx'),
    ]

    operations = [
        migrations.RunPython(clean_junk, noop),
    ]
