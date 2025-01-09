"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

templatetags/organization_info.py - This template tag returns two pieces of information:
    a users default org -- based on the url if they are not staff or based on the users default_organization
                           field if they are staff.
    list of orgs        -- A queryset of the organizations the user is a part of if they are staff
"""
from django import template
from seed.lib.superperms.orgs.models import Organization, OrganizationUser, get_helpdesk_orgs_for_domain, get_helpdesk_organizations
from helpdesk.decorators import is_helpdesk_staff

register = template.Library()


@register.simple_tag
def organization_info(user, request):
    """
    Given user and request,
    If user is staff, return Helpdesk Orgs to display in dropdown that they have access to in helpdesk
    If user is public or not logged in, return the org in the url (if available). If no org in url, and user is logged
        in, return the user's helpdesk org. Otherwise, if one Helpdesk Org available, return that one Helpdesk Org
    A non-logged in user, with no org in url with multiple Helpdesk Orgs will see an empty helpdesk page.
    Precedence:
        Staff Member
        URL
        Public Member

    """
    try:
        domain_id = getattr(request, 'domain_id', 0)
        return_info = {'default_org': None, 'orgs': [], 'url': ''}

        if is_helpdesk_staff(user):  # todo - change to "staff of any org" ?
            helpdesk_orgs = get_helpdesk_organizations()
            orgs = OrganizationUser.objects.filter(user=user, role_level__gt=3).values('organization')
            org = user.default_organization.helpdesk_organization
            return_info['orgs'] = helpdesk_orgs.filter(id__in=orgs)
            return_info['default_org'] = org
            return_info['url'] = '?org=' + org.name

        else:
            helpdesk_orgs = get_helpdesk_orgs_for_domain(domain_id)
            if len(helpdesk_orgs) == 1:
                return_info['default_org'] = helpdesk_orgs.first()
                return_info['url'] = ''
            elif 'org' in request.GET:
                url_org = request.GET.get('org')  # todo is this unsafe?
                try:
                    org = Organization.objects.filter(name=url_org).first()
                    return_info['default_org'] = org.helpdesk_organization
                    return_info['url'] = '?org=' + org.name
                except AttributeError:
                    pass
            else:
                if not user.is_anonymous:
                    org = user.default_organization.helpdesk_organization
                    return_info['default_org'] = org
                    return_info['url'] = '?org=' + org.name
                elif user.is_anonymous and len(helpdesk_orgs) > 1:
                    return_info['default_org'] = {'name': 'Select an Organization'}
                    return_info['orgs'] = helpdesk_orgs

        return return_info
    except Exception as e:
        import sys
        print("'organization_info' template tag (django-helpdesk) crashed with following error:",
              file=sys.stderr)
        print(e, file=sys.stderr)
        return ''
