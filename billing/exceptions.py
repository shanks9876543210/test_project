# class InsufficientStock(Exception):
#     def __init__(self, sku, available, requested):
#         self.sku = sku
#         self.available = available
#         self.requested = requested
#         super().__init__(f"{sku}: {available} available, {requested} requested")




class InsufficientStock(Exception):
    def __init__(self, sku, available, requested):
        self.sku = sku
        self.available = available
        self.requested = requested
        super().__init__(f"{sku}: {available} available, {requested} requested")


class InvalidReturnError(Exception):
    pass


class InvalidInvoiceStateError(Exception):
    pass