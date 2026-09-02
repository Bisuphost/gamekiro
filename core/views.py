from django.shortcuts import redirect, render


def home(request):
    if request.user.is_authenticated and request.session.get("onboarding_required"):
        return redirect("accounts:profile-setup")
    return render(request, "core/home.html")
