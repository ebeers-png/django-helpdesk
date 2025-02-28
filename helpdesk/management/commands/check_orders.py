#!/usr/bin/python
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.timezone import get_current_timezone

from helpdesk.models import Order, OrderSettings, PAYMENT_STATUS
from helpdesk.views.public import _check_order, _mark_buildings_paid

import logging
from seed.management.commands._localtools import get_logger

logger = logging.getLogger('scripts')
if logger.getEffectiveLevel() >= 30:
    # If script is not run with wrapper, still output to console for viewing purposes
    # Don't set to logging.DEBUG because that includes all of the django DB changes
    logger = get_logger(logging.INFO)


def process_orders():
    # check all orders older than 30 min but younger than 1 hour
    half_hour = datetime.today().astimezone(get_current_timezone()) - timedelta(minutes=30)
    hour = datetime.today().astimezone(get_current_timezone()) - timedelta(hours=1)
    for settings in OrderSettings.objects.all():
        org = settings.organization
        orders = Order.objects.filter(
            organization=org,
            payment_status=PAYMENT_STATUS.pending,
            token_created__gte=hour,
            token_created__lte=half_hour
        )
        if orders.exists():
            for order in orders:
                data = _check_order(order)

                if data['matchingOrders'] == 0:
                    order.message = data
                    order.payment_status = PAYMENT_STATUS.failed
                    order.save()
                    logger.info(f"Marked order {order.id} as failed: no matching orders")
                else:
                    for o in data['orders']:
                        if o['localRefId'] == '2025 Reporting Form' and o['orderStatus'] == 'COMPLETE':
                            with transaction.atomic():
                                order.payment_status = PAYMENT_STATUS.success
                                logger.info(f"Marked order {order.id} as successful")

                                ids = []  # update our record with the properties that were paid for
                                for item in o['items']:
                                    for field in item['itemAttributes']:
                                        if field['fieldName'] == 'VIEWID':
                                            ids.append(int(field['fieldValue']))
                                order.properties.set(ids)
                                _mark_buildings_paid(order, settings)
                                order.save()

                        elif o['localRefId'] == '2025 Reporting Form' and o['orderStatus'] == 'OPEN':
                            logger.info(f"Left order {order.id} unchanged: still open")
                            order.attempt_count += 1
                            order.save()

        older_orders = Order.objects.filter(
            organization=org,
            payment_status=PAYMENT_STATUS.pending,
            token_created__lte=hour
        )
        for order in older_orders:
            order.payment_status = PAYMENT_STATUS.failed
            order.save()
            logger.info(f"Marked order {order.id} as failed: token older than 1 hour")


class Command(BaseCommand):

    def __init__(self):
        BaseCommand.__init__(self)

    help = 'Check pending orders and update their status'

    def handle(self, *args, **options):
        logger.info(f"Starting to process orders - {datetime.today().astimezone(get_current_timezone())}")
        process_orders()
        logger.info(f"Finished processing orders.")


if __name__ == '__main__':
    process_orders()
