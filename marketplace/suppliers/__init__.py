from .manual import ManualSupplier
from .mock import MockSupplier

SUPPLIERS = {
    "manual": ManualSupplier(),
    "mock": MockSupplier(),
}


def get_supplier(slug):
    try:
        return SUPPLIERS[slug]
    except KeyError as exc:
        raise ValueError(f"Unknown supplier: {slug!r}") from exc
