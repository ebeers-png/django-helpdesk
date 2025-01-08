# Generated by Django 3.2.20 on 2025-01-08 20:13

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0105_disallow_null_savedsearch_organization'),
    ]

    operations = [
        migrations.CreateModel(
            name='DependsOn',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.TextField(blank=True, help_text='Show field when the parent has this value. Use Yes or No when the parent field is a boolean (a checkbox).', null=True, verbose_name='Expected Value')),
                ('parent_help_text', models.TextField(blank=True, help_text='This text will display under the parent field when the dependent field is visible.', null=True, verbose_name='Parent Help Text')),
                ('dependent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='parent_fields', to='helpdesk.customfield')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='dependent_fields', to='helpdesk.customfield')),
            ],
        ),
    ]
