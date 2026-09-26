from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass
class ProductData:
    external_id: str
    title: str
    price_minor: int
    currency: str


@dataclass
class PriceData:
    external_id: str
    price_minor: int
    currency: str


@dataclass
class SupplierStatus:
    healthy: bool
    detail: str = ""


class SupplierAdapter(Protocol):
    slug: str

    def sync_products(self) -> Iterable[ProductData]: ...

    def sync_prices(self) -> Iterable[PriceData]: ...

    def acquire_keys(self, product, quantity: int) -> Iterable[str]: ...

    def status(self) -> SupplierStatus: ...
