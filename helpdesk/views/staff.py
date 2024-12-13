"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

views/staff.py - The bulk of the application - provides most business logic and
                 renders all staff-facing views.
"""
import re
from copy import deepcopy
import json
from django.forms import model_to_dict
import pandas as pd
import dateutil
import pytz

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.urls import reverse, reverse_lazy
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, F, Case, When, ProtectedError
from django.http import HttpResponseRedirect, Http404, HttpResponse, JsonResponse, HttpRequest
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.translation import ugettext as _
from django.utils.html import escape
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.generic.edit import FormView, UpdateView

from helpdesk.forms import CUSTOMFIELD_DATE_FORMAT, CUSTOMFIELD_DATETIME_FORMAT, PreviewWidget
from helpdesk.query import (
    get_query_class,
    query_to_base64,
    query_from_base64,
    get_extra_data_columns,
)

from helpdesk.serializers import DatatablesTicketSerializer, ReportTicketSerializer

from helpdesk.user import HelpdeskUser

from helpdesk.decorators import (
    helpdesk_staff_member_required,
    is_helpdesk_staff,
    list_of_helpdesk_staff,
)
from helpdesk.forms import (
    TicketForm, UserSettingsForm, EmailIgnoreForm, EditTicketForm, TicketCCForm,
    EditFollowUpForm, TicketDependencyForm, MultipleTicketSelectForm,
    EditQueueForm, EditFormTypeForm, PreSetReplyForm, EmailTemplateForm, TagForm
)
from helpdesk.lib import (
    safe_template_context,
    process_attachments,
    queue_template_context,
)
from helpdesk.models import (
    Ticket, Queue, FollowUp, TimeSpent, TicketChange, PreSetReply, FollowUpAttachment, SavedSearch,
    IgnoreEmail, TicketCC, TicketDependency, UserSettings, KBItem, CustomField, is_unlisted,
    FormType, EmailTemplate, get_markdown, clean_html, Tag
)

from helpdesk import settings as helpdesk_settings
import helpdesk.views.abstract_views as abstract_views
from helpdesk.views.permissions import MustBeStaffMixin
from ..lib import format_time_spent

from rest_framework import status
from rest_framework.decorators import api_view

from datetime import timedelta, datetime

from ..templated_email import send_templated_mail

from seed.models import PropertyView, Property, TaxLotView, TaxLot, Column
from post_office.models import STATUS, Email
from urllib.parse import urlparse, urlunparse
from django.http import QueryDict
User = get_user_model()
Query = get_query_class()

staff_member_required = user_passes_test(
    lambda u: u.is_authenticated and u.is_active and is_helpdesk_staff(u))


def set_user_timezone(request):
    if 'helpdesk_timezone' not in request.session:
        tz = request.GET.get('timezone')
        request.session["helpdesk_timezone"] = tz
        timezone.activate(tz)
        response_data = {'status': True, 'message': 'user timezone set successfully to %s.' % tz}
    else:
        response_data = {'status': False, 'message': 'user timezone has already been set'}
    return JsonResponse(response_data, status=status.HTTP_200_OK)


@helpdesk_staff_member_required
def set_default_org(request, user_id, org_id):
    """
    Change the default org of the user based on their dropdown menu selection
    Reload them back to the same page
    """
    from seed.landing.models import SEEDUser as User
    from seed.lib.superperms.orgs.models import Organization
    user = User.objects.get(pk=user_id)
    user.default_organization_id = org_id
    user.save()

    # todo The query is changed, which is good, but the path didn't, and that can be wrong. grrr
    # complicated way of replacing the org query with the new org's name
    # https://stackoverflow.com/questions/5755150/altering-one-query-parameter-in-a-url-django
    (scheme, netloc, path, params, query, fragment) = urlparse(request.META['HTTP_REFERER'])
    query_dict = QueryDict(query).copy()
    query_dict['org'] = Organization.objects.get(id=org_id).name
    query = query_dict.urlencode()
    url = urlunparse((scheme, netloc, path, params, query, fragment))
    return redirect(url)


@helpdesk_staff_member_required
def preview_html(request):
    md = request.POST.get('md')
    return JsonResponse({'md_html': clean_html(md)})


@helpdesk_staff_member_required
def preview_markdown(request):
    md = request.POST.get('md')
    is_kbitem = request.POST.get('is_kbitem', 'false')
    org = request.user.default_organization

    class MarkdownNumbers(object):
        def __init__(self, start=1, pattern=''):
            self.count = start - 1
            self.pattern = pattern

        def __call__(self, match):
            self.count += 1
            return self.pattern.format(self.count).replace('\x01', match[1])

    if is_kbitem == 'true':
        import re
        anchor_target_pattern = r'{\:\s*#(\w+)\s*}'
        anchor_link_pattern = r'\[(.+)\]\(#(\w+)\)'
        new_md, anchor_target_count = re.subn(anchor_target_pattern, "{: #anchor-\g<1> }", md)
        new_md, anchor_link_count = re.subn(anchor_link_pattern, "[\g<1>](#anchor-\g<2>)", new_md)

        title_pattern = r'^(.*)\n!~!'
        body_pattern = r'~!~'
        title = "<div markdown='1' class='card mb-2'>\n<div markdown='1' id=\"header{0}\" class='btn btn-link card-header h5' " \
                "style='text-align: left; 'data-toggle='collapse' data-target='#collapse{0}' role='region' " \
                "aria-expanded='false' aria-controls='collapse{0}'>\1\n{{: .mb-0}}</div>\n" \
                "<div markdown='1' id='collapse{0}' class='collapse card-body mt-1' role='region'" \
                "aria-labelledby='header{0}' data-parent='#header{0}' style='padding-top:0;padding-bottom:0;margin:0;'>"
        body = "</div>\n</div>"

        new_md, title_count = re.subn(title_pattern, MarkdownNumbers(start=1, pattern=title), new_md, flags=re.MULTILINE)
        new_md, body_count = re.subn(body_pattern, body, new_md)
        if (anchor_target_count != 0) or (title_count != 0 and title_count == body_count):
            return JsonResponse({'md_html': get_markdown(new_md, org, kb=True)})
    return JsonResponse({'md_html': get_markdown(md, org)})


def _get_queue_choices(queues):
    """Return list of `choices` array for html form for given queues

    idea is to return only one choice if there is only one queue or add empty
    choice at the beginning of the list, if there are more queues
    """

    queue_choices = []
    if len(queues) > 1:
        queue_choices = [('', '--------')]
    queue_choices += [(q.id, q.title) for q in queues]
    return queue_choices


@helpdesk_staff_member_required
def queue_list(request):
    huser = HelpdeskUser(request.user)
    queue_list = huser.get_queues()                # Queues in user's default org (or all if superuser)

    # user settings num tickets per page
    if request.user.is_authenticated and hasattr(request.user, 'usersettings_helpdesk'):
        queues_per_page = request.user.usersettings_helpdesk.tickets_per_page
    else:
        queues_per_page = 25

    paginator = Paginator(
        queue_list, queues_per_page)
    try:
        queue_list = paginator.page(request.GET.get(_('q_page'), 1))
    except PageNotAnInteger:
        queue_list = paginator.page(1)
    except EmptyPage:
        queue_list = paginator.page(
            paginator.num_pages)

    return render(request, 'helpdesk/queue_list.html', {
        'queue_list': queue_list,
        'debug': settings.DEBUG,
    })


@helpdesk_staff_member_required
def create_queue(request):
    org = request.user.default_organization.helpdesk_organization
    if request.method == "GET":
        form = EditQueueForm("create", organization=org.id)

        return render(request, 'helpdesk/edit_queue.html', {
            'form': form,
            'action': "Create",
            'debug': settings.DEBUG,
        })
    elif request.method == "POST":
        form = EditQueueForm("create", request.POST, organization=org.id)

        if form.is_valid():
            queue = Queue(**form.cleaned_data, organization=org)
            queue.save()
            return HttpResponseRedirect(reverse('helpdesk:maintain_queues'))

        redo_form = EditQueueForm(
            "create", 
            request.POST, 
            organization=org.id,
            initial = form.cleaned_data
        )

        return render(request, 'helpdesk/edit_queue.html', {
            'form': redo_form,
            'errors': form.errors,
            'action': "Create",
            'debug': settings.DEBUG,
        })


@helpdesk_staff_member_required
def edit_queue(request, slug):
    """Edit Queue"""
    queue = get_object_or_404(Queue, slug=slug)
    org = request.user.default_organization.helpdesk_organization

    if request.method == "GET":
        form = EditQueueForm(
            "edit",
            organization=org.id,
            initial = model_to_dict(queue)
        )

        return render(request, 'helpdesk/edit_queue.html', {
            'queue': queue,
            'form': form,
            'action': "Edit",
            'debug': settings.DEBUG,
        })
    elif request.method == "POST":
        form = EditQueueForm("edit", request.POST, organization=org.id)
        if form.is_valid():
            del form.cleaned_data['slug'] # slug field disabled, use existing slug instead
            Queue.objects.filter(pk=queue.pk).update(**form.cleaned_data, slug=queue.slug)
        return HttpResponseRedirect(reverse('helpdesk:maintain_queues'))


@helpdesk_staff_member_required
def form_list(request):
    org = request.user.default_organization.helpdesk_organization
    form_list = FormType.objects.filter(organization=org)
    
    # user settings num tickets per page
    if request.user.is_authenticated and hasattr(request.user, 'usersettings_helpdesk'):
        forms_per_page = request.user.usersettings_helpdesk.tickets_per_page
    else:
        forms_per_page = 25

    paginator = Paginator(
        form_list, forms_per_page)
    try:
        form_list = paginator.page(request.GET.get(_('q_page'), 1))
    except PageNotAnInteger:
        form_list = paginator.page(1)
    except EmptyPage:
        form_list = paginator.page(
            paginator.num_pages)

    return render(request, 'helpdesk/form_list.html', {
        'form_list': form_list,
        'debug': settings.DEBUG,
    })


@helpdesk_staff_member_required
def create_form(request):
    org = request.user.default_organization.helpdesk_organization
    
    if request.method == "GET":
        # Create empty form and save it to the database to generate the default Custom Fields.
        formtype = FormType(organization = org, name="Unnamed Form")
        formtype.save()
        form = EditFormTypeForm(
            initial_customfields = CustomField.objects.filter(ticket_form=formtype),
            organization = org,
            instance = formtype
        )

        return render(request, 'helpdesk/edit_form.html', {
            'formtype': formtype,
            'form': form,
            'action': "Create",
            'debug': settings.DEBUG,
        })
    elif request.method == "POST":
        form = EditFormTypeForm(request.POST, organization = org)
        formtype = get_object_or_404(FormType, pk=request.POST.get('id'))
        formset = form.CustomFieldFormSet(request.POST)

        if form.is_valid():
            FormType.objects.filter(pk=formtype.pk).update(**form.cleaned_data)

            if formset.is_valid():
                for df in formset.deleted_forms:
                    if df.cleaned_data['id']: df.cleaned_data['id'].delete()

                for cf in formset.cleaned_data:
                    if not cf or cf['DELETE']: continue # continue to next item if form is empty or item is being deleted
                    del cf['DELETE']

                    cf['ticket_form'] = formtype

                    if cf['id']:
                        customfield = cf['id']
                        del cf['id']
                        CustomField.objects.filter(id=customfield.id).update(**cf)
                    else:
                        customfield = CustomField(**cf)
                        customfield.save()
                
                return HttpResponseRedirect(reverse('helpdesk:maintain_forms'))
            
        form.customfield_formset = formset
        return render(request, "helpdesk/edit_form.html", {
            'formtype': formtype,
            'form': form,
            'formset': formset,
            'action': "Create",
            'debug': settings.DEBUG,             
        })
    

@helpdesk_staff_member_required
def edit_form(request, pk):
    formtype = get_object_or_404(FormType, pk=pk)
    
    if request.method == "GET":
        form = EditFormTypeForm(
            initial = model_to_dict(formtype),
            initial_customfields = CustomField.objects.filter(ticket_form=formtype),
            organization = formtype.organization,
            pk = pk
        )
        
        return render(request, 'helpdesk/edit_form.html', {
            'formtype': formtype,
            'form': form,
            'action': "Edit",
            'debug': settings.DEBUG,
        })
    elif request.method == "POST":
        form = EditFormTypeForm(request.POST, organization = formtype.organization)
        formset = form.CustomFieldFormSet(request.POST)

        if form.is_valid():
            FormType.objects.filter(pk=formtype.pk).update(**form.cleaned_data)
            
            if formset.is_valid():
                for df in formset.deleted_forms:
                    if df.cleaned_data['id']: df.cleaned_data['id'].delete()

                for cf in formset.cleaned_data:
                    if not cf or cf['DELETE']: continue # continue to next item if form is empty or item is being deleted
                    del cf['DELETE']

                    cf['ticket_form'] = formtype

                    if cf['id']:
                        customfield = cf['id']
                        del cf['id']
                        CustomField.objects.filter(id=customfield.id).update(**cf)
                    else:
                        customfield = CustomField(**cf)
                        customfield.save()

                return HttpResponseRedirect(reverse('helpdesk:maintain_forms'))

        form.customfield_formset = formset
        return render(request, "helpdesk/edit_form.html", {
            'formtype': formtype,
            'form': form,
            'formset': formset,
            'action': "Edit",
            'debug': settings.DEBUG,             
        })


@helpdesk_staff_member_required
def delete_form(request, pk):
    form = get_object_or_404(FormType, pk=pk)
    form.delete()
    return HttpResponseRedirect(reverse('helpdesk:maintain_forms'))


@helpdesk_staff_member_required
def duplicate_form(request, pk):
    formtype = get_object_or_404(FormType, pk=pk)
    original_custom_fields = CustomField.objects.filter(ticket_form=formtype)
    
    formtype.pk = None
    formtype._state.adding = True
    
    formtype.save() # generate default custom fields
    CustomField.objects.filter(ticket_form=formtype).delete() # delete default custom fields

    # Copy fields into new form
    for custom_field in original_custom_fields:
        custom_field.pk = None
        custom_field._state.adding = True
        custom_field.ticket_form = formtype
        custom_field.save()

    return HttpResponseRedirect(reverse('helpdesk:maintain_forms'))


def copy_field(request):
    """
    Asynchonously copy CustomField to another form
    """
    form = EditFormTypeForm.CustomFieldFormSet.form(request.POST)
    form_id = request.POST.get('form_id')
    if form.is_valid():
        cf = form.cleaned_data
        target_form = FormType.objects.get(id=form_id)
        base = cf['field_name'] + '_copy'
        
        high = CustomField.objects.filter(ticket_form=target_form, field_name__regex=(base + r'(\d+)')).order_by('field_name').last()
        if high != None: # if copies exist, use the index after the highest to ensure availability
            import re
            match = re.search(base + r'(\d+)', high.field_name)
            copy = base + str(int(match.group(1)) + 1)
        else: # otherwise just use 1
            copy = base + '1'

        del cf['field_name'] # override field_name manually
        customfield = CustomField(**cf, field_name=copy, ticket_form=target_form)
        customfield.save()

        return JsonResponse({'copied': True})
    else:
        return JsonResponse({'copied': False, 'errors': form.errors})


@helpdesk_staff_member_required
def dashboard(request):
    """
    A quick summary overview for users: A list of their own tickets, a table
    showing ticket counts by queue/status, and a list of unassigned tickets
    with options for them to 'Take' ownership of said tickets.
    """
    # user settings num tickets per page
    if request.user.is_authenticated and hasattr(request.user, 'usersettings_helpdesk'):
        tickets_per_page = request.user.usersettings_helpdesk.tickets_per_page
    else:
        tickets_per_page = 25

    # page vars for the three ticket tables
    user_tickets_page = request.GET.get(_('ut_page'), 1)
    user_tickets_closed_resolved_page = request.GET.get(_('utcr_page'), 1)
    all_tickets_reported_by_current_user_page = request.GET.get(_('atrbcu_page'), 1)
    unassigned_tickets_page = request.GET.get(_('uat_page'), 1)

    huser = HelpdeskUser(request.user)
    user_queues = huser.get_queues()                # Queues in user's default org (or all if superuser)
    active_tickets = Ticket.objects.select_related('queue').exclude(
        status__in=[Ticket.CLOSED_STATUS, Ticket.RESOLVED_STATUS],
    )

    # Get only active tickets
    active_tickets = active_tickets.filter(
        queue__in=user_queues).exclude(status=Ticket.DUPLICATE_STATUS)

    # open & reopened tickets, assigned to current user
    tickets = active_tickets.filter(
        assigned_to=request.user,
    )

    # closed & resolved tickets, assigned to current user
    tickets_closed_resolved = Ticket.objects.select_related('queue').filter(
        assigned_to=request.user,
        status__in=[Ticket.CLOSED_STATUS, Ticket.RESOLVED_STATUS, Ticket.DUPLICATE_STATUS],
        queue__in=user_queues,
    )

    unassigned_tickets = active_tickets.filter(
        assigned_to__isnull=True,
        kbitem__isnull=True,
        queue__in=user_queues,
    )

    kbitems = huser.get_assigned_kb_items()

    # all tickets, reported by current user, in their default org
    all_tickets_reported_by_current_user = ''
    email_current_user = request.user.email
    if email_current_user:
        all_tickets_reported_by_current_user = Ticket.objects.select_related('queue').filter(
            submitter_email=email_current_user,
            queue__in=user_queues
        ).exclude(status=Ticket.DUPLICATE_STATUS).order_by(
            # Custom Ordering: New, Open / Reopened, Replied, Resolved, Closed
            Case(When(status=Ticket.NEW_STATUS, then=0), default=1),
            Case(When(status__in=[Ticket.OPEN_STATUS, Ticket.REOPENED_STATUS], then=1), default=2),
            Case(When(status=Ticket.REPLIED_STATUS, then=2), default=3),
            Case(When(status=Ticket.RESOLVED_STATUS, then=3), default=4),
            Case(When(status=Ticket.CLOSED_STATUS, then=3), default=4),
            '-id'
        )

    tickets_in_queues = Ticket.objects.filter(
        queue__in=user_queues,
    )
    basic_ticket_stats = calc_basic_ticket_stats(tickets_in_queues)

    # The following query builds a grid of queues & ticket statuses,
    # to be displayed to the user. EG:
    #          Open  Resolved
    # Queue 1    10     4
    # Queue 2     4    12
    # code never used (and prone to sql injections)
    # queues = HelpdeskUser(request.user).get_queues().values_list('id', flat=True)
    # from_clause = """FROM    helpdesk_ticket t,
    #                 helpdesk_queue q"""
    # if queues:
    #     where_clause = """WHERE   q.id = t.queue_id AND
    #                     q.id IN (%s)""" % (",".join(("%d" % pk for pk in queues)))
    # else:
    #     where_clause = """WHERE   q.id = t.queue_id"""

    # get user assigned tickets page
    paginator = Paginator(
        tickets, tickets_per_page)
    try:
        tickets = paginator.page(user_tickets_page)
    except PageNotAnInteger:
        tickets = paginator.page(1)
    except EmptyPage:
        tickets = paginator.page(
            paginator.num_pages)

    # get user completed tickets page
    paginator = Paginator(
        tickets_closed_resolved, tickets_per_page)
    try:
        tickets_closed_resolved = paginator.page(
            user_tickets_closed_resolved_page)
    except PageNotAnInteger:
        tickets_closed_resolved = paginator.page(1)
    except EmptyPage:
        tickets_closed_resolved = paginator.page(
            paginator.num_pages)

    # get user submitted tickets page
    paginator = Paginator(
        all_tickets_reported_by_current_user, tickets_per_page)
    try:
        all_tickets_reported_by_current_user = paginator.page(
            all_tickets_reported_by_current_user_page)
    except PageNotAnInteger:
        all_tickets_reported_by_current_user = paginator.page(1)
    except EmptyPage:
        all_tickets_reported_by_current_user = paginator.page(
            paginator.num_pages)

    # get unassigned tickets page
    paginator = Paginator(
        unassigned_tickets, tickets_per_page)
    try:
        unassigned_tickets = paginator.page(
            unassigned_tickets_page)
    except PageNotAnInteger:
        unassigned_tickets = paginator.page(1)
    except EmptyPage:
        unassigned_tickets = paginator.page(
            paginator.num_pages)

    return render(request, 'helpdesk/dashboard.html', {
        'user_tickets': tickets,
        'user_tickets_closed_resolved': tickets_closed_resolved,
        'unassigned_tickets': unassigned_tickets,
        'kbitems': kbitems,
        'all_tickets_reported_by_current_user': all_tickets_reported_by_current_user,
        'basic_ticket_stats': basic_ticket_stats,
        'debug': settings.DEBUG,
    })


