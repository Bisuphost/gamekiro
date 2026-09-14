from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("privacy/", views.PrivacyPolicyView.as_view(), name="privacy"),
    path("terms/", views.TermsOfServiceView.as_view(), name="terms"),
    path("cookies/", views.CookiePolicyView.as_view(), name="cookies"),
]
