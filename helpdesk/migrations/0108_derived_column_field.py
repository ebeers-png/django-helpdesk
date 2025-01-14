# Generated by Django 3.2.20 on 2025-01-14 17:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0107_prepopulation'),
    ]

    operations = [
        migrations.AddField(
            model_name='formtype',
            name='view_only',
            field=models.BooleanField(default=False, help_text='Should this form not allow any submissions? Removes the submit button from the form.', verbose_name='View-Only Form?'),
        ),
        migrations.AlterField(
            model_name='customfield',
            name='data_type',
            field=models.CharField(blank=True, choices=[('varchar', 'Character (single line)'), ('text', 'Text (multi-line)'), ('integer', 'Integer'), ('decimal', 'Decimal'), ('list', 'List'), ('boolean', 'Boolean (checkbox yes/no)'), ('date', 'Date'), ('time', 'Time'), ('datetime', 'Date & Time'), ('email', 'E-Mail Address'), ('url', 'URL'), ('ipaddress', 'IP Address'), ('slug', 'Slug'), ('attachment', 'Attachment'), ('key_value', 'Key Value'), ('derived_column', 'Derived Column')], help_text='Allows you to restrict the data entered into this field', max_length=100, null=True, verbose_name='Data Type'),
        ),
    ]
