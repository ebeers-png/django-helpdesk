from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect

from django.contrib.auth.decorators import user_passes_test


from helpdesk import settings as helpdesk_settings
from seed.lib.superperms.orgs.decorators import requires_member
from seed.lib.superperms.orgs.models import OrganizationUser


def check_staff_status(check_staff=False):
    """
    Somewhat ridiculous currying to check user permissions without using lambdas.
    The function most only take one User parameter at the end for use with
    the Django function user_passes_test.
    """
    def check_superuser_status(check_superuser):
        def check_user_status(u):
            is_ok = u.is_authenticated and u.is_active
            is_member = False
            if u.is_authenticated:  # If False, person is an AnonymousUser and can't be searched for
                org_user = OrganizationUser.objects.filter(user=u)
                if not org_user.exists():
                    return false
                org_user = org_user.first()  # TODO change later using the user's current org
                is_member = requires_member(org_user)
            if check_staff:
                return is_ok and u.is_staff and is_member
            elif check_superuser:
                return is_ok and u.is_superuser and is_member
            else:
                return is_ok
        return check_user_status
    return check_superuser_status


if callable(helpdesk_settings.HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE):
    # apply a custom user validation condition
    is_helpdesk_staff = helpdesk_settings.HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE
elif helpdesk_settings.HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE:
    # treat 'normal' users like 'staff'
    is_helpdesk_staff = check_staff_status(False)(False)
else:
    is_helpdesk_staff = check_staff_status(True)(False)

helpdesk_staff_member_required = user_passes_test(is_helpdesk_staff)
helpdesk_superuser_required = user_passes_test(check_staff_status(False)(True))


def protect_view(view_func):
    """
    Decorator for protecting the views checking user, redirecting
    to the log-in page if necessary or returning 404 status code
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated and helpdesk_settings.HELPDESK_REDIRECT_TO_LOGIN_BY_DEFAULT:
            return redirect('helpdesk:login')
        elif not request.user.is_authenticated and helpdesk_settings.HELPDESK_ANON_ACCESS_RAISES_404:
            raise Http404
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def staff_member_required(view_func):
    """
    Decorator for staff member the views checking user, redirecting
    to the log-in page if necessary or returning 403
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated and not request.user.is_active:
            return redirect('helpdesk:login')
        if not helpdesk_settings.HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE and not request.user.is_staff:
            raise PermissionDenied()
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def superuser_required(view_func):
    """
    Decorator for superuser member the views checking user, redirecting
    to the log-in page if necessary or returning 403
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated and not request.user.is_active:
            return redirect('helpdesk:login')
        if not request.user.is_superuser:
            raise PermissionDenied()
        return view_func(request, *args, **kwargs)

    return _wrapped_view
