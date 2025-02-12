"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

views/public.py - All public facing views, eg non-staff (no authentication
                  required) views.
"""
import logging
from csv import DictReader
from os.path import join
from importlib import import_module
from django.core.exceptions import (
    ObjectDoesNotExist, PermissionDenied, ImproperlyConfigured,
)
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.shortcuts import render, get_object_or_404
from django.utils.http import urlquote
from django.utils.translation import ugettext as _
from django.conf import settings
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import TemplateView
from django.views.generic.edit import FormView
from django.db.models import Q, OuterRef, Subquery

from helpdesk import settings as helpdesk_settings
from helpdesk.decorators import protect_view, is_helpdesk_staff
import helpdesk.views.staff as staff
import helpdesk.views.abstract_views as abstract_views
from helpdesk.lib import text_is_spam
from helpdesk.models import Ticket, UserSettings, CustomField, FormType, TicketCC, is_unlisted, FollowUp
from helpdesk.user import huser_from_request

logger = logging.getLogger(__name__)


def create_ticket(request, form_id=None,  *args, **kwargs, ):
    # Verify form_id provided by URL.
    try:
        form_int = int(form_id)
    except TypeError:
        return HttpResponseRedirect(reverse('helpdesk:home'))

    form = get_object_or_404(FormType, id=form_int)
    has_form_access = huser_from_request(request).can_access_form(form)
    if is_helpdesk_staff(request.user):
        if has_form_access:
            return staff.CreateTicketView.as_view(form_id=form_int)(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse('helpdesk:home'))

    # If not user: Check if form is public, and if not, return to login or homepage
    if form is not None and form.public and has_form_access:
        return CreateTicketView.as_view(form_id=form_int)(request, *args, **kwargs)
    elif helpdesk_settings.HELPDESK_REDIRECT_TO_LOGIN_BY_DEFAULT:
        return HttpResponseRedirect(reverse('login'))
    else:
        if request.GET and 'org' in request.GET:
            return HttpResponseRedirect(reverse('helpdesk:home') + '?org=' + request.GET.get('org'))
        else:
            return HttpResponseRedirect(reverse('helpdesk:home'))


def ticket_list_owner(request):
    email = request.user.email
    org_id = request.user.default_organization.helpdesk_organization.id
    search_query = None
    search_results = None

    # find email fields and use for lookups
    email_fields = CustomField.objects.filter(
        ticket_form__organization_id=org_id,
        data_type='email',
        notifications=True,
    ).order_by('field_name').distinct('field_name').values_list('field_name', flat=True)
    cc_q_list = Q()
    for field in email_fields:
        cc_q_list |= Q(**{f'extra_data__{field}__iexact': email})

    # create querysets
    open_tickets = (Ticket.objects.filter(ticket_form__organization_id=org_id)
                    .exclude(Q(status=Ticket.DUPLICATE_STATUS) | Q(status=Ticket.RESOLVED_STATUS) | Q(status=Ticket.CLOSED_STATUS)))
    submitted_tickets = open_tickets.filter(submitter_email__iexact=email)
    cc_tickets = (open_tickets
                  .exclude(pk__in=submitted_tickets.values_list('pk', flat=True))
                  .filter(Q(contact_email__iexact=email) | Q(ticketcc__email__iexact=email) | Q(ticketcc__user=request.user) | cc_q_list)
                  )
    resolved_tickets = (Ticket.objects
                        .filter(Q(status=Ticket.DUPLICATE_STATUS) | Q(status=Ticket.RESOLVED_STATUS) | Q(status=Ticket.CLOSED_STATUS), ticket_form__organization_id=org_id)
                        .filter(Q(ticketcc__email__iexact=email) | Q(ticketcc__user=request.user) | Q(submitter_email__iexact=email) | Q(contact_email__iexact=email) | cc_q_list))

    # annotate tickets with "last reply"
    followup_subquery = FollowUp.objects.filter(ticket_id=OuterRef("id")).reverse().values('date')
    submitted_tickets = submitted_tickets.annotate(last_reply=Subquery(followup_subquery[:1]))
    cc_tickets = cc_tickets.annotate(last_reply=Subquery(followup_subquery[:1]))
    resolved_tickets = resolved_tickets.annotate(last_reply=Subquery(followup_subquery[:1]))

    if request.GET and 'q' in request.GET:
        search_query = request.GET.get('q')
        search_results = (Ticket.objects
            .filter(ticket_form__organization_id=org_id)
            .filter(Q(ticketcc__email__iexact=email) | Q(ticketcc__user=request.user) | Q(submitter_email__iexact=email) | Q(contact_email__iexact=email) | cc_q_list)
            .filter(Q(title__icontains=search_query) | Q(description__icontains=search_query)))
        search_results = search_results.annotate(last_reply=Subquery(followup_subquery[:1]))

    return render(request, 'helpdesk/ticket_list_owner.html', {
        'search_query': search_query,
        'search_results': search_results,
        'submitted_tickets': submitted_tickets,
        'completed_tickets': resolved_tickets,
        'cc_tickets': cc_tickets,
    })


"""
def ticket_detail(request, ticket_id):
    ticket_org = request.user.default_organization.helpdesk_organization.name
    user_email = request.user.email
    ticket = Ticket.objects.get(id=ticket_id)

    # Search for email in the two default email fields
    email_lower = user_email.casefold()
    emails = {ticket.submitter_email, ticket.contact_email}
    emails.discard(None)
    if email_lower not in {e.casefold() for e in emails}:
        # Search for email in ticket's CCs
        ticket_cc_emails = TicketCC.objects.filter(
            Q(ticket=ticket),
            Q(can_view=True) | Q(can_update=True)
        ).values_list('email', flat=True)
        emails.update(ticket_cc_emails)
        emails.discard(None)

        if email_lower not in {e.casefold() for e in emails}:
            # Search for email in the ticket's extra_data email fields that can receive notifications
            ticket_email_fields = CustomField.objects.filter(
                ticket_form=ticket.ticket_form,
                data_type='email',
                notifications=True
            ).values_list('field_name', flat=True)
            ticket_email_extra_data_values = [v for k, v in ticket.extra_data.items() if k in ticket_email_fields]
            emails.update(ticket_email_extra_data_values)
            emails.discard(None)
            # Otherwise, not allowed
            if email_lower not in {e.casefold() for e in emails}:
                return search_for_ticket(request, _('Invalid ticket ID or e-mail address. Please try again.'),
                                         ticket=ticket)

    cc_user = TicketCC.objects.filter(ticket=ticket, email=user_email).first()
    # Redirect CC User to Homepage if they aren't allowed to view the ticket
    if cc_user and not cc_user.can_view:
        return render(request, 'helpdesk/public_error.html', {
            'error_message': TicketCC.VIEW_WARNING % (ticket.submitter_email if ticket.submitter_email else ''),
            'ticket': ticket,
            'debug': settings.DEBUG,
        })
    elif cc_user and cc_user.can_view:
        can_update = cc_user.can_update
    elif user_email == ticket.submitter_email:
        can_update = True
    else:
        can_update = True

    extra_display = CustomField.objects.filter(ticket_form=ticket.ticket_form).values()
    extra_data = []
    for field in extra_display:
        if field['public'] and not is_unlisted(field['field_name']) and not field['data_type'] == 'attachment':
            if field['field_name'] in ticket.extra_data:
                field['value'] = ticket.extra_data[field['field_name']]
            else:
                field['value'] = getattr(ticket, field['field_name'], None)
            extra_data.append(field)

    return render(request, 'helpdesk/ticket_detail.html', {
        'ticket': ticket,
        'helpdesk_settings': helpdesk_settings,
        'extra_data': extra_data,
        'can_update': can_update,
        'debug': settings.DEBUG,
    })
