from django import forms

from games.models import Game

from .models import OrganizerProfile, Tournament


class OrganizerApplicationForm(forms.ModelForm):
    class Meta:
        model = OrganizerProfile
        fields = ["display_name", "about"]
        widgets = {"about": forms.Textarea(attrs={"rows": 4})}
        labels = {"about": "Why do you want to host tournaments?"}


class TournamentForm(forms.ModelForm):
    game = forms.ModelChoiceField(queryset=Game.objects.order_by("title"))
    starts_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    ends_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = Tournament
        fields = [
            "title",
            "game",
            "platform",
            "description",
            "rules",
            "room_code",
            "room_password",
            "max_players",
            "starts_at",
            "ends_at",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "rules": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "room_code": "Room code",
            "room_password": "Room password",
        }
        help_texts = {
            "room_code": "Only visible to joined players and you.",
            "room_password": "Only visible to joined players and you.",
        }

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "End time must be after the start time.")
        max_players = cleaned_data.get("max_players")
        if max_players is not None and max_players < 2:
            self.add_error("max_players", "A tournament needs at least 2 players.")
        return cleaned_data
