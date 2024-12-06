# Generated by Django 3.2.18 on 2023-07-18 19:07

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0085_alter_helptext'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customfield',
            name='help_text',
            field=models.TextField(blank=True, help_text="Shown to the user when editing the ticket.<br/><br/><a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", null=True, verbose_name='Help Text'),
        ),
        migrations.AlterField(
            model_name='followup',
            name='comment',
            field=models.TextField(blank=True, help_text="<a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", null=True, verbose_name='Comment'),
        ),
        migrations.AlterField(
            model_name='formtype',
            name='description',
            field=models.TextField(blank=True, help_text="Introduction text included in the form.<br/><br/><a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", null=True),
        ),
        migrations.AlterField(
            model_name='kbcategory',
            name='description',
            field=models.TextField(help_text="Full description on knowledgebase category page.<br/><br/><a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", verbose_name='Full description on knowledgebase category page'),
        ),
        migrations.AlterField(
            model_name='kbcategory',
            name='preview_description',
            field=models.TextField(blank=True, help_text="Optional short description that will describe the category on the Knowledgebase Overview page.<br/><br/><a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", null=True, verbose_name='Short description'),
        ),
        migrations.AlterField(
            model_name='kbitem',
            name='answer',
            field=models.TextField(help_text="The body of the article, or answer to the question.<br/><br/><a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", verbose_name='Article body'),
        ),
        migrations.AlterField(
            model_name='ticket',
            name='description',
            field=models.TextField(blank=True, help_text="<a href='/static/seed/pdf/Markdown_Cheat_Sheet.pdf' target='_blank' rel='noopener noreferrer'             title='ClearlyEnergy Markdown Cheat Sheet'>Markdown syntax</a> allowed, but no raw HTML.", null=True),
        ),
    ]