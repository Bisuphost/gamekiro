import uuid

from .base import SupplierStatus


class MockSupplier:
    slug = "mock"

    def sync_products(self):
        return []

    def sync_prices(self):
        return []

    def acquire_keys(self, product, quantity):
        return [f"MOCK-{uuid.uuid4().hex[:12].upper()}" for _ in range(quantity)]

    def status(self):
        return SupplierStatus(healthy=True, detail="Deterministic test supplier.")
