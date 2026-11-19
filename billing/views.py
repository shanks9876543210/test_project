# import hashlib
# import hmac

# from django.conf import settings
# from django.db import connection
# from django.db.models import Count, Sum
# from django.utils import timezone
# from django.utils.dateparse import parse_date
# from rest_framework import status, viewsets
# from rest_framework.decorators import action
# from rest_framework.permissions import AllowAny
# from rest_framework.response import Response
# from rest_framework.views import APIView

# from .exceptions import InsufficientStock
# from .models import Customer, Invoice, Product
# from .serializers import (
#     CustomerSerializer,
#     InvoiceCreateSerializer,
#     InvoiceSerializer,
#     PaymentCreateSerializer,
#     ProductSerializer,
# )
# from .services import cancel_invoice, create_invoice, record_payment
# from .tasks import process_gateway_payment


# def current_business(request):
#     return request.user.profile.business


# class CustomerViewSet(viewsets.ModelViewSet):
#     serializer_class = CustomerSerializer

#     def get_queryset(self):
#         return Customer.objects.filter(business=current_business(self.request)).order_by("name")

#     def perform_create(self, serializer):
#         serializer.save(business=current_business(self.request))


# class ProductViewSet(viewsets.ModelViewSet):
#     serializer_class = ProductSerializer

#     def get_queryset(self):
#         return Product.objects.filter(business=current_business(self.request)).order_by("sku")

#     def perform_create(self, serializer):
#         serializer.save(business=current_business(self.request))


# class InvoiceViewSet(viewsets.ModelViewSet):
#     serializer_class = InvoiceSerializer
#     http_method_names = ["get", "post", "patch", "head", "options"]

#     def get_queryset(self):
#         queryset = Invoice.objects.filter(business=current_business(self.request))
#         status_param = self.request.query_params.get("status")
#         if status_param:
#             queryset = queryset.filter(status=status_param)
#         return queryset

#     def create(self, request, *args, **kwargs):
#         serializer = InvoiceCreateSerializer(data=request.data, context={"request": request})
#         serializer.is_valid(raise_exception=True)
#         data = serializer.validated_data
#         business = current_business(request)

#         try:
#             invoice = create_invoice(
#                 business=business,
#                 customer=Customer.objects.get(pk=data["customer_id"], business=business),
#                 due_date=data["due_date"],
#                 items=data["items"],
#                 user=request.user,
#             )
#         except InsufficientStock as exc:
#             return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
#         except Product.DoesNotExist:
#             return Response({"detail": "Product not found."}, status=status.HTTP_400_BAD_REQUEST)

#         return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)

#     @action(detail=True, methods=["post"])
#     def payments(self, request, pk=None):
#         invoice = Invoice.objects.get(pk=pk)
#         serializer = PaymentCreateSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         payment = record_payment(
#             invoice=invoice,
#             amount=serializer.validated_data["amount"],
#             method=serializer.validated_data["method"],
#             user=request.user,
#             gateway_ref=serializer.validated_data.get("reference", ""),
#         )
#         return Response(
#             {"id": payment.id, "status": invoice.status, "balance": str(invoice.balance)},
#             status=status.HTTP_201_CREATED,
#         )

#     @action(detail=True, methods=["post"])
#     def cancel(self, request, pk=None):
#         invoice = self.get_object()
#         if invoice.status in (Invoice.Status.PAID, Invoice.Status.PARTIALLY_PAID):
#             return Response(
#                 {"detail": "Invoices with payments cannot be cancelled."},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )
#         if invoice.status == Invoice.Status.CANCELLED:
#             return Response({"detail": "Already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

#         cancel_invoice(invoice)
#         return Response(InvoiceSerializer(invoice).data)


# class SalesReportView(APIView):
#     def get(self, request):
#         business = current_business(request)
#         today = timezone.localdate()
#         start = parse_date(request.query_params.get("from", "")) or today.replace(day=1)
#         end = parse_date(request.query_params.get("to", "")) or today

#         invoices = Invoice.objects.filter(
#             business=business, created_at__range=(start, end)
#         ).exclude(status=Invoice.Status.CANCELLED)

