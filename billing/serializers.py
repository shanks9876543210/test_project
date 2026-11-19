# from rest_framework import serializers

# from .models import Customer, Invoice, InvoiceItem, Payment, Product


# class CustomerSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Customer
#         fields = ["id", "name", "phone", "email", "created_at"]
#         read_only_fields = ["created_at"]


# class ProductSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Product
#         fields = ["id", "sku", "name", "unit_price", "stock"]


# class InvoiceItemSerializer(serializers.ModelSerializer):
#     product_name = serializers.CharField(source="product.name", read_only=True)

#     class Meta:
#         model = InvoiceItem
#         fields = ["id", "product", "product_name", "quantity", "unit_price", "line_total"]


# class InvoiceSerializer(serializers.ModelSerializer):
#     customer_name = serializers.CharField(source="customer.name", read_only=True)
#     items = InvoiceItemSerializer(many=True, read_only=True)
#     balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

#     class Meta:
#         model = Invoice
#         fields = "__all__"


# class InvoiceLineInputSerializer(serializers.Serializer):
#     product_id = serializers.IntegerField()
#     quantity = serializers.IntegerField(min_value=1)


# class InvoiceCreateSerializer(serializers.Serializer):
#     customer_id = serializers.IntegerField()
#     due_date = serializers.DateField()
#     items = InvoiceLineInputSerializer(many=True, allow_empty=False)

#     def validate_customer_id(self, value):
#         business = self.context["request"].user.profile.business
#         if not Customer.objects.filter(pk=value, business=business).exists():
#             raise serializers.ValidationError("Customer not found.")
#         return value

#     def validate_items(self, value):
#         product_ids = [row["product_id"] for row in value]
#         if len(product_ids) != len(set(product_ids)):
#             raise serializers.ValidationError("Each product can appear only once.")
#         return value


# class PaymentCreateSerializer(serializers.Serializer):
#     amount = serializers.DecimalField(max_digits=14, decimal_places=2)
#     method = serializers.ChoiceField(choices=Payment.Method.choices)
#     reference = serializers.CharField(required=False, allow_blank=True, max_length=100)



from rest_framework import serializers

from .models import (
    CreditNote,
    CreditNoteItem,
    Customer,
    Invoice,
    InvoiceItem,
    Payment,
    Product,
)


# class CustomerSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Customer
#         fields = ["id", "name", "phone", "email", "created_at"]
#         read_only_fields = ["created_at"]
class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "email", "created_at"]
        read_only_fields = ["created_at"]

    def validate_phone(self, value):
        phone = value.strip()
        if not phone:
            return phone
        business = self.context["request"].user.profile.business
        qs = Customer.objects.filter(business=business, phone=phone)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A customer with this phone number already exists in your business."
            )
        return phone


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "sku", "name", "unit_price", "stock"]


class InvoiceItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = InvoiceItem
        fields = [
            "id",
            "product",
            "product_sku",
            "product_name",
            "quantity",
            "unit_price",
            "line_total",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    items = InvoiceItemSerializer(many=True, read_only=True)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "customer",
            "customer_name",
            "status",
            "issue_date",
            "due_date",
            "subtotal",
            "tax",
            "total",
            "amount_paid",
            "credit_total",
            "balance",
            "items",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "number",
            "status",
            "subtotal",
            "tax",
            "total",
            "amount_paid",
            "credit_total",
            "balance",
            "items",
            "created_at",
        ]


class InvoicePatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["due_date"]


class InvoiceLineInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class InvoiceCreateSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    due_date = serializers.DateField()
    items = InvoiceLineInputSerializer(many=True, allow_empty=False)

    def validate_customer_id(self, value):
        business = self.context["request"].user.profile.business
        if not Customer.objects.filter(pk=value, business=business).exists():
            raise serializers.ValidationError("Customer not found.")
        return value

    def validate_items(self, value):
        product_ids = [row["product_id"] for row in value]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError("Each product can appear only once.")
        return value


class PaymentCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=100)


class ReturnLineInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class ReturnCreateSerializer(serializers.Serializer):
    items = ReturnLineInputSerializer(many=True, allow_empty=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    refund_method = serializers.ChoiceField(
        choices=CreditNote.RefundMethod.choices, required=False
    )

    def validate_items(self, value):
        product_ids = [row["product_id"] for row in value]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError("Each product can appear only once in a return.")
        return value


class CreditNoteItemSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = CreditNoteItem
        fields = [
            "id",
            "product",
            "product_sku",
            "product_name",
            "quantity",
            "unit_price",
            "line_total",
        ]


class CreditNoteSerializer(serializers.ModelSerializer):
    credit_note_number = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    business_name = serializers.CharField(source="business.name", read_only=True)
    business_pan = serializers.CharField(source="business.pan_number", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    items = CreditNoteItemSerializer(many=True, read_only=True)

    class Meta:
        model = CreditNote
        fields = [
            "id",
            "credit_note_number",
            "number",
            "invoice",
            "invoice_number",
            "business_name",
            "business_pan",
            "customer",
            "customer_name",
            "reason",
            "subtotal",
            "tax",
            "total",
            "credit_applied",
            "refund_amount",
            "refund_method",
            "items",
            "created_at",
        ]

    def get_credit_note_number(self, obj):
        return f"CN-{obj.number}"

    def get_invoice_number(self, obj):
        return f"INV-{obj.invoice.number}"