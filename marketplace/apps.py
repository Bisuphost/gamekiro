from django.apps import AppConfig


class MarketplaceConfig(AppConfig):
    name = "marketplace"

    def ready(self):
        import marketplace.checks  # noqa: F401
        import marketplace.signals  # noqa: F401
