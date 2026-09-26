from django.urls import path

from .views import (
    admin_views,
    cart,
    checkout,
    library,
    orders,
    storefront,
    verification,
    webhooks,
    wishlist,
)

app_name = "marketplace"

urlpatterns = [
    path("", storefront.home, name="home"),
    path("browse/", storefront.browse, name="browse"),
    path("p/<slug:slug>/", storefront.product_detail, name="product_detail"),
    path("cart/", cart.view_cart, name="cart"),
    path("cart/count/", cart.cart_count, name="cart_count"),
    path("cart/add/", cart.add_to_cart, name="cart_add"),
    path("cart/update/", cart.update_cart, name="cart_update"),
    path("cart/remove/", cart.remove_from_cart, name="cart_remove"),
    path("coupon/validate/", cart.validate_coupon_view, name="coupon_validate"),
    path("verify-email/", verification.verify_email, name="verify_email"),
    path("verify-email/resend/", verification.verify_email_resend, name="verify_email_resend"),
    path(
        "verify-email/confirm/<str:token>/",
        verification.verify_email_confirm,
        name="verify_email_confirm",
    ),
    path("checkout/", checkout.checkout_review, name="checkout"),
    path("checkout/confirm/", checkout.checkout_confirm, name="checkout_confirm"),
    path("checkout/mock/<str:reference>/", checkout.mock_checkout_page, name="mock_checkout"),
    path("orders/", orders.order_list, name="order_list"),
    path("orders/<str:reference>/", orders.order_detail, name="order_detail"),
    path("orders/<str:reference>/pay/", checkout.order_pay, name="order_pay"),
    path(
        "orders/<str:reference>/check-status/",
        orders.check_payment_status,
        name="order_check_status",
    ),
    path("library/", library.library_list, name="library"),
    path("library/<int:entitlement_id>/reveal/", library.reveal_key, name="library_reveal"),
    path("wishlist/", wishlist.wishlist_view, name="wishlist"),
    path("wishlist/toggle/", wishlist.toggle_wishlist, name="wishlist_toggle"),
    path("webhooks/<str:provider>/", webhooks.webhook_receiver, name="webhook"),
    path("manage/", admin_views.dashboard, name="manage_dashboard"),
    path("manage/inventory/import/", admin_views.import_inventory, name="manage_import_inventory"),
    path(
        "manage/orders/<str:reference>/",
        admin_views.manage_order_detail,
        name="manage_order_detail",
    ),
    path(
        "manage/orders/<str:reference>/redrive/",
        admin_views.redrive_fulfillment,
        name="manage_order_redrive",
    ),
    path(
        "manage/orders/<str:reference>/refund/",
        admin_views.initiate_refund,
        name="manage_order_refund",
    ),
    path(
        "manage/keys/<int:key_id>/reveal/", admin_views.admin_reveal_key, name="manage_key_reveal"
    ),
    path("manage/killswitch/", admin_views.toggle_killswitch, name="manage_killswitch"),
]
