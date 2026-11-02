from django.contrib import admin
from django.utils import timezone

from .models import OrganizerProfile, Tournament, TournamentParticipant


@admin.action(description="Approve selected applications")
def approve_organizers(modeladmin, request, queryset):
    queryset.update(status=OrganizerProfile.STATUS_APPROVED, reviewed_at=timezone.now())


@admin.action(description="Reject selected applications")
def reject_organizers(modeladmin, request, queryset):
    queryset.update(status=OrganizerProfile.STATUS_REJECTED, reviewed_at=timezone.now())


@admin.register(OrganizerProfile)
class OrganizerProfileAdmin(admin.ModelAdmin):
    list_display = ["display_name", "user", "status", "applied_at"]
    list_filter = ["status"]
    search_fields = ["display_name", "user__username"]
    actions = [approve_organizers, reject_organizers]


class TournamentParticipantInline(admin.TabularInline):
    model = TournamentParticipant
    extra = 0


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ["title", "organizer", "starts_at", "ends_at", "max_players", "is_cancelled"]
    list_filter = ["is_cancelled"]
    search_fields = ["title", "game_title", "organizer__display_name"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [TournamentParticipantInline]


@admin.register(TournamentParticipant)
class TournamentParticipantAdmin(admin.ModelAdmin):
    list_display = ["tournament", "user", "joined_at"]
    search_fields = ["tournament__title", "user__username"]
