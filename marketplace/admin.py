from django.contrib import admin

from .models import (
    ActivationPlatform,
    AuditLog,
    Coupon,
    CouponRedemption,
    CronLease,
    Dispute,
    Entitlement,
    FulfillmentJob,
    GameKey,
    InventoryImportBatch,
    KeyAccessLog,
    MarketplaceSettings,
    Order,
    OrderAuditLog,
    OrderItem,
    OutboundEmail,
    Payment,
    Product,
    ProductImage,
    Refund,
    Region,
    SupplierIntegration,
    WebhookEvent,
)


class ReadOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ActivationPlatform)
class ActivationPlatformAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("name", "code")
    search_fields = ("name", "code")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "game",
        "activation_platform",
        "region",
        "status",
        "unit_price_minor",
        "is_purchasable",
    )
    list_filter = ("status", "is_purchasable", "activation_platform", "region")
    search_fields = ("game__title", "slug", "publisher", "developer")
    prepopulated_fields = {"slug": ("edition",)}
    autocomplete_fields = ("game",)
    inlines = [ProductImageInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("product", "product_title", "unit_price_minor", "delivered_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "status", "total_minor", "currency", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("reference", "user__username", "user__email")
    readonly_fields = (
        "reference",
        "user",
        "subtotal_minor",
        "discount_minor",
        "tax_minor",
        "total_minor",
        "currency",
        "provider",
        "provider_session_id",
        "payment_reference",
        "reservation_expires_at",
        "paid_at",
        "fulfilled_at",
    )
    inlines = [OrderItemInline]

    def has_add_permission(self, request):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "order",
        "provider",
        "attempt",
        "status",
        "amount_minor",
        "refunded_minor",
        "currency",
        "created_at",
    )
    list_filter = ("provider", "status")
    search_fields = (
        "order__reference",
        "provider_payment_id",
        "provider_reference",
        "idempotency_key",
    )


@admin.register(WebhookEvent)
class WebhookEventAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("provider", "event_type", "status", "attempts", "created_at")
    list_filter = ("provider", "event_type", "status")
    search_fields = ("event_id", "order__reference")


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order", "payment", "amount_minor", "status", "source", "created_at")
    list_filter = ("status", "source")
    search_fields = ("order__reference", "provider_refund_id", "idempotency_key")


@admin.register(Dispute)
class DisputeAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "order",
        "provider",
        "status",
        "response_due_at",
        "closed_at",
        "outcome",
        "created_at",
    )
    list_filter = ("provider", "closed_at")
    search_fields = ("order__reference", "provider_dispute_id")


@admin.register(InventoryImportBatch)
class InventoryImportBatchAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "product",
        "source",
        "imported_count",
        "duplicate_count",
        "invalid_count",
        "created_at",
    )
    search_fields = ("product__game__title",)


@admin.register(GameKey)
class GameKeyAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "product", "status", "masked_hint", "created_at")
    list_filter = ("status", "product")
    search_fields = ("masked_hint", "key_fingerprint")

    def get_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name != "encrypted_key"]


@admin.register(KeyAccessLog)
class KeyAccessLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "key", "order", "action", "created_at")
    list_filter = ("action",)
    search_fields = ("user__username",)


@admin.register(Entitlement)
class EntitlementAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "product", "granted_at", "revoked_at")
    search_fields = ("user__username", "product__game__title")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "kind", "value", "is_active", "starts_at", "ends_at")
    list_filter = ("kind", "is_active")
    search_fields = ("code",)
    filter_horizontal = ("products",)


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("coupon", "user", "order", "amount_minor", "created_at")
    search_fields = ("coupon__code", "user__username", "order__reference")


@admin.register(SupplierIntegration)
class SupplierIntegrationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "enabled")
    search_fields = ("name", "slug")


@admin.register(MarketplaceSettings)
class MarketplaceSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "marketplace_enabled",
        "purchases_enabled",
        "fulfillment_enabled",
        "stripe_enabled",
        "paypal_enabled",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("action", "actor", "summary", "created_at")
    list_filter = ("action",)
    search_fields = ("summary", "target_type", "target_id")


@admin.register(OrderAuditLog)
class OrderAuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order", "action", "actor", "created_at")
    search_fields = ("order__reference",)


@admin.register(FulfillmentJob)
class FulfillmentJobAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order", "status", "attempts", "run_after", "updated_at")
    list_filter = ("status",)


@admin.register(OutboundEmail)
class OutboundEmailAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("to_email", "subject", "status", "attempts", "run_after")
    list_filter = ("status",)


@admin.register(CronLease)
class CronLeaseAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "holder", "expires_at")
