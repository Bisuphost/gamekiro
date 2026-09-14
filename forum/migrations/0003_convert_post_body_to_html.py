from django.db import migrations
from django.utils.html import linebreaks


def convert_plain_text_to_html(apps, schema_editor):
    Post = apps.get_model("forum", "Post")
    for post in Post.objects.all().iterator():
        post.body = linebreaks(post.body)
        post.save(update_fields=["body"])


def convert_html_back_to_plain_text(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("forum", "0002_thread_title_search_vector_and_more"),
    ]

    operations = [
        migrations.RunPython(convert_plain_text_to_html, convert_html_back_to_plain_text),
    ]