dashboard = staff_member_required(dashboard)


def ticket_perm_check(request, ticket):
    huser = HelpdeskUser(request.user, request)
    if not huser.check_default_org(ticket.ticket_form.organization):
        return HttpResponseRedirect(reverse('helpdesk:home'))
    else:
        if not huser.can_access_queue(ticket.queue):
            raise PermissionDenied()
        if not huser.can_access_ticket(ticket):
            raise PermissionDenied()


@helpdesk_staff_member_required
def delete_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    if request.method == 'GET':
        return render(request, 'helpdesk/delete_ticket.html', {
            'ticket': ticket,
            'next': request.GET.get('next', 'home'),
            'debug': settings.DEBUG,
        })
    else:
        ticket.delete()
        redirect_to = 'helpdesk:home'
        if request.POST.get('next') == 'dashboard':
            redirect_to = 'helpdesk:dashboard'
        return HttpResponseRedirect(reverse(redirect_to))


delete_ticket = staff_member_required(delete_ticket)


@helpdesk_staff_member_required
def followup_edit(request, ticket_id, followup_id):
    """Edit followup options with an ability to change the ticket."""
    followup = get_object_or_404(FollowUp, id=followup_id)
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    if request.method == 'GET':
        form = EditFollowUpForm(initial={
            'title': escape(followup.title),
            'ticket': followup.ticket,
            'comment': escape(followup.comment),
            'public': followup.public,
            'new_status': followup.new_status,
        })

        ticketcc_string, show_subscribe = \
            return_ticketccstring_and_show_subscribe(request.user, ticket)

        return render(request, 'helpdesk/followup_edit.html', {
            'followup': followup,
            'ticket': ticket,
            'form': form,
            'ticketcc_string': ticketcc_string,
            'debug': settings.DEBUG,
        })
    elif request.method == 'POST':
        form = EditFollowUpForm(request.POST)
        if form.is_valid():
            title = form.cleaned_data['title']
            _ticket = form.cleaned_data['ticket']
            comment = form.cleaned_data['comment']
            public = form.cleaned_data['public']
            new_status = form.cleaned_data['new_status']
            # will save previous date
            old_date = followup.date
            new_followup = FollowUp(title=title, date=old_date, ticket=_ticket,
                                    comment=comment, public=public,
                                    new_status=new_status,)
            
            # keep old user if one did exist before.
            if followup.user:
                new_followup.user = followup.user
            new_followup.save()
            # get list of old attachments & link them to new_followup
            attachments = FollowUpAttachment.objects.filter(followup=followup)
            for attachment in attachments:
                attachment.followup = new_followup
                attachment.save()
            # delete old followup
            followup.delete()
        return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket.id]))


followup_edit = staff_member_required(followup_edit)


@helpdesk_staff_member_required
def followup_delete(request, ticket_id, followup_id):
    """followup delete for superuser"""

    ticket = get_object_or_404(Ticket, id=ticket_id)
    if not request.user.is_superuser:  # todo
        return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket.id]))

    followup = get_object_or_404(FollowUp, id=followup_id)
    followup.delete()
    return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket.id]))


followup_delete = staff_member_required(followup_delete)


@helpdesk_staff_member_required
def view_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    if 'take' in request.GET:
        # Allow the user to assign the ticket to themselves whilst viewing it.

        # Trick the update_ticket() view into thinking it's being called with
        # a valid POST.
        request.POST = {
            'owner': request.user.id,
            'public': 0,
            'title': ticket.title,
            'comment': ''
        }
        return update_ticket(request, ticket_id)

    if 'subscribe' in request.GET:
        # Allow the user to subscribe him/herself to the ticket whilst viewing it.
        ticket_cc, show_subscribe = \
            return_ticketccstring_and_show_subscribe(request.user, ticket)
        if show_subscribe:
            subscribe_staff_member_to_ticket(ticket, request.user)
            return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket.id]))

    if 'close' in request.GET and ticket.status == Ticket.RESOLVED_STATUS:
        if not ticket.assigned_to:
            owner = 0
        else:
            owner = ticket.assigned_to.id

        # Trick the update_ticket() view into thinking it's being called with
        # a valid POST.
        request.POST = {
            'new_status': Ticket.CLOSED_STATUS,
            'public': 1,
            'owner': owner,
            'title': ticket.title,
            'comment': _('Accepted resolution and closed ticket'),
        }

        return update_ticket(request, ticket_id)

    org = ticket.ticket_form.organization
    users = list_of_helpdesk_staff(org)
    # TODO add back HELPDESK_STAFF_ONLY_TICKET_OWNERS setting
    """if helpdesk_settings.HELPDESK_STAFF_ONLY_TICKET_OWNERS:
        staff_ids = [u.id for u in users if is_helpdesk_staff(u, org=org)]  # todo
        users = users.filter(id__in=staff_ids)"""
    users = users.order_by('last_name', 'first_name', 'email')

    queues = Queue.objects.filter(organization=org)
    queue_choices = _get_queue_choices(queues)
    # TODO: shouldn't this template get a form to begin with?
    form = TicketForm(initial={'due_date': ticket.due_date},
                      queue_choices=queue_choices,
                      form_id=ticket.ticket_form.pk)

    ticketcc_string, show_subscribe = \
        return_ticketccstring_and_show_subscribe(request.user, ticket)

    submitter_userprofile = ticket.get_submitter_userprofile()
    """if submitter_userprofile is not None:
        content_type = ContentType.objects.get_for_model(submitter_userprofile)
        submitter_userprofile_url = reverse(
            'admin:{app}_{model}_change'.format(app=content_type.app_label, model=content_type.model),
            kwargs={'object_id': submitter_userprofile.id}  # TODO problem
        )
    else:"""
    submitter_userprofile_url = None

    display_data = CustomField.objects.filter(ticket_form=ticket.ticket_form).only(
        'label', 'data_type',
        'field_name', 'column',
    )
    extra_data = []
    for values, object in zip(display_data.values(), display_data):  # TODO check how many queries this runs
        if not is_unlisted(values['field_name']) and not values['data_type'] == 'attachment':
            if values['field_name'] in ticket.extra_data:
                values['value'] = ticket.extra_data[values['field_name']]
            else:
                values['value'] = getattr(ticket, values['field_name'], None)
            values['has_column'] = True if object.column else False
            extra_data.append(values)

    prop_display_column = Column.objects.filter(organization=org, column_name=org.property_display_field, table_name='PropertyState').first()
    if prop_display_column:
        if prop_display_column.is_extra_data:
            prop_display_query = f'state__extra_data__{prop_display_column.column_name}'
        else:
            prop_display_query = f'state__{prop_display_column.column_name}'
    else:
        prop_display_query = 'state__address_line_1'

    taxlot_display_column = Column.objects.filter(organization=org, column_name=org.taxlot_display_field, table_name='TaxLotState').first()
    if taxlot_display_column:
        if taxlot_display_column.is_extra_data:
            taxlot_display_query = f'state__extra_data__{taxlot_display_column.column_name}'
        else:
            taxlot_display_query = f'state__{taxlot_display_column.column_name}'
    else:
        taxlot_display_query = 'state__address_line_1'

    properties = list(
        PropertyView.objects.filter(property_id__in=ticket.beam_property.all().values_list('id', flat=True))
        .order_by('property_id', '-cycle__end').distinct('property_id').values('id', 'property_id', address=F(prop_display_query)))
    taxlots = list(
        TaxLotView.objects.filter(taxlot_id__in=ticket.beam_taxlot.all().values_list('id', flat=True))
        .order_by('taxlot_id', '-cycle__end').distinct('taxlot_id').values('id', 'taxlot_id', address=F(taxlot_display_query)))

    for p in properties:
        if p['address'] is None or p['address'] == '':
            p['address'] = '(No address found)'
    for t in taxlots:
        if t['address'] is None or t['address'] == '':
            t['address'] = '(No address found)'

    return render(request, 'helpdesk/ticket.html', {
        'ticket': ticket,
        'submitter_userprofile_url': submitter_userprofile_url,
        'form': form,
        'active_users': users,
        'priorities': Ticket.PRIORITY_CHOICES,
        'preset_replies': PreSetReply.objects.filter(organization=org).filter(
            Q(queues=ticket.queue) | Q(queues__isnull=True)),
        'tag_choices': Tag.objects.filter(organization=org).only('id', 'name', 'color'),
        'ticketcc_string': ticketcc_string,
        'SHOW_SUBSCRIBE': show_subscribe,
        'extra_data': extra_data,
        'properties': properties,
        'taxlots': taxlots,
        'is_staff': is_helpdesk_staff(request.user),
        'debug': settings.DEBUG,
    })


def return_ticketccstring_and_show_subscribe(user, ticket):
    """used in view_ticket() and followup_edit()"""
    # create the ticketcc_string and check whether current user is already
    # subscribed
    username = user.get_username().upper()
    useremail = user.email.upper()
    strings_to_check = list()
    strings_to_check.append(username)
    strings_to_check.append(useremail)

    ticketcc_string = ''
    all_ticketcc = ticket.ticketcc_set.all()
    counter_all_ticketcc = len(all_ticketcc) - 1
    show_subscribe = True
    for i, ticketcc in enumerate(all_ticketcc):
        ticketcc_this_entry = str(ticketcc.display)
        ticketcc_string += ticketcc_this_entry
        if i < counter_all_ticketcc:
            ticketcc_string += ', '
        if strings_to_check.__contains__(ticketcc_this_entry.upper()):
            show_subscribe = False

    # check whether current user is a submitter or assigned to ticket
    assignedto_username = str(ticket.assigned_to).upper()
    strings_to_check = list()
    if ticket.submitter_email is not None:
        submitter_email = ticket.submitter_email.upper()
        strings_to_check.append(submitter_email)
    strings_to_check.append(assignedto_username)
    if strings_to_check.__contains__(username) or strings_to_check.__contains__(useremail):
        show_subscribe = False

    return ticketcc_string, show_subscribe


def subscribe_to_ticket_updates(ticket, user=None, email=None, can_view=True, can_update=False):
    if ticket is not None:
        queryset = TicketCC.objects.filter(ticket=ticket, user=user, email=email)

        # Don't create duplicate entries for subscribers
        if queryset.count() > 0:
            return None

        if user is None and len(email) < 5:
            raise ValidationError(
                _('When you add somebody on Cc, you must provide either a User or a valid email. Email: %s' % email)
            )

        ticketcc = TicketCC(
            ticket=ticket,
            user=user,
            email=email,
            can_view=can_view,
            can_update=can_update
        )
        ticketcc.save()
        return ticketcc


def subscribe_staff_member_to_ticket(ticket, user, email='', can_view=True, can_update=False):
    """used in view_ticket() and update_ticket()"""
    return subscribe_to_ticket_updates(ticket=ticket, user=user, email=email, can_view=can_view, can_update=can_update)


