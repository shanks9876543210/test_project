from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Business,
    CreditNote,
    Customer,
    Invoice,
    PaymentReminderLog,
    Product,
    StaffProfile,
)
from .services import create_invoice, record_payment
from .tasks import send_overdue_reminders

User = get_user_model()
KATHMANDU = ZoneInfo("Asia/Kathmandu")


class BillingBaseTestCase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Himalayan Traders", pan_number="601234567")
        self.user = User.objects.create_user(username="owner1", password="password123")
        StaffProfile.objects.create(
            user=self.user, business=self.business, role=StaffProfile.Role.OWNER
        )

        self.customer = Customer.objects.create(
            business=self.business,
            name="Ram Bahadur",
            phone="9841000001",
            email="ram@example.com",
        )
        self.prod_a = Product.objects.create(
            business=self.business, sku="SKU-A", name="Rice 25kg", unit_price=Decimal("2000.00"), stock=10
        )
        self.prod_b = Product.objects.create(
            business=self.business, sku="SKU-B", name="Cooking Oil 5L", unit_price=Decimal("1000.00"), stock=20
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ReturnsFeatureTests(BillingBaseTestCase):
    def test_partial_return_on_paid_invoice_restocks_and_records_refund(self):
        # Create invoice: 2 * 2000 + 1 * 1000 = 5000 subtotal + 650 VAT = 5650 total
        invoice = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=timezone.localdate() + timedelta(days=7),
            items=[
                {"product_id": self.prod_a.id, "quantity": 2},
                {"product_id": self.prod_b.id, "quantity": 1},
            ],
            user=self.user,
        )
        record_payment(invoice=invoice, amount=Decimal("5650.00"), method="cash", user=self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        # Return 1 unit of prod_a (Rs. 2000 + 13% VAT 260 = Rs. 2260)
        resp = self.client.post(
            f"/api/invoices/{invoice.id}/returns/",
            {
                "items": [{"product_id": self.prod_a.id, "quantity": 1}],
                "reason": "Damaged bag",
                "refund_method": "cash",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.data
        self.assertEqual(data["credit_note_number"], "CN-1")
        self.assertEqual(Decimal(data["subtotal"]), Decimal("2000.00"))
        self.assertEqual(Decimal(data["tax"]), Decimal("260.00"))
        self.assertEqual(Decimal(data["total"]), Decimal("2260.00"))
        self.assertEqual(Decimal(data["credit_applied"]), Decimal("0.00"))
        self.assertEqual(Decimal(data["refund_amount"]), Decimal("2260.00"))
        self.assertEqual(data["refund_method"], "cash")

        # Verify stock was restored from 8 back to 9
        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.stock, 9)

        # Verify invoice balance remains 0 and credit_total is updated
        invoice.refresh_from_db()
        self.assertEqual(invoice.credit_total, Decimal("2260.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))

        # Verify accountant HTML document endpoint
        doc_resp = self.client.get(f"/api/returns/{data['id']}/document/")
        self.assertEqual(doc_resp.status_code, 200)
        self.assertIn("CREDIT NOTE", doc_resp.content.decode())
        self.assertIn("CN-1", doc_resp.content.decode())
        self.assertIn("601234567", doc_resp.content.decode())

    def test_return_on_unpaid_invoice_reduces_customer_balance(self):
        invoice = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=timezone.localdate() + timedelta(days=7),
            items=[{"product_id": self.prod_a.id, "quantity": 3}],
            user=self.user,
        )
        # Total = 6000 + 780 = 6780
        resp = self.client.post(
            f"/api/invoices/{invoice.id}/returns/",
            {"items": [{"product_id": self.prod_a.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Decimal(resp.data["credit_applied"]), Decimal("2260.00"))
        self.assertEqual(Decimal(resp.data["refund_amount"]), Decimal("0.00"))

        invoice.refresh_from_db()
        self.assertEqual(invoice.balance, Decimal("4520.00"))
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

        # Check outstanding report reflects reduced balance
        out_resp = self.client.get("/api/reports/outstanding/?q=Ram")
        self.assertEqual(out_resp.status_code, 200)
        self.assertEqual(Decimal(str(out_resp.data[0]["outstanding"])), Decimal("4520.00"))

    def test_cannot_over_return_across_multiple_returns_or_cancelled_invoice(self):
        invoice = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=timezone.localdate() + timedelta(days=7),
            items=[{"product_id": self.prod_b.id, "quantity": 2}],
            user=self.user,
        )
        # First return: 1 unit succeeds
        r1 = self.client.post(
            f"/api/invoices/{invoice.id}/returns/",
            {"items": [{"product_id": self.prod_b.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(r1.status_code, 201)

        # Second return: 2 units fails because only 1 remains
        r2 = self.client.post(
            f"/api/invoices/{invoice.id}/returns/",
            {"items": [{"product_id": self.prod_b.id, "quantity": 2}]},
            format="json",
        )
        self.assertEqual(r2.status_code, 400)

        # Cancelled invoice cannot be returned
        inv2 = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=timezone.localdate() + timedelta(days=7),
            items=[{"product_id": self.prod_b.id, "quantity": 1}],
            user=self.user,
        )
        self.client.post(f"/api/invoices/{inv2.id}/cancel/")
        r3 = self.client.post(
            f"/api/invoices/{inv2.id}/returns/",
            {"items": [{"product_id": self.prod_b.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(r3.status_code, 400)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class PaymentReminderTests(BillingBaseTestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        # Overdue unpaid invoice -> SHOULD remind
        self.overdue_inv = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=today - timedelta(days=3),
            items=[{"product_id": self.prod_b.id, "quantity": 1}],
            user=self.user,
        )
        # Not yet due invoice -> SHOULD NOT remind
        self.future_inv = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=today + timedelta(days=5),
            items=[{"product_id": self.prod_b.id, "quantity": 1}],
            user=self.user,
        )
        # Overdue but already paid -> SHOULD NOT remind
        self.paid_inv = create_invoice(
            business=self.business,
            customer=self.customer,
            due_date=today - timedelta(days=2),
            items=[{"product_id": self.prod_b.id, "quantity": 1}],
            user=self.user,
        )
        record_payment(
            invoice=self.paid_inv, amount=self.paid_inv.total, method="cash", user=self.user
        )

    @patch("billing.tasks.send_sms")
    @patch("billing.tasks.timezone.localtime")
    def test_sends_once_per_day_during_daytime_and_blocks_duplicates(
        self, mock_localtime, mock_send_sms
    ):
        # 11:30 AM Kathmandu time (allowed window: 09:00 - 19:00)
        mock_localtime.return_value = datetime(2026, 11, 19, 11, 30, tzinfo=KATHMANDU)

        res1 = send_overdue_reminders()
        self.assertEqual(res1["queued"], 1)
        self.assertEqual(mock_send_sms.call_count, 1)
        self.assertIn("INV-1", mock_send_sms.call_args[0][1])

        # Second run same day -> 0 queued, no duplicate SMS
        res2 = send_overdue_reminders()
        self.assertEqual(res2["queued"], 0)
        self.assertEqual(mock_send_sms.call_count, 1)
        self.assertEqual(PaymentReminderLog.objects.filter(status="sent").count(), 1)

    @patch("billing.tasks.send_sms")
    @patch("billing.tasks.timezone.localtime")
    def test_quiet_hours_blocks_night_messages(self, mock_localtime, mock_send_sms):
        # 10:15 PM Kathmandu time (outside 09:00 - 19:00)
        mock_localtime.return_value = datetime(2026, 11, 19, 22, 15, tzinfo=KATHMANDU)

        res = send_overdue_reminders()
        self.assertEqual(res["reason"], "quiet_hours")
        self.assertEqual(mock_send_sms.call_count, 0)