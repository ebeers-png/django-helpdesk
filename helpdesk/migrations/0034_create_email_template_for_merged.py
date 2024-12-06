# Generated by Django 2.2.13 on 2020-10-29 22:34

from django.db import migrations


def forwards_func(apps, schema_editor):
    EmailTemplate = apps.get_model("helpdesk", "EmailTemplate")
    db_alias = schema_editor.connection.alias
    latest_template = EmailTemplate.objects.order_by('-id').first()  # because PG sequences are not reset
    new_id = latest_template.id + 1 if latest_template else 1
    EmailTemplate.objects.using(db_alias).create(
        id=new_id,
        template_name='merged',
        subject='(Merged)',
        heading='Ticket merged',
        plain_text="""Hello,

This is a courtesy e-mail to let you know that ticket {{ ticket.ticket }} ("{{ ticket.title }}") by {{ ticket.submitter_email }} has been merged to ticket {{ ticket.merged_to.ticket }}.

From now on, please answer on this ticket, or you can include the tag {{ ticket.merged_to.ticket }} in your e-mail subject.""",
        html="""<p style="font-family: sans-serif; font-size: 1em;">Hello,</p>

<p style="font-family: sans-serif; font-size: 1em;">This is a courtesy e-mail to let you know that ticket <b>{{ ticket.ticket }}</b> (<em>{{ ticket.title }}</em>) by {{ ticket.submitter_email }} has been merged to ticket <a href="{{ ticket.merged_to.staff_url }}">{{ ticket.merged_to.ticket }}</a>.</p>

<p style="font-family: sans-serif; font-size: 1em;">From now on, please answer on this ticket, or you can include the tag <b>{{ ticket.merged_to.ticket }}</b> in your e-mail subject.</p>""",
        locale='en'
    )
    EmailTemplate.objects.using(db_alias).create(
        id=new_id + 1,  # because PG sequences are not reset
        template_name='merged',
        subject='(Fusionné)',
        heading='Ticket Fusionné',
        plain_text="""Bonjour,

Ce courriel indicatif permet de vous prévenir que le ticket  {{ ticket.ticket }} ("{{ ticket.title }}") par {{ ticket.submitter_email }} a été fusionné au ticket {{ ticket.merged_to.ticket }}.

Veillez à répondre sur ce ticket dorénavant, ou bien inclure la balise {{ ticket.merged_to.ticket }} dans le sujet de votre réponse par mail.""",
        html="""<p style="font-family: sans-serif; font-size: 1em;">Bonjour,</p>

<p style="font-family: sans-serif; font-size: 1em;">Ce courriel indicatif permet de vous prévenir que le ticket <b>{{ ticket.ticket }}</b> (<em>{{ ticket.title }}</em>) par {{ ticket.submitter_email }}  a été fusionné au ticket <a href="{{ ticket.merged_to.staff_url }}">{{ ticket.merged_to.ticket }}</a>.</p>

<p style="font-family: sans-serif; font-size: 1em;">Veillez à répondre sur ce ticket dorénavant, ou bien inclure la balise <b>{{ ticket.merged_to.ticket }}</b> dans le sujet de votre réponse par mail.</p>""",
        locale='fr'
    )


def reverse_func(apps, schema_editor):
    EmailTemplate = apps.get_model("helpdesk", "EmailTemplate")
    db_alias = schema_editor.connection.alias
    EmailTemplate.objects.using(db_alias).filter(template_name='merged').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0033_ticket_merged_to'),
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_func),
    ]