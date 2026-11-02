from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from games.models import Game

from .models import OrganizerProfile, Tournament, TournamentParticipant


def make_organizer(username="organizer", status=OrganizerProfile.STATUS_APPROVED):
    user = User.objects.create_user(username=username, password="pass12345")
    return OrganizerProfile.objects.create(user=user, display_name=username, status=status)


def make_game(title="Test Game"):
    return Game.objects.create(title=title, slug=title.lower().replace(" ", "-"))


def make_tournament(organizer, starts_in_hours=1, duration_hours=1, max_players=4, **kwargs):
    now = timezone.now()
    game = kwargs.pop("game", None) or make_game()
    return Tournament.objects.create(
        organizer=organizer,
        game=game,
        title=kwargs.pop("title", "Test Cup"),
        slug=kwargs.pop("slug", "test-cup"),
        max_players=max_players,
        starts_at=now + timedelta(hours=starts_in_hours),
        ends_at=now + timedelta(hours=starts_in_hours + duration_hours),
        **kwargs,
    )


class TournamentStatusTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()

    def test_upcoming_tournament_is_joinable_not_live(self):
        tournament = make_tournament(self.organizer, starts_in_hours=2)
        self.assertFalse(tournament.is_live)
        self.assertFalse(tournament.is_past)
        self.assertTrue(tournament.is_joinable)

    def test_live_tournament(self):
        tournament = make_tournament(self.organizer, starts_in_hours=-1, duration_hours=2)
        self.assertTrue(tournament.is_live)
        self.assertFalse(tournament.is_past)

    def test_past_tournament(self):
        tournament = make_tournament(self.organizer, starts_in_hours=-3, duration_hours=1)
        self.assertTrue(tournament.is_past)
        self.assertFalse(tournament.is_live)
        self.assertFalse(tournament.is_joinable)

    def test_full_tournament_is_not_joinable(self):
        tournament = make_tournament(self.organizer, max_players=1)
        TournamentParticipant.objects.create(
            tournament=tournament, user=User.objects.create_user("p1", password="pass12345")
        )
        self.assertTrue(tournament.is_full)
        self.assertFalse(tournament.is_joinable)

    def test_cancelled_tournament_is_not_joinable_or_live(self):
        tournament = make_tournament(self.organizer, starts_in_hours=-1, is_cancelled=True)
        self.assertFalse(tournament.is_live)
        self.assertFalse(tournament.is_joinable)


class RoomVisibilityTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.tournament = make_tournament(self.organizer, room_code="ABC123")
        self.outsider = User.objects.create_user("outsider", password="pass12345")
        self.participant = User.objects.create_user("participant", password="pass12345")
        TournamentParticipant.objects.create(tournament=self.tournament, user=self.participant)

    def test_organizer_can_view_room(self):
        self.assertTrue(self.tournament.can_view_room_details(self.organizer.user))

    def test_participant_can_view_room(self):
        self.assertTrue(self.tournament.can_view_room_details(self.participant))

    def test_outsider_cannot_view_room(self):
        self.assertFalse(self.tournament.can_view_room_details(self.outsider))


class TournamentJoinViewTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.tournament = make_tournament(self.organizer, max_players=1)
        self.user = User.objects.create_user("joiner", password="pass12345")
        self.client = Client()

    def test_join_requires_login(self):
        url = reverse("tournaments:tournament_join", args=[self.tournament.slug])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_join_then_full_blocks_second_join(self):
        self.client.login(username="joiner", password="pass12345")
        url = reverse("tournaments:tournament_join", args=[self.tournament.slug])
        self.client.post(url)
        self.assertTrue(self.tournament.is_participant(self.user))

        other = User.objects.create_user("other", password="pass12345")
        self.client.login(username="other", password="pass12345")
        self.client.post(url)
        self.assertFalse(self.tournament.is_participant(other))

    def test_leave_removes_participant(self):
        self.client.login(username="joiner", password="pass12345")
        join_url = reverse("tournaments:tournament_join", args=[self.tournament.slug])
        leave_url = reverse("tournaments:tournament_leave", args=[self.tournament.slug])
        self.client.post(join_url)
        self.client.post(leave_url)
        self.assertFalse(self.tournament.is_participant(self.user))


class OrganizerApplicationTests(TestCase):
    def test_non_organizer_cannot_create_tournament(self):
        User.objects.create_user("regular", password="pass12345")
        client = Client()
        client.login(username="regular", password="pass12345")
        response = client.get(reverse("tournaments:tournament_create"))
        self.assertRedirects(response, reverse("tournaments:organizer_apply"))

    def test_pending_organizer_cannot_create_tournament(self):
        organizer = make_organizer(status=OrganizerProfile.STATUS_PENDING)
        client = Client()
        client.login(username=organizer.user.username, password="pass12345")
        response = client.get(reverse("tournaments:tournament_create"))
        self.assertRedirects(response, reverse("tournaments:organizer_apply"))
