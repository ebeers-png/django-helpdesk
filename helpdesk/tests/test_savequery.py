# -*- coding: utf-8 -*-
from django.urls import reverse
from django.test import TestCase
from helpdesk.models import Queue
from helpdesk.tests.helpers import get_user
from seed.lib.superperms.orgs.models import *


class TestSavingSharedQuery(TestCase):
    def setUp(self):
        self.org = Organization.objects.create()
        q = Queue(title='Q1', slug='q1', organization=self.org)
        q.save()
        self.q = q

    def test_cansavequery(self):
        """Can a query be saved"""
        url = reverse('helpdesk:savequery')
        self.client.login(username=get_user(is_staff=True, organization=self.org).get_username(),
                          password='password')
        response = self.client.post(
            url,
            data={
                'title': 'ticket on my queue',
                'queue': self.q,
                'shared': 'on',
                'query_encoded':
                    'KGRwMApWZmlsdGVyaW5nCnAxCihkcDIKVnN0YXR1c19faW4KcDMKKG'
                    'xwNApJMQphSTIKYUkzCmFzc1Zzb3J0aW5nCnA1ClZjcmVhdGVkCnA2CnMu'
            })
        self.assertEqual(response.status_code, 302)
        self.assertTrue('tickets/?saved-query=1' in response.url)