def update_ticket(request, ticket_id, public=False):
    ticket = None
    # So if the update isn't public, or the user isn't a staff member:
    # Locate the ticket through the submitter email and the secret key.
    if not (public or is_helpdesk_staff(request.user)):

        key = request.POST.get('key')
        email = request.POST.get('mail')

        if key and email:
            ticket = Ticket.objects.get(
                id=ticket_id,
                submitter_email__iexact=email,  # TODO: Other email fields should work for this too.  # todo todo
                secret_key__iexact=key
            )

        if not ticket:
            return HttpResponseRedirect(
                '%s?next=%s' % (reverse('helpdesk:login'), request.path)
            )

    if not ticket:
        ticket = get_object_or_404(Ticket, id=ticket_id)

    comment = request.POST.get('comment', '')
    new_status = int(request.POST.get('new_status', ticket.status))
    title = request.POST.get('title', '')
    public = request.POST.get('public', False)
    owner = int(request.POST.get('owner', -1))
    priority = int(request.POST.get('priority', ticket.priority))
    mins_spent = int(request.POST.get("time_spent", '0').strip() or '0')
    time_spent = timedelta(minutes=mins_spent)

    # NOTE: jQuery's default for dates is mm/dd/yy
    # very US-centric but for now that's the only format supported
    # until we clean up code to internationalize a little more
    due_date = request.POST.get('due_date', None)
    due_date = due_date if due_date else None

    utc = pytz.timezone('UTC')
    if due_date is not None:
        # https://stackoverflow.com/questions/26264897/time-zone-field-in-isoformat
        due_date = timezone.get_current_timezone().localize(dateutil.parser.parse(due_date))
        due_date = due_date.astimezone(utc)

    no_changes_excluding_time_spent = all([  # excludes spent time so we can re-use it to send emails
        not request.FILES,
        not comment,
        new_status == ticket.status,
        title == ticket.title,
        priority == int(ticket.priority),
        due_date == ticket.due_date,
        (owner == -1) or (not owner and not ticket.assigned_to) or
        (owner and User.objects.get(id=owner) == ticket.assigned_to),
    ])
    if no_changes_excluding_time_spent and mins_spent == 0:
        return return_to_ticket(request.user, request, helpdesk_settings, ticket)

    # We need to allow the 'ticket' and 'queue' contexts to be applied to the
    # comment.
    context = safe_template_context(ticket)

    from django.template import engines
    template_func = engines['django'].from_string
    # this prevents system from trying to render any template tags
    # broken into two stages to prevent changes from first replace being themselves
    # changed by the second replace due to conflicting syntax
    comment = comment.replace('{%', 'X-HELPDESK-COMMENT-VERBATIM').replace('%}', 'X-HELPDESK-COMMENT-ENDVERBATIM')
    comment = comment.replace(
        'X-HELPDESK-COMMENT-VERBATIM', '{% verbatim %}{%'
    ).replace(
        'X-HELPDESK-COMMENT-ENDVERBATIM', '%}{% endverbatim %}'
    )
    # render the neutralized template
    comment = template_func(comment).render(context)

    if owner == -1 and ticket.assigned_to:
        owner = ticket.assigned_to.id

    f = FollowUp(ticket=ticket, date=timezone.now(), comment=comment)

    if is_helpdesk_staff(request.user, ticket.ticket_form.organization_id):
        f.user = request.user

    f.public = public

    old_status_str = ticket.get_status_display()
    old_status = ticket.status

    reassigned = False
    old_owner = ticket.assigned_to

    if owner != -1:
        if owner != 0 and ((ticket.assigned_to and owner != ticket.assigned_to.id) or not ticket.assigned_to):
            new_user = User.objects.get(id=owner)
            f.title = _('Assigned to %(name)s') % {
                'name': new_user.get_full_name() or new_user.get_username(),
            }
            ticket.assigned_to = new_user
            reassigned = True
        # user changed owner to 'unassign'
        elif owner == 0 and ticket.assigned_to is not None:
            f.title = _('Unassigned')
            ticket.assigned_to = None
        elif ticket.queue.reassign_when_closed and ticket.assigned_to and owner == ticket.assigned_to.id and (
                ticket.queue.default_owner and new_status != ticket.status and new_status == Ticket.CLOSED_STATUS):
            new_user = ticket.queue.default_owner
            f.title = _('Assigned to %(name)s') % {
                'name': new_user.get_full_name() or new_user.get_username(),
            }
            ticket.assigned_to = new_user
            reassigned = True
    elif ticket.queue.reassign_when_closed and (
            ticket.queue.default_owner and new_status != ticket.status and new_status == Ticket.CLOSED_STATUS):
        # if no owner already assigned in this update, set the default owner if the ticket is being closed
        new_user = ticket.queue.default_owner
        f.title = _('Assigned to %(name)s') % {
            'name': new_user.get_full_name() or new_user.get_username(),
        }
        ticket.assigned_to = new_user
        reassigned = True

    submitter_user = User.objects.filter(email=ticket.submitter_email).first()
    is_internal = is_helpdesk_staff(submitter_user, ticket.ticket_form.organization_id)
    user_is_staff = is_helpdesk_staff(request.user, ticket.ticket_form.organization_id)
    closed_statuses = [Ticket.CLOSED_STATUS, Ticket.RESOLVED_STATUS, Ticket.DUPLICATE_STATUS]

    # Handling public-side updates
    if not user_is_staff and ticket.status == Ticket.REPLIED_STATUS and ticket.status == new_status:
        f.new_status = ticket.status = new_status = Ticket.OPEN_STATUS
        ticket.save()
        f.save()

    if new_status != ticket.status:  # Manually setting status to New, Open, Replied, Resolved, Closed, or Duplicate
        ticket.status = new_status
        f.new_status = new_status
        if f.title:
            f.title += ' and %s' % ticket.get_status_display()
        else:
            f.title = '%s' % ticket.get_status_display()
    elif comment and not user_is_staff and ticket.status in closed_statuses:
        # If a non-staff user, set status to Open/Reopened
        f.new_status = ticket.status = Ticket.REOPENED_STATUS
        f.save()

    if not f.title:
        if f.comment:
            f.title = _('Comment')
        else:
            f.title = _('Updated')

    ticket.save()
    f.save()

    files = []
    if request.FILES:
        files = process_attachments(f, request.FILES.getlist('attachment'))

    if title and title != ticket.title:
        c = TicketChange(
            followup=f,
            field=_('Title'),
            old_value=ticket.title,
            new_value=title,
        )
        c.save()
        ticket.title = title

    if new_status != old_status:
        c = TicketChange(
            followup=f,
            field=_('Status'),
            old_value=old_status_str,
            new_value=ticket.get_status_display(),
        )
        c.save()

    if ticket.assigned_to != old_owner:
        old_name = new_name = "no one"
        if old_owner:
            old_name = old_owner.get_full_name() or old_owner.get_username()
        if ticket.assigned_to:
            new_name = ticket.assigned_to.get_full_name() or ticket.assigned_to.get_username()
        c = TicketChange(
            followup=f,
            field=_('Owner'),
            old_value=old_name,
            new_value=new_name,
        )
        c.save()

    if priority != ticket.priority:
        c = TicketChange(
            followup=f,
            field=_('Priority'),
            old_value=ticket.priority,
            new_value=priority,
        )
        c.save()
        ticket.priority = priority

    if due_date != ticket.due_date:
        old_value = ticket.due_date.astimezone(timezone.get_current_timezone()).strftime(CUSTOMFIELD_DATETIME_FORMAT) if ticket.due_date else 'None'
        new_value = due_date.astimezone(timezone.get_current_timezone()).strftime(CUSTOMFIELD_DATETIME_FORMAT) if due_date else 'None'
        c = TicketChange(
            followup=f,
            field=_('Due on'),
            old_value=old_value,
            new_value=new_value,
        )
        c.save()
        ticket.due_date = due_date

    if new_status in (Ticket.RESOLVED_STATUS, Ticket.CLOSED_STATUS):
        if new_status == Ticket.RESOLVED_STATUS or ticket.resolution is None:
            ticket.resolution = comment

    # ticket might have changed above, so we re-instantiate context with the
    # (possibly) updated ticket.
    context = safe_template_context(ticket)
    context.update(
        resolution=ticket.resolution,
        comment=f.comment,
        private=(not public),
    )
    """
    Begin emailing updates to users.
    If public:
        - submitter
        - cc_public
        - extra
    Always:
        - queue_updated (if there's a queue updated user)
        - assigned_user (if there's an assigned user)
        - cc_users
    Never:
        - queue_new
    """

    messages_sent_to = set()
    try:
        messages_sent_to.add(request.user.email)
    except AttributeError:
        pass

    # Emails an update to the owner
    if reassigned:
        template_staff = 'assigned_owner'  # reassignment template
    elif f.new_status == Ticket.RESOLVED_STATUS:
        template_staff = 'resolved_owner'
    elif f.new_status == Ticket.CLOSED_STATUS:
        template_staff = 'closed_owner'
    else:
        template_staff = 'updated_owner'

    if ticket.assigned_to and (not no_changes_excluding_time_spent) and (
        ticket.assigned_to.usersettings_helpdesk.email_on_ticket_change
        or (reassigned and ticket.assigned_to.usersettings_helpdesk.email_on_ticket_assigned)
    ):
        messages_sent_to.update(
            ticket.send_ticket_mail(    # sends the assigned/resolved/closed/updated_owner template to the owner.
                {'assigned_to': (template_staff, context)},
                organization=ticket.ticket_form.organization,
                dont_send_to=messages_sent_to,
                fail_silently=True,
                files=files,
                user=None if not is_helpdesk_staff(request.user, ticket.ticket_form.organization_id) else request.user,
                source='updated (owner)'
            )
        )

    # Send an email about the reassignment to the previously assigned user
    if old_owner and reassigned and old_owner.email not in messages_sent_to:
        send_templated_mail(
            template_name='assigned_cc_user',
            context=context,
            recipients=[old_owner.email],
            sender=ticket.queue.from_address,
            fail_silently=True,
            organization=ticket.ticket_form.organization,
            user=None if not is_helpdesk_staff(request.user, ticket.ticket_form.organization_id) else request.user,
            source='updated (reassigned owner)',
            ticket=ticket
        )

    # Emails an update to users who follow all ticket updates.
    if reassigned:
        template_cc = 'assigned_cc_user'  # reassignment template
    elif f.new_status == Ticket.RESOLVED_STATUS:
        template_cc = 'resolved_cc_user'
    elif f.new_status == Ticket.CLOSED_STATUS:
        template_cc = 'closed_cc_user'
    else:
        template_cc = 'updated_cc_user'

    if not no_changes_excluding_time_spent:
        messages_sent_to.update(
            ticket.send_ticket_mail(
                {'queue_updated': (template_cc, context),
                 'cc_users': (template_cc, context)},
                organization=ticket.ticket_form.organization,
                dont_send_to=messages_sent_to,
                fail_silently=True,
                files=files,
                user=None if not is_helpdesk_staff(request.user, ticket.ticket_form.organization_id) else request.user,
                source="updated (CC'd staff)"
            ))

        # Public users (submitter, public CC, and extra_field emails) are only updated if there's a new status or a comment.
        if public and (
                (f.comment and not no_changes_excluding_time_spent)
                or
                (f.new_status in (Ticket.RESOLVED_STATUS, Ticket.CLOSED_STATUS))):
            if f.new_status == Ticket.RESOLVED_STATUS:
                template = 'resolved_'
            elif f.new_status == Ticket.CLOSED_STATUS:
                template = 'closed_'
            else:
                template = 'updated_'

            roles = {
                'submitter': (template + 'submitter', context),
                'cc_public': (template + 'cc_public', context),
                'extra': (template + 'cc_public', context),
            }
            # todo is this necessary?
            if is_internal:
                roles['submitter'] = (template + 'cc_user', context)

            messages_sent_to.update(
                ticket.send_ticket_mail(
                    roles,
                    organization=ticket.ticket_form.organization,
                    dont_send_to=messages_sent_to,
                    fail_silently=True,
                    files=files,
                    user=None if not is_helpdesk_staff(request.user, ticket.ticket_form.organization_id) else request.user,
                    source='updated (public)'
                ))

    ticket.save()

    # auto subscribe user if enabled
    if helpdesk_settings.HELPDESK_AUTO_SUBSCRIBE_ON_TICKET_RESPONSE and request.user.is_authenticated:
        ticketcc_string, SHOW_SUBSCRIBE = return_ticketccstring_and_show_subscribe(request.user, ticket)
        if SHOW_SUBSCRIBE:
            subscribe_staff_member_to_ticket(ticket, request.user)

    return return_to_ticket(request.user, request, helpdesk_settings, ticket)

@helpdesk_staff_member_required
def start_timer(request):
    if request.method == 'POST':
        ticket_id = request.POST.get('ticket_id')
        ticket = get_object_or_404(Ticket, id=ticket_id)

        new_timespent = TimeSpent(
            user=request.user,
            ticket=ticket,
            start_time=timezone.now(),
        )

        new_timespent.save()

        return JsonResponse({'ticket_id': ticket_id})

@helpdesk_staff_member_required
def stop_timer(request):
    if request.method == 'POST':
        userr = request.user
        ticket_id = request.POST.get('ticket_id')
        ticket = get_object_or_404(Ticket, id=ticket_id)
        for timespent in TimeSpent.objects.filter(user=userr, ticket=ticket, stop_time__isnull=True):
            timespent.stop_time = timezone.now()
            timespent.save()
        return JsonResponse({'total_time_spent': ticket.time_spent_formatted})


@helpdesk_staff_member_required
def get_elapsed_time(request, ticket_id):
    if request.method == 'GET':
        userr = request.user
        ticket = get_object_or_404(Ticket, id=ticket_id)
        timespent = TimeSpent.objects.filter(user=userr, ticket=ticket, stop_time__isnull=True).first()
        if timespent:
            return JsonResponse({'start_time': timespent.start_time})
    return JsonResponse({'start_time': None})

def return_to_ticket(user, request, helpdesk_settings, ticket):
    """Helper function for update_ticket"""
    huser = HelpdeskUser(user, request)
    if is_helpdesk_staff(user) and huser.can_access_ticket(ticket):
        return HttpResponseRedirect(ticket.get_absolute_url())
    else:
        return HttpResponseRedirect(ticket.ticket_url)


