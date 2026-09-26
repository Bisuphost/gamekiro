from functools import wraps

from django.shortcuts import redirect, render

from marketplace.services import flags as flags_service
from marketplace.services import verification as verification_service


def require_marketplace_enabled(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not flags_service.marketplace_enabled():
            return render(
                request,
                "marketplace/_disabled.html",
                {"message": flags_service.maintenance_message()},
                status=503,
            )
        return view_func(request, *args, **kwargs)

    return wrapper


def require_verified_email(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not verification_service.is_verified(request.user):
            return redirect("marketplace:verify_email")
        return view_func(request, *args, **kwargs)

    return wrapper
