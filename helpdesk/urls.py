"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

urls.py - Mapping of URL's to our various views. Note we always used NAMED
          views for simplicity in linking later on.
"""

from django.conf.urls import url
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView

from helpdesk.decorators import helpdesk_staff_member_required, protect_view
from helpdesk import settings as helpdesk_settings
from helpdesk.views import feeds, staff, public, kb, login
try:
    # TODO: why is it imported? due to some side-effect or by mistake?
    import helpdesk.tasks  # NOQA
except ImportError:
    pass


class DirectTemplateView(TemplateView):
    extra_context = None

    def get_context_data(self, **kwargs):
        context = super(self.__class__, self).get_context_data(**kwargs)
        if self.extra_context is not None:
            for key, value in self.extra_context.items():
                if callable(value):
                    context[key] = value()
                else:
                    context[key] = value
        return context


app_name = 'helpdesk'

base64_pattern = r'(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$'

urlpatterns = [
    url(r'^dashboard/$',
        staff.dashboard,
        name='dashboard'),

    url(r'^tickets/$',
        staff.ticket_list,
        name='list'),

    url(r'^tickets/tags/$',
        staff.get_tags,
        name='get_tags'),

    url(r'^tickets/update/$',
        staff.mass_update,
        name='mass_update'),

    url(r'^tickets/update_tags/$',
        staff.mass_update_tags,
        name='mass_update_tags'),
    
    url(r'^tickets/get_elapsed_time/(?P<ticket_id>[0-9]+)/$',
        staff.get_elapsed_time,
        name='get_elapsed_time'),

    
    url(r'^tickets/start_timer/$',
        staff.start_timer,
        name='start_timer'),
    
    url(r'^tickets/stop_timer/$',
        staff.stop_timer,
        name='stop_timer'),


    url(r'^tickets/merge$',
        staff.merge_tickets,
        name='merge_tickets'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/$',
        staff.view_ticket,
        name='view'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/followup_edit/(?P<followup_id>[0-9]+)/$',
        staff.followup_edit,
        name='followup_edit'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/followup_delete/(?P<followup_id>[0-9]+)/$',
        staff.followup_delete,
        name='followup_delete'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/edit/$',
        staff.edit_ticket,
        name='edit'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/update/$',
        staff.update_ticket,
        name='update'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/delete/$',
        staff.delete_ticket,
        name='delete'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/hold/$',
        staff.hold_ticket,
        name='hold'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/unhold/$',
        staff.unhold_ticket,
        name='unhold'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/cc/$',
        staff.ticket_cc,
        name='ticket_cc'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/cc/add/$',
        staff.ticket_cc_add,
        name='ticket_cc_add'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/cc/delete/(?P<cc_id>[0-9]+)/$',
        staff.ticket_cc_del,
        name='ticket_cc_del'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/dependency/add/$',
        staff.ticket_dependency_add,
        name='ticket_dependency_add'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/dependency/delete/(?P<dependency_id>[0-9]+)/$',
        staff.ticket_dependency_del,
        name='ticket_dependency_del'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/attachment_delete/(?P<attachment_id>[0-9]+)/$',
        staff.attachment_del,
        name='attachment_del'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/beam_unpair/(?P<inventory_type>(property|taxlot))/(?P<inventory_id>[0-9]+)/$',
        staff.beam_unpair,
        name='ticket_beam_unpair'),

    url(r'^tickets/(?P<ticket_id>[0-9]+)/edit_tags/$', staff.edit_ticket_tags, name='edit_ticket_tags'),

    url(r'^raw/(?P<type>\w+)/$',
        staff.raw_details,
        name='raw'),

    url(r'^rss/$',
        staff.rss_list,
        name='rss_index'),

    url(r'^reports/$',
        staff.report_index,
        name='report_index'),

    url(r'^reports/(?P<report>\w+)/$',
        staff.run_report,
        name='run_report'),

    url(r'^save_query/$',
        staff.save_query,
        name='savequery'),

    url(r'^delete_query/(?P<id>[0-9]+)/$',
        staff.delete_saved_query,
        name='delete_query'),

    url(r'^reject_query/(?P<id>[0-9]+)/$',
        staff.reject_saved_query,
        name='reject_query'),

    url(r'^reshare_query/(?P<id>[0-9]+)/$',
        staff.reshare_saved_query,
        name='reshare_query'),

    url(r'^unshare_query/(?P<id>[0-9]+)/$',
        staff.unshare_saved_query,
        name='unshare_query'),

    url(r'^settings/$',
        staff.EditUserSettingsView.as_view(),
        name='user_settings'),

    url(r'^ignore/$', staff.email_ignore, name='email_ignore'),
    url(r'^ignore/add/$', staff.email_ignore_add, name='email_ignore_add'),
    url(r'^ignore/edit/(?P<id>[0-9]+)/$', staff.email_ignore_edit, name='email_ignore_edit'),
    url(r'^ignore/delete/(?P<id>[0-9]+)/$', staff.email_ignore_del, name='email_ignore_del'),

    url(r'^tag/$', staff.tag_list, name='tag_list'),
    url(r'^tag/add/$', staff.tag_add, name='tag_add'),
    url(r'^tag/edit/(?P<id>[0-9]+)/$', staff.tag_edit, name='tag_edit'),
    url(r'^tag/delete/(?P<id>[0-9]+)/$', staff.tag_delete, name='tag_delete'),

    url(r'^preset_reply/$', staff.preset_reply_list, name='preset_reply_list'),
    url(r'^preset_reply/add/$', staff.preset_reply_add, name='preset_reply_add'),
    url(r'^preset_reply/edit/(?P<id>[0-9]+)/$', staff.preset_reply_edit, name='preset_reply_edit'),
    url(r'^preset_reply/delete/(?P<id>[0-9]+)/$', staff.preset_reply_delete, name='preset_reply_delete'),

    url(r'^email_template/$', staff.email_template_list, name='email_template_list'),
    url(r'^email_template/edit/(?P<id>[0-9]+)/$', staff.email_template_edit, name='email_template_edit'),
    url(r'^email_template/default/(?P<id>[0-9]+)/$', staff.email_template_default, name='email_template_default'),

    url(r'^preview_html$', staff.preview_html, name="preview_html"),

    url(r'^timeline_ticket_list/(?P<query>{})$'.format(base64_pattern),
        staff.timeline_ticket_list,
        name="timeline_ticket_list"),

    url(r'set_default_org/(?P<user_id>[0-9]+)/(?P<org_id>[0-9]+)/$',
        staff.set_default_org,
        name="set_default_org"),

    url(r'set_user_timezone/$',
        staff.set_user_timezone,
        name="set_user_timezone"),

    url(r'emails/(?P<ticket_id>[0-9]+)/$',
        staff.enable_disable_emails,
        name="enable_disable_emails"),

    url(r'pair_property_milestone/(?P<ticket_id>[0-9]+)/$',
        staff.pair_property_milestone,
        name="pair_property_milestone"),

    url(r'pair_property_ticket/(?P<ticket_id>[0-9]+)/$',
        staff.pair_property_ticket,
        name="pair_property_ticket"),

    url(r'copy_to_beam/(?P<ticket_id>[0-9]+)/$',
        staff.load_copy_to_beam,
        name="copy_to_beam"),

    url(r'get_building_data/(?P<ticket_id>[0-9]+)/$',
        staff.get_building_data,
        name="get_building_data"),

    url(r'update_building_data/(?P<ticket_id>[0-9]+)/$',
        staff.update_building_data,
        name="update_building_data"),

    url(r'edit_inventory_labels/(?P<inventory_type>(property|taxlot))/(?P<ticket_id>[0-9]+)/$',
        staff.edit_inventory_labels,
        name="edit_inventory_labels"),

    url(r'export_report/$',
        staff.export_report,
        name="export_report"),
]

urlpatterns += [
    # 9/24: Removed form and 'View Ticket' feature from homepage -- so just display the KB home. Add this back later
    # url(r'^$',
    #    protect_view(public.Homepage.as_view()),
    #    name='home'),

    url(r'^tickets/submit/(?P<form_id>[0-9]+)/$',
        public.create_ticket,
        name='submit'),

    url(r'^tickets/submit_iframe/$',
        public.CreateTicketIframeView.as_view(),
        name='submit_iframe'),

    url(r'^tickets/success_iframe/$',  # Ticket was submitted successfully
        public.SuccessIframeView.as_view(),
        name='success_iframe'),

    url(r'^view/$',
        public.view_ticket,
        name='public_view'),

    url(r'^change_language/$',
        public.change_language,
        name='public_change_language'),
    
    url(r'^bps_pathway_calculator/$',
        public.bps_pathway_calculator,
        name='public_bps_pathway_calculator'),
]

urlpatterns += [
    url(r'^rss/user/(?P<user_name>[^/]+)/$',
        helpdesk_staff_member_required(feeds.OpenTicketsByUser()),
        name='rss_user'),

    url(r'^rss/user/(?P<user_name>[^/]+)/(?P<queue_slug>[A-Za-z0-9_-]+)/$',
        helpdesk_staff_member_required(feeds.OpenTicketsByUser()),
        name='rss_user_queue'),

    url(r'^rss/queue/(?P<queue_slug>[A-Za-z0-9_-]+)/$',
        helpdesk_staff_member_required(feeds.OpenTicketsByQueue()),
        name='rss_queue'),

    url(r'^rss/unassigned/$',
        helpdesk_staff_member_required(feeds.UnassignedTickets()),
        name='rss_unassigned'),

    url(r'^rss/recent_activity/$',
        helpdesk_staff_member_required(feeds.RecentFollowUps()),
        name='rss_activity'),
]


urlpatterns += [
    url(r'^login/$',
        login.login,
        name='login'),

    url(r'^logout/$',
        auth_views.LogoutView.as_view(
            template_name='helpdesk/registration/login.html',
            next_page='../'),
        name='logout'),

    url(r'^password_change/$',
        auth_views.PasswordChangeView.as_view(
            template_name='helpdesk/registration/change_password.html',
            success_url='./done'),
        name='password_change'),

    url(r'^password_change/done$',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='helpdesk/registration/change_password_done.html',),
        name='password_change_done'),
]

if helpdesk_settings.HELPDESK_KB_ENABLED:
    urlpatterns += [
        url(r'^$',  # 9/24: Displaying KB index as Home, delete later
            kb.index,
            name='home'),

        url(r'^kb/$',
            kb.index,
            name='kb_index'),

        url(r'^kb/manage$',
            kb.manage,
            name='kb_manage'),

        url(r'^kb/create/article$', 
            kb.create_article,
            name='create_kb_article'),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/create/$',
            kb.create_article,
            name='create_kb_article'),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/(?P<pk>[0-9]+)/$',
            kb.article,
            name='kb_article'),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/(?P<pk>[0-9]+)/edit/$',
            kb.edit_article,
            name="edit_kb_article"),

        url(r'^preview_markdown$',
            staff.preview_markdown,
            name="preview_markdown"),

        url(r'^kb/upload_attachment',
            kb.upload_attachment,
            name="upload_attachment"),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/(?P<pk>[0-9]+)/delete/$',
            kb.delete_article,
            name="delete_kb_article"),

        url(r'^kb/create/$',
            kb.create_category,
            name='create_kb_category'),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/$',
            kb.category,
            name='kb_category'),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/edit/$',
            kb.edit_category,
            name="edit_kb_category"),

        url(r'^kb/(?P<slug>[A-Za-z0-9_-]+)/delete/$',
            kb.delete_category,
            name="delete_kb_category"),

        url(r'^kb/(?P<item>[0-9]+)/vote/$',
            kb.vote,
            name='kb_vote'),

        url(r'^kb_iframe/(?P<slug>[A-Za-z0-9_-]+)/$',
            kb.category_iframe,
            name='kb_category_iframe'),
    ]
else:  # 9/24: else-block added in case KB isn't enabled. Delete block later
    urlpatterns += [
        url(r'^$',
            protect_view(public.Homepage.as_view()),
            name='home'),
    ]

urlpatterns += [
    url(r'^help/context/$',
        TemplateView.as_view(template_name='helpdesk/help_context.html'),
        name='help_context'),

    url(r'^system_settings/$',
        login_required(DirectTemplateView.as_view(template_name='helpdesk/system_settings.html')),
        name='system_settings'),
    
    url(r'^system_settings/maintain_queues/$',
        staff.queue_list,
        name='maintain_queues'),

    url(r'^system_settings/maintain_queues/create/$',
        staff.create_queue,
        name='create_queue'),

    url(r'^system_settings/maintain_queues/(?P<slug>[A-Za-z0-9_-]+)/edit/$',
        staff.edit_queue,
        name='edit_queue'),

    url(r'^system_settings/maintain_forms/$',
        staff.form_list,
        name='maintain_forms'),
        
    url(r'^system_settings/maintain_forms/create/$',
        staff.create_form,
        name="create_form"),

    url(r'^system_settings/maintain_forms/(?P<pk>[0-9]+)/edit/$',
        staff.edit_form,
        name="edit_form"),

    url(r'^system_settings/maintain_forms/(?P<pk>[0-9]+)/delete/$',
        staff.delete_form,
        name="delete_form"),

    url(r'^system_settings/maintain_forms/(?P<pk>[0-9]+)/duplicate/$',
        staff.duplicate_form,
        name="duplicate_form"),

    url(r'^system_settings/maintain_forms/copy_field/',
        staff.copy_field,
        name="copy_field"),

    url(r'^notifications/$',
        staff.CreateNotificationView.as_view(),
        name="notifications"),

    url(r'^notifications/(?P<notification_id>[0-9]+)/$',
        staff.mark_notification_as_read,
        name="mark_notification_as_read"),
    
    url(r'^notifications/json/$',
        staff.notifications_json,
        name="notifications_json"),
    
    url(r'^create_announcement/$',
        staff.CreateAnnouncementView.as_view(),
        name="create_announcement"),
    
    url(r'^mark_announcement_as_read/(?P<notification_id>[0-9]+)/$',
        staff.mark_announcement_as_read,
        name="mark_announcement_as_read"),
    
     url(r'^run_actions/$',
        staff.run_actions,
        name="run_actions"),
    

]