@helpdesk_staff_member_required
def mass_update(request):
    tickets = request.POST.get('selected_ids')
    action = request.POST.get('action', None)
    if not (tickets and action):
        return JsonResponse({'update_status': "Ticket Update Failed: No tickets or action"})
    tickets = tickets.split(',')

    if action.startswith('assign_'):
        parts = action.split('_')
        user = User.objects.get(id=parts[1])
        action = 'assign'
    if action == 'kbitem_none':
        kbitem = None
        action = 'set_kbitem'
    if action.startswith('kbitem_'):
        parts = action.split('_')
        kbitem = KBItem.objects.get(id=parts[1])
        action = 'set_kbitem'
    elif action == 'take':
        user = request.user
        action = 'assign'
    elif action == 'merge':
        # Redirect to the Merge View with selected tickets id in the GET request
        return redirect(
            reverse('helpdesk:merge_tickets') + '?' + '&'.join(['tickets=%s' % ticket_id for ticket_id in tickets])
        )
    elif action == 'export':
        return export_ticket_table(request, tickets)
    elif action == 'pair':
        return batch_pair_properties_tickets(request, tickets)

    huser = HelpdeskUser(request.user)
    if action == 'assign':
        ticket_context = Ticket.objects.filter(id__in=tickets).exclude(assigned_to=user)  # exclude any already assigned to the assignee
        if ticket_context:
            org = ticket_context.first().ticket_form.organization
            # update the tickets first
            for t in ticket_context:
                t.assigned_to = user
                t.save()
                f = FollowUp(ticket=t,
                             date=timezone.now(),
                             title=_('Assigned to %(username)s in bulk update' % {
                                 'username': user.get_full_name() or user.get_username()
                             }),
                             public=False,  # DC
                             user=request.user)
                f.save()
            if user.usersettings_helpdesk.email_on_ticket_assign and user != request.user:
                # customized context in order for the bulk template to work properly
                # yes, queues can have different sender addresses, but it seems unnecessary to rewrite everything just for that edge case
                context = {
                    'private': False,
                    'tickets': [{
                        'ticket': mark_safe(t.ticket),
                        'staff_url': mark_safe(t.staff_url),
                        'title': t.title
                    } for t in ticket_context],
                    'queue': {'organization_id': org.id},
                    'ticket': {},
                    'user': request.user.get_full_name() or request.user.get_username()
                }
                ticket_context.first().send_ticket_mail(
                    roles={'assigned_to': ('bulk_assigned_owner', context)},
                    organization=org,
                    dont_send_to=set(),
                    fail_silently=True,
                    user=None if not is_helpdesk_staff(request.user, org.id) else request.user,
                    source='bulk (reassigned owner)'
                )
    else:
        for t in Ticket.objects.filter(id__in=tickets):
            if not huser.can_access_queue(t.queue):
                continue

            if action == 'assign' and t.assigned_to != user:
                t.assigned_to = user
                t.save()
                f = FollowUp(ticket=t,
                             date=timezone.now(),
                             title=_('Assigned to %(username)s in bulk update' % {
                                 'username': user.get_full_name() or user.get_username()
                             }),
                             public=False,  # DC
                             user=request.user)
                f.save()
            elif action == 'unassign' and t.assigned_to is not None:
                t.assigned_to = None
                t.save()
                f = FollowUp(ticket=t,
                             date=timezone.now(),
                             title=_('Unassigned in bulk update'),
                             public=False,  # DC
                             user=request.user)
                f.save()
            elif action == 'set_kbitem':
                t.kbitem = kbitem
                t.save()
                f = FollowUp(ticket=t,
                             date=timezone.now(),
                             title=_('KBItem set in bulk update'),
                             public=False,
                             user=request.user)
                f.save()
            elif action == 'close' and t.status != Ticket.CLOSED_STATUS:
                t.status = Ticket.CLOSED_STATUS
                t.save()
                f = FollowUp(ticket=t,
                             date=timezone.now(),
                             title=_('Closed in bulk update'),
                             public=False,
                             user=request.user,
                             new_status=Ticket.CLOSED_STATUS)
                f.save()
                if t.queue.reassign_when_closed and t.queue.default_owner:
                    new_user = t.queue.default_owner
                    f.title += ' and Assigned to %(name)s' % {
                        'name': new_user.get_full_name() or new_user.get_username(),
                    }
                    t.assigned_to = new_user
                    f.save()
                    t.save()
            elif action == 'close_public' and t.status != Ticket.CLOSED_STATUS:
                t.status = Ticket.CLOSED_STATUS
                t.save()
                f = FollowUp(ticket=t,
                             date=timezone.now(),
                             title=_('Closed in bulk update'),
                             public=True,
                             user=request.user,
                             new_status=Ticket.CLOSED_STATUS)
                f.save()
                if t.queue.reassign_when_closed and t.queue.default_owner:
                    new_user = t.queue.default_owner
                    old_user = t.assigned_to
                    f.title += ' and Assigned to %(name)s' % {
                        'name': new_user.get_full_name() or new_user.get_username(),
                    }
                    t.assigned_to = new_user
                    f.save()
                    t.save()

                # Send email to Submitter, Queue CC, CC'd Users, CC'd Public, Extra Fields, and Owner
                context = safe_template_context(t)
                context.update(resolution=t.resolution,
                               queue=queue_template_context(t.queue),
                               private=False)

                messages_sent_to = set()
                try:
                    messages_sent_to.add(request.user.email)
                except AttributeError:
                    pass

                roles = {
                    'submitter': ('closed_submitter', context),
                    'queue_updated': ('closed_cc_user', context),
                    'cc_users': ('closed_cc_user', context),
                    'cc_public': ('closed_cc_public', context),
                    'extra': ('closed_cc_public', context),
                }
                if t.assigned_to and t.assigned_to.usersettings_helpdesk.email_on_ticket_change:
                    roles['assigned_to'] = ('closed_owner', context)

                messages_sent_to.update(
                    t.send_ticket_mail(
                        roles,
                        organization=t.ticket_form.organization,
                        dont_send_to=messages_sent_to,
                        fail_silently=True,
                        user=None if not is_helpdesk_staff(request.user, t.ticket_form.organization_id) else request.user,
                        source='bulk (closed)'
                    )
                )
                if t.queue.reassign_when_closed and t.queue.default_owner and old_user and old_user.email not in messages_sent_to:
                    send_templated_mail(
                        template_name='closed_owner',
                        context=context,
                        recipients=[old_user.email],
                        sender=t.queue.from_address,
                        fail_silently=True,
                        organization=t.ticket_form.organization,
                        user=None if not is_helpdesk_staff(request.user, t.ticket_form.organization_id) else request.user,
                        source='bulk (closed and auto-reassigned)',
                        ticket=t
                    )

            elif action == 'delete':
                # todo create a note of this somewhere?
                t.delete()

    return JsonResponse({'update_status': "Ticket Update Complete"})


mass_update = staff_member_required(mass_update)


# Prepare ticket attributes which will be displayed in the table to choose which value to keep when merging
# commented out are duplicate with customfields TODO delete later
ticket_attributes = (
    ('created', _('Created date')),
    # ('due_date', _('Due on')),
    ('get_status_display', _('Status')),
    # ('submitter_email', _('Submitter email')),
    ('assigned_to', _('Owner')),
    # ('description', _('Description')),
    ('resolution', _('Resolution')),
)


@staff_member_required
def merge_tickets(request):
    """
    An intermediate view to merge up to 3 tickets in one main ticket.
    The user has to first select which ticket will receive the other tickets information and can also choose which
    data to keep per attributes as well as custom fields.
    Follow-ups and ticketCC will be moved to the main ticket and other tickets won't be able to receive new answers.
    """
    ticket_select_form = MultipleTicketSelectForm(request.GET or None)
    tickets = custom_fields = None
    if ticket_select_form.is_valid():
        tickets = ticket_select_form.cleaned_data.get('tickets')
        custom_fields = CustomField.objects.filter(ticket_form_id__in=list(tickets.values_list('ticket_form__id'))
                                                   ).order_by('field_name').distinct('field_name').exclude(
                    field_name__in=['cc_emails', 'attachment', 'queue']
                )

        default = _('Not defined')
        for ticket in tickets:
            ticket.values = {}
            # Prepare the value for each attribute of this ticket
            for attribute, display_name in ticket_attributes:
                if attribute.startswith('get_') and attribute.endswith('_display'):
                    # Hack to call methods like get_FIELD_display()
                    value = getattr(ticket, attribute, default)()
                else:
                    value = getattr(ticket, attribute, default)
                ticket.values[attribute] = {
                    'value': value,
                    'checked': str(ticket.id) == request.POST.get(attribute)
                }
            # Prepare the value for each custom fields of this ticket
            for custom_field in custom_fields:
                try:
                    value = getattr(ticket, custom_field.field_name)
                except AttributeError:
                    # Search in extra_data
                    if custom_field.field_name in ticket.extra_data.keys():
                        value = ticket.extra_data[custom_field.field_name]
                    else:
                        value = default
                ticket.values[custom_field.field_name] = {
                    'value': value,
                    'checked': str(ticket.id) == request.POST.get(custom_field.field_name)
                }

        if request.method == 'POST':
            # Find which ticket has been chosen to be the main one
            try:
                chosen_ticket = tickets.get(id=request.POST.get('chosen_ticket'))
            except Ticket.DoesNotExist:
                ticket_select_form.add_error(
                    field='tickets',
                    error=_('Please choose a ticket in which the others will be merged into.')
                )
            else:
                # Save ticket fields values
                for attribute, display_name in ticket_attributes:
                    id_for_attribute = request.POST.get(attribute)
                    if id_for_attribute != chosen_ticket.id:
                        try:
                            selected_ticket = tickets.get(id=id_for_attribute)
                        except (Ticket.DoesNotExist, ValueError):
                            continue

                        # Check if attr is a get_FIELD_display
                        if attribute.startswith('get_') and attribute.endswith('_display'):
                            # Keep only the FIELD part
                            attribute = attribute[4:-8]
                        # Get value from selected ticket and then save it on the chosen ticket
                        value = getattr(selected_ticket, attribute)
                        setattr(chosen_ticket, attribute, value)
                # Save custom fields values
                for custom_field in custom_fields:
                    id_for_custom_field = request.POST.get(custom_field.field_name)
                    if id_for_custom_field != chosen_ticket.id:
                        try:
                            selected_ticket = tickets.get(id=id_for_custom_field)
                        except (Ticket.DoesNotExist, ValueError):
                            continue
                        try:
                            value = getattr(selected_ticket, custom_field.field_name)
                            setattr(chosen_ticket, custom_field.field_name, value)
                        except AttributeError:
                            # Search in extra_data
                            if custom_field.field_name in ticket.extra_data.keys():
                                value = selected_ticket.extra_data[custom_field.field_name]
                            else:
                                value = default
                            chosen_ticket.extra_data[custom_field.field_name] = value

                # Save changes
                chosen_ticket.save()

                # For other tickets, save the link to the ticket in which they have been merged to
                # and set status to DUPLICATE
                for ticket in tickets.exclude(id=chosen_ticket.id):
                    ticket.merged_to = chosen_ticket
                    ticket.status = Ticket.DUPLICATE_STATUS
                    ticket.save()

                    # Send mail to submitter email and ticket CC to let them know ticket has been merged
                    context = safe_template_context(ticket)
                    context['private'] = False
                    if ticket.submitter_email:
                        send_templated_mail(
                            template_name='merged',
                            context=context,
                            recipients=[ticket.submitter_email],
                            bcc=[cc.email_address for cc in ticket.ticketcc_set.select_related('user')],
                            sender=ticket.queue.from_address,
                            fail_silently=True,
                            organization=ticket.ticket_form.organization,
                            user=None if not is_helpdesk_staff(request.user, ticket.ticket_form.organization_id) else request.user,
                            source='merging',
                            ticket=chosen_ticket
                        )

                    # Move all followups and update their title to know they come from another ticket
                    ticket.followup_set.update(
                        ticket=chosen_ticket,
                        title=_(('[Merged from #%(id)d] %(title)s') % {'id': ticket.id, 'title': ticket.title})[:200],
                    )

                    # Move all emails to the chosen ticket
                    ticket.emails.update(ticket_id=chosen_ticket.id)

                    # Add submitter_email, assigned_to email and ticketcc to chosen ticket if necessary
                    chosen_ticket.add_email_to_ticketcc_if_not_in(email=ticket.submitter_email)
                    if ticket.assigned_to and ticket.assigned_to.email:
                        chosen_ticket.add_email_to_ticketcc_if_not_in(email=ticket.assigned_to.email)
                    for ticketcc in ticket.ticketcc_set.all():
                        chosen_ticket.add_email_to_ticketcc_if_not_in(ticketcc=ticketcc)
                return redirect(chosen_ticket)

    return render(request, 'helpdesk/ticket_merge.html', {
        'tickets': tickets,
        'ticket_attributes': ticket_attributes,
        'custom_fields': custom_fields,
        'ticket_select_form': ticket_select_form,
        'debug': settings.DEBUG,
    })

@helpdesk_staff_member_required
def ticket_list(request):
    context = {}
    huser = HelpdeskUser(request.user)
    org = request.user.default_organization.helpdesk_organization

    default_query_params = {
        'filtering': {'status__in': [1, 2, 6, 7]},
        'sorting': 'created',
        'desc': True,
        'search_string': '',
    }
    query_params = {
        'filtering': {},
        'sorting': None,
        'desc': False,
        'search_string': '',
    }

    int_list_filter_in_params = {
        'u': 'assigned_to__id__in',
        's': 'status__in',
        'kb': 'kbitem__in',
        'queue': 'queue__id__in',
        'form': 'ticket_form__id__in',
        'priority': 'priority__in',
        't': 'tags__in',
    }
    int_list_filter_null_params = dict([
        ('u', 'assigned_to__id__isnull'),
        ('kb', 'kbitem__isnull'),
        ('t', 'tags__isnull'),
    ])

    int_filter_params = {
        'inv-min': 'paired_count__gte',
        'inv-max': 'paired_count__lte',
    }

    DATE_REGEX = r"(?:[0-9]{2})?[0-9]{2}-(?:1[0-2]|0?[1-9])-(?:3[01]|[12][0-9]|0?[1-9])"
    DATE_PARAM_KEY = {
        'from': 'created__date__gte',
        'to': 'created__date__lte',
        're-from': 'last_reply__date__gte',
        're-to': 'last_reply__date__lte',
    }
    SORT_CHOICES = [
        'created',
        'last_reply',
        'title',
        'queue',
        'status',
        'priority',
        'assigned_to',
        'paired_count',
        'submitter_email',
    ]
    TICKETS_PER_PAGE = [
        10,
        25,
        50,
        100
    ]

    if request.GET.get('search_type', None) == 'header':
        query = request.GET.get('q')
        filter = None
        if query.find('-') > 0:
            try:
                queue, id = Ticket.queue_and_id_from_query(query)
                id = int(id)
            except ValueError:
                id = None

            if id:
                filter = {'queue__slug': queue, 'id': id}
        else:
            try:
                query = int(query)
            except ValueError:
                query = None

            if query:
                filter = {'id': int(query)}

        if filter:
            try:
                ticket = huser.get_tickets_in_queues().get(**filter)
                return HttpResponseRedirect(ticket.staff_url)
            except Ticket.DoesNotExist:
                # Go on to standard keyword searching
                pass

    try:
        saved_query, query_params = load_saved_query(request, query_params)
    except QueryLoadError:
        return HttpResponseRedirect(reverse('helpdesk:list'))

    if saved_query:
        pass
    elif not {'q', 'sort', 'desc', *int_list_filter_in_params.keys(), *DATE_PARAM_KEY.keys()}.intersection(request.GET):
        # Fall-back if no querying is being done
        query_params = deepcopy(default_query_params)
    else:
        for param, filter_command in int_list_filter_in_params.items():
            if not request.GET.get(param) is None:
                patterns = request.GET.getlist(param)
                try:
                    pattern_pks = [int(pattern) for pattern in patterns]
                    if -1 in pattern_pks:
                        if param in int_list_filter_null_params:
                            query_params['filtering'][int_list_filter_null_params[param]] = True
                    else:
                        query_params['filtering'][filter_command] = pattern_pks
                except ValueError:
                    pass

        for param, filter_command in int_filter_params.items():
            if not request.GET.get(param) is None:
                pattern = request.GET.get(param)
                try:
                    pattern_pk = int(pattern)
                    if pattern_pk >= 0:
                        query_params['filtering'][filter_command] = pattern_pk
                except ValueError:
                    pass

        for param, filter_command in DATE_PARAM_KEY.items():
            if not request.GET.get(param, '') == '':
                patterns = request.GET.get(param)
                match = re.match(DATE_REGEX, patterns)
                if match:
                    query_params['filtering'][filter_command] = match.group(0)

        # KEYWORD SEARCHING
        q = request.GET.get('q', '')
        context['query'] = q
        query_params['search_string'] = q

        sort = request.GET.get('sort', None)
        if sort not in SORT_CHOICES:
            sort = 'created'
        query_params['sorting'] = sort

        sortreverse = request.GET.get('desc', None)
        if sortreverse == 'on':
            query_params['desc'] = True

        """
        filter_contains_params = dict([
            ('ticket', 'title__icontains'),
            ('priority', 'priority__icontains'), # need to search words not numbers
            ('queue', 'queue__title__icontains'),
            ('form', 'ticket_form__name__icontains'),
            ('status', 'status__icontains'), # need to search words not numbers
            ('assigned_to', 'assigned_to__username__icontains'), # currently only checks this one
            ('assigned_to', 'assigned_to__email__icontains'), 
            ('submitter', 'submitter_email__icontains'),
            ('paired_count', 'paired_count__icontains'), # this needs to be updated once I figure out how to filter on paired_count
            ('created', 'created__icontains'),
            ('last_reply', 'last_reply__icontains'),
            ('due_date', 'due_date__icontains'),
            # ('time_spent', 'status__icontains'), # this needs to be updated once I figure out how to filter on time_spent
            ('kbitem', 'kbitem__title__icontains'),
        ])
        
        # breakpoint()
        for item in request.POST.items():
            if item[0].endswith('_filter') and item[1] != '':
                col = item[0].replace('_filter', '')
                query_params['filtering'][filter_contains_params[col]] = item[1]
        """
    urlsafe_query = query_to_base64(query_params)  # urlsafe_query is only used to save saved queries
    qs = Query(huser, query_params=query_params).refresh_query()

    # After query is run, replaces null-filters with in-filters=[-1], so page can properly display that filter.
    for param, null_query in int_list_filter_null_params.items():
        popped = query_params['filtering'].pop(null_query, None)
        if popped is not None:
            query_params['filtering'][int_list_filter_in_params[param]] = [-1]

    # Choices/data for the rest of the page
    user_saved_queries = SavedSearch.objects.filter(user=request.user, organization=org)  # todo: + org
    form_choices = FormType.objects.filter(organization=org).order_by('name')
    user_choices = list_of_helpdesk_staff(org).order_by('last_name', 'first_name', 'email').values('id', 'username', 'first_name', 'last_name')
    tag_choices = Tag.objects.filter(organization=org).order_by('name').values('id', 'name', 'color')

    """
    # Get extra data columns to be displayed if only 1 queue is selected
    extra_data_columns = {}
    if len(query_params['filtering'].get('queue__id__in', [])) == 1:
        extra_data_columns = get_extra_data_columns(query_params['filtering']['queue__id__in'][0])
    """

    page = request.GET.get('p', '1')
    try:
        page = int(page)
    except ValueError:
        page = 1

    if request.GET.get('n', None):
        tickets_per_page = request.GET.get('n')
        try:
            tickets_per_page = int(tickets_per_page)
            if tickets_per_page not in TICKETS_PER_PAGE:
                tickets_per_page = TICKETS_PER_PAGE[1]
        except ValueError:
            tickets_per_page = TICKETS_PER_PAGE[1]
    elif request.user.is_authenticated and hasattr(request.user, 'usersettings_helpdesk'):
        tickets_per_page = request.user.usersettings_helpdesk.tickets_per_page
    else:
        tickets_per_page = 25

    tickets = qs
    paginator = Paginator(tickets, tickets_per_page)
    try:
        tickets = paginator.page(page)
    except PageNotAnInteger:
        tickets = paginator.page(1)
    except EmptyPage:
        tickets = paginator.page(paginator.num_pages)

    return render(request, 'helpdesk/ticket_list.html', dict(
        context,
        tickets_per_page=tickets_per_page,
        ticket_list=tickets,
        user_choices=user_choices,
        queue_choices=huser.get_queues(),
        form_choices=form_choices,
        priority_choices=Ticket.PRIORITY_CHOICES,
        status_choices=Ticket.STATUS_CHOICES,
        tag_choices=tag_choices,
        urlsafe_query=urlsafe_query,
        user_saved_queries=user_saved_queries,
        query_params=query_params,
        from_saved_query=saved_query is not None,
        saved_query=saved_query,
        # extra_data_columns=extra_data_columns,
        debug=settings.DEBUG,
        ticket_index_start=paginator.page(page).start_index(),
        ticket_index_end=paginator.page(page).end_index(),
        ticket_total=paginator.count,
    ))

