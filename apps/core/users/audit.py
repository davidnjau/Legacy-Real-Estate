from django.contrib.auth.models import AnonymousUser

from users.models import AuditLog

_REQUEST_ACTOR = object()


def record_audit_event(
    request,
    action,
    entity,
    *,
    old_values=None,
    new_values=None,
    actor=_REQUEST_ACTOR,
):
    if actor is _REQUEST_ACTOR:
        actor = getattr(request, "user", None)
    if isinstance(actor, AnonymousUser) or not getattr(actor, "is_authenticated", False):
        actor = None

    return AuditLog.objects.create(
        user=actor,
        action=action,
        entity=entity,
        old_values=old_values,
        new_values=new_values,
        ip_address=request.META.get("REMOTE_ADDR"),
    )
