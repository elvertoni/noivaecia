from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rentals', '0006_rental_customer_status_idx'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='rental',
            index=models.Index(
                fields=['status', 'pickup_date', 'number'],
                name='rental_status_pickup_num_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='rental',
            index=models.Index(
                fields=['status', 'return_date', 'number'],
                name='rental_status_return_num_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='rental',
            index=models.Index(
                fields=['customer', 'pickup_date'],
                name='rental_customer_pickup_idx',
            ),
        ),
    ]
