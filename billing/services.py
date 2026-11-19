# from django.db import transaction
# from django.db.models import F, Max

# from .exceptions import InsufficientStock
# from .models import Invoice, InvoiceItem, Payment, Product
# from .tasks import send_invoice_email

# VAT_RATE = 0.13


# def next_invoice_number(business):
#     last = Invoice.objects.filter(business=business).aggregate(n=Max("number"))["n"]
#     return (last or 0) + 1


# def calculate_totals(invoice):
#     subtotal = 0
#     for item in invoice.items.all():
#         subtotal += float(item.unit_price) * item.quantity
#     tax = round(subtotal * VAT_RATE, 2)
#     invoice.subtotal = subtotal
#     invoice.tax = tax
#     invoice.total = subtotal + tax


# @transaction.atomic
# def create_invoice(*, business, customer, due_date, items, user):
#     invoice = Invoice.objects.create(
#         business=business,
#         customer=customer,
#         number=next_invoice_number(business),
#         status=Invoice.Status.ISSUED,
#         due_date=due_date,
#         created_by=user,
#     )

#     for row in items:
#         product = Product.objects.get(pk=row["product_id"], business=business)
#         quantity = row["quantity"]
#         if product.stock < quantity:
#             raise InsufficientStock(product.sku, product.stock, quantity)

#         product.stock -= quantity
#         product.save(update_fields=["stock"])

#         InvoiceItem.objects.create(
#             invoice=invoice,
#             product=product,
#             quantity=quantity,
#             unit_price=product.unit_price,
#             line_total=float(product.unit_price) * quantity,
#         )

#     calculate_totals(invoice)
#     invoice.save(update_fields=["subtotal", "tax", "total"])

#     send_invoice_email.delay(invoice.id)
#     return invoice


# @transaction.atomic
# def record_payment(*, invoice, amount, method, user=None, gateway_ref=""):
#     payment = Payment.objects.create(
#         invoice=invoice,
#         amount=amount,
#         method=method,
#         gateway_ref=gateway_ref,
#         recorded_by=user,
#     )

#     invoice.amount_paid += amount
#     if invoice.amount_paid >= invoice.total:
#         invoice.status = Invoice.Status.PAID
#     else:
#         invoice.status = Invoice.Status.PARTIALLY_PAID
#     invoice.save(update_fields=["amount_paid", "status"])
#     return payment


# @transaction.atomic
# def cancel_invoice(invoice):
#     for item in invoice.items.select_related("product"):
#         Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)
#     invoice.status = Invoice.Status.CANCELLED
#     invoice.save(update_fields=["status"])
#     return invoice


from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from .exceptions import InsufficientStock, InvalidInvoiceStateError, InvalidReturnError
from .models import (
    CreditNote,
    CreditNoteItem,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentReminderLog,
    Product,
)
from .tasks import send_invoice_email

VAT_RATE = Decimal("0.13")
TWO_PLACES = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def next_invoice_number(business):
    last = Invoice.objects.filter(business=business).aggregate(n=Max("number"))["n"]
    return (last or 0) + 1


def next_credit_note_number(business):
    last = CreditNote.objects.filter(business=business).aggregate(n=Max("number"))["n"]
    return (last or 0) + 1


def calculate_totals(invoice):
    subtotal = Decimal("0.00")
    for item in invoice.items.all():
        subtotal += item.unit_price * item.quantity
    subtotal = quantize_money(subtotal)
    tax = quantize_money(subtotal * VAT_RATE)
    invoice.subtotal = subtotal
    invoice.tax = tax
    invoice.total = subtotal + tax


@transaction.atomic
def create_invoice(*, business, customer, due_date, items, user):
    invoice = Invoice.objects.create(
        business=business,
        customer=customer,
        number=next_invoice_number(business),
        status=Invoice.Status.ISSUED,
        due_date=due_date,
        created_by=user,
    )

    for row in items:
        product = Product.objects.select_for_update().get(
            pk=row["product_id"], business=business
        )
        quantity = row["quantity"]
        if product.stock < quantity:
            raise InsufficientStock(product.sku, product.stock, quantity)

        product.stock -= quantity
        product.save(update_fields=["stock"])

        line_total = quantize_money(product.unit_price * quantity)
        InvoiceItem.objects.create(
            invoice=invoice,
            product=product,
            quantity=quantity,
            unit_price=product.unit_price,
            line_total=line_total,
        )

    calculate_totals(invoice)
    invoice.save(update_fields=["subtotal", "tax", "total"])

    send_invoice_email.delay(invoice.id)
    return invoice


@transaction.atomic
def record_payment(*, invoice, amount, method, user=None, gateway_ref=""):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

    if invoice.status == Invoice.Status.CANCELLED:
        raise InvalidInvoiceStateError("Cannot record payment on a cancelled invoice.")
    if amount <= Decimal("0.00"):
        raise InvalidInvoiceStateError("Payment amount must be greater than zero.")
    if gateway_ref and Payment.objects.filter(invoice=invoice, gateway_ref=gateway_ref).exists():
        return Payment.objects.get(invoice=invoice, gateway_ref=gateway_ref)

    payment = Payment.objects.create(
        invoice=invoice,
        amount=amount,
        method=method,
        gateway_ref=gateway_ref,
        recorded_by=user,
    )

    invoice.amount_paid += amount
    if invoice.balance == Decimal("0.00"):
        invoice.status = Invoice.Status.PAID
    else:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    invoice.save(update_fields=["amount_paid", "status"])
    return payment