#         totals = invoices.aggregate(
#             count=Count("id"),
#             gross=Sum("total"),
#             vat=Sum("tax"),
#             collected=Sum("amount_paid"),
#         )
#         overdue = invoices.filter(
#             due_date__lt=today,
#             status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
#         ).count()

#         return Response(
#             {
#                 "from": start,
#                 "to": end,
#                 "invoice_count": totals["count"],
#                 "gross_sales": totals["gross"] or 0,
#                 "vat_collected": totals["vat"] or 0,
#                 "amount_collected": totals["collected"] or 0,
#                 "overdue_invoices": overdue,
#             }
#         )


# class OutstandingCustomersView(APIView):
#     def get(self, request):
#         business = current_business(request)
#         search = request.query_params.get("q", "")

#         sql = f"""
#             SELECT c.id, c.name, c.phone,
#                    COALESCE(SUM(i.total - i.amount_paid), 0) AS outstanding
#             FROM billing_customer c
#             LEFT JOIN billing_invoice i
#                    ON i.customer_id = c.id
#                   AND i.status IN ('issued', 'partially_paid')
#             WHERE c.business_id = %s
#               AND LOWER(c.name) LIKE LOWER('%%{search}%%')
#             GROUP BY c.id, c.name, c.phone
#             ORDER BY outstanding DESC
#             LIMIT 50
#         """
#         with connection.cursor() as cursor:
#             cursor.execute(sql, [business.id])
#             columns = [col[0] for col in cursor.description]
#             rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

#         return Response(rows)


# class GatewayWebhookView(APIView):
#     authentication_classes = []
#     permission_classes = [AllowAny]

#     def post(self, request):
#         signature = request.headers.get("X-Paygate-Signature", "")
#         expected = hmac.new(
#             settings.PAYMENT_GATEWAY["WEBHOOK_SECRET"].encode(),
#             request.body,
#             hashlib.sha256,
#         ).hexdigest()
#         if not hmac.compare_digest(signature, expected):
#             return Response(status=status.HTTP_401_UNAUTHORIZED)

#         if request.data.get("event") == "payment.succeeded":
#             process_gateway_payment.delay(request.data["data"])

#         return Response({"received": True})




import hashlib
import hmac
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import escape
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import InsufficientStock, InvalidInvoiceStateError, InvalidReturnError
from .models import CreditNote, Customer, Invoice, Product
from .serializers import (
    CreditNoteSerializer,
    CustomerSerializer,
    InvoiceCreateSerializer,
    InvoicePatchSerializer,
    InvoiceSerializer,
    PaymentCreateSerializer,
    ProductSerializer,
    ReturnCreateSerializer,
)
from .services import cancel_invoice, create_invoice, create_return, record_payment
from .tasks import process_gateway_payment
from django.db import IntegrityError

from rest_framework.exceptions import ValidationError

def current_business(request):
    return request.user.profile.business


# class CustomerViewSet(viewsets.ModelViewSet):
#     serializer_class = CustomerSerializer

#     def get_queryset(self):
#         return Customer.objects.filter(business=current_business(self.request)).order_by("name")

#     def perform_create(self, serializer):
#         serializer.save(business=current_business(self.request))


# class ProductViewSet(viewsets.ModelViewSet):
#     serializer_class = ProductSerializer

#     def get_queryset(self):
#         return Product.objects.filter(business=current_business(self.request)).order_by("sku")

#     def perform_create(self, serializer):
#         serializer.save(business=current_business(self.request))

class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return Customer.objects.filter(business=current_business(self.request)).order_by("name")

    def perform_create(self, serializer):
        try:
            serializer.save(business=current_business(self.request))
        except IntegrityError:
            raise ValidationError(
                {"phone": ["A customer with this phone number already exists in your business."]}
            )

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError(
                {"phone": ["A customer with this phone number already exists in your business."]}
            )


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer

    def get_queryset(self):
        return Product.objects.filter(business=current_business(self.request)).order_by("sku")

    def perform_create(self, serializer):
        try:
            serializer.save(business=current_business(self.request))
        except IntegrityError:
            raise ValidationError(
                {"sku": ["A product with this SKU already exists in your business."]}
            )

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError(
                {"sku": ["A product with this SKU already exists in your business."]}
            )


class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = (
            Invoice.objects.filter(business=current_business(self.request))
            .select_related("customer", "business")
            .prefetch_related("items__product")
        )
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def get_serializer_class(self):
        if self.action == "partial_update":
            return InvoicePatchSerializer
        return InvoiceSerializer

    def create(self, request, *args, **kwargs):
        serializer = InvoiceCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        business = current_business(request)

        try:
            invoice = create_invoice(
                business=business,
                customer=Customer.objects.get(pk=data["customer_id"], business=business),
                due_date=data["due_date"],
                items=data["items"],
                user=request.user,
            )
        except InsufficientStock as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Product.DoesNotExist:
            return Response({"detail": "Product not found."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def payments(self, request, pk=None):
        invoice = self.get_object()
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payment = record_payment(
                invoice=invoice,
                amount=serializer.validated_data["amount"],
                method=serializer.validated_data["method"],
                user=request.user,
                gateway_ref=serializer.validated_data.get("reference", ""),
            )
        except InvalidInvoiceStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        invoice.refresh_from_db()
        return Response(
            {"id": payment.id, "status": invoice.status, "balance": str(invoice.balance)},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        invoice = self.get_object()
        if invoice.status in (Invoice.Status.PAID, Invoice.Status.PARTIALLY_PAID):
            return Response(
                {"detail": "Invoices with payments cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if invoice.status == Invoice.Status.CANCELLED:
            return Response({"detail": "Already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cancel_invoice(invoice)
        except InvalidInvoiceStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(InvoiceSerializer(invoice).data)

    @action(detail=True, methods=["get", "post"])
    def returns(self, request, pk=None):
        invoice = self.get_object()

        if request.method == "GET":
            credit_notes = invoice.credit_notes.select_related(
                "business", "customer", "invoice"
            ).prefetch_related("items__product")
            return Response(CreditNoteSerializer(credit_notes, many=True).data)

        serializer = ReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            credit_note = create_return(
                invoice=invoice,
                items=data["items"],
                reason=data.get("reason", ""),
                refund_method=data.get("refund_method"),
                user=request.user,
            )
        except InvalidReturnError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(CreditNoteSerializer(credit_note).data, status=status.HTTP_201_CREATED)


class CreditNoteViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CreditNoteSerializer

    def get_queryset(self):
        return (
            CreditNote.objects.filter(business=current_business(self.request))
            .select_related("business", "customer", "invoice")
            .prefetch_related("items__product")
        )

    @action(detail=True, methods=["get"])
    def document(self, request, pk=None):
        cn = self.get_object()
        rows_html = "".join(
            f"<tr>"
            f"<td>{escape(item.product.sku)}</td>"
            f"<td>{escape(item.product.name)}</td>"
            f"<td style='text-align:right'>{item.quantity}</td>"
            f"<td style='text-align:right'>Rs. {item.unit_price:.2f}</td>"
            f"<td style='text-align:right'>Rs. {item.line_total:.2f}</td>"
            f"</tr>"
            for item in cn.items.all()
        )
        pan_line = f"<p>PAN: {escape(cn.business.pan_number)}</p>" if cn.business.pan_number else ""
        reason_line = f"<p><strong>Reason:</strong> {escape(cn.reason)}</p>" if cn.reason else ""

        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Credit Note CN-{cn.number}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; color: #222; }}
    .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #222; padding-bottom: 12px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 8px; text-align: left; }}
    .totals {{ margin-top: 20px; width: 320px; margin-left: auto; }}
    .totals td {{ border: none; padding: 4px 8px; }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h2>{escape(cn.business.name)}</h2>
      {pan_line}
    </div>
    <div style="text-align:right">
      <h1>CREDIT NOTE</h1>
      <p><strong>No:</strong> CN-{cn.number}</p>
      <p><strong>Date:</strong> {cn.created_at:%Y-%m-%d}</p>
      <p><strong>Ref Invoice:</strong> INV-{cn.invoice.number}</p>
    </div>
  </div>
  <p><strong>Customer:</strong> {escape(cn.customer.name)}</p>
  {reason_line}
  <table>
    <thead>
      <tr>
        <th>SKU</th>
        <th>Item</th>
        <th style="text-align:right">Returned Qty</th>
        <th style="text-align:right">Unit Price</th>
        <th style="text-align:right">Line Total</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <table class="totals">
    <tr><td>Subtotal:</td><td style="text-align:right">Rs. {cn.subtotal:.2f}</td></tr>
    <tr><td>VAT (13%):</td><td style="text-align:right">Rs. {cn.tax:.2f}</td></tr>
    <tr><td><strong>Total Credit:</strong></td><td style="text-align:right"><strong>Rs. {cn.total:.2f}</strong></td></tr>
    <tr><td>Applied to Balance:</td><td style="text-align:right">Rs. {cn.credit_applied:.2f}</td></tr>
    <tr><td>Refund Issued ({escape(cn.get_refund_method_display())}):</td><td style="text-align:right">Rs. {cn.refund_amount:.2f}</td></tr>
  </table>
</body>
</html>"""
        return HttpResponse(html, content_type="text/html; charset=utf-8")


class SalesReportView(APIView):
    def get(self, request):
        business = current_business(request)
        today = timezone.localdate()
        start = parse_date(request.query_params.get("from", "")) or today.replace(day=1)
        end = parse_date(request.query_params.get("to", "")) or today

        invoices = Invoice.objects.filter(
            business=business, issue_date__range=(start, end)
        ).exclude(status=Invoice.Status.CANCELLED)

        totals = invoices.aggregate(
            count=Count("id"),
            gross=Sum("total"),
            vat=Sum("tax"),
            collected=Sum("amount_paid"),
        )
        credits = CreditNote.objects.filter(
            business=business, created_at__date__range=(start, end)
        ).aggregate(
            total_credited=Sum("total"),
            vat_credited=Sum("tax"),
            refunds=Sum("refund_amount"),
        )
        overdue = invoices.filter(
            due_date__lt=today,
            status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
        ).count()

        gross_sales = totals["gross"] or Decimal("0.00")
        vat_collected = totals["vat"] or Decimal("0.00")
        total_credited = credits["total_credited"] or Decimal("0.00")
        vat_credited = credits["vat_credited"] or Decimal("0.00")
        refunds_issued = credits["refunds"] or Decimal("0.00")

        return Response(
            {
                "from": start,
                "to": end,
                "invoice_count": totals["count"],
                "gross_sales": gross_sales,
                "returns_total": total_credited,
                "net_sales": gross_sales - total_credited,
                "vat_collected": vat_collected - vat_credited,
                "amount_collected": totals["collected"] or Decimal("0.00"),
                "refunds_issued": refunds_issued,
                "overdue_invoices": overdue,
            }
        )


class OutstandingCustomersView(APIView):
    def get(self, request):
        business = current_business(request)
        search = request.query_params.get("q", "")

        sql = """
            SELECT c.id, c.name, c.phone,
                   COALESCE(SUM(i.total - i.amount_paid - i.credit_total), 0) AS outstanding
            FROM billing_customer c
            LEFT JOIN billing_invoice i
                   ON i.customer_id = c.id
                  AND i.status IN ('issued', 'partially_paid')
            WHERE c.business_id = %s
              AND LOWER(c.name) LIKE LOWER(%s)
            GROUP BY c.id, c.name, c.phone
            ORDER BY outstanding DESC
            LIMIT 50
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [business.id, f"%{search}%"])
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return Response(rows)


class GatewayWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Paygate-Signature", "")
        expected = hmac.new(
            settings.PAYMENT_GATEWAY["WEBHOOK_SECRET"].encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        if request.data.get("event") == "payment.succeeded":
            process_gateway_payment.delay(request.data["data"])

        return Response({"received": True})