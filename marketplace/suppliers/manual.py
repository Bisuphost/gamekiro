from .base import SupplierStatus


class ManualSupplier:
    slug = "manual"

    def sync_products(self):
        return []

    def sync_prices(self):
        return []

    def acquire_keys(self, product, quantity):
        raise NotImplementedError(
            "The manual supplier has no API — import keys via the admin import form."
        )

    def status(self):
        return SupplierStatus(healthy=True, detail="Manual import only, no live API.")
