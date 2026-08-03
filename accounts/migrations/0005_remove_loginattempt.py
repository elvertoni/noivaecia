from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_loginattempt'),
    ]

    operations = [
        migrations.DeleteModel(
            name='LoginAttempt',
        ),
    ]
