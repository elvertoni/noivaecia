from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0003_category_is_placeholder_category_legacy_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='description_search',
            field=models.CharField(blank=True, max_length=220, verbose_name='descrição normalizada'),
        ),
    ]
