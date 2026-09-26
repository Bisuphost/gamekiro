from marketplace.models import AuditLog


def log(actor, action, target=None, summary="", ip=None, **metadata):
    target_type = ""
    target_id = ""
    if target is not None:
        target_type = target.__class__.__name__
        target_id = str(target.pk)
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary or action,
        metadata=metadata,
        ip_address=ip,
    )
