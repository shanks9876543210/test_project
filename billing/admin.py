# from django.contrib import admin

# from .models import Business, Customer, Invoice, InvoiceItem, Payment, Product, StaffProfile


# class InvoiceItemInline(admin.TabularInline):
#     model = InvoiceItem
#     extra = 0


# @admin.register(Invoice)
# class InvoiceAdmin(admin.ModelAdmin):
#     list_display = ("number", "business", "customer", "status", "total", "amount_paid", "due_date")
#     list_filter = ("status", "business")
#     inlines = [InvoiceItemInline]


# admin.site.register(Business)
# admin.site.register(StaffProfile)
# admin.site.register(Customer)
# admin.site.register(Product)
# admin.site.register(Payment)


from django.contrib import admin

from .models import (
    Business,
    CreditNote,
    CreditNoteItem,
    Customer,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentReminderLog,
    Product,
    StaffProfile,
)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


class CreditNoteItemInline(admin.TabularInline):
    model = CreditNoteItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "business",
        "customer",
        "status",
        "total",
        "amount_paid",
        "credit_total",
        "due_date",
    )
    list_filter = ("status", "business")
    inlines = [InvoiceItemInline]


@admin.register(CreditNote)
class CreditNoteAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "business",
        "invoice",
        "customer",
        "total",
        "credit_applied",
        "refund_amount",
        "refund_method",
        "created_at",
    )
    list_filter = ("business", "refund_method")
    inlines = [CreditNoteItemInline]


@admin.register(PaymentReminderLog)
class PaymentReminderLogAdmin(admin.ModelAdmin):
    list_display = ("invoice", "phone", "sent_date", "status", "sent_at")
    list_filter = ("status", "sent_date")


admin.site.register(Business)
admin.site.register(StaffProfile)
admin.site.register(Customer)
admin.site.register(Product)
admin.site.register(Payment)