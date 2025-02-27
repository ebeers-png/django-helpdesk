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

import requests
from django.core.exceptions import (
    ObjectDoesNotExist, PermissionDenied, ImproperlyConfigured,
)
from rest_framework import status
from django.urls import reverse
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.http import urlquote
from django.utils.translation import ugettext as _
from django.conf import settings
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import TemplateView
from django.views.generic.edit import FormView
from django.db.models import Q

from helpdesk.forms import OrderForm
from helpdesk import settings as helpdesk_settings
from helpdesk.decorators import protect_view, is_helpdesk_staff
import helpdesk.views.staff as staff
import helpdesk.views.abstract_views as abstract_views
from helpdesk.lib import find_beam_view, text_is_spam
from helpdesk.models import Ticket, UserSettings, CustomField, FormType, TicketCC, is_extra_data, is_unlisted, \
    OrderSettings, Order, PAYMENT_TYPE, PAYMENT_STATUS
from helpdesk.user import huser_from_request
from seed.lib.superperms.orgs.models import Organization
from seed.models import PropertyState, PropertyView, DataQualityResults

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
        if FormType.objects.get(pk=self.form_id).view_only:
            return HttpResponseRedirect(reverse('helpdesk:home'))

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


def evaluate_derived_column(request):
    building_id = request.POST.get('building_id', None)
    form_id = request.POST.get("form_id", None)
    derived_column_field_name = request.POST.get("derived_column_field", None)
    derived_column_field_name = derived_column_field_name[2:] if derived_column_field_name[0:2] == "e_" else derived_column_field_name

    form = FormType.objects.filter(pk=form_id).first()
    derived_column_field = form.customfield_set.filter(field_name=derived_column_field_name).select_related('column').first()
    if not form:
        return JsonResponse({"status": "error", "error": "Invalid form ID"})
    
    if not derived_column_field:
        return JsonResponse({"status": "error", "error": "Invalid derived column field name"})

    if not derived_column_field.column.derived_column:
        return JsonResponse({"status": "error", "error": "Column is not a derived column"})

    # map form data to BEAM columns
    parameters = {}
    fields = (
        form.customfield_set.exclude(field_name=derived_column_field_name)
        .exclude(column__isnull=True)
        .select_related("column")
    )
    for field in fields:
        field_name = "e_" + field.field_name if is_extra_data(field.field_name) else field.field_name
        value = request.POST.get(field_name, None)
        if value not in ['', None]:
            parameters[field.column.column_name] = value

    # find BEAM inventory view if provided
    view = None
    if building_id and form.pull_cycle:
        building_id_field = form.customfield_set.filter(field_name='building_id').first()
        if building_id_field and building_id_field.column:
            view = find_beam_view(form.organization_id, form.pull_cycle, building_id_field.column, building_id)

    return JsonResponse({
        "status": "success", 
        "data": derived_column_field.column.derived_column.evaluate_in_sequence(
                    inventory_view=view, column_parameters=parameters)
    })

def change_language(request):
    return_to = ''
    if 'return_to' in request.GET:
        return_to = request.GET['return_to']

    return render(request, 'helpdesk/public_change_language.html', {'next': return_to, 'debug': settings.DEBUG})


def _mark_buildings_paid(order, order_settings):
    paid_label = order_settings.label_paid
    unpaid_label = order_settings.label_not_paid
    for p in order.properties.all():
        p.labels.remove(unpaid_label)
        p.labels.add(paid_label)


class BuildingOrderView(TemplateView):
    template_name = "helpdesk/payment.html"

    def get(self, *args, **kwargs):
        # Create an instance of the formset
        order_form = OrderForm()
        return self.render_to_response({
            'order_form': order_form
        })

    def post(self, *args, **kwargs):
        """Submit the order for the user."""
        org_id = self.request.POST.get("org_id", None)
        org = Organization.objects.get(id=org_id)
        payment_type = self.request.POST.get("payment_type", "cc")
        state_ids = self.request.POST.getlist("state_id", [])

        order_form = OrderForm(data=self.request.POST)

        # Check if submitted forms are valid
        if order_form.is_valid():
            # Create the order object
            order = order_form.save(commit=False)
            order.organization = org
            order.payment_type = PAYMENT_TYPE.cc if payment_type == 'cc' else PAYMENT_TYPE.ach
            order.save()
            property_views = PropertyView.objects.filter(  # todo check that they are payable
                cycle=org.ordersettings.cycle,
                state__id__in=state_ids,
                property__organization=org)
            order.properties.set(property_views)

            # Send payment to checkout page, and then redirect user to the checkout.
            response = _prepare_payment(self.request, order, org.ordersettings)
            data = response.json()

            if response.status_code != 200:
                # todo need to catch this properly
                print(data)
                return self.render_to_response({'order_form': order_form})

            order.token = data['token']
            order.save()
            return HttpResponseRedirect(data['htmL5RedirectUrl'])

        return self.render_to_response({'order_form': order_form})


