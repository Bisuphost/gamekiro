from django.conf import settings
from django.db import models
from django.utils import timezone


class OrganizerProfile(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organizer_profile"
    )
    display_name = models.CharField(max_length=100)
    about = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    applied_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return self.display_name

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED


class Tournament(models.Model):
    organizer = models.ForeignKey(
        OrganizerProfile, on_delete=models.CASCADE, related_name="tournaments"
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    game_title = models.CharField(max_length=120)
    platform = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    rules = models.TextField(blank=True)
    room_code = models.CharField(max_length=50, blank=True)
    room_password = models.CharField(max_length=50, blank=True)
    max_players = models.PositiveIntegerField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_cancelled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return self.title

    @property
    def participant_count(self):
        return self.participants.count()

    @property
    def spots_left(self):
        return max(self.max_players - self.participant_count, 0)

    @property
    def is_full(self):
        return self.participant_count >= self.max_players

    @property
    def is_past(self):
        return timezone.now() > self.ends_at

    @property
    def is_live(self):
        now = timezone.now()
        return not self.is_cancelled and self.starts_at <= now <= self.ends_at

    @property
    def is_joinable(self):
        return not self.is_cancelled and not self.is_past and not self.is_full

    def is_participant(self, user):
        if not user.is_authenticated:
            return False
        return self.participants.filter(user=user).exists()

    def can_view_room_details(self, user):
        if not user.is_authenticated:
            return False
        if user == self.organizer.user:
            return True
        return self.is_participant(user)


class TournamentParticipant(models.Model):
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tournament_entries"
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["tournament", "user"], name="unique_tournament_entry")
        ]

    def __str__(self):
        return f"{self.user} in {self.tournament}"
