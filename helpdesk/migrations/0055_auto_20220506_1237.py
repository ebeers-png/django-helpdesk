# Generated by Django 3.2.7 on 2022-05-06 19:37

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('seed', '0176_auto_20220506_1221'),
        ('helpdesk', '0054_queue_keep_mail'),
    ]

    operations = [
        migrations.AddField(
            model_name='queue',
            name='importer',
            field=models.ForeignKey(blank=True, help_text="Assign the email address that this queue should import from. If this queue is not the organization's default queue, this queue will only import responses to tickets, unless match_on is filled out. \nNOTE: Importing and sending should be done from the same email address.", null=True, on_delete=django.db.models.deletion.SET_NULL, to='seed.emailimporter'),
        ),
        migrations.AddField(
            model_name='queue',
            name='match_on',
            field=models.JSONField(blank=True, default=list, help_text="A list of strings. If you'd like only emails with certain subject lines to be imported into this queue, list that text here. Otherwise, leave blank."),
        ),
        migrations.AddField(
            model_name='queue',
            name='sender',
            field=models.ForeignKey(blank=True, help_text="Assign the email address that this queue should send emails from. If no sender is provided, the organization's sender will be used. \nNOTE: Importing and sending should be done from the same email address.", null=True, on_delete=django.db.models.deletion.SET_NULL, to='seed.emailsender'),
        ),
    ]
