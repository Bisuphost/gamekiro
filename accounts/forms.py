from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from games.models import Game

from .models import Profile, ProfileGame


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    agree_to_terms = forms.BooleanField(
        required=True,
        label="I am at least 13 years old and I agree to the Terms of Service and Privacy Policy.",
        error_messages={"required": "You must confirm your age and agree to continue."},
    )

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
    game = forms.ModelChoiceField(
        queryset=Game.objects.all(),
        widget=forms.HiddenInput(),
        error_messages={
            "required": "Search and select a game first.",
            "invalid_choice": "Search and select a game from the list.",
        },
    )

    class Meta:
        model = ProfileGame
        fields = ["game", "status"]