@transaction.atomic
def cancel_invoice(invoice):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.credit_notes.exists():
        raise InvalidInvoiceStateError("Invoices with returns cannot be cancelled.")

    for item in invoice.items.select_related("product"):
        Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)
    invoice.status = Invoice.Status.CANCELLED
    invoice.save(update_fields=["status"])
    return invoice


@transaction.atomic
def create_return(*, invoice, items, reason="", refund_method=None, user=None):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        raise InvalidReturnError(
            f"Cannot return items from an invoice with status '{invoice.status}'."
        )

    # Map invoice items by product_id (existing API enforces 1 line per product per invoice)
    invoice_items_by_product = {
        item.product_id: item for item in invoice.items.select_related("product")
    }

    # Calculate already-returned quantities per invoice_item
    prior_returns = (
        CreditNoteItem.objects.filter(credit_note__invoice=invoice)
        .values("invoice_item_id")
        .annotate(returned_qty=Sum("quantity"))
    )
    returned_by_item = {row["invoice_item_id"]: row["returned_qty"] for row in prior_returns}

    validated_lines = []
    cn_subtotal = Decimal("0.00")

    for row in items:
        product_id = row["product_id"]
        qty = row["quantity"]

        inv_item = invoice_items_by_product.get(product_id)
        if not inv_item:
            raise InvalidReturnError(f"Product {product_id} is not on invoice INV-{invoice.number}.")

        already_returned = returned_by_item.get(inv_item.id, 0)
        returnable_qty = inv_item.quantity - already_returned
        if qty > returnable_qty:
            raise InvalidReturnError(
                f"Cannot return {qty} of {inv_item.product.sku}; only {returnable_qty} returnable."
            )

        line_total = quantize_money(inv_item.unit_price * qty)
        cn_subtotal += line_total
        validated_lines.append((inv_item, qty, line_total))

    cn_subtotal = quantize_money(cn_subtotal)

    # Check if this return brings total returned subtotal to 100% of invoice subtotal
    prior_cn_totals = invoice.credit_notes.aggregate(
        prev_sub=Sum("subtotal"), prev_tax=Sum("tax"), prev_total=Sum("total")
    )
    prev_sub = prior_cn_totals["prev_sub"] or Decimal("0.00")
    prev_tax = prior_cn_totals["prev_tax"] or Decimal("0.00")

    if prev_sub + cn_subtotal == invoice.subtotal:
        # Ensure exact tax reconciliation on full return
        cn_tax = invoice.tax - prev_tax
    else:
        cn_tax = quantize_money(cn_subtotal * VAT_RATE)

    cn_total = cn_subtotal + cn_tax

    # Calculate financial reconciliation between unpaid balance reduction vs cash/bank refund
    unpaid_before = invoice.balance
    credit_applied = min(cn_total, unpaid_before)
    refund_amount = cn_total - credit_applied

    if refund_amount > Decimal("0.00"):
        effective_refund_method = refund_method or CreditNote.RefundMethod.CASH
        if effective_refund_method == CreditNote.RefundMethod.NONE:
            raise InvalidReturnError(
                "A refund method (cash, bank, gateway) is required when refunding a paid amount."
            )
    else:
        effective_refund_method = CreditNote.RefundMethod.NONE

    credit_note = CreditNote.objects.create(
        business=invoice.business,
        invoice=invoice,
        customer=invoice.customer,
        number=next_credit_note_number(invoice.business),
        reason=reason,
        subtotal=cn_subtotal,
        tax=cn_tax,
        total=cn_total,
        credit_applied=credit_applied,
        refund_amount=refund_amount,
        refund_method=effective_refund_method,
        created_by=user,
    )

    for inv_item, qty, line_total in validated_lines:
        Product.objects.filter(pk=inv_item.product_id).update(stock=F("stock") + qty)
        CreditNoteItem.objects.create(
            credit_note=credit_note,
            invoice_item=inv_item,
            product=inv_item.product,
            quantity=qty,
            unit_price=inv_item.unit_price,
            line_total=line_total,
        )

    invoice.credit_total += cn_total
    if invoice.balance == Decimal("0.00"):
        invoice.status = Invoice.Status.PAID
    elif invoice.amount_paid > Decimal("0.00"):
        invoice.status = Invoice.Status.PARTIALLY_PAID
    else:
        invoice.status = Invoice.Status.ISSUED

    invoice.save(update_fields=["credit_total", "status"])
    return credit_note


def get_overdue_invoices_for_reminder(as_of_date=None):
    today = as_of_date or timezone.localdate()
    already_reminded_ids = PaymentReminderLog.objects.filter(
        sent_date=today,
        status=PaymentReminderLog.Status.SENT,
    ).values_list("invoice_id", flat=True)

    return (
        Invoice.objects.select_related("business", "customer")
        .filter(
            status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
            due_date__lt=today,
        )
        .exclude(customer__phone="")
        .exclude(id__in=already_reminded_ids)
    )