import re
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from users.admin import AuditLogAdmin, AuditedGroupAdmin, BranchAdmin
from users.audit import record_audit_event
from users.models import AuditLog, Branch, SMSLoginChallenge, UserProfile
from users.roles import SUPER_ADMIN_ROLE
from users.sms import send_sms


class HealthCheckTests(SimpleTestCase):
    def test_health_endpoint_returns_ok(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_endpoint_is_public_and_read_only(self):
        response = self.client.post("/api/v1/health")

        self.assertEqual(response.status_code, 405)


class UserAccessTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.nairobi = Branch.objects.get(name="Nairobi")
        self.nakuru = Branch.objects.get(name="Nakuru")

    def create_user(self, **kwargs):
        defaults = {
            "email": "person@example.com",
            "name": "Example Person",
            "phone": "254712345678",
        }
        defaults.update(kwargs)
        email = defaults["email"]
        user = self.user_model.objects.create_user(
            username=email,
            email=email,
            password="Long-test-passphrase-29!",
        )
        UserProfile.objects.create(
            user=user,
            name=defaults["name"],
            phone=defaults["phone"],
        )
        return user

    def test_seeded_srs_roles_and_branches_exist(self):
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)),
            {
                "Super Admin (Director)",
                "Branch Manager",
                "Accountant",
                "Property Officer / Agent",
                "Land Sales Agent",
                "Caretaker",
                "Landlord (portal)",
                "Tenant (portal)",
                "Land Buyer (portal)",
            },
        )
        self.assertEqual(
            set(Branch.objects.values_list("name", flat=True)),
            {"Nairobi", "Nakuru"},
        )

    def test_user_can_have_multiple_roles_and_branches(self):
        user = self.create_user()
        user.groups.add(
            Group.objects.get(name="Accountant"),
            Group.objects.get(name="Caretaker"),
        )
        user.profile.branches.add(self.nairobi, self.nakuru)

        self.assertEqual(user.groups.count(), 2)
        self.assertEqual(
            set(user.profile.accessible_branches()),
            {self.nairobi, self.nakuru},
        )

    def test_super_admin_can_access_all_branches(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        user.profile.branches.add(self.nairobi)

        self.assertEqual(
            set(user.profile.accessible_branches()),
            {self.nairobi, self.nakuru},
        )
        self.assertTrue(user.has_perm("auth.change_group"))
        self.assertTrue(user.has_perm("users.change_branch"))
        self.assertTrue(user.has_perm("users.view_auditlog"))
        self.assertFalse(user.has_perm("users.add_auditlog"))

    def test_super_admin_can_view_audit_log_without_mutation_permissions(self):
        user = self.create_user()
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        request = RequestFactory().get("/admin/users/auditlog/")
        request.user = user
        audit_admin = AuditLogAdmin(AuditLog, admin.site)

        self.assertTrue(audit_admin.has_module_permission(request))
        self.assertTrue(audit_admin.has_view_permission(request))
        self.assertFalse(audit_admin.has_add_permission(request))
        self.assertFalse(audit_admin.has_change_permission(request))
        self.assertFalse(audit_admin.has_delete_permission(request))

    def test_finance_role_django_admin_login_requires_sms_verification(self):
        user = self.create_user()
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.groups.add(Group.objects.get(name="Accountant"))
        delivered = []
        client = Client()

        failed_password = client.post(
            "/admin/login/",
            {"username": user.email, "password": "wrong"},
        )
        self.assertEqual(failed_password.status_code, 200)
        self.assertIsNone(client.session.get("_auth_user_id"))
        self.assertTrue(
            AuditLog.objects.filter(action="login.failed", user__isnull=True).exists()
        )

        with patch(
            "users.admin_authentication.send_sms",
            side_effect=lambda phone, message: delivered.append(message),
        ):
            challenged = client.post(
                "/admin/login/",
                {"username": user.email, "password": "Long-test-passphrase-29!"},
            )

        self.assertEqual(challenged.status_code, 200)
        self.assertIsNone(client.session.get("_auth_user_id"))
        self.assertEqual(SMSLoginChallenge.objects.filter(user=user).count(), 1)

        code = re.search(r"\b(\d{6})\b", delivered[0]).group(1)
        wrong_code = "000000" if code != "000000" else "000001"
        rejected = client.post(
            "/admin/login/",
            {
                "username": user.email,
                "password": "Long-test-passphrase-29!",
                "mfa_code": wrong_code,
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(
            SMSLoginChallenge.objects.get(user=user).attempts,
            1,
        )

        verified = client.post(
            "/admin/login/",
            {
                "username": user.email,
                "password": "Long-test-passphrase-29!",
                "mfa_code": code,
            },
        )

        self.assertEqual(verified.status_code, 302)
        self.assertEqual(client.session.get("_auth_user_id"), str(user.pk))
        self.assertFalse(SMSLoginChallenge.objects.filter(user=user).exists())
        self.assertTrue(
            AuditLog.objects.filter(user=user, action="login.succeeded").exists()
        )

    def test_non_finance_django_admin_login_is_audited(self):
        user = self.create_user()
        user.is_staff = True
        user.save(update_fields=["is_staff"])

        response = self.client.post(
            "/admin/login/",
            {"username": user.email, "password": "Long-test-passphrase-29!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.succeeded",
            ).exists()
        )

    def test_email_and_phone_login_establish_a_session(self):
        user = self.create_user()

        for identifier in (user.email, user.profile.phone):
            self.client.post(
                "/api/v1/auth/logout",
                data="{}",
                content_type="application/json",
            )
            response = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": identifier, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["id"], user.pk)
            self.assertEqual(
                self.client.get("/api/v1/auth/me").json()["user"]["id"],
                user.pk,
            )

    def test_phone_only_user_can_sign_in(self):
        user = self.user_model.objects.create_user(
            username="254712345679",
            email="",
            password="Long-test-passphrase-29!",
        )
        UserProfile.objects.create(
            user=user,
            name="Phone Only",
            phone="254712345679",
        )

        response = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": "254712345679",
                "password": "Long-test-passphrase-29!",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["user"]["email"])
        self.assertEqual(response.json()["user"]["phone"], "254712345679")

    def test_invalid_password_does_not_create_session(self):
        self.create_user()

        response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": "person@example.com", "password": "wrong"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        event = AuditLog.objects.get(action="login.failed")
        self.assertIsNone(event.user)
        self.assertIsNone(event.old_values)
        self.assertIsNone(event.new_values)

    def test_audit_events_capture_actor_action_entity_time_values_and_ip(self):
        user = self.create_user()
        request = RequestFactory().post("/")
        request.user = user
        request.META["REMOTE_ADDR"] = "192.0.2.10"

        event = record_audit_event(
            request,
            "branch.updated",
            "Branch",
            old_values={"name": "Old"},
            new_values={"name": "New"},
        )

        self.assertEqual(event.user, user)
        self.assertEqual(event.action, "branch.updated")
        self.assertEqual(event.entity, "Branch")
        self.assertIsNotNone(event.timestamp)
        self.assertEqual(event.old_values, {"name": "Old"})
        self.assertEqual(event.new_values, {"name": "New"})
        self.assertEqual(event.ip_address, "192.0.2.10")

    def test_audit_log_is_append_only(self):
        event = AuditLog.objects.create(action="test", entity="Test")

        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=event.pk).update(action="changed")
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=event.pk).delete()

    def test_audit_log_database_trigger_rejects_direct_edits_and_deletes(self):
        event = AuditLog.objects.create(action="test", entity="Test")

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users_auditlog SET action = %s WHERE id = %s",
                        ["changed", event.pk],
                    )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM users_auditlog WHERE id = %s",
                        [event.pk],
                    )

    def test_deleting_audited_user_preserves_event_and_clears_actor(self):
        user = self.create_user()
        user_id = user.pk
        request = RequestFactory().post("/")
        request.user = user
        event = record_audit_event(request, "user.deleted", "User", old_values={"id": user_id})

        user.delete()

        event.refresh_from_db()
        self.assertIsNone(event.user)
        self.assertEqual(event.old_values, {"id": user_id})

    def test_branch_admin_changes_are_audited(self):
        actor = self.create_user()
        request = RequestFactory().post("/")
        request.user = actor
        request.META["REMOTE_ADDR"] = "192.0.2.11"
        branch_admin = BranchAdmin(Branch, admin.site)
        branch = Branch(name="Kisumu", town="Kisumu")

        branch_admin.save_model(request, branch, form=None, change=False)
        created = AuditLog.objects.get(action="branch.created", entity="Branch")
        self.assertEqual(created.user, actor)
        self.assertEqual(created.new_values["name"], "Kisumu")
        self.assertEqual(created.ip_address, "192.0.2.11")

        branch.town = "Kisumu Central"
        branch_admin.save_model(request, branch, form=None, change=True)
        updated = AuditLog.objects.get(action="branch.updated", entity="Branch")
        self.assertEqual(updated.old_values["town"], "Kisumu")
        self.assertEqual(updated.new_values["town"], "Kisumu Central")

    def test_role_admin_changes_are_audited(self):
        actor = self.create_user()
        request = RequestFactory().post("/")
        request.user = actor
        role_admin = AuditedGroupAdmin(Group, admin.site)
        role = Group(name="Audit role")
        form = SimpleNamespace(instance=role, save_m2m=lambda: None)

        role_admin.save_model(request, role, form, change=False)
        role_admin.save_related(request, form, [], change=False)
        created = AuditLog.objects.get(action="role.created", entity="Role")
        self.assertEqual(created.user, actor)
        self.assertEqual(created.new_values["name"], "Audit role")

        role.name = "Renamed audit role"
        role_admin.save_model(request, role, form, change=True)
        role_admin.save_related(request, form, [], change=True)
        updated = AuditLog.objects.get(action="role.updated", entity="Role")
        self.assertEqual(updated.old_values["name"], "Audit role")
        self.assertEqual(updated.new_values["name"], "Renamed audit role")

    @override_settings(AUTH_LOGIN_MAX_ATTEMPTS=2, AUTH_LOGIN_LOCKOUT_SECONDS=60)
    def test_repeated_password_failures_lock_the_account(self):
        user = self.create_user()
        data = {"identifier": user.email, "password": "wrong"}

        for _ in range(2):
            response = self.client.post(
                "/api/v1/auth/login",
                data=data,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)

        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.failed_login_attempts, 2)
        self.assertIsNotNone(profile.locked_until)
        locked_response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )
        self.assertEqual(locked_response.status_code, 401)

    @override_settings(AUTH_SMS_BACKEND="test_backend.SMS")
    def test_finance_roles_require_sms_verification(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        delivered = []

        with patch("users.views.send_sms", side_effect=lambda phone, message: delivered.append(message)):
            response = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["mfa_required"])
        self.assertNotIn("code", response.json())
        self.assertEqual(SMSLoginChallenge.objects.count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.mfa_challenge_issued",
            ).exists()
        )
        code = re.search(r"\b(\d{6})\b", delivered[0]).group(1)

        challenge_id = response.json()["challenge_id"]
        for malformed_payload in (
            {"challenge_id": challenge_id, "code": "123"},
            {"challenge_id": challenge_id, "code": "١٢٣٤٥٦"},
            {"challenge_id": True, "code": code},
        ):
            malformed_code = self.client.post(
                "/api/v1/auth/mfa/verify",
                data=malformed_payload,
                content_type="application/json",
            )
            self.assertEqual(malformed_code.status_code, 400)
        self.assertEqual(SMSLoginChallenge.objects.get().attempts, 0)

        invalid_code = "000000" if code != "000000" else "000001"
        rejected = self.client.post(
            "/api/v1/auth/mfa/verify",
            data={
                "challenge_id": response.json()["challenge_id"],
                "code": invalid_code,
            },
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(SMSLoginChallenge.objects.get().attempts, 1)

        verified = self.client.post(
            "/api/v1/auth/mfa/verify",
            data={"challenge_id": response.json()["challenge_id"], "code": code},
            content_type="application/json",
        )

        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["user"]["id"], user.pk)
        self.assertFalse(SMSLoginChallenge.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.succeeded",
            ).exists()
        )

    @override_settings(AUTH_SMS_MAX_ATTEMPTS=1)
    def test_final_invalid_sms_code_consumes_challenge(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        with patch("users.views.send_sms"):
            challenge_response = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )
        challenge_id = challenge_response.json()["challenge_id"]

        rejected = self.client.post(
            "/api/v1/auth/mfa/verify",
            data={"challenge_id": challenge_id, "code": "000000"},
            content_type="application/json",
        )

        self.assertEqual(rejected.status_code, 401)
        self.assertFalse(SMSLoginChallenge.objects.filter(pk=challenge_id).exists())
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_login_requires_csrf_token(self):
        user = self.create_user()
        client = Client(enforce_csrf_checks=True)
        login_data = {
            "identifier": user.email,
            "password": "Long-test-passphrase-29!",
        }

        rejected = client.post(
            "/api/v1/auth/login",
            data=login_data,
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)

        csrf_response = client.get("/api/v1/auth/csrf")
        accepted = client.post(
            "/api/v1/auth/login",
            data=login_data,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrfToken"],
        )
        self.assertEqual(accepted.status_code, 200)

    def test_console_sms_backend_fails_closed_outside_debug(self):
        from users.sms import ConsoleSMSBackend

        with override_settings(DEBUG=False):
            with self.assertRaises(ImproperlyConfigured):
                ConsoleSMSBackend().send("254712345678", "test")

    def test_sms_authentication_fails_closed_without_a_production_provider(self):
        with override_settings(DEBUG=False, AUTH_SMS_BACKEND=""):
            with self.assertRaises(ImproperlyConfigured):
                send_sms("254712345678", "test")
