from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='product',
            unique_together=set(),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(
                fields=('category', 'code'),
                name='catalog_product_lookup_idx',
            ),
        ),
    ]
