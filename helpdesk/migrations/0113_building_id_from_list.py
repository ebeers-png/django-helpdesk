# Generated by Django 3.2.20 on 2025-01-29 18:55

from django.db import migrations, models

def building_id_from_list(apps, schema_editor):
    """
    If a ticket's `building_id` value is a list, replace its value with the first
    ID in the list.
    """
    Ticket = apps.get_model('helpdesk', 'Ticket')
    
    for ticket in Ticket.objects.filter(building_id__isnull=False):
        if isinstance(ticket.building_id, list):
            ticket.building_id = ticket.building_id[0]
            ticket.save()

class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0112_alter_ticket_building_id'),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, building_id_from_list)
    ]
