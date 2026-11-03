from django.shortcuts import get_object_or_404

from users.models import Branch


def accessible_branches(user):
    if not user.is_authenticated:
        return Branch.objects.none()

    profile = getattr(user, "profile", None)
    if profile is None:
        return Branch.objects.none()

    return profile.accessible_branches()


def get_accessible_branch_or_404(user, branch_id):
    return get_object_or_404(accessible_branches(user), pk=branch_id)
