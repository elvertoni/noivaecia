from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0003_customer_is_active'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(
                fields=['name'],
                name='customer_name_idx',
            ),
        ),
    ]