ticket_list = staff_member_required(ticket_list)


class QueryLoadError(Exception):
    pass


def load_saved_query(request, query_params=None):
    saved_query = None

    if request.GET.get('saved-query', None):
        try:
            saved_query = SavedSearch.objects.get(
                Q(pk=request.GET.get('saved-query')) &
                ((Q(shared=True) & ~Q(opted_out_users__in=[request.user])) | Q(user=request.user))
            )
        except (SavedSearch.DoesNotExist, ValueError):
            raise QueryLoadError()

        try:
            # we get a string like: b'stuff'
            # so leave of the first two chars (b') and last (')
            if saved_query.query.startswith('b\''):
                b64query = saved_query.query[2:-1]
            else:
                b64query = saved_query.query
            query_params = query_from_base64(b64query)
        except json.JSONDecodeError:
            raise QueryLoadError()
    return (saved_query, query_params)

@helpdesk_staff_member_required
@api_view(['GET'])
def timeline_ticket_list(request, query):
    query = Query(HelpdeskUser(request.user), base64query=query)
    return (JsonResponse(query.get_timeline_context(), status=status.HTTP_200_OK))


@helpdesk_staff_member_required
def edit_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    form = EditTicketForm(request.POST or None, instance=ticket)
    if form.is_valid():
        ticket = form.save()
        return redirect(ticket)

    return render(request, 'helpdesk/edit_ticket.html', {'form': form, 'ticket': ticket, 'errors': form.errors, 'debug': settings.DEBUG})


edit_ticket = staff_member_required(edit_ticket)


def attach_ticket_to_property_milestone(request, ticket):
    from seed.models import PropertyMilestone, Note
    from django.utils.timezone import now

    property_milestone_id = request.GET.get('property_milestone_id', None)
    pm = PropertyMilestone.objects.filter(id=property_milestone_id).first()
    if pm:
        pm.ticket = ticket
        # Only set submission_date if it has never been set
        if not pm.submission_date:
            pm.submission_date = timezone.now() if ticket.created is None else ticket.created
        pm.implementation_status = PropertyMilestone.MILESTONE_IN_REVIEW
        pm.save()

        note_kwargs = {'organization_id': request.GET.get('org_id'),
                       'user': request.user if request.user.is_authenticated else None,
                       'name': 'Automatically Created', 'property_view': pm.property_view,
                       'note_type': Note.LOG,
                       'log_data': [{'model': 'PropertyMilestone', 'name': pm.milestone.name,
                                     'action': 'edited with the following:'},
                                    {'field': 'Milestone Submitted Ticket',
                                     'previous_value': 'None', 'new_value': f'Ticket ID {pm.ticket.id}',
                                     'state_id': pm.property_view.state.id}]
                       }
        Note.objects.create(**note_kwargs)


class CreateTicketView(MustBeStaffMixin, abstract_views.AbstractCreateTicketMixin, FormView):
    template_name = 'helpdesk/create_ticket.html'
    form_class = TicketForm
    form_id = None

    def get_initial(self):
        initial_data = super().get_initial()
        return initial_data

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        queues = HelpdeskUser(self.request.user, self.request).get_queues()
        kwargs["queue_choices"] = _get_queue_choices(queues)
        kwargs['form_id'] = self.form_id
        return kwargs

    def form_valid(self, form):
        self.ticket = form.save(form_id=self.form_id, user=self.request.user if self.request.user.is_authenticated else None)
        if self.request.GET.get('milestone_beam_redirect', False):
            # Pair Ticket to Milestone
            attach_ticket_to_property_milestone(self.request, self.ticket)
        return super().form_valid(form)

    def get_success_url(self):
        request = self.request
        if HelpdeskUser(request.user, request).can_access_queue(self.ticket.queue):
            return self.ticket.get_absolute_url()
        else:
            return reverse('helpdesk:dashboard')


@helpdesk_staff_member_required
def raw_details(request, type):
    # TODO: This currently only supports spewing out 'PreSetReply' objects,
    # in the future it needs to be expanded to include other items. All it
    # does is return a plain-text representation of an object.

    if type not in ('preset',):
        raise Http404

    if type == 'preset' and request.GET.get('id', False):
        try:
            preset = PreSetReply.objects.get(id=request.GET.get('id'))
            return HttpResponse(preset.body)
        except PreSetReply.DoesNotExist:
            raise Http404

    raise Http404


raw_details = staff_member_required(raw_details)


@helpdesk_staff_member_required
def hold_ticket(request, ticket_id, unhold=False):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    if unhold:
        ticket.on_hold = False
        title = _('Ticket taken off hold')
    else:
        ticket.on_hold = True
        title = _('Ticket placed on hold')

    f = FollowUp(
        ticket=ticket,
        user=request.user,
        title=title,
        date=timezone.now(),
        public=True,
    )
    f.save()

    ticket.save()

    return HttpResponseRedirect(ticket.get_absolute_url())


hold_ticket = staff_member_required(hold_ticket)


@helpdesk_staff_member_required
def unhold_ticket(request, ticket_id):
    return hold_ticket(request, ticket_id, unhold=True)


unhold_ticket = staff_member_required(unhold_ticket)


@helpdesk_staff_member_required
def rss_list(request):
    return render(request, 'helpdesk/rss_list.html', {'queues': Queue.objects.all(), 'debug': settings.DEBUG})


rss_list = staff_member_required(rss_list)


@helpdesk_staff_member_required
def report_index(request):
    huser = HelpdeskUser(request.user)
    user_queues = huser.get_queues()
    Tickets = Ticket.objects.filter(queue__in=user_queues)
    number_tickets = Tickets.count()
    saved_query = request.GET.get('saved-query', None)

    basic_ticket_stats = calc_basic_ticket_stats(Tickets)

    # The following query builds a grid of queues & ticket statuses,
    # to be displayed to the user. EG:
    #          Open  Resolved
    # Queue 1    10     4
    # Queue 2     4    12
    Queues = user_queues if user_queues else Queue.objects.all()

    dash_tickets = []
    for queue in Queues:
        dash_ticket = {
            'queue': queue.id,
            'name': queue.title,
            'open': queue.ticket_set.filter(status__in=[1, 2]).count(),
            'resolved': queue.ticket_set.filter(status=3).count(),
            'closed': queue.ticket_set.filter(status=4).count(),
            'time_spent': format_time_spent(queue.time_spent),
            'dedicated_time': format_time_spent(queue.dedicated_time)
        }
        dash_tickets.append(dash_ticket)

    return render(request, 'helpdesk/report_index.html', {
        'number_tickets': number_tickets,
        'saved_query': saved_query,
        'basic_ticket_stats': basic_ticket_stats,
        'dash_tickets': dash_tickets,
        'debug': settings.DEBUG,
    })


report_index = staff_member_required(report_index)


@helpdesk_staff_member_required
def run_report(request, report):
    if Ticket.objects.all().count() == 0 or report not in (
            'queuemonth', 'usermonth', 'queuestatus', 'queuepriority', 'userstatus',
            'userpriority', 'userqueue', 'daysuntilticketclosedbymonth'):
        return HttpResponseRedirect(reverse("helpdesk:report_index"))

    report_queryset = Ticket.objects.all().select_related().filter(
        queue__in=HelpdeskUser(request.user).get_queues()
    )

    try:
        saved_query, query_params = load_saved_query(request)
    except QueryLoadError:
        return HttpResponseRedirect(reverse('helpdesk:report_index'))

    if request.GET.get('saved-query', None):
        report_queryset = Query(report_queryset, query_to_base64(query_params)).__run__(report_queryset)

    from collections import defaultdict
    summarytable = defaultdict(lambda: "")
    # a second table for more complex queries
    summarytable2 = defaultdict(lambda: "")

    first_ticket = Ticket.objects.all().order_by('created')[0]
    first_month = first_ticket.created.month
    first_year = first_ticket.created.year

    last_ticket = Ticket.objects.all().order_by('-created')[0]
    last_month = last_ticket.created.month
    last_year = last_ticket.created.year

    periods = []
    year, month = first_year, first_month
    working = True
    periods.append("%s-%s" % (year, month))

    while working:
        month += 1
        if month > 12:
            year += 1
            month = 1
        if (year > last_year) or (month > last_month and year >= last_year):
            working = False
        periods.append("%s-%s" % (year, month))

    if report == 'userpriority':
        title = _('User by Priority')
        col1heading = _('User')
        possible_options = [t[1].title() for t in Ticket.PRIORITY_CHOICES]
        charttype = 'bar'

    elif report == 'userqueue':
        title = _('User by Queue')
        col1heading = _('User')
        queue_options = HelpdeskUser(request.user).get_queues()
        possible_options = [q.title for q in queue_options]
        charttype = 'bar'

    elif report == 'userstatus':
        title = _('User by Status')
        col1heading = _('User')
        possible_options = [s[1].title() for s in Ticket.STATUS_CHOICES]
        charttype = 'bar'

    elif report == 'usermonth':
        title = _('User by Month')
        col1heading = _('User')
        possible_options = periods
        charttype = 'date'

    elif report == 'queuepriority':
        title = _('Queue by Priority')
        col1heading = _('Queue')
        possible_options = [t[1].title() for t in Ticket.PRIORITY_CHOICES]
        charttype = 'bar'

    elif report == 'queuestatus':
        title = _('Queue by Status')
        col1heading = _('Queue')
        possible_options = [s[1].title() for s in Ticket.STATUS_CHOICES]
        charttype = 'bar'

    elif report == 'queuemonth':
        title = _('Queue by Month')
        col1heading = _('Queue')
        possible_options = periods
        charttype = 'date'

    elif report == 'daysuntilticketclosedbymonth':
        title = _('Average days until ticket was closed (by created month)')
        col1heading = _('Queue')
        possible_options = periods
        charttype = 'date'

    metric3 = False
    for ticket in report_queryset:
        if report == 'userpriority':
            metric1 = u'%s' % ticket.get_assigned_to
            metric2 = u'%s' % ticket.get_priority_display()

        elif report == 'userqueue':
            metric1 = u'%s' % ticket.get_assigned_to
            metric2 = u'%s' % ticket.queue.title

        elif report == 'userstatus':
            metric1 = u'%s' % ticket.get_assigned_to
            metric2 = u'%s' % ticket.get_status_display()

        elif report == 'usermonth':
            metric1 = u'%s' % ticket.get_assigned_to
            metric2 = u'%s-%s' % (ticket.created.year, ticket.created.month)

        elif report == 'queuepriority':
            metric1 = u'%s' % ticket.queue.title
            metric2 = u'%s' % ticket.get_priority_display()

        elif report == 'queuestatus':
            metric1 = u'%s' % ticket.queue.title
            metric2 = u'%s' % ticket.get_status_display()

        elif report == 'queuemonth':
            metric1 = u'%s' % ticket.queue.title
            metric2 = u'%s-%s' % (ticket.created.year, ticket.created.month)

        elif report == 'daysuntilticketclosedbymonth':
            if ticket.status not in [Ticket.CLOSED_STATUS, Ticket.RESOLVED_STATUS, Ticket.DUPLICATE_STATUS]: continue
            metric1 = u'%s' % ticket.queue.title
            metric2 = u'%s-%s' % (ticket.created.year, ticket.created.month)
            metric3 = ticket.modified - ticket.created
            metric3 = metric3.days

        if (metric1, metric2) in summarytable: 
            summarytable[metric1, metric2] += 1
        else: 
            summarytable[metric1, metric2] = 1

        if metric3:
            if report == 'daysuntilticketclosedbymonth':
                if (metric1, metric2) in summarytable2: 
                    summarytable2[metric1, metric2] += metric3
                else: 
                    summarytable2[metric1, metric2] = metric3

    table = []

    if report == 'daysuntilticketclosedbymonth':
        for key in summarytable2.keys():
            summarytable[key] = round(summarytable2[key] / summarytable[key], 1)
            if float(summarytable[key]) == int(summarytable[key]): 
                summarytable[key] = int(summarytable[key])

    header1 = sorted(set(list(i for i, _ in summarytable.keys())))

    column_headings = [col1heading] + possible_options

    # Prepare a dict to store totals for each possible option
    totals = {}
    # Pivot the data so that 'header1' fields are always first column
    # in the row, and 'possible_options' are always the 2nd - nth columns.
    for item in header1:
        data = []
        for hdr in possible_options:
            if hdr not in totals.keys():
                totals[hdr] = summarytable[item, hdr] or 0
            else:
                totals[hdr] += summarytable[item, hdr] or 0
            data.append(summarytable[item, hdr])
        table.append([item] + data)

    # Zip data and headers together in one list for Morris.js charts
    # will get a list like [(Header1, Data1), (Header2, Data2)...]
    seriesnum = 0
    morrisjs_data = []
    for label in column_headings[1:]:
        seriesnum += 1
        datadict = {"x": label}
        for n in range(0, len(table)):
            datadict[n] = 0 if table[n][seriesnum] == "" else table[n][seriesnum]
        morrisjs_data.append(datadict)

    series_names = []
    for series in table:
        series_names.append(series[0])

    # Add total row to table
    total_data = ['Total']
    for hdr in possible_options:
        total_data.append(str(totals[hdr]))

    return render(request, 'helpdesk/report_output.html', {
        'title': title,
        'charttype': charttype,
        'data': table,
        'total_data': total_data,
        'headings': column_headings,
        'series_names': series_names,
        'morrisjs_data': morrisjs_data,
        'from_saved_query': saved_query is not None,
        'saved_query': saved_query,
        'debug': settings.DEBUG,
    })


