# Generated by Django 3.2.18 on 2023-07-06 19:16

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0082_auto_20230706_1127'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='customfield',
            name='list_values',
        ),
    ]
