#!/usr/bin/python
"""
Jutda Helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

scripts/get_email.py - Designed to be run from cron, this script checks the
                       POP and IMAP boxes, or a local mailbox directory,
                       defined for the queues within a
                       helpdesk, creating tickets from the new messages (or
                       adding to existing tickets if needed)
"""
from django.core.management.base import BaseCommand

from helpdesk.email import process_email


class Command(BaseCommand):

    def __init__(self):
        BaseCommand.__init__(self)

    help = 'Process django-helpdesk queues and process e-mails via POP3/IMAP or ' \
           'from a local mailbox directory as required, feeding them into the helpdesk.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--quiet',
            action='store_true',
            dest='quiet',
            default=False,
            help='Hide details about each queue/message as they are processed',
        )

        parser.add_argument(
            '--debug',
            required=False,
            action='store_true',
            dest='debugging',
            default=False,
            help='Will create new tickets and queue emails, but not delete emails from the servers.',
        )

        parser.add_argument(
            '--date',
            required=False,
            action='store_true',
            default=False
        )

        parser.add_argument(
            '--labels',
            nargs='+',
            type=str
        )

    def handle(self, *args, **options):
        quiet = options.get('quiet', False)
        debugging = options.get('debugging', False)

        options_list = {}
        if options.get('date', None):
            options_list['date'] = True

        # For Google emails
        labels = options.get('labels', None)
        if labels:
            options_list['labels'] = labels

        process_email(quiet=quiet, debugging=debugging, options=options_list)


if __name__ == '__main__':
    process_email()
