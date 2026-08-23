from django import forms

from .models import Profile


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = [
            "bio",
            "avatar",
            "discord_username",
            "steam_id",
            "psn_id",
            "other_links",
        ]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 5}),
        }
