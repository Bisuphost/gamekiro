from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Profile, ProfileGame


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
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


class ProfileGameForm(forms.ModelForm):
    class Meta:
        model = ProfileGame
        fields = ["game", "status"]
        widgets = {
            "status": forms.Select(attrs={"class": "rounded border border-slate-300 px-3 py-2"}),
        }
