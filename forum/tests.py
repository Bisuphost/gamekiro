import base64
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Category, Post, PostImage, Thread
from .services import RATE_LIMIT_MAX
from .views import POSTS_PER_PAGE, THREADS_PER_PAGE

ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class CategoryDetailSearchFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")

    def _detail_url(self, **params):
        url = reverse("forum:category_detail", args=[self.category.slug])
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_search_matches_whole_word_in_title(self):
        Thread.objects.create(
            category=self.category, author=self.user, title="Best RPGs 2026", slug="rpgs"
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Platformer tier list", slug="plat"
        )
        response = self.client.get(self._detail_url(q="rpgs"))
        self.assertContains(response, "Best RPGs 2026")
        self.assertNotContains(response, "Platformer tier list")

    def test_search_does_not_match_post_body(self):
        thread = Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Off-topic chat",
            slug="off-topic-chat",
        )
        Post.objects.create(thread=thread, author=self.user, body="talking about rpg stuff")
        response = self.client.get(self._detail_url(q="rpg"))
        self.assertNotContains(response, "Off-topic chat")

    def test_filter_pinned(self):
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Pinned one",
            slug="pinned-one",
            is_pinned=True,
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Plain one", slug="plain-one"
        )
        response = self.client.get(self._detail_url(filter="pinned"))
        self.assertContains(response, "Pinned one")
        self.assertNotContains(response, "Plain one")

    def test_filter_locked(self):
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Locked one",
            slug="locked-one",
            is_locked=True,
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Open one", slug="open-one"
        )
        response = self.client.get(self._detail_url(filter="locked"))
        self.assertContains(response, "Locked one")
        self.assertNotContains(response, "Open one")

    def test_filter_and_search_combine(self):
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Zelda thread",
            slug="zelda-thread",
            is_pinned=True,
        )
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Mario thread",
            slug="mario-thread",
            is_pinned=True,
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Zelda info", slug="zelda-info"
        )
        response = self.client.get(self._detail_url(q="zelda", filter="pinned"))
        self.assertContains(response, "Zelda thread")
        self.assertNotContains(response, "Mario thread")
        self.assertNotContains(response, "Zelda info")

    def test_search_uses_word_matching_not_substring(self):
        Thread.objects.create(
            category=self.category, author=self.user, title="Best RPGs 2026", slug="rpgs"
        )
        response = self.client.get(self._detail_url(q="pg"))
        self.assertNotContains(response, "Best RPGs 2026")

    def test_category_pagination_preserves_query_params(self):
        for i in range(THREADS_PER_PAGE + 5):
            Thread.objects.create(
                category=self.category,
                author=self.user,
                title=f"Term thread {i:02d}",
                slug=f"term-thread-{i:02d}",
            )
        response = self.client.get(self._detail_url(q="term", page="2"))
        self.assertContains(response, "q=term")


class ThreadDetailPaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.user, title="Long thread", slug="long-thread"
        )
        for i in range(POSTS_PER_PAGE + 5):
            Post.objects.create(thread=self.thread, author=self.user, body=f"Post body {i:02d}")
        self.client.login(username="player", password="password123")

    def _thread_url(self, **params):
        url = reverse("forum:thread_detail", args=[self.category.slug, self.thread.slug])
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_thread_detail_paginates_posts(self):
        response = self.client.get(self._thread_url())
        self.assertEqual(response.context["posts"].paginator.num_pages, 2)
        self.assertEqual(len(response.context["posts"].object_list), POSTS_PER_PAGE)

        response = self.client.get(self._thread_url(page="2"))
        self.assertEqual(len(response.context["posts"].object_list), 5)

    def test_thread_detail_page_2_does_not_break_reply_form(self):
        response = self.client.get(self._thread_url(page="2"))
        self.assertIsNotNone(response.context["reply_form"])
        self.assertContains(response, "Reply")


class RateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other_user = User.objects.create_user(username="rival", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.other_user, title="Hello", slug="hello"
        )
        self.client.login(username="player", password="password123")

    def test_thread_creation_rate_limited(self):
        url = reverse("forum:thread_create", args=[self.category.slug])
        for i in range(RATE_LIMIT_MAX["thread"]):
            self.client.post(url, {"title": f"Thread {i}", "body": "body"})
        self.assertEqual(Thread.objects.filter(author=self.user).count(), RATE_LIMIT_MAX["thread"])

        response = self.client.post(url, {"title": "One too many", "body": "body"}, follow=True)
        self.assertEqual(Thread.objects.filter(author=self.user).count(), RATE_LIMIT_MAX["thread"])
        self.assertContains(response, "posting too fast")

    def test_post_creation_rate_limited(self):
        url = reverse("forum:post_create", args=[self.category.slug, self.thread.slug])
        for i in range(RATE_LIMIT_MAX["post"]):
            self.client.post(url, {"body": f"Reply {i}"})
        self.assertEqual(Post.objects.count(), RATE_LIMIT_MAX["post"])

        response = self.client.post(url, {"body": "One too many"}, follow=True)
        self.assertEqual(Post.objects.count(), RATE_LIMIT_MAX["post"])
        self.assertContains(response, "posting too fast")


class ThreadCreateAnyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")

    def test_requires_login(self):
        response = self.client.get(reverse("forum:thread_create_any"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_form_shows_a_category_picker(self):
        self.client.login(username="player", password="password123")
        response = self.client.get(reverse("forum:thread_create_any"))
        self.assertContains(response, "General")

    def test_creates_thread_in_chosen_category(self):
        self.client.login(username="player", password="password123")
        response = self.client.post(
            reverse("forum:thread_create_any"),
            {"category": self.category.pk, "title": "Hello there", "body": "First post"},
        )
        thread = Thread.objects.get(title="Hello there")
        self.assertEqual(thread.category, self.category)
        self.assertEqual(thread.author, self.user)
        self.assertTrue(Post.objects.filter(thread=thread, body="First post").exists())
        self.assertRedirects(
            response,
            reverse("forum:thread_detail", args=[self.category.slug, thread.slug]),
        )

    def test_missing_category_is_a_form_error_not_a_crash(self):
        self.client.login(username="player", password="password123")
        response = self.client.post(
            reverse("forum:thread_create_any"),
            {"title": "Hello there", "body": "First post"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Thread.objects.filter(title="Hello there").exists())


class RichTextBodySanitizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.client.login(username="player", password="password123")

    def test_thread_body_keeps_safe_formatting_and_strips_scripts(self):
        response = self.client.post(
            reverse("forum:thread_create_any"),
            {
                "category": self.category.pk,
                "title": "Formatted post",
                "body": "<p>Hello <strong>world</strong></p><script>alert(1)</script>",
            },
        )
        thread = Thread.objects.get(title="Formatted post")
        post = Post.objects.get(thread=thread)
        self.assertIn("<strong>world</strong>", post.body)
        self.assertNotIn("<script>", post.body)
        self.assertRedirects(
            response,
            reverse("forum:thread_detail", args=[self.category.slug, thread.slug]),
        )

    def test_thread_body_strips_dangerous_attributes(self):
        self.client.post(
            reverse("forum:thread_create_any"),
            {
                "category": self.category.pk,
                "title": "Malicious post",
                "body": '<p onclick="steal()">hi</p><a href="javascript:steal()">click</a>',
            },
        )
        post = Post.objects.get(thread__title="Malicious post")
        self.assertNotIn("onclick", post.body)
        self.assertNotIn("javascript:", post.body)

    def test_thread_body_that_is_only_markup_is_rejected_as_empty(self):
        response = self.client.post(
            reverse("forum:thread_create_any"),
            {"category": self.category.pk, "title": "Empty post", "body": "<p><br></p>"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Thread.objects.filter(title="Empty post").exists())

    def test_reply_body_is_sanitized_too(self):
        thread = Thread.objects.create(
            category=self.category, author=self.user, slug="t1", title="T1"
        )
        Post.objects.create(thread=thread, author=self.user, body="<p>opening post</p>")
        self.client.post(
            reverse("forum:post_create", args=[self.category.slug, thread.slug]),
            {"body": "<p>a reply</p><script>bad()</script>"},
        )
        reply = Post.objects.filter(thread=thread).exclude(body="<p>opening post</p>").first()
        self.assertIsNotNone(reply)
        self.assertIn("a reply", reply.body)
        self.assertNotIn("<script>", reply.body)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PostImageUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")

    def _upload(self, filename="pic.png", content=ONE_PIXEL_PNG, content_type="image/png"):
        image = SimpleUploadedFile(filename, content, content_type=content_type)
        return self.client.post(reverse("forum:upload_post_image"), {"image": image})

    def test_requires_login(self):
        response = self._upload()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_valid_image_upload_returns_url(self):
        self.client.login(username="player", password="password123")
        response = self._upload()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["url"].startswith("/media/post_images/"))
        self.assertEqual(PostImage.objects.filter(uploader=self.user).count(), 1)

    def test_non_image_file_is_rejected(self):
        self.client.login(username="player", password="password123")
        response = self._upload(
            filename="not-an-image.txt", content=b"just text", content_type="text/plain"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertEqual(PostImage.objects.count(), 0)

    def test_uploaded_image_survives_sanitization_in_a_thread_body(self):
        self.client.login(username="player", password="password123")
        upload_response = self._upload()
        image_url = upload_response.json()["url"]

        self.client.post(
            reverse("forum:thread_create_any"),
            {
                "category": self.category.pk,
                "title": "Post with an image",
                "body": f'<p>look at this</p><img src="{image_url}" alt="pic">',
            },
        )
        post = Post.objects.get(thread__title="Post with an image")
        self.assertIn(f'src="{image_url}"', post.body)

    def test_hotlinked_external_image_src_is_stripped(self):
        self.client.login(username="player", password="password123")
        self.client.post(
            reverse("forum:thread_create_any"),
            {
                "category": self.category.pk,
                "title": "Post with hotlink",
                "body": '<img src="https://evil.example.com/track.png" alt="tracker">',
            },
        )
        post = Post.objects.get(thread__title="Post with hotlink")
        self.assertNotIn("evil.example.com", post.body)

    def test_image_only_body_is_not_treated_as_empty(self):
        self.client.login(username="player", password="password123")
        upload_response = self._upload()
        image_url = upload_response.json()["url"]

        response = self.client.post(
            reverse("forum:thread_create_any"),
            {
                "category": self.category.pk,
                "title": "Just an image",
                "body": f'<img src="{image_url}" alt="pic">',
            },
        )
        self.assertTrue(Thread.objects.filter(title="Just an image").exists())
        self.assertEqual(response.status_code, 302)


class RichTextExtendedFormattingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.client.login(username="player", password="password123")

    def _create_thread(self, body):
        self.client.post(
            reverse("forum:thread_create_any"),
            {"category": self.category.pk, "title": "Formatting test", "body": body},
        )
        return Post.objects.get(thread__title="Formatting test")

    def test_youtube_embed_is_allowed_and_sandboxed(self):
        post = self._create_thread(
            '<iframe class="ql-video" src="https://www.youtube.com/embed/dQw4w9WgXcQ" '
            'frameborder="0" allowfullscreen="true"></iframe>'
        )
        self.assertIn('src="https://www.youtube.com/embed/dQw4w9WgXcQ"', post.body)
        self.assertIn("sandbox=", post.body)

    def test_vimeo_embed_is_allowed(self):
        post = self._create_thread(
            '<iframe src="https://player.vimeo.com/video/123456789"></iframe>'
        )
        self.assertIn('src="https://player.vimeo.com/video/123456789"', post.body)

    def test_arbitrary_iframe_src_is_stripped(self):
        post = self._create_thread('<iframe src="https://evil.example.com/phish"></iframe>')
        self.assertNotIn("evil.example.com", post.body)

    def test_iframe_onload_handler_is_stripped(self):
        post = self._create_thread(
            '<iframe src="https://www.youtube.com/embed/abc" onload="alert(1)"></iframe>'
        )
        self.assertNotIn("onload", post.body)

    def test_spoiler_span_is_preserved(self):
        post = self._create_thread(
            '<p>The ending is <span class="ql-spoiler-true">a twist</span>.</p>'
        )
        self.assertIn('<span class="ql-spoiler-true">a twist</span>', post.body)

    def test_spoiler_span_cannot_smuggle_extra_classes(self):
        post = self._create_thread('<span class="ql-spoiler-true evil-tracker">hi</span>')
        self.assertNotIn("evil-tracker", post.body)

    def test_span_onclick_is_stripped(self):
        post = self._create_thread('<span onclick="alert(1)" class="random">hi</span>')
        self.assertNotIn("onclick", post.body)

    def test_superscript_and_subscript_survive(self):
        post = self._create_thread("<p>x<sup>2</sup> and H<sub>2</sub>O</p>")
        self.assertIn("<sup>2</sup>", post.body)
        self.assertIn("<sub>2</sub>", post.body)

    def test_headers_survive(self):
        post = self._create_thread("<h2>A heading</h2><p>body text</p>")
        self.assertIn("<h2>A heading</h2>", post.body)