def _get_field(p, field):
    if field.is_extra_data:
        return getattr(p.extra_data, field.column_name, "")
    else:
        return getattr(p, field.column_name, "")


def _check_order(order):
    """
    Look up order using the order's token.
    """
    url = f'https://securecheckout-uat.cdc.nicusa.com/ccprest/api/v1/co/tokens/{order.token}'
    merchant_code = settings.PAYMENT_MERCHANT_CODE_SECRET
    merchant_key = settings.PAYMENT_MERCHANT_KEY_SECRET
    api_key = settings.PAYMENT_API_KEY

    headers = {
        "MerchantCode": merchant_code,
        "MerchantKey": merchant_key,
        "ApiKey": api_key
    }
    response = requests.get(url, headers=headers)
    data = response.json()
    return data


def lookup_building_for_payment(request):
    if request.is_ajax and request.method == "GET":
        org_id = request.GET.get("org", None)
        building_id = request.GET.get('id', None)
        org_settings = OrderSettings.objects.get(organization_id=org_id)

        # look up the building
        building_id_col = org_settings.building_id
        try:
            if building_id_col.is_extra_data:
                building = PropertyState.objects.get(**{
                    f'extra_data__{building_id_col.column_name}': building_id,
                    "propertyview__cycle": org_settings.cycle,
                    "organization": org_settings.organization
                })
            else:
                building = PropertyState.objects.get(**{
                    building_id_col.column_name: building_id,
                    "propertyview__cycle": org_settings.cycle,
                    "organization": org_settings.organization
                })
        except PropertyState.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Building not found. Please ensure your ID is correct."
            }, status=status.HTTP_404_NOT_FOUND)
        except PropertyState.MultipleObjectsReturned:
            return JsonResponse({
                "status": "error",
                "message": "Building not found. Please ask your admin to ensure there are no duplicates of your building in the database."
            }, status=status.HTTP_404_NOT_FOUND)

        # check whether building can be paid for
        if Order.objects.filter(payment_status=PAYMENT_STATUS.pending, properties=building.propertyview_set.first()):
            # building is in someone's cart and has not been released yet
            return JsonResponse({
                "status": "error",
                "message": "Building cannot be paid for at this time."
            }, status=status.HTTP_404_NOT_FOUND)

        if not building.propertyview_set.first().labels.filter(id=org_settings.label_not_paid.id).exists():
            if building.propertyview_set.first().labels.filter(id=org_settings.label_paid.id).exists():
                # building has a "paid" label
                return JsonResponse({
                    "status": "error",
                    "message": "This building has already been paid for."
                }, status=status.HTTP_404_NOT_FOUND)

            dq_result = DataQualityResults.objects.filter(property_state=building, data_quality=org_settings.dq).order_by('-created').first()
            if dq_result.status is org_settings.excluded_status:
                # building has a "not paid" label, but it's excluded
                return JsonResponse({
                    "status": "error",
                    "message": "This building is not covered and does not need to be paid for."
                }, status=status.HTTP_404_NOT_FOUND)

            # building must have a "not paid" label
            return JsonResponse({
                "status": "error",
                "message": "Building not found. Please ensure your ID is correct."
            }, status=status.HTTP_404_NOT_FOUND)

        # return autofill information
        cols = {
            'address_line_1': org_settings.address_line_1,
            'address_line_2': org_settings.address_line_2,
            'city': org_settings.city,
            'state': org_settings.state,
            'zip': org_settings.zip,
        }

        data = {}
        for field, col in cols.items():
            data[field] = _get_field(building, col)

        data['_state_id'] = building.id
        return JsonResponse({
            "status": "success",
            "data": data
        })


