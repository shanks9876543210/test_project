# Implementation Notes

## 1. Questions & Assumptions Made

### Feature 1: Returns (`CreditNote`)
- **Accounting model (Mutate invoice vs. Credit Note):** Rather than deleting or editing `InvoiceItem` rows on issued invoices—which breaks audit trails and tax filings—I modeled every return as an immutable `CreditNote` (`CN-{number}`, sequential per business) linked to the original `Invoice`.
- **VAT calculation:** Returns recalculate 13% VAT proportionally on the returned subtotal. When cumulative returns on an invoice reach 100% of the original subtotal, the credit note's tax is reconciled against `invoice.tax - prior_credited_tax` so rounding never leaves a Rs. 0.01 discrepancy.
- **Paid vs. Unpaid invoices (Balance vs. Refund):**
  - If an invoice is unpaid (or partially paid), the credit note first reduces the customer's unpaid balance (`credit_applied`).
  - If the credit note total exceeds the remaining unpaid balance (for example, the customer already paid in full), the excess is recorded as `refund_amount` with an explicit `refund_method` (`cash`, `bank`, or `gateway`, defaulting to `cash` if omitted).
- **Cancelled / Draft invoices:** Returns are rejected on `draft` or `cancelled` invoices (cancelled invoices already restock items via `/cancel/`). Invoices with recorded returns cannot be cancelled afterwards.
- **Accountant document:** Added `GET /api/returns/` and `GET /api/returns/{id}/` (structured JSON) plus `GET /api/returns/{id}/document/` (printable HTML Credit Note containing Business Name, PAN, `CN-#`, referenced `INV-#`, line items, 13% VAT breakdown, and balance/refund split).

### Feature 2: Overdue SMS Payment Reminders
- **Quiet hours:** Enforced an allowed sending window of **09:00 to 19:00 (`Asia/Kathmandu`)** inside both the Celery Beat schedule and the task execution itself (`is_within_sms_hours()`). Checking inside the task ensures that even if a worker backlog drains late at night, no customer receives an SMS during quiet hours.
- **Frequency cap (At most 1 SMS/day):** Added `PaymentReminderLog` with a PostgreSQL partial unique constraint on `(invoice_id, sent_date)` where `status = 'sent'`, combined with `select_for_update()` on the `Invoice` row. Even if Celery Beat or a manual trigger overlaps, a customer receives at most one reminder per overdue invoice per calendar day.
- **Eligibility:** Reminders target only `issued` or `partially_paid` invoices where `due_date < today`, `balance > 0`, and `customer.phone` is non-empty. Failed SMS attempts are logged with `status='failed'` so they do not block retries on the next cycle while isolating failures per customer.

---

## 2. Changes Made to Existing Code & Why

1. **Fixed SQL Injection in `OutstandingCustomersView` (`billing/views.py`):**
   - Replaced raw string interpolation `LIKE LOWER('%%{search}%%')` with a parameterized `%s` argument (`f"%{search}%"`), and updated the outstanding formula to `total - amount_paid - credit_total` so returned goods reduce customer balances in reports.
2. **Fixed Cross-Tenant Isolation Bug in `InvoiceViewSet.payments` (`billing/views.py`):**
   - Changed `Invoice.objects.get(pk=pk)` to `self.get_object()` so staff users cannot record payments against invoices belonging to another business.
3. **Locked Down `PATCH /api/invoices/{id}/` (`billing/serializers.py`, `billing/views.py`):**
   - Previously `InvoiceSerializer` exposed `fields = "__all__"` on `PATCH`, allowing clients to directly overwrite `total`, `tax`, `status`, or `business`. Restricted `PATCH` via `InvoicePatchSerializer` to `due_date`.
4. **Replaced `float` Money Math with `Decimal` (`billing/services.py`):**
   - `calculate_totals` and `create_invoice` previously converted `unit_price` to Python `float`, risking binary floating-point rounding drift on 13% VAT. Standardized on `Decimal` with `ROUND_HALF_UP`.
5. **Added Row-Level Locking & Webhook Idempotency (`billing/services.py`, `billing/tasks.py`):**
   - Added `select_for_update()` during stock deduction, payment recording, cancellation, and returns to prevent race conditions under concurrent requests.
   - Deduplicated `gateway_ref` in `record_payment` and `process_gateway_payment` so payment gateway webhook retries do not double-credit an invoice or send duplicate confirmation SMS messages.
6. **Fixed Date Range Filtering in `SalesReportView` (`billing/views.py`):**
   - Changed `created_at__range=(start, end)` (which truncated the `end` date at `00:00:00`) to `issue_date__range=(start, end)`, and included net sales, credited VAT, and refunds issued.

---

## 3. What I Would Do Next With Another Day

1. **Per-Business Configurable Reminder Settings:** Add `reminder_quiet_start`, `reminder_quiet_end`, `reminder_cadence_days` (e.g., remind on Day 1, Day 7, Day 14 overdue instead of daily), and `sms_reminders_enabled` on `Business` so each shop can customize tone and frequency.
2. **Role-Based Permissions (`StaffProfile.Role`):** Enforce that `cashier` users can create invoices/payments, while only `owner` or `accountant` roles can approve large returns or issue cash/bank refunds.
3. **PDF Generation & Customer Statement Ledger:** Render `CreditNote` documents to PDF (e.g., via WeasyPrint) for direct email attachment to the customer/accountant, and add a chronological customer ledger endpoint combining invoices, payments, and credit notes.
4. **Celery Beat Container in `docker-compose.yml`:** Add a dedicated `beat` service (or `--beat` flag on a single worker) and Dead Letter Queue / alerting on repeated SMS gateway failures.