run_report = staff_member_required(run_report)


@helpdesk_staff_member_required
def save_query(request):
    title = request.POST.get('title', None)
    shared = request.POST.get('shared', False)
    visible_cols = request.POST.get('visible', '').split(',')

    if shared == 'on':  # django only translates '1', 'true', 't' into True
        shared = True
    else:
        shared = False
    query_encoded = request.POST.get('query_encoded', None)

    if not title or not query_encoded:
        return HttpResponseRedirect(reverse('helpdesk:list'))

    query_unencoded = query_from_base64(query_encoded)
    query_unencoded['visible_cols'] = visible_cols
    query_encoded = query_to_base64(query_unencoded)

    org = request.user.default_organization.helpdesk_organization
    query = SavedSearch(title=title, shared=shared, query=query_encoded, user=request.user, organization=org)
    query.save()

    return HttpResponseRedirect('%s?saved-query=%s' % (reverse('helpdesk:list'), query.id))


save_query = staff_member_required(save_query)


@helpdesk_staff_member_required
def delete_saved_query(request, id):
    query = get_object_or_404(SavedSearch, id=id, user=request.user)

    if request.method == 'POST':
        query.delete()
        return HttpResponseRedirect(reverse('helpdesk:list'))


delete_saved_query = staff_member_required(delete_saved_query)


@helpdesk_staff_member_required
def reject_saved_query(request, id):
    user = request.user
    query = get_object_or_404(SavedSearch, id=id)

    query.opted_out_users.add(user)
    return HttpResponseRedirect(reverse('helpdesk:list'))


reject_saved_query = staff_member_required(reject_saved_query)


@helpdesk_staff_member_required
def reshare_saved_query(request, id):
    user = request.user
    query = get_object_or_404(SavedSearch, id=id, user=user)

    query.opted_out_users.clear()
    query.shared = True
    query.save()
    return HttpResponseRedirect(reverse('helpdesk:list') + '?saved-query=%s' % query.id)


reshare_saved_query = staff_member_required(reshare_saved_query)


@helpdesk_staff_member_required
def unshare_saved_query(request, id):
    user = request.user
    query = get_object_or_404(SavedSearch, id=id, user=user)

    query.shared = False
    query.save()
    return HttpResponseRedirect(reverse('helpdesk:list') + '?saved-query=%s' % query.id)


unshare_saved_query = staff_member_required(unshare_saved_query)


class EditUserSettingsView(MustBeStaffMixin, UpdateView):
    template_name = 'helpdesk/user_settings.html'
    form_class = UserSettingsForm
    model = UserSettings
    success_url = reverse_lazy('helpdesk:dashboard')

    def get_object(self):
        return UserSettings.objects.get_or_create(user=self.request.user)[0]


@helpdesk_staff_member_required
def email_ignore(request):
    org = request.user.default_organization.helpdesk_organization

    return render(request, 'helpdesk/email_ignore_list.html', {
        'ignore_list': IgnoreEmail.objects.filter(organization=org),
        'debug': settings.DEBUG,
    })


email_ignore = staff_member_required(email_ignore)


@helpdesk_staff_member_required
def email_ignore_add(request):
    if request.method == 'POST':
        form = EmailIgnoreForm(request.POST, organization=request.user.default_organization.helpdesk_organization)
        if form.is_valid():
            saved_form = form.save(commit=False)
            saved_form.organization = request.user.default_organization.helpdesk_organization
            saved_form.save()
            saved_form.queues.set(form.cleaned_data['queues'])
            return HttpResponseRedirect(reverse('helpdesk:email_ignore'))
    else:
        form = EmailIgnoreForm(request.GET, organization=request.user.default_organization.helpdesk_organization)

    return render(request, 'helpdesk/email_ignore_add.html', {'form': form, 'debug': settings.DEBUG})


email_ignore_add = staff_member_required(email_ignore_add)


@helpdesk_staff_member_required
def email_ignore_edit(request, id):
    ignored_address = get_object_or_404(IgnoreEmail, id=id)
    form = EmailIgnoreForm(request.POST or None, instance=ignored_address)
    if form.is_valid():
        form.save()
        return HttpResponseRedirect(reverse('helpdesk:email_ignore'))

    return render(request, 'helpdesk/email_ignore_add.html', {'form': form, 'debug': settings.DEBUG})


email_ignore_edit = helpdesk_staff_member_required(email_ignore_edit)


@helpdesk_staff_member_required
def email_ignore_del(request, id):
    ignore = get_object_or_404(IgnoreEmail, id=id)
    if request.method == 'POST':
        ignore.delete()
        return HttpResponseRedirect(reverse('helpdesk:email_ignore'))
    else:
        return render(request, 'helpdesk/email_ignore_del.html', {'ignore': ignore, 'debug': settings.DEBUG})


email_ignore_del = helpdesk_staff_member_required(email_ignore_del)


@helpdesk_staff_member_required
def preset_reply_list(request):
    org = request.user.default_organization.helpdesk_organization
    replies = PreSetReply.objects.filter(organization=org)
    reply_list = []
    for reply in replies:
        reply_list.append({
            'id': reply.id,
            'queues': reply.queues.all(),
            'name': reply.name,
            'body': reply.get_markdown()
        })

    return render(request, 'helpdesk/preset_reply_list.html', {
        'reply_list': reply_list,
        'debug': settings.DEBUG,
    })


preset_reply_list = helpdesk_staff_member_required(preset_reply_list)


@helpdesk_staff_member_required
def preset_reply_add(request):
    org = request.user.default_organization.helpdesk_organization
    if request.method == 'POST':
        form = PreSetReplyForm(request.POST, organization=org)
        if form.is_valid():
            saved_form = form.save(commit=False)
            saved_form.organization = org
            saved_form.save()
            saved_form.queues.set(form.cleaned_data['queues'])
            return HttpResponseRedirect(reverse('helpdesk:preset_reply_list'))
    else:
        form = PreSetReplyForm(request.GET, organization=org)

    return render(request, 'helpdesk/preset_reply_add.html', {'form': form, 'debug': settings.DEBUG})


preset_reply_add = helpdesk_staff_member_required(preset_reply_add)


@helpdesk_staff_member_required
def preset_reply_edit(request, id):
    preset_reply = get_object_or_404(PreSetReply, id=id)
    form = PreSetReplyForm(request.POST or None, instance=preset_reply)
    if form.is_valid():
        form.save()
        return HttpResponseRedirect(reverse('helpdesk:preset_reply_list'))

    return render(request, 'helpdesk/preset_reply_add.html', {'form': form, 'debug': settings.DEBUG})


preset_reply_edit = helpdesk_staff_member_required(preset_reply_edit)


@helpdesk_staff_member_required
def preset_reply_delete(request, id):
    preset_reply = get_object_or_404(PreSetReply, id=id)
    if request.method == 'POST':
        preset_reply.delete()
        return HttpResponseRedirect(reverse('helpdesk:preset_reply_list'))
    else:
        return render(request, 'helpdesk/preset_reply_delete.html', {'reply': preset_reply, 'debug': settings.DEBUG})


preset_reply_delete = helpdesk_staff_member_required(preset_reply_delete)


@helpdesk_staff_member_required
def email_template_list(request):
    org = request.user.default_organization.helpdesk_organization
    templates = EmailTemplate.objects.filter(organization=org, locale='en')  # hiding the large amount of currently-unused templates
    template_list = []
    for template in templates:
        template_list.append({
            'id': template.id,
            'template_name': template.template_name,
            'subject': template.subject,
            'heading': template.heading,
            'plain_text': template.plain_text,
            'html': mark_safe(template.clean_html())
        })

    return render(request, 'helpdesk/email_template_list.html', {
        'template_list': template_list,
        'debug': settings.DEBUG,
    })


email_template_list = helpdesk_staff_member_required(email_template_list)


@helpdesk_staff_member_required
def email_template_edit(request, id):
    template = get_object_or_404(EmailTemplate, id=id)
    form = EmailTemplateForm(request.POST or None, instance=template)
    if form.is_valid():
        form.save()
        return HttpResponseRedirect(reverse('helpdesk:email_template_list'))

    return render(request, 'helpdesk/email_template_edit.html', {'template_id': template.id, 'form': form, 'debug': settings.DEBUG})


email_template_edit = helpdesk_staff_member_required(email_template_edit)


@helpdesk_staff_member_required
def email_template_default(request, id):
    template = get_object_or_404(EmailTemplate, id=id)
    if request.method == 'GET':
        with open("./seed/utils/data/emailtemplates_en.json", "r") as read_file:
            raw_templates = json.load(read_file)

        for rm in raw_templates:
            if rm['template_name'] == template.template_name:
                template.heading = rm.get('heading')
                template.subject = rm.get('subject')
                template.plain_text = rm.get('plain_text')
                template.html = rm.get('html')
                template.save()
                break

    return HttpResponseRedirect(reverse('helpdesk:email_template_edit', kwargs={'id': template.id}))


email_template_default = helpdesk_staff_member_required(email_template_default)


@helpdesk_staff_member_required
def edit_ticket_tags(request, ticket_id):
    """
    Given a ticket_id and list of tag ids, assigns those tags to the ticket.
    """
    ticket = get_object_or_404(Ticket, id=ticket_id)
    tag_ids = request.POST.getlist('tag', [])
    tags = Tag.objects.filter(id__in=tag_ids)
    ticket.tags.set(tags)
    return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket_id]))


@helpdesk_staff_member_required
def tag_list(request):
    """
    Returns a list of tags for the settings page.
    """
    org = request.user.default_organization.helpdesk_organization

    return render(request, 'helpdesk/tag_list.html', {
        'tag_list': Tag.objects.filter(organization=org),
        'debug': settings.DEBUG,
    })


@helpdesk_staff_member_required
def tag_add(request):
    """
    Creates a new Tag.
    """
    if request.method == 'POST':
        form = TagForm(request.POST, organization=request.user.default_organization.helpdesk_organization)
        if form.is_valid():
            saved_form = form.save(commit=False)
            saved_form.organization = request.user.default_organization.helpdesk_organization
            saved_form.save()
            return HttpResponseRedirect(reverse('helpdesk:tag_list'))
    else:
        form = TagForm(request.GET, organization=request.user.default_organization.helpdesk_organization)

    return render(request, 'helpdesk/tag_add.html', {'form': form, 'debug': settings.DEBUG})


@helpdesk_staff_member_required
def tag_edit(request, id):
    """
    Edits a Tag's data.
    """
    tag = get_object_or_404(Tag, id=id)
    form = TagForm(request.POST or None, instance=tag)
    if form.is_valid():
        form.save()
        return HttpResponseRedirect(reverse('helpdesk:tag_list'))

    return render(request, 'helpdesk/tag_add.html', {'form': form, 'debug': settings.DEBUG})


@helpdesk_staff_member_required
def tag_delete(request, id):
    """
    Deletes a tag, removing it from all tickets.
    """
    tag = get_object_or_404(Tag, id=id)
    if request.method == 'POST':
        tag.delete()
        return HttpResponseRedirect(reverse('helpdesk:tag_list'))
    else:
        return render(request, 'helpdesk/tag_delete.html', {'tag': tag, 'debug': settings.DEBUG})


@helpdesk_staff_member_required
def get_tags(request):
    """
    Returns a dictionary with tag ids as keys and on, indeterminate, or off as values, to style the checkboxes for editing ticket tags en-masse.
    """
    ticket_ids = request.GET.getlist('selected[]')
    if not ticket_ids:
        return JsonResponse({
            'success': False,
            'message': 'Must pass a ticket ID'
        }, status=status.HTTP_400_BAD_REQUEST)

    tag_checkboxes = {}
    for tag in Tag.objects.filter(organization=request.user.default_organization.helpdesk_organization):
        count = tag.tickets.filter(id__in=ticket_ids).count()
        if count == 0:
            tag_checkboxes[tag.id] = 'off'
        elif count != len(ticket_ids):
            tag_checkboxes[tag.id] = 'indeterminate'
        elif count == len(ticket_ids):
            tag_checkboxes[tag.id] = 'on'

    return JsonResponse({
        "tags": tag_checkboxes,
    }, status=200)


@helpdesk_staff_member_required
def mass_update_tags(request):
    """
    Takes a list of ticket ids, tag ids to add, and tag ids to remove, and adds and/or removes tags from those tickets.
    """
    ticket_ids = request.POST.getlist('selected[]')
    if not ticket_ids:
        return JsonResponse({
            'success': False,
            'message': 'Must pass a ticket ID'
        }, status=status.HTTP_400_BAD_REQUEST)

    add_tag_ids = request.POST.getlist('add_tags[]')
    remove_tag_ids = request.POST.getlist('remove_tags[]')

    add_tags = Tag.objects.filter(id__in=add_tag_ids, organization=request.user.default_organization.helpdesk_organization)
    remove_tags = Tag.objects.filter(id__in=remove_tag_ids, organization=request.user.default_organization.helpdesk_organization)

    for ticket in Ticket.objects.filter(id__in=ticket_ids, ticket_form__organization=request.user.default_organization.helpdesk_organization):
        ticket.tags.add(*add_tags)
        ticket.tags.remove(*remove_tags)
    return JsonResponse({'update_status': "Ticket Update Complete"})


@helpdesk_staff_member_required
def ticket_cc(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    copies_to = ticket.ticketcc_set.all()
    return render(request, 'helpdesk/ticket_cc_list.html', {
        'copies_to': copies_to,
        'ticket': ticket,
        'debug': settings.DEBUG,
    })


ticket_cc = staff_member_required(ticket_cc)


@helpdesk_staff_member_required
def ticket_cc_add(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    form = TicketCCForm(initial={'can_view': True, 'can_update': True})
    if request.method == 'POST':
        form = TicketCCForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data.get('user')
            email = form.cleaned_data.get('email')
            if user and ticket.ticketcc_set.filter(user=user).exists():
                form.add_error('user', _('Cannot add the same user twice'))
            elif user and user.email and ticket.ticketcc_set.filter(email=user.email).exists():
                form.add_error('user', _('Cannot add the same email address twice'))
            elif email and ticket.ticketcc_set.filter(email=email).exists():
                form.add_error('email', _('Cannot add the same email address twice'))
            else:
                ticketcc = form.save(commit=False)
                ticketcc.ticket = ticket
                if user and user.email:
                    ticketcc.email = user.email
                ticketcc.save()
                return HttpResponseRedirect(reverse('helpdesk:ticket_cc', kwargs={'ticket_id': ticket.id}))

    # Add list of users to the TicketCCForm
    users = list_of_helpdesk_staff(ticket.ticket_form.organization)
    users = users.order_by('last_name', 'first_name', 'email')

    form.fields['user'].choices = [('', '--------')] + [
        (u.id, (u.get_full_name() or u.get_username())) for u in users]

    return render(request, 'helpdesk/ticket_cc_add.html', {
        'ticket': ticket,
        'form': form,
        'debug': settings.DEBUG,
    })


ticket_cc_add = staff_member_required(ticket_cc_add)


@helpdesk_staff_member_required
def ticket_cc_del(request, ticket_id, cc_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    cc = get_object_or_404(TicketCC, ticket__id=ticket_id, id=cc_id)

    if request.method == 'POST':
        cc.delete()
        return HttpResponseRedirect(reverse('helpdesk:ticket_cc', kwargs={'ticket_id': cc.ticket.id}))

    return render(request, 'helpdesk/ticket_cc_del.html', {'ticket': ticket, 'cc': cc, 'debug': settings.DEBUG})


ticket_cc_del = staff_member_required(ticket_cc_del)


@helpdesk_staff_member_required
def ticket_dependency_add(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)

    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    if request.method == 'POST':
        form = TicketDependencyForm(request.POST, org=ticket.queue.organization)
        if form.is_valid():
            ticketdependency = form.save(commit=False)
            ticketdependency.ticket = ticket
            if not TicketDependency.objects.filter(ticket=ticket, depends_on=ticketdependency.depends_on).exists()\
                    and ticketdependency.ticket != ticketdependency.depends_on:
                ticketdependency.save()
            return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket.id]))
    else:
        form = TicketDependencyForm(None, org=ticket.queue.organization)
    return render(request, 'helpdesk/ticket_dependency_add.html', {
        'ticket': ticket,
        'form': form,
        'debug': settings.DEBUG,
    })


