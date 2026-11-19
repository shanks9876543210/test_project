# import logging
# from decimal import Decimal

# import requests
# from celery import shared_task
# from django.conf import settings
# from django.core.mail import send_mail

# logger = logging.getLogger(__name__)


# @shared_task
# def send_invoice_email(invoice_id):
#     from .models import Invoice

#     invoice = Invoice.objects.select_related("customer", "business").get(pk=invoice_id)
#     if not invoice.customer.email:
#         return

#     send_mail(
#         subject=f"Invoice #{invoice.number} from {invoice.business.name}",
#         message=(
#             f"Dear {invoice.customer.name},\n\n"
#             f"Your invoice #{invoice.number} for Rs. {invoice.total} is due on "
#             f"{invoice.due_date:%d %b %Y}.\n\nThank you for your business."
#         ),
#         from_email=None,
#         recipient_list=[invoice.customer.email],
#     )


# def send_sms(phone, text):
#     response = requests.post(
#         settings.SMS_GATEWAY_URL,
#         json={"to": phone, "text": text},
#         headers={"Authorization": f"Bearer {settings.SMS_GATEWAY_TOKEN}"},
#         timeout=10,
#     )
#     response.raise_for_status()


# @shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
# def process_gateway_payment(self, payload):
#     from .models import Invoice, Payment
#     from .services import record_payment

#     invoice = Invoice.objects.select_related("customer").get(pk=payload["invoice_id"])
#     amount = Decimal(str(payload["amount"]))

#     record_payment(
#         invoice=invoice,
#         amount=amount,
#         method=Payment.Method.GATEWAY,
#         gateway_ref=payload["transaction_id"],
#     )
#     logger.info("Recorded gateway payment %s for invoice %s", payload["transaction_id"], invoice.pk)

#     if invoice.customer.phone:
#         send_sms(
#             invoice.customer.phone,
#             f"Payment of Rs. {amount} received for invoice #{invoice.number}. Thank you!",
#         )



import logging
from datetime import time
from decimal import Decimal

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

QUIET_HOURS_START = time(9, 0)   # 09:00 AM local time
QUIET_HOURS_END = time(19, 0)    # 07:00 PM local time


def is_within_sms_hours(now=None) -> bool:
    local_now = timezone.localtime(now) if now else timezone.localtime()
    current_t = local_now.time()
    return QUIET_HOURS_START <= current_t < QUIET_HOURS_END


@shared_task
def send_invoice_email(invoice_id):
    from .models import Invoice

    invoice = Invoice.objects.select_related("customer", "business").get(pk=invoice_id)
    if not invoice.customer.email:
        return

    send_mail(
        subject=f"Invoice #{invoice.number} from {invoice.business.name}",
        message=(
            f"Dear {invoice.customer.name},\n\n"
            f"Your invoice #{invoice.number} for Rs. {invoice.total} is due on "
            f"{invoice.due_date:%d %b %Y}.\n\nThank you for your business."
        ),
        from_email=None,
        recipient_list=[invoice.customer.email],
    )


def send_sms(phone, text):
    response = requests.post(
        settings.SMS_GATEWAY_URL,
        json={"to": phone, "text": text},
        headers={"Authorization": f"Bearer {settings.SMS_GATEWAY_TOKEN}"},
        timeout=10,
    )
    response.raise_for_status()


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def process_gateway_payment(self, payload):
    from .models import Invoice, Payment
    from .services import record_payment

    invoice = Invoice.objects.select_related("customer").get(pk=payload["invoice_id"])
    amount = Decimal(str(payload["amount"]))
    tx_id = payload["transaction_id"]

    already_recorded = Payment.objects.filter(invoice=invoice, gateway_ref=tx_id).exists()
    record_payment(
        invoice=invoice,
        amount=amount,
        method=Payment.Method.GATEWAY,
        gateway_ref=tx_id,
    )
    logger.info("Recorded gateway payment %s for invoice %s", tx_id, invoice.pk)

    if not already_recorded and invoice.customer.phone:
        send_sms(
            invoice.customer.phone,
            f"Payment of Rs. {amount} received for invoice #{invoice.number}. Thank you!",
        )


@shared_task
def send_single_overdue_reminder(invoice_id, ignore_quiet_hours=False):
    from .models import Invoice, PaymentReminderLog

    if not ignore_quiet_hours and not is_within_sms_hours():
        logger.info("Skipping reminder for invoice %s: outside SMS hours.", invoice_id)
        return "skipped_quiet_hours"

    today = timezone.localdate()

    with transaction.atomic():
        invoice = (
            Invoice.objects.select_for_update()
            .select_related("business", "customer")
            .filter(pk=invoice_id)
            .first()
        )
        if not invoice:
            return "not_found"

        if invoice.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
            return "not_unpaid"

        if invoice.due_date >= today or invoice.balance <= Decimal("0.00"):
            return "not_overdue"

        phone = (invoice.customer.phone or "").strip()
        if not phone:
            return "no_phone"

        if PaymentReminderLog.objects.filter(
            invoice=invoice, sent_date=today, status=PaymentReminderLog.Status.SENT
        ).exists():
            return "already_sent_today"

        days_overdue = (today - invoice.due_date).days
        message = (
            f"Reminder from {invoice.business.name}: Invoice INV-{invoice.number} "
            f"for Rs. {invoice.balance:.2f} was due on {invoice.due_date:%d %b %Y} "
            f"({days_overdue} day(s) overdue). Please arrange payment."
        )

        try:
            send_sms(phone, message)
            PaymentReminderLog.objects.create(
                invoice=invoice,
                phone=phone,
                sent_date=today,
                message=message,
                status=PaymentReminderLog.Status.SENT,
            )
            logger.info("Sent overdue SMS reminder for invoice %s to %s", invoice.id, phone)
            return "sent"
        except IntegrityError:
            # Concurrent worker already committed a SENT log for today
            return "already_sent_today"
        except Exception as exc:
            logger.exception("Failed to send overdue SMS for invoice %s", invoice.id)
            PaymentReminderLog.objects.create(
                invoice=invoice,
                phone=phone,
                sent_date=today,
                message=message,
                status=PaymentReminderLog.Status.FAILED,
                error_message=str(exc),
            )
            return "failed"


@shared_task
def send_overdue_reminders(ignore_quiet_hours=False):
    from .services import get_overdue_invoices_for_reminder

    if not ignore_quiet_hours and not is_within_sms_hours():
        logger.info("Skipping overdue reminder batch: outside SMS hours (09:00-19:00).")
        return {"queued": 0, "reason": "quiet_hours"}

    today = timezone.localdate()
    invoice_ids = list(
        get_overdue_invoices_for_reminder(as_of_date=today).values_list("id", flat=True)
    )
    for inv_id in invoice_ids:
        send_single_overdue_reminder.delay(inv_id, ignore_quiet_hours=ignore_quiet_hours)

    logger.info("Queued %d overdue invoice reminders.", len(invoice_ids))
    return {"queued": len(invoice_ids)}