"""


class BaseCreateTicketView(abstract_views.AbstractCreateTicketMixin, FormView):
    form_id = None

    def get_form_class(self):
        try:
            the_module, the_form_class = helpdesk_settings.HELPDESK_PUBLIC_TICKET_FORM_CLASS.rsplit(".", 1)
            the_module = import_module(the_module)
            the_form_class = getattr(the_module, the_form_class)
        except Exception as e:
            raise ImproperlyConfigured(
                f"Invalid custom form class {helpdesk_settings.HELPDESK_PUBLIC_TICKET_FORM_CLASS}"
            ) from e
        return the_form_class

    def dispatch(self, *args, **kwargs):
        request = self.request
        if not request.user.is_authenticated and helpdesk_settings.HELPDESK_REDIRECT_TO_LOGIN_BY_DEFAULT:
            return HttpResponseRedirect(reverse('login'))

        if is_helpdesk_staff(request.user):
            try:
                if request.user.usersettings_helpdesk.login_view_ticketlist:
                    return HttpResponseRedirect(reverse('helpdesk:list'))
                else:
                    return HttpResponseRedirect(reverse('helpdesk:dashboard'))
            except UserSettings.DoesNotExist:
                return HttpResponseRedirect(reverse('helpdesk:dashboard'))
        return super().dispatch(*args, **kwargs)

    def get_form_kwargs(self, *args, **kwargs):
        kwargs = super().get_form_kwargs(*args, **kwargs)
        if '_hide_fields_' in self.request.GET:
            kwargs['hidden_fields'] = self.request.GET.get('_hide_fields_', '').split(',')
        kwargs['readonly_fields'] = self.request.GET.get('_readonly_fields_', '').split(',')
        kwargs['form_id'] = self.form_id
        if self.form_id is None:
            kwargs['form_id'] = self.form_id = 1  # TODO remove hardcoding!!!!!
        return kwargs

    def form_valid(self, form):
        request = self.request
        if 'description' in form.cleaned_data and text_is_spam(form.cleaned_data['description'], request):
            # This submission is spam. Let's not save it.
            return render(request, 'helpdesk/public_spam.html', {'debug': settings.DEBUG})
        else:
            ticket = form.save(form_id=self.form_id, user=self.request.user if self.request.user.is_authenticated else None)
            if request.GET.get('milestone_beam_redirect', False):
                # Pair Ticket to Milestone
                staff.attach_ticket_to_property_milestone(self.request, ticket)
            try:
                return HttpResponseRedirect(ticket.ticket_url)
            except ValueError:
                # if someone enters a non-int string for the ticket
                return HttpResponseRedirect(reverse('helpdesk:home'))


class CreateTicketIframeView(BaseCreateTicketView):
    template_name = 'helpdesk/public_create_ticket_iframe.html'

    @csrf_exempt
    @xframe_options_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def form_valid(self, form):
        if super().form_valid(form).status_code == 302:
            return HttpResponseRedirect(reverse('helpdesk:success_iframe'))


class SuccessIframeView(TemplateView):
    template_name = 'helpdesk/success_iframe.html'

    @xframe_options_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)


class CreateTicketView(BaseCreateTicketView):
    template_name = 'helpdesk/public_create_ticket.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Add the CSS error class to the form in order to better see them in the page
        form.error_css_class = 'text-danger'
        return form


class Homepage(CreateTicketView):
    template_name = 'helpdesk/public_homepage.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['kb_categories'] = huser_from_request(self.request).get_allowed_kb_categories()
        return context


def search_for_ticket(request, error_message=None, ticket=None):
    if hasattr(settings, 'HELPDESK_VIEW_A_TICKET_PUBLIC') and settings.HELPDESK_VIEW_A_TICKET_PUBLIC:
        email = request.GET.get('email', None)
        return render(request, 'helpdesk/public_view_form.html', {
            'ticket': False,
            'email': email,
            'error_message': error_message,
            'helpdesk_settings': helpdesk_settings,
            'debug': settings.DEBUG,
        })
    else:
        return render(request, 'helpdesk/public_error.html', {
            'error_message': TicketCC.VIEW_WARNING % (ticket.submitter_email if ticket and ticket.submitter_email else 'Not Found'),
            'ticket': ticket,
            'debug': settings.DEBUG,
        })

def bps_pathway_calculator(request):
    csv_path = './seed/utils/data/bps_property_targets.csv'
    property_types = []
    eui_2026 = {}
    eui_2030 = {}
    ghgi_2026 = {}
    ghgi_2030 = {}
    with open(csv_path, 'r') as f:
        csvreader = DictReader(f)
        for t in csvreader:
            property_type = t.get('property_type')
            if property_type is None:
                continue
            try:
                eui_2026[property_type] = float(t.get('site_eui_kbtu_sqft_2026', None))
                eui_2030[property_type] = float(t.get('site_eui_kbtu_sqft_2030', None))
                ghgi_2026[property_type] = float(t.get('ghgi_kgco2e_sqft_2026', None))
                ghgi_2030[property_type] = float(t.get('ghgi_kgco2e_sqft_2030', None))
            except (TypeError, ValueError) as e:
                logger.exception(e)
                continue
            property_types.append(_(property_type))

    ghgi_pathways = {
        # NOTE: template uses "std" suffix to determine if std/non-std pathway
        'ghg': _('Greenhouse Gas Reduction'),
        'ghg_std': _('Standard Percent Greenhouse Gas Reduction'),
    }
    eui_pathways = {
        # NOTE: template uses "std" suffix to determine if std/non-std pathway
        'eui': _('Energy Efficiency'),
        'eui_std': _('Standard Percent Energy Efficiency'),
    }
    return render(request, 'helpdesk/public_bps_pathway_calculator.html', {
        'helpdesk_settings': helpdesk_settings,
        'debug': settings.DEBUG,
        'ghgi_pathways': ghgi_pathways,
        'eui_pathways': eui_pathways,
        'property_types': property_types,
        'eui_2026': eui_2026,
        'eui_2030': eui_2030,
        'ghgi_2026': ghgi_2026,
        'ghgi_2030': ghgi_2030,
    })

@protect_view
def view_ticket(request):
    ticket_org = request.GET.get('org', None)
    ticket_req = request.GET.get('ticket', None)
    email = request.GET.get('email', None)
    key = request.GET.get('key', '')

    if not (ticket_req and email):
        if ticket_req is None and email is None:
            return search_for_ticket(request)
        else:
            return search_for_ticket(request, _('Missing ticket ID or e-mail address. Please try again.'))

    queue, ticket_id = Ticket.queue_and_id_from_query(ticket_req)
    try:
        if hasattr(settings, 'HELPDESK_VIEW_A_TICKET_PUBLIC') and settings.HELPDESK_VIEW_A_TICKET_PUBLIC:
            ticket = Ticket.objects.get(id=ticket_id)
        else:
            ticket = Ticket.objects.get(id=ticket_id, secret_key__iexact=key)
    except (ObjectDoesNotExist, ValueError):
        return search_for_ticket(request, _('Invalid ticket ID or e-mail address. Please try again.'))

    # Search for email in the two default email fields
    email_lower = email.casefold()
    emails = {ticket.submitter_email, ticket.contact_email}
    emails.discard(None)
    if email_lower not in {e.casefold() for e in emails}:
        # Search for email in ticket's CCs
        ticket_cc_emails = TicketCC.objects.filter(
            Q(ticket=ticket),
            Q(can_view=True) | Q(can_update=True)
        ).values_list('email', flat=True)
        emails.update(ticket_cc_emails)
        emails.discard(None)

        if email_lower not in {e.casefold() for e in emails}:
            # Search for email in the ticket's extra_data email fields that can receive notifications
            ticket_email_fields = CustomField.objects.filter(
                ticket_form=ticket.ticket_form,
                data_type='email',
                notifications=True
            ).values_list('field_name', flat=True)
            ticket_email_extra_data_values = [v for k, v in ticket.extra_data.items() if k in ticket_email_fields]
            emails.update(ticket_email_extra_data_values)
            emails.discard(None)
            # Otherwise, not allowed
            if email_lower not in {e.casefold() for e in emails}:
                return search_for_ticket(request, _('Invalid ticket ID or e-mail address. Please try again.'),
                                         ticket=ticket)

    if is_helpdesk_staff(request.user) and ticket_org == request.user.default_organization.helpdesk_organization.name:
        redirect_url = reverse('helpdesk:view', args=[ticket_id])
        if 'close' in request.GET:
            redirect_url += '?close'
        return HttpResponseRedirect(redirect_url)

    cc_user = TicketCC.objects.filter(ticket=ticket, email=email).first()
    # Redirect CC User to Homepage if they aren't allowed to view the ticket
    if cc_user and not cc_user.can_view:
        return render(request, 'helpdesk/public_error.html', {
            'error_message': TicketCC.VIEW_WARNING % (ticket.submitter_email if ticket.submitter_email else ''),
            'ticket': ticket,
            'debug': settings.DEBUG,
        })
    elif cc_user and cc_user.can_view:
        can_update = cc_user.can_update
    elif email == ticket.submitter_email:
        can_update = True

    if 'close' in request.GET and ticket.status == Ticket.RESOLVED_STATUS:
        from helpdesk.views.staff import update_ticket
        # Trick the update_ticket() view into thinking it's being called with
        # a valid POST.
        request.POST = {
            'new_status': Ticket.CLOSED_STATUS,
            'public': 1,
            'title': ticket.title,
            'comment': _('Submitter accepted resolution and closed ticket'),
        }
        if ticket.assigned_to:
            request.POST['owner'] = ticket.assigned_to.id
        request.GET = {}

        return update_ticket(request, ticket_id, public=True)

    # redirect user back to this ticket if possible.
    redirect_url = ''
    if helpdesk_settings.HELPDESK_NAVIGATION_ENABLED:
        redirect_url = reverse('helpdesk:view', args=[ticket_id])

    extra_display = CustomField.objects.filter(ticket_form=ticket.ticket_form).values()
    extra_data = []
    for field in extra_display:
        if field['public'] and not is_unlisted(field['field_name']) and not field['data_type'] == 'attachment':
            if field['field_name'] in ticket.extra_data:
                field['value'] = ticket.extra_data[field['field_name']]
            else:
                field['value'] = getattr(ticket, field['field_name'], None)
            extra_data.append(field)
    return render(request, 'helpdesk/public_view_ticket.html', {
        'key': key,
        'mail': email,
        'ticket': ticket,
        'helpdesk_settings': helpdesk_settings,
        'next': redirect_url,
        'extra_data': extra_data,
        'can_update': can_update,
        'debug': settings.DEBUG,
    })


def change_language(request):
    return_to = ''
    if 'return_to' in request.GET:
        return_to = request.GET['return_to']

    return render(request, 'helpdesk/public_change_language.html', {'next': return_to, 'debug': settings.DEBUG})
