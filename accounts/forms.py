from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Profile


from .models import Profile


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]
class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = [
            "bio",
            "avatar",
            "discord_username",
            "steam_id",
            "psn_id",
            "website",
            "other_links",
        ]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 5}),
        }
