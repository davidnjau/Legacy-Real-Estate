from django.contrib import admin
from django.urls import path

from config.views import health_check
from users.views import (
    csrf_token,
    current_user,
    login_view,
    logout_view,
    verify_sms_challenge,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health", health_check, name="health"),
    path("api/v1/auth/csrf", csrf_token, name="auth-csrf"),
    path("api/v1/auth/login", login_view, name="auth-login"),
    path("api/v1/auth/mfa/verify", verify_sms_challenge, name="auth-mfa-verify"),
    path("api/v1/auth/logout", logout_view, name="auth-logout"),
    path("api/v1/auth/me", current_user, name="auth-current-user"),
]
