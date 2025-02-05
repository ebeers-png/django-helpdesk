# Generated by Django 3.2.20 on 2025-02-04 16:01

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings

class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0114_ticket_beam_portfolio'),
    ]

    operations = [
        migrations.AddField(
            model_name='formtype',
            name='push_cycle',
            field=models.ForeignKey(blank=True, help_text='BEAM Cycle to push property data to. Required if automatically copying data or creating portfolios.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='push_forms', to='seed.cycle'),
        ),
        migrations.AlterField(
            model_name='formtype',
            name='pull_cycle',
            field=models.ForeignKey(blank=True, help_text='BEAM Cycle to pull property data from. Required if prepopulate is checked or using derived column fields.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pull_forms', to='seed.cycle'),
        ),
        migrations.AddField(
            model_name='formtype',
            name='auto_copy',
            field=models.BooleanField(default=False, help_text='Should form submissions automatically copy data to the paired BEAM property or tax lot? If multi-property pairing is checked, property fields will be copied to all paired properties.', verbose_name='Automatically Copy to BEAM?'),
        ),
        migrations.AddField(
            model_name='formtype',
            name='auto_pair',
            field=models.BooleanField(default=False, help_text='Should form submissions automatically attempt to pair with the BEAM inventory?', verbose_name='Automatically Pair?'),
        ),
        migrations.AddField(
            model_name='formtype',
            name='auto_create_portfolio',
            field=models.BooleanField(default=False, help_text='Should form submissions automatically create BEAM portfolio from the paired properties?', verbose_name='Automatically Create Portfolio?'),
        ),
        migrations.AddField(
            model_name='formtype',
            name='authorizing_user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customfield',
            name='auto_copy',
            field=models.BooleanField(default=False, help_text='If the form is set to automatically copy to BEAM, should this field be included?', verbose_name='Include in Automatic Copy?'),
        ),
        migrations.AddConstraint(
            model_name='formtype',
            constraint=models.CheckConstraint(check=models.Q(('auto_copy', False), models.Q(('auto_copy', True), ('auto_pair', True)), _connector='OR'), name='form_auto_pair_if_auto_copy'),
        ),
    ]
