from django.contrib.contenttypes.models import ContentType

from .models import Report

REPORTABLE_MODELS = {"forum": {"post"}, "games": {"review"}}


def hidden_object_ids(model_class):
    content_type = ContentType.objects.get_for_model(model_class)
    return Report.objects.filter(content_type=content_type, hides_target=True).values_list(
        "object_id", flat=True
    )
