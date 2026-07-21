from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('company', '0003_company_whatsapp_report_number_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='whatsapp_report_number',
            field=models.TextField(
                blank=True,
                help_text='Informe um ou mais números separados por vírgula, espaço ou linha.',
                verbose_name='números do WhatsApp (com DDI, ex: 5543999998888)',
            ),
        ),
    ]
