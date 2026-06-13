from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rentals', '0005_alter_rental_pickup_date_alter_rental_return_date_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='rental',
            index=models.Index(
                fields=['customer', 'status'],
                name='rental_customer_status_idx',
            ),
        ),
    ]