ticket_dependency_add = staff_member_required(ticket_dependency_add)


@helpdesk_staff_member_required
def ticket_dependency_del(request, ticket_id, dependency_id):
    dependency = get_object_or_404(TicketDependency, ticket__id=ticket_id, id=dependency_id)
    if request.method == 'POST':
        dependency.delete()
        return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket_id]))
    return render(request, 'helpdesk/ticket_dependency_del.html', {'dependency': dependency, 'debug': settings.DEBUG})


ticket_dependency_del = staff_member_required(ticket_dependency_del)


@helpdesk_staff_member_required
def attachment_del(request, ticket_id, attachment_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm

    attachment = get_object_or_404(FollowUpAttachment, id=attachment_id)
    if request.method == 'POST':
        attachment.delete()
        return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket_id]))
    return render(request, 'helpdesk/ticket_attachment_del.html', {
        'attachment': attachment,
        'filename': attachment.filename,
        'debug': settings.DEBUG,
    })


@helpdesk_staff_member_required
def beam_unpair(request, ticket_id, inventory_type, inventory_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    perm = ticket_perm_check(request, ticket)
    if perm is not None:
        return perm
    if inventory_type == 'property':
        prop = get_object_or_404(Property, id=inventory_id)
        ticket.beam_property.remove(prop)
    else:
        taxlot = get_object_or_404(TaxLot, id=inventory_id)
        ticket.beam_taxlot.remove(taxlot)

    return HttpResponseRedirect(reverse('helpdesk:view', args=[ticket_id]))


def calc_average_nbr_days_until_ticket_resolved(Tickets):
    nbr_closed_tickets = Tickets.count()
    days_per_ticket = 0
    days_each_ticket = list()

    for ticket in Tickets:
        time_ticket_open = ticket.modified - ticket.created
        days_this_ticket = time_ticket_open.days
        days_per_ticket += days_this_ticket
        days_each_ticket.append(days_this_ticket)

    if nbr_closed_tickets > 0:
        mean_per_ticket = days_per_ticket / nbr_closed_tickets
    else:
        mean_per_ticket = 0

    return mean_per_ticket


def calc_basic_ticket_stats(Tickets):
    # all not closed tickets (open, reopened, resolved,) - independent of user
    all_open_tickets = Tickets.exclude(status__in=[Ticket.CLOSED_STATUS, Ticket.RESOLVED_STATUS, Ticket.DUPLICATE_STATUS])
    today = timezone.now()

    date_3 = date_rel_to_today(today, 3)
    date_7 = date_rel_to_today(today, 7)
    date_14 = date_rel_to_today(today, 14)
    date_30 = date_rel_to_today(today, 30)
    date_60 = date_rel_to_today(today, 60)
    date_3_str = date_3.strftime(CUSTOMFIELD_DATE_FORMAT)
    date_7_str = date_7.strftime(CUSTOMFIELD_DATE_FORMAT)
    date_14_str = date_14.strftime(CUSTOMFIELD_DATE_FORMAT)
    date_30_str = date_30.strftime(CUSTOMFIELD_DATE_FORMAT)
    date_60_str = date_60.strftime(CUSTOMFIELD_DATE_FORMAT)

    # > 0 & <= 3
    ota_le_3 = all_open_tickets.filter(created__gte=date_3)
    N_ota_le_3 = ota_le_3.count()

    # > 3 & <= 7
    ota_le_7_ge_3 = all_open_tickets.filter(created__gte=date_7, created__lt=date_3)
    N_ota_le_7_ge_3 = ota_le_7_ge_3.count()

    # > 7 & <= 14
    ota_le_14_ge_7 = all_open_tickets.filter(created__gte=date_14, created__lt=date_7)
    N_ota_le_14_ge_7 = ota_le_14_ge_7.count()

    # > 14
    ota_ge_14 = all_open_tickets.filter(created__lt=date_14)
    N_ota_ge_14 = ota_ge_14.count()

    # > 0 & <= 30
    ota_le_30 = all_open_tickets.filter(created__gte=date_30)
    N_ota_le_30 = ota_le_30.count()

    # >= 30 & <= 60
    ota_le_60_ge_30 = all_open_tickets.filter(created__gte=date_60, created__lte=date_30)
    N_ota_le_60_ge_30 = ota_le_60_ge_30.count()

    # >= 60
    ota_ge_60 = all_open_tickets.filter(created__lte=date_60)
    N_ota_ge_60 = ota_ge_60.count()

    # (O)pen (T)icket (S)tats
    ots = list()
    # label, number entries, color, sort_string
    ots.append(['Tickets < 3 days', N_ota_le_3, 'success',
                sort_string(date_3_str, ''), ])
    ots.append(['Tickets 4 - 7 days', N_ota_le_7_ge_3,
                'success' if N_ota_le_7_ge_3 == 0 else 'warning',
                sort_string(date_7_str, date_3_str), ])
    ots.append(['Tickets 8 - 14 days', N_ota_le_14_ge_7,
                'success' if N_ota_le_14_ge_7 == 0 else 'warning',
                sort_string(date_14_str, date_7_str), ])
#    ots.append(['Tickets 30 - 60 days', N_ota_le_60_ge_30,
#                'success' if N_ota_le_60_ge_30 == 0 else 'warning',
#                sort_string(date_60_str, date_30_str), ])
    ots.append(['Tickets > 14 days', N_ota_ge_14,
                'success' if N_ota_ge_14 == 0 else 'danger',
                sort_string('', date_14_str), ])

    # all closed tickets - independent of user.
    all_closed_tickets = Tickets.filter(status=Ticket.CLOSED_STATUS)
    average_nbr_days_until_ticket_closed = \
        calc_average_nbr_days_until_ticket_resolved(all_closed_tickets)
    # all closed tickets that were opened in the last 60 days.
    all_closed_last_60_days = all_closed_tickets.filter(created__gte=date_60)
    average_nbr_days_until_ticket_closed_last_60_days = \
        calc_average_nbr_days_until_ticket_resolved(all_closed_last_60_days)

    # put together basic stats
    basic_ticket_stats = {
        'average_nbr_days_until_ticket_closed': average_nbr_days_until_ticket_closed,
        'average_nbr_days_until_ticket_closed_last_60_days':
            average_nbr_days_until_ticket_closed_last_60_days,
        'open_ticket_stats': ots,
    }

    return basic_ticket_stats


def get_color_for_nbr_days(nbr_days):
    if nbr_days < 5:
        color_string = 'green'
    elif nbr_days < 10:
        color_string = 'orange'
    else:  # more than 10 days
        color_string = 'red'

    return color_string


def days_since_created(today, ticket):
    return (today - ticket.created).days


def date_rel_to_today(today, offset):
    return today - timedelta(days=offset)


def sort_string(begin, end):
    return 'sort=created&from=%s&to=%s&s=%s&s=%s&s=%s&s=%s' % (
        begin, end, Ticket.OPEN_STATUS, Ticket.REOPENED_STATUS, Ticket.REPLIED_STATUS, Ticket.NEW_STATUS)

@staff_member_required
def enable_disable_emails(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)

    ticket.allow_sending = not ticket.allow_sending
    ticket.save()
    if not ticket.allow_sending:
        emails = Email.objects.filter(ticket=ticket, status=STATUS.queued)
        try:
            emails.delete()
        except ProtectedError:
            pass

    return redirect(ticket)

@staff_member_required
def pair_property_ticket(request, ticket_id):
    """
    Pair BEAM properties and taxlots based on the information in the ticket.
    TODO: Use celery to have building lookup & pairing happen in the background.
    """
    ticket = get_object_or_404(Ticket, id=ticket_id)
    _pair_properties_by_form(request, ticket.ticket_form, [ticket])
    return redirect(ticket)


@staff_member_required
def batch_pair_properties_tickets(request, ticket_ids):
    tickets = Ticket.objects.filter(id__in=ticket_ids).order_by('ticket_form')

    forms = {}
    for t in tickets:
        if t.ticket_form_id not in forms:
            forms[t.ticket_form_id] = {'form': t.ticket_form, 'tickets': []}
        forms[t.ticket_form_id]['tickets'].append(t)

    for f in forms.values():
        _pair_properties_by_form(request, f['form'], f['tickets'])

    return HttpResponseRedirect(reverse('helpdesk:list'))


@staff_member_required
def _pair_properties_by_form(request, form, tickets):
    from seed.models import PropertyState, TaxLotState, TaxLotView, PropertyView, Cycle

    org = form.organization.id
    fields = form.customfield_set.exclude(column__isnull=True).exclude(lookup=False).select_related("column")

    def lookup(query, state, view, cycle, building):
        """ Queries database for either properties or taxlots. """
        if query and cycle:
            possible_views = view.objects.filter(cycle=cycle)
            if view == PropertyView:
                states = state.objects.filter(propertyview__in=possible_views).filter(**query)
            else:
                states = state.objects.filter(taxlotview__in=possible_views).filter(**query)
            buildings = building.objects.filter(views__state__in=states).distinct('pk')
            if buildings:
                return buildings
        return None

    cycles = Cycle.objects.filter(organization_id=org, end__isnull=False).order_by('end')
    for ticket in tickets:
        lookups = {'PropertyState': {}, 'TaxLotState': {}}
        for f in fields:
            # Locates value from ticket that will be searched for in BEAM
            if f.field_name in ticket.extra_data \
                    and ticket.extra_data[f.field_name] is not None and ticket.extra_data[f.field_name] != '':
                value = ticket.extra_data[f.field_name]
            elif hasattr(ticket, f.field_name) \
                    and getattr(ticket, f.field_name, None) is not None and getattr(ticket, f.field_name, None) != '':
                value = getattr(ticket, f.field_name, None)
            else:
                continue

            # Creates a query term and pairs it with the value
            # TODO: Check for the data type and cast value as that type before putting it in lookups?
            if f.column.column_name and hasattr(f.column, 'is_extra_data') and f.column.table_name:
                query_term = 'extra_data__%s' % f.column.column_name if f.column.is_extra_data else f.column.column_name
                lookups[f.column.table_name][query_term] = value

        property_lookup, taxlot_lookup = None, None
        for c in cycles:
            # if statement checks that if there's a prop query, there should be prop views in that cycle, as well as
            # taxlot views if there's a taxlot query. if no queries, it doesn't matter whether it has views in that cycle.
            if not property_lookup and lookups['PropertyState'] and PropertyView.objects.filter(cycle=c).exists():
                property_lookup = lookup(lookups['PropertyState'], PropertyState, PropertyView, c, Property)
                if property_lookup:
                    ticket.beam_property.add(*property_lookup)

            if not taxlot_lookup and lookups['TaxLotState'] and TaxLotView.objects.filter(cycle=c).exists():
                taxlot_lookup = lookup(lookups['TaxLotState'], TaxLotState, TaxLotView, c, TaxLot)
                if taxlot_lookup:
                    ticket.beam_taxlot.add(*taxlot_lookup)

            if (not (lookups['PropertyState'] and not property_lookup)) and \
                    (not (lookups['TaxLotState'] and not taxlot_lookup)):
                break


@staff_member_required
def pair_property_milestone(request, ticket_id):
    """
    Prompt user to select one of the Ticket's paired property's milestone to pair Ticket to
    """
    from seed.models import Milestone,  Note, Pathway, PropertyView, PropertyMilestone

    ticket = get_object_or_404(Ticket, id=ticket_id)

    if request.method == 'POST':
        pv_id = request.POST.get('property_id', '').split('-')[1]
        milestone_id = request.POST.get('milestone_id').split('-')[2]

        pm = PropertyMilestone.objects.get(property_view_id=pv_id, milestone_id=milestone_id)

        # Create Note about pairing
        note_kwargs = {'organization_id': ticket.ticket_form.organization.id, 'user': request.user,
                       'name': 'Automatically Created', 'property_view': pm.property_view, 'note_type': Note.LOG,
                       'log_data': [{'model': 'PropertyMilestone', 'name': pm.milestone.name,
                                     'action': 'edited with the following:'},
                                    {'field': 'Milestone Paired Ticket',
                                     'previous_value': f'Ticket ID {pm.ticket.id if pm.ticket else "None"}',
                                     'new_value': f'Ticket ID {ticket.id}', 'state_id': pm.property_view.state.id},
                                    {'field': 'Implementation Status',
                                     'previous_value': pm.get_implementation_status_display(),
                                     'new_value': 'In Review', 'state_id': pm.property_view.state.id},
                                    {'field': 'Submission Date',
                                     'previous_value': pm.submission_date.strftime('%Y-%m-%d %H:%M:%S') if pm.submission_date else 'None',
                                     'new_value': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
                                     'state_id': pm.property_view.state.id}
                                    ]
                       }
        Note.objects.create(**note_kwargs)

        pm.ticket = ticket
        pm.implementation_status = PropertyMilestone.MILESTONE_IN_REVIEW
        # Only set submission_date if it has never been set
        if not pm.submission_date:
            pm.submission_date = timezone.now() if ticket.created is None else ticket.created
        pm.save()

        return return_to_ticket(request.user, request, helpdesk_settings, ticket)

    properties = ticket.beam_property.all()
    org = ticket.ticket_form.organization

    prop_display_column = Column.objects.filter(organization=org, column_name=org.property_display_field, table_name='PropertyState').first()
    if prop_display_column:
        if prop_display_column.is_extra_data:
            prop_display_query = f'state__extra_data__{prop_display_column.column_name}'
        else:
            prop_display_query = f'state__{prop_display_column.column_name}'
    else:
        prop_display_query = 'state__address_line_1'

    # get all pathways attached to those properties and index them by property id
    # get all milestones for each pathway and index them by pathway id
    properties_per_cycle = {}
    pathways_per_property = {}
    milestones_per_pathway = {}
    for p in properties:
        # if there is no address, we will try to display the PM id instead
        views = PropertyView.objects.filter(property_id=p.id)\
                     .annotate(address=F(prop_display_query), pm_id=F('state__pm_property_id')).only('id', 'cycle')
        for view in views:
            if view.cycle not in properties_per_cycle:
                properties_per_cycle[view.cycle] = [view]
            else:
                properties_per_cycle[view.cycle].append(view)

            pathways = Pathway.objects.filter(cycle_group__cyclegroupmapping__cycle_id=view.cycle.id)
            pathways_per_property[view.id] = pathways

            milestones_per_pathway[view.id] = {}
            for pathway in pathways:
                milestones_per_pathway[view.id][pathway.id] = Milestone.objects.filter(
                    pathwaymilestone__pathway_id=pathway.id,
                    propertymilestone__property_view_id=view.id
                )

    return render(request, 'helpdesk/pair_property_milestone.html', {
        'ticket': ticket,
        'properties_per_cycle': properties_per_cycle,
        'pathways_per_property': pathways_per_property,
        'milestones_per_pathway': milestones_per_pathway,
        'debug': settings.DEBUG,
    })


def load_copy_to_beam(request, ticket_id):
    """
    Loads page for copying ticket data to BEAM.
    """
    from seed.models import PropertyView, Cycle, PropertyState, TaxLotView

    ticket = get_object_or_404(Ticket, id=ticket_id)
    org = ticket.ticket_form.organization

    prop_display_column = Column.objects.filter(organization=org, column_name=org.property_display_field, table_name='PropertyState').first()
    if prop_display_column:
        prop_display_query = f'state__extra_data__{prop_display_column.column_name}' if prop_display_column.is_extra_data else f'state__{prop_display_column.column_name}'
    else:
        prop_display_query = 'state__address_line_1'

    taxlot_display_column = Column.objects.filter(organization=org, column_name=org.taxlot_display_field, table_name='TaxLotState').first()
    if taxlot_display_column:
        taxlot_display_query = f'state__extra_data__{taxlot_display_column.column_name}' if taxlot_display_column.is_extra_data else f'state__{taxlot_display_column.column_name}'
    else:
        taxlot_display_query = 'state__address_line_1'

    properties_per_cycle = {}
    views = PropertyView.objects.filter(property__in=ticket.beam_property.all()) \
        .annotate(address=F(prop_display_query), building_id=F('state__pm_property_id')).only('id', 'cycle')
    for view in views:
        if view.cycle not in properties_per_cycle:
            properties_per_cycle[view.cycle] = [view]
        else:
            properties_per_cycle[view.cycle].append(view)

    taxlots_per_cycle = {}
    views = TaxLotView.objects.filter(taxlot__in=ticket.beam_taxlot.all()) \
        .annotate(address=F(taxlot_display_query), building_id=F('state__jurisdiction_tax_lot_id')).only('state_id', 'cycle')
    for view in views:
        if view.cycle not in taxlots_per_cycle:
            taxlots_per_cycle[view.cycle] = [view]
        else:
            taxlots_per_cycle[view.cycle].append(view)

    cycles = set(list(properties_per_cycle.keys()) + list(taxlots_per_cycle.keys()))

    return render(request, 'helpdesk/ticket_copy_to_beam.html', {
        'ticket': ticket,
        'cycles': cycles,
        'properties_per_cycle': properties_per_cycle,
        'taxlots_per_cycle': taxlots_per_cycle,
        'debug': settings.DEBUG,
    })


def get_building_data(request, ticket_id):
    """Given a cycle ID and view ID, loads ticket data and state data for the copy_to_beam page."""
    from seed.models import Cycle
    from seed.serializers.properties import PropertyStateSerializer
    from seed.serializers.taxlots import TaxLotStateSerializer

    ticket = get_object_or_404(Ticket, id=ticket_id)
    if request.is_ajax and request.method == "GET":

        org = ticket.ticket_form.organization
        inventory_type = request.GET.get('inventory_type', 'PropertyState')
        cycle_id = request.GET.get('cycle_id', 0)
        view_id = request.GET.get('view_id', 0)

        cycle = Cycle.objects.filter(organization=org, id=cycle_id).first()
        if inventory_type == 'PropertyState':
            view = PropertyView.objects.filter(state__organization=org, id=view_id).first()
            state = PropertyStateSerializer(view.state).data
        else:
            view = TaxLotView.objects.filter(state__organization=org, id=view_id).first()
            state = TaxLotStateSerializer(view.state).data
        ticket_data = []
        beam_data = []
        if cycle:
            form_fields = ticket.ticket_form.customfield_set.exclude(column__isnull=True).select_related("column")
            for f in form_fields:
                if f.column.table_name == inventory_type:
                    if f.field_name in ticket.extra_data \
                            and ticket.extra_data[f.field_name] is not None and ticket.extra_data[f.field_name] != '':
                        value = ticket.extra_data[f.field_name]
                    elif hasattr(ticket, f.field_name) \
                            and getattr(ticket, f.field_name, None) is not None and getattr(ticket, f.field_name, None) != '':
                        value = getattr(ticket, f.field_name, None)
                    else:
                        value = ''

                    # If the data types differ, we can't allow users to copy over the data.
                    if f.data_type in ['varchar', 'text', 'email', 'url', 'ipaddress', 'slug', 'list']:
                        data_type = 'string'
                    elif f.data_type in ['date', 'datetime']:
                        data_type = 'datetime'
                    else:
                        data_type = f.data_type

                    ticket_data.append({
                        'display': f.label,
                        'field_name': f.field_name,
                        'value': value,
                        'data_type': data_type
                    })

                    value = ''
                    if f.column.column_name and hasattr(f.column, 'is_extra_data') and f.column.table_name:
                        if f.column.is_extra_data and f.column.column_name in state['extra_data']:
                            value = state['extra_data'][f.column.column_name]
                        elif not f.column.is_extra_data and f.column.column_name in state:
                            value = state[f.column.column_name]

                    # If the data types differ, we can't allow users to copy over the data.
                    # Uses DATA_TYPE_PARSERS to avoid needing to update this section whenever the data types change!
                    if f.column.data_type in Column.DATA_TYPE_PARSERS.keys():
                        try:
                            typed_value = Column.DATA_TYPE_PARSERS[f.column.data_type]("0")
                        except ValueError:  # Invalid isoformat string: '0'
                            data_type = 'datetime'
                        else:
                            if isinstance(typed_value, bool):
                                data_type = 'boolean'
                            elif isinstance(typed_value, str):
                                data_type = 'string'
                            elif isinstance(typed_value, float):
                                data_type = 'decimal'
                            elif isinstance(typed_value, int):
                                data_type = 'integer'
                            elif isinstance(typed_value, datetime):
                                data_type = 'datetime'
                            else:
                                data_type = f.column.data_type

                    beam_data.append({
                        'display': f.column.display_name if f.column.display_name else f.column.column_name,
                        'value': value,
                        'column_name': f.column.column_name,
                        'is_matching_criteria': f.column.is_matching_criteria,
                        'data_type': data_type
                    })

        return JsonResponse({
            "ticket_data": ticket_data,
            'beam_data': beam_data
        }, status=200)


def update_building_data(request, ticket_id):
    """
    Given a cycle ID, view ID, and list of ticket fields, copies the data from those fields to their columns in BEAM.
    """

    from seed.models import PropertyView, Cycle, TaxLotView
    from seed.views.v3.properties import PropertyViewSet
    from seed.views.v3.taxlots import TaxlotViewSet

    if request.is_ajax and request.method == "POST":
        ticket = get_object_or_404(Ticket, id=ticket_id)
        org = ticket.ticket_form.organization
        # get the form data
        inventory_type = request.POST.get('inventory_type', '')
        cycle_id = request.POST.get('cycle_id', '')
        view_id = request.POST.get('view_id', '')
        fields = request.POST.getlist('fields[]', [])

        cycle = get_object_or_404(Cycle, organization=org, id=cycle_id)
        if inventory_type == 'PropertyState':
            view = get_object_or_404(PropertyView, id=view_id)
        else:
            view = get_object_or_404(TaxLotView, id=view_id)
        form_fields = ticket.ticket_form.customfield_set.filter(field_name__in=fields, column__isnull=False).select_related("column")
        update_data, extra_data, data = {}, {}, {}
        for f in form_fields:
            if f.field_name in ticket.extra_data \
                    and ticket.extra_data[f.field_name] is not None and ticket.extra_data[f.field_name] != '':
                value = ticket.extra_data[f.field_name]
            elif hasattr(ticket, f.field_name) \
                    and getattr(ticket, f.field_name, None) is not None and getattr(ticket, f.field_name, None) != '':
                value = getattr(ticket, f.field_name, None)
            else:
                value = ''
            if f.column.is_extra_data:
                extra_data[f.column.column_name] = value
            else:
                data[f.column.column_name] = value

        # Create request to send to BEAM
        update_data = {'state': data}
        update_data['state']['extra_data'] = extra_data
        update_request = HttpRequest()
        update_request.method = 'PUT'
        update_request.query_params = QueryDict('organization_id=' + str(org.id))
        update_request.user = request.user
        update_request.data = update_data
        update_request.parser_context = {}

        if inventory_type == 'PropertyState':
            viewset = PropertyViewSet()
        else:
            viewset = TaxlotViewSet()
        viewset.request = update_request
        response = viewset.update(update_request, pk=view.id)
        return response


def add_remove_label(org_id, user, payload, inventory_type):
    """

    """
    from seed.views.v3.label_inventories import LabelInventoryViewSet
    from django.http import QueryDict

    request = HttpRequest()
    request.method = 'PUT'
    request.query_params = QueryDict('organization_id='+str(org_id))
    request.user = user
    request.data = payload
    livs = LabelInventoryViewSet()
    livs.request = request
    ret = livs.put(request, inventory_type).data
    return ret


@staff_member_required
def edit_inventory_labels(request, inventory_type, ticket_id):
    """
    Prompt User to Add/Remove Labels from a Selected Paired Property
    """
    from seed.models import PropertyView, StatusLabel as Label, TaxLotView

    if inventory_type == 'property':
        view_class = PropertyView
        beam_inventories = 'beam_property'
    else:
        view_class = TaxLotView
        beam_inventories = 'beam_taxlot'

    ticket = get_object_or_404(Ticket, id=ticket_id)
    org_id = ticket.ticket_form.organization_id

    if request.method == 'POST':
        remove_ids = [i.replace('remove_', '') for i in request.POST.keys() if 'remove' in i]
        add_ids = [i.replace('add_', '') for i in request.POST.keys() if 'add' in i]

        pv_id = request.POST.get('inventory_id', '').split('-')[1]
        payload = {'inventory_ids': [pv_id], 'add_label_ids': add_ids, 'remove_label_ids': remove_ids}
        add_remove_label(org_id, request.user, payload, inventory_type)

        return return_to_ticket(request.user, request, helpdesk_settings, ticket)

    inventories = getattr(ticket, beam_inventories).all()

    labels_per_view = {}
    property_views_per_cycle = {}
    for inv in inventories:
        views = view_class.objects.filter(**{inventory_type + '_id': inv.id})
        for view in views:
            if view.cycle not in property_views_per_cycle:
                property_views_per_cycle[view.cycle] = [view]
            else:
                property_views_per_cycle[view.cycle].append(view)

            labels_per_view[view.id] = view.labels.all()

    labels = Label.objects.filter(super_organization_id=org_id)

    return render(request, 'helpdesk/edit_inventory_labels.html', {
        'ticket': ticket,
        'property_views_per_cycle': property_views_per_cycle,
        'labels_per_view': labels_per_view,
        'labels': labels,
        'inventory_type': inventory_type.capitalize(),
        'debug': settings.DEBUG,
    })


def export_ticket_table(request, tickets):
    """
    Export Tickets as they would be visible in the Ticket List
    """
    visible_cols = request.POST.get('visible').split(',')
    visible_cols.insert(2, 'title')  # Add title since it's concatenated in Front-End
    num_queues = request.POST.get('queue_length', '0')

    qs = Ticket.objects.filter(id__in=tickets)
    org = request.user.default_organization.helpdesk_organization
    do_extra_data = int(num_queues) == 1

    return export(qs, org, DatatablesTicketSerializer, do_extra_data=do_extra_data, visible_cols=visible_cols)


@staff_member_required
def export_report(request):
    """
    Export Tickets in a report format. This is different from exporting from the TicketList  page which exports the
    table as it is
    """
    action = request.POST.get('action')
    paginate = action == 'export_year'  # TODO

    user_queue_ids = HelpdeskUser(request.user).get_queues().values_list('id', flat=True)
    qs = Ticket.objects.filter(queue_id__in=user_queue_ids
                               ).order_by('created', 'ticket_form'
                                          ).select_related('ticket_form__organization', 'assigned_to', 'queue',
                                                           ).prefetch_related('followup_set__user', 'beam_property')
    org = request.user.default_organization.helpdesk_organization

    return export(qs, org, ReportTicketSerializer, paginate=paginate)


def export(qs, org, serializer, paginate=False, do_extra_data=True, visible_cols=[]):
    """
    Helper function for exporting the Ticket Table and for Reports. Lots of input variables describing which process
    :param qs: QuerySet of Tickets to Serialize and output to csv file.
    :param org: Organization object associated to all of the Forms
    :param serializer: Ticket Serializer to use on Queryset
    :param paginate:  TODO file into separate sheets for each year of tickets
    :param do_extra_data: Bool, process and save extra data fields to csv file
    :param visible_cols: List of visible cols to include in output
    :return: None, starts downloading the csv file
    """
    from collections import OrderedDict
    from helpdesk.serializers import ORG_TO_ID_FIELD_MAPPING

    ticket_form_ids = list(set(qs.values_list('ticket_form_id', flat=True)))
    building_id_org_field = None
    if org.name in ORG_TO_ID_FIELD_MAPPING:
        building_id_org_field = ORG_TO_ID_FIELD_MAPPING.get(org.name)

    # Fields that could be omitted from Form but still required a Display Name
    just_in_case_mapping = {
        'submitter_email': 'Submitter Email',
        'description': 'Description',
        'contact_name': 'Contact Name',
        'contact_email': 'Contact Email',
        'building_name': 'Building Name',
        'building_address': 'Building Address',
        'building_id': 'Building ID',
        'pm_id': 'Portfolio Manager ID',
        'title': 'Subject',
    }

    # Fields that either don't take a column or are generated by serializer
    other_mapping = {
        'last_reply': 'Last Reply',
        'status': 'Status',
        'paired_count': 'Number of Paired Tickets',
        'submitter': 'Submitter Email',
        'ticket': 'Ticket',
        'get_status': 'Ticket Status',
        'formtype': 'Ticket Form',
        'created': 'Created',
        'id': 'Ticket ID',
        'kbitem': 'Knowledgebase Item',
        'assigned_to': 'Assigned To',
        'time_spent': 'Time Spent',
        'merged_to': 'Merged To',
        'first_staff_followup': 'Date of First Staff Followup',
        'closed_date': 'Ticket Closed Date',
        'is_followup_required': 'Is Followup Required?',
        'number_staff_followups': 'Number of Staff Followups',
        'number_public_followups': 'Number of Public Followups',
        'property_type': 'Primary Property Type - Portfolio Manager-Calculated'
    }

    # Split tickets up into separate forms, serialize, and concatenate them
    report = pd.DataFrame()
    for ticket_form_id in ticket_form_ids:
        sub_qs = qs.filter(ticket_form_id=ticket_form_id)

        form = FormType.objects.get(id=ticket_form_id)
        data = serializer(sub_qs, many=True).data

        # Get extra data columns and their display names
        extra_data_cols = {}
        if do_extra_data:
            extra_data_cols = form.get_extra_fields_mapping()

        # Get Standard columns and their display names
        column_mapping = form.get_fields_mapping()
        mappings = {**column_mapping, **extra_data_cols, **other_mapping}
        mappings.update({k: v for k, v in just_in_case_mapping.items() if k not in mappings})  # Doesn't overwrite
        if mappings['building_id'] == 'Building ID' and building_id_org_field:
            mappings['building_id'] = building_id_org_field

        # Process Data
        output = []
        for row in data:
            # Move extra data from being a nested dict to being other fields
            extra_data = row.pop('extra_data')
            if do_extra_data:
                row.update(extra_data)

            # Get the data that is only visible
            if visible_cols:
                for col in list(set(row.keys()).difference(visible_cols)):
                    row.pop(col)

            # Replace Columns with their display names
            renamed_row = OrderedDict((mappings.get(k, k), v if v else '') for k, v in row.items())
            output.append(renamed_row)
        output = pd.json_normalize(output)
        report = report.append(output, ignore_index=True)
    if 'Ticket ID' in report:
        report = report.set_index('Ticket ID')
    elif 'Ticket' in report:
        report = report.set_index('Ticket')

    time_stamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    media_dir = 'helpdesk/reports/'
    file_name = f'ticket_export_{time_stamp}.csv'
    media_path = media_dir + file_name

    path = default_storage.save(media_path, ContentFile(b''))
    full_path = settings.MEDIA_ROOT + '/' + path
    report.to_csv(full_path)

    # initiate the download, user will stay on the same page
    with open(full_path, 'rb') as f:
        response = HttpResponse(f.read(), content_type='application/vnd.ms-excel')
        response['Content-Disposition'] = 'attachment; filename=' + file_name
        response['Content-Type'] = 'application/vnd.ms-excel; charset=utf-16'
        return response