def _prepare_payment(request, order, order_settings):
    """
    Creates the order and sends the order off to the checkout page.
    """
    url = 'https://securecheckout-uat.cdc.nicusa.com/ccprest/api/v1/co/tokens'
    merchant_code = settings.PAYMENT_MERCHANT_CODE_SECRET
    merchant_key = settings.PAYMENT_MERCHANT_KEY_SECRET
    service_code_cc = settings.PAYMENT_SERVICE_CODE_CC
    service_code_ach = settings.PAYMENT_SERVICE_CODE_ACH
    api_key = settings.PAYMENT_API_KEY

    data = {
        "OrderTotal": order.properties.all().count() * 100.00,
        "MerchantCode": merchant_code,
        "MerchantKey": merchant_key,
        "ServiceCode": service_code_cc if order.payment_type == PAYMENT_TYPE.cc else service_code_ach,
        "UniqueTransId": order.id,  # todo can be 1-32 characters
        "LocalRef": "2025 Reporting Form",  # todo change based on cycle
        "PaymentType": "CC" if order.payment_type == PAYMENT_TYPE.cc else 'ACH',
        "SuccessUrl": request.build_absolute_uri(reverse('helpdesk:payment_complete') + "?org=" + order.organization.name),
        "FailureUrl": request.build_absolute_uri(reverse('helpdesk:payment_failed') + "?org=" + order.organization.name),
        "DuplicateUrl": request.build_absolute_uri(reverse('helpdesk:payment_duplicate') + "?org=" + order.organization.name),
        "CancelUrl": request.build_absolute_uri(reverse('helpdesk:payment_canceled') + "?org=" + order.organization.name),
        "Phone": None,
        "Email": order.payer_email,
        "BCCEmail1": None,
        "BCCEmail2": None,
        "BCCEmail3": None,
        "CustomerId": None,
        "CompanyName": order.payer_company,
        "FinancialSessionId": None,
        "CustomerAddress": {
                "Name": f"{order.payer_first_name} {order.payer_last_name}",
                "Address1": None,
                "Address2": None,
                "City": None,
                "State": None,
                "Zip": None,
                "Country": "US"
        },
        "BillingAddress": None,
        "OrderAttributes": None,
        "LineItems": []
    }
    for p in order.properties.all():
        data['LineItems'].append({
            "Sku": "AcctRecv",
            "Description": f"{_get_field(p.state, order_settings.building_id)}/{order.payer_first_name} {order.payer_last_name}",
            "UnitPrice": 100.00,
            "Quantity": 1,
            "InstanceId": None,
            "ItemAttributes": [{"FieldName": "VIEWID", "FieldValue": p.id}]
        })

    headers = {'ApiKey': api_key}
    response = requests.post(
        url,
        json=data,
        headers=headers)

    return response


def payment_complete(request):
    """
    Checks whether order was successful. If so, updates buildings and order's payment status.
    """
    message = "Your order could not be found. If you would like to check the status of your payment, please contact our Help Center."
    token = request.GET.get('token', None)
    org = request.GET.get('org', None)
    if not token:
        return HttpResponseRedirect(reverse("helpdesk:home") + ("?org=" + org if org else ''))
    try:
        order = Order.objects.get(token=token)
    except Order.DoesNotExist:
        return render(request, 'helpdesk/payment_complete.html', {
            'message': message,
            'debug': settings.DEBUG,
        })

    data = _check_order(order)
    if data['matchingOrders'] != 0:
        for o in data['orders']:
            if o['localRefId'] == '2025 Reporting Form' and o['orderStatus'] == 'COMPLETE':
                order.payment_status = PAYMENT_STATUS.success
                order.save()

                ids = []  # update our record with the properties that were paid for
                for item in o['items']:
                    for field in item['itemAttributes']:
                        if field['fieldName'] == 'VIEWID':
                            ids.append(int(field['fieldValue']))
                order.properties.set(ids)
                _mark_buildings_paid(order, order.organization.ordersettings)
                message = "Thank you for your payment!"  # todo add more to the message
    else:
        # todo if order was not completed correctly
        return render(request, 'helpdesk/payment_failed.html', {
            'debug': settings.DEBUG,
        })

    return render(request, 'helpdesk/payment_complete.html', {
        'message': message,
        'debug': settings.DEBUG,
    })


def payment_failed(request):
    """
    Handles all 'return' pages that aren't complete/success.
    Update the order to allow someone else to pay for the building.
    """
    token = request.GET.get('token', None)
    org = request.GET.get('org', None)
    if not token:
        return HttpResponseRedirect(reverse("helpdesk:home") + ("?org=" + org if org else ''))
    try:
        order = Order.objects.get(token=token)
    except Order.DoesNotExist:
        return render(request, 'helpdesk/payment_failed.html', {
            'debug': settings.DEBUG,
        })

    data = _check_order(order)
    if data['matchingOrders'] == 0:
        order.payment_status = PAYMENT_STATUS.failed
        order.save()

    if 'duplicate' in request.path:
        return render(request, 'helpdesk/payment_duplicate.html', {
            'debug': settings.DEBUG,
        })
    if 'failed' in request.path:
        return render(request, 'helpdesk/payment_failed.html', {
            'debug': settings.DEBUG,
        })
    if 'canceled' in request.path:
        return render(request, 'helpdesk/payment_canceled.html', {
            'debug': settings.DEBUG,
        })
    return render(request, 'helpdesk/payment_failed.html', {
        'debug': settings.DEBUG,
    })
