from django.conf import settings
from django.templatetags.static import static


def seo(request):
    site_url = settings.SITE_URL.rstrip("/")
    canonical_url = site_url + request.path
    page = request.GET.get("page")
    if page:
        canonical_url += f"?page={page}"
    return {
        "canonical_url": canonical_url,
        "site_url": site_url,
        "default_og_image": site_url + static("img/logo.png"),
    }
