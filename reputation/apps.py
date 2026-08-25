from django.apps import AppConfig


class ReputationConfig(AppConfig):
    name = "reputation"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from reactions.models import Reaction

        from . import signals

        post_save.connect(signals.handle_reaction_saved, sender=Reaction)
        post_delete.connect(signals.handle_reaction_deleted, sender=Reaction)
