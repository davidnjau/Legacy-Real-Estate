from django.http import JsonResponse


class ResidentialSessionAccessMiddleware:
    ALLOWED_PREFIXES = (
        "/api/v1/auth/",
        "/api/v1/tenant/",
        "/api/v1/landlord/",
    )
    ALLOWED_PATHS = {
        "/api/v1/caretaker-invitations/verify",
        "/api/v1/caretaker-invitations/complete",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.session.get("auth_scope") == "residential"
            and not request.path_info.startswith(self.ALLOWED_PREFIXES)
            and request.path_info.rstrip("/") not in self.ALLOWED_PATHS
        ):
            return JsonResponse(
                {
                    "title": "Staff sign-in required",
                    "detail": (
                        "Sign in with your staff credentials before opening "
                        "a staff workspace."
                    ),
                    "error": "staff-step-up-required",
                },
                status=403,
            )
        return self.get_response(request)
