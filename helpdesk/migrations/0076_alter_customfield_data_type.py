# Generated by Django 3.2.14 on 2023-01-09 19:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0075_forms_unlisted'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customfield',
            name='data_type',
            field=models.CharField(blank=True, choices=[('varchar', 'Character (single line)'), ('text', 'Text (multi-line)'), ('integer', 'Integer'), ('decimal', 'Decimal'), ('list', 'List'), ('boolean', 'Boolean (checkbox yes/no)'), ('date', 'Date'), ('time', 'Time'), ('datetime', 'Date & Time'), ('email', 'E-Mail Address'), ('url', 'URL'), ('ipaddress', 'IP Address'), ('slug', 'Slug'), ('attachment', 'Attachment')], help_text='Allows you to restrict the data entered into this field', max_length=100, null=True, verbose_name='Data Type'),
        ),
    ]
