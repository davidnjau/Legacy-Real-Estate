from django.contrib.auth.models import AnonymousUser

from users.models import Agency, AgencyMembership, AuditLog, Branch

_REQUEST_ACTOR = object()


def record_audit_event(
    request,
    action,
    entity,
    *,
    old_values=None,
    new_values=None,
    actor=_REQUEST_ACTOR,
    agency=None,
):
    if actor is _REQUEST_ACTOR:
        actor = getattr(request, "user", None)
    if isinstance(actor, AnonymousUser) or not getattr(actor, "is_authenticated", False):
        actor = None

    if agency is None:
        for values in (new_values, old_values):
            if not isinstance(values, dict):
                continue
            agency_id = values.get("agency_id")
            branch_id = values.get("branch_id")
            branch = values.get("branch")
            if branch_id is None and isinstance(branch, dict):
                branch_id = branch.get("id")
            if agency_id is not None:
                agency = Agency.objects.filter(pk=agency_id).first()
            elif branch_id is not None:
                branch = Branch.objects.select_related("agency").filter(
                    pk=branch_id
                ).first()
                agency = branch.agency if branch else None
            if agency is not None:
                break

    if agency is None and actor is not None:
        agency_ids = list(
            AgencyMembership.objects.filter(
                profile__user=actor,
                agency__is_active=True,
                is_active=True,
            )
            .values_list("agency_id", flat=True)
            .distinct()[:2]
        )
        if len(agency_ids) == 1:
            agency = Agency.objects.filter(pk=agency_ids[0]).first()

    return AuditLog.objects.create(
        agency=agency,
        user=actor,
        action=action,
        entity=entity,
        old_values=old_values,
        new_values=new_values,
        ip_address=getattr(request, "META", {}).get("REMOTE_ADDR"),
    )
