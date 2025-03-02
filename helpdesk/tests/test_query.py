from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from helpdesk.models import KBCategory, KBItem, Queue, Ticket, FormType
from helpdesk.query import query_to_base64

from helpdesk.tests.helpers import (get_staff_user, reload_urlconf, User, create_ticket, print_response)
from seed.lib.superperms.orgs.models import Organization, OrganizationUser, ROLE_BUILDING_VIEWER

class QueryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create()
        self.form = FormType.objects.create(organization=self.org)
        self.queue = Queue.objects.create(
            title="Test queue",
            slug="test_queue",
            allow_public_submission=True,
            organization=self.org,
        )
        self.queue.save()
        cat = KBCategory.objects.create(
            title="Test Cat",
            slug="test_cat",
            description="This is a test category",
            queue=self.queue,
            organization=self.org,
        )
        cat.save()
        self.kbitem1 = KBItem.objects.create(
            category=cat,
            title="KBItem 1",
            question="What?",
            answer="A KB Item",
        )
        self.user = get_staff_user(organization=self.org)
        self.ticket1 = Ticket.objects.create(
            title="unassigned to kbitem",
            queue=self.queue,
            description="lol",
            ticket_form=self.form,
        )
        self.ticket2 = Ticket.objects.create(
            title="assigned to kbitem",
            queue=self.queue,
            description="lol",
            kbitem=self.kbitem1,
            ticket_form=self.form,
        )

    def tearDown(self):
        Ticket.objects.all().delete()

    def loginUser(self, is_staff=True):
        """Create a staff user and login"""
        User = get_user_model()
        self.user = User.objects.create(
            username='User_1',
            default_organization=self.org,
        )
        self.user.set_password('pass')
        self.user.save()
        self.org.users.add(self.user)               # Gets added as staff member of org automatically
        if not is_staff:
            OrganizationUser.objects.filter(organization=self.org, user=self.user).update(role_level=ROLE_BUILDING_VIEWER)
        self.client.login(username='User_1', password='pass')

    def test_query_basic(self):
        self.loginUser()
        query = query_to_base64({})
        response = self.client.get(reverse('helpdesk:datatables_ticket_list', args=[query]))
        t1 = Ticket.objects.filter(title='unassigned to kbitem').first()
        t2 = Ticket.objects.filter(title='assigned to kbitem').first()
        self.assertEqual(
            response.json(),
            {
                "data":
                [{"ticket": "%s [test_queue-%s]" % (t1.id, t1.id), "id": t1.id, "priority": 3, "title": "unassigned to kbitem", "queue": {"title": "Test queue", "id": t1.queue.id}, "status": "Open", "created": "now", "due_date": None, "assigned_to": "None", "submitter": None, "row_class": "", "time_spent": "", "kbitem": ""},
                 {"ticket": "%s [test_queue-%s]" % (t2.id, t2.id), "id": t2.id, "priority": 3, "title": "assigned to kbitem", "queue": {"title": "Test queue", "id": t2.queue.id}, "status": "Open", "created": "now", "due_date": None, "assigned_to": "None", "submitter": None, "row_class": "", "time_spent": "", "kbitem": "KBItem 1"}],
                "recordsFiltered": 2,
                "recordsTotal": 2,
                "draw": 0,
            },
        )

    def test_query_by_kbitem(self):
        self.loginUser()
        query = query_to_base64(
            {'filtering': {'kbitem__in': [self.kbitem1.pk]}}
        )
        response = self.client.get(reverse('helpdesk:datatables_ticket_list', args=[query]))
        ticket = Ticket.objects.filter(title='assigned to kbitem').first()
        self.assertEqual(
            response.json(),
            {
                "data":
                [{"ticket": "%s [test_queue-%s]" % (ticket.id, ticket.id), "id": ticket.id, "priority": 3, "title": "assigned to kbitem", "queue": {"title": "Test queue", "id": ticket.queue.id}, "status": "Open", "created": "now", "due_date": None, "assigned_to": "None", "submitter": None, "row_class": "", "time_spent": "", "kbitem": "KBItem 1"}],
                "recordsFiltered": 1,
                "recordsTotal": 1,
                "draw": 0,
            },
        )

    def test_query_by_no_kbitem(self):
        self.loginUser()
        query = query_to_base64(
            {'filtering_or': {'kbitem__in': [self.kbitem1.pk]}}
        )
        response = self.client.get(reverse('helpdesk:datatables_ticket_list', args=[query]))
        ticket = Ticket.objects.filter(title='assigned to kbitem').first()
        self.assertEqual(
            response.json(),
            {
                "data":
                [{"ticket": "%s [test_queue-%s]" % (ticket.id, ticket.id), "id": ticket.id, "priority": 3, "title": "assigned to kbitem", "queue": {"title": "Test queue", "id": ticket.queue.id}, "status": "Open", "created": "now", "due_date": None, "assigned_to": "None", "submitter": None, "row_class": "", "time_spent": "", "kbitem": "KBItem 1"}],
                "recordsFiltered": 1,
                "recordsTotal": 1,
                "draw": 0,
            },
        )
