from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone


class Business(models.Model):
    name = models.CharField(max_length=200)
    pan_number = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ACCOUNTANT = "accountant", "Accountant"
        CASHIER = "cashier", "Cashier"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="staff")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CASHIER)

    def __str__(self):
        return f"{self.user} @ {self.business}"


class Customer(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "phone"],
                condition=~models.Q(phone=""),
                name="unique_business_customer_phone",
            )
        ]

    def __str__(self):
        return self.name


class Product(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="products")
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField(default=0)

    class Meta:
        unique_together = ("business", "sku")

    def __str__(self):
        return f"{self.sku} - {self.name}"


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="invoices")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("business", "number")

    def __str__(self):
        return f"INV-{self.number}"

    @property
    def balance(self):
        due = self.total - self.amount_paid - self.credit_total
        return max(Decimal("0.00"), due)


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank transfer"
        GATEWAY = "gateway", "Online gateway"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    gateway_ref = models.CharField(max_length=100, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self):
        return f"{self.amount} for {self.invoice}"


class CreditNote(models.Model):
    class RefundMethod(models.TextChoices):
        NONE = "none", "None (Balance reduction only)"
        CASH = "cash", "Cash refund"
        BANK = "bank", "Bank transfer refund"
        GATEWAY = "gateway", "Gateway refund"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="credit_notes")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="credit_notes")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="credit_notes")
    number = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    tax = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    credit_applied = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Portion of credit note that reduced unpaid invoice balance.",
    )
    refund_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Portion refunded back to customer because invoice was already paid.",
    )
    refund_method = models.CharField(
        max_length=20, choices=RefundMethod.choices, default=RefundMethod.NONE
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("business", "number")

    def __str__(self):
        return f"CN-{self.number}"


class CreditNoteItem(models.Model):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="items")
    invoice_item = models.ForeignKey(
        InvoiceItem, on_delete=models.PROTECT, related_name="returned_items"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)


class PaymentReminderLog(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="reminder_logs"
    )
    phone = models.CharField(max_length=20)
    sent_date = models.DateField(default=timezone.localdate)
    sent_at = models.DateTimeField(auto_now_add=True)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "sent_date"],
                condition=models.Q(status="sent"),
                name="unique_daily_sent_invoice_reminder",
            )
        ]

    def __str__(self):
        return f"Reminder for {self.invoice} on {self.sent_date} ({self.status})"