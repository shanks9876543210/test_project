# from django.urls import path
# from rest_framework.routers import DefaultRouter

# from .views import (
#     CustomerViewSet,
#     GatewayWebhookView,
#     InvoiceViewSet,
#     OutstandingCustomersView,
#     ProductViewSet,
#     SalesReportView,
# )

# router = DefaultRouter()
# router.register("customers", CustomerViewSet, basename="customer")
# router.register("products", ProductViewSet, basename="product")
# router.register("invoices", InvoiceViewSet, basename="invoice")

# urlpatterns = router.urls + [
#     path("reports/sales/", SalesReportView.as_view(), name="sales-report"),
#     path("reports/outstanding/", OutstandingCustomersView.as_view(), name="outstanding-customers"),
#     path("webhooks/paygate/", GatewayWebhookView.as_view(), name="paygate-webhook"),
# ]


from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CreditNoteViewSet,
    CustomerViewSet,
    GatewayWebhookView,
    InvoiceViewSet,
    OutstandingCustomersView,
    ProductViewSet,
    SalesReportView,
)

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("products", ProductViewSet, basename="product")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("returns", CreditNoteViewSet, basename="credit-note")

urlpatterns = router.urls + [
    path("reports/sales/", SalesReportView.as_view(), name="sales-report"),
    path("reports/outstanding/", OutstandingCustomersView.as_view(), name="outstanding-customers"),
    path("webhooks/paygate/", GatewayWebhookView.as_view(), name="paygate-webhook"),
]