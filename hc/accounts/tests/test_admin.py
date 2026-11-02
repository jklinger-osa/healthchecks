from __future__ import annotations

from django.contrib.auth.models import Permission
from django.core import mail
from django.test import override_settings

from hc.accounts.models import Member, Project
from hc.payments.models import Subscription
from hc.test import BaseTestCase


class AccountsAdminTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()

        self.alice.is_staff = True
        self.alice.is_superuser = True
        self.alice.save()

    def test_it_shows_profiles(self) -> None:
        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/admin/accounts/profile/")
        self.assertContains(r, "alice@example.org")
        self.assertContains(r, "bob@example.org")

    def test_it_escapes_emails_when_showing_profiles(self) -> None:
        self.bob.email = "bob&friends@example.org"
        self.bob.save()

        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/admin/accounts/profile/")
        # The amperstand should be escaped
        self.assertNotContains(r, "bob&friends@example.org")

    def test_it_escapes_emails_when_showing_profiles_with_subscriptions(self) -> None:
        self.bob.email = "bob&friends@example.org"
        self.bob.save()

        self.sub = Subscription(user=self.bob)
        self.sub.plan_name = "Custom Plan"
        self.sub.save()

        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/admin/accounts/profile/")
        # The amperstand should be escaped
        self.assertNotContains(r, "bob&friends@example.org")
        self.assertContains(r, "<span>Custom Plan</span>")

    def test_it_shows_projects(self) -> None:
        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/admin/accounts/project/")
        self.assertContains(r, "Alices Project")
        self.assertContains(r, "Default Project for bob@example.org")

    def test_it_escapes_emails_when_showing_projects(self) -> None:
        self.bob.email = "bob&friends@example.org"
        self.bob.save()

        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/admin/accounts/project/")
        # The amperstand should be escaped
        self.assertNotContains(r, "bob&friends@example.org")


class UserProjectAccessAdminTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice.is_staff = True
        self.alice.is_superuser = True
        self.alice.save()
        self.client.login(username="alice@example.org", password="password")
        self.url = f"/admin/auth/user/{self.bob.pk}/change/"

    def user_data(self) -> dict[str, str]:
        return {
            "username": self.bob.username,
            "email": self.bob.email,
            "is_active": "on",
            "date_joined_0": self.bob.date_joined.strftime("%Y-%m-%d"),
            "date_joined_1": self.bob.date_joined.strftime("%H:%M:%S"),
            "memberships-TOTAL_FORMS": "1",
            "memberships-INITIAL_FORMS": "1",
            "memberships-0-id": str(self.bobs_membership.pk),
            "memberships-0-project": str(self.project.pk),
            "memberships-0-role": "w",
            "_save": "Save",
        }

    def test_it_shows_memberships_and_separate_ownership(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Project memberships")
        self.assertContains(response, "Project ownership")
        self.assertContains(response, "Default Project for bob@example.org")
        self.assertContains(response, "Alices Project")
        inline = response.context["inline_admin_formsets"][0]
        self.assertEqual(inline.formset.prefix, "memberships")
        self.assertEqual(inline.formset.initial_forms[0].instance, self.bobs_membership)

    def test_it_adds_membership(self) -> None:
        other = Project.objects.create(
            owner=self.alice, name="Another project", badge_key="another"
        )
        data = self.user_data()
        data.update(
            {
                "memberships-TOTAL_FORMS": "2",
                "memberships-1-project": str(other.pk),
                "memberships-1-role": "r",
            }
        )
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Member.objects.get(user=self.bob, project=other).role, "r")
        self.bobs_membership.refresh_from_db()
        self.assertEqual(self.bobs_membership.role, "w")
        self.assertEqual(len(mail.outbox), 0)

    def test_it_explicitly_changes_membership_role(self) -> None:
        data = self.user_data()
        data["memberships-0-role"] = "m"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.bobs_membership.refresh_from_db()
        self.assertEqual(self.bobs_membership.role, "m")
        data["memberships-0-role"] = "r"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.bobs_membership.refresh_from_db()
        self.assertEqual(self.bobs_membership.role, "r")

    def test_it_removes_membership_without_changing_ownership(self) -> None:
        data = self.user_data()
        data["memberships-0-DELETE"] = "on"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Member.objects.filter(pk=self.bobs_membership.pk).exists())
        self.bobs_project.refresh_from_db()
        self.assertEqual(self.bobs_project.owner_id, self.bob.pk)

    def test_it_rejects_duplicate_memberships(self) -> None:
        data = self.user_data()
        data.update(
            {
                "memberships-TOTAL_FORMS": "2",
                "memberships-1-project": str(self.project.pk),
                "memberships-1-role": "r",
            }
        )
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Member.objects.filter(user=self.bob, project=self.project).count(), 1
        )
        self.assertTrue(
            any(
                form.errors
                for form in response.context["inline_admin_formsets"][0].formset.forms
            )
        )

    def test_it_rejects_membership_for_a_project_the_user_owns(self) -> None:
        data = self.user_data()
        data.update(
            {
                "memberships-TOTAL_FORMS": "2",
                "memberships-1-project": str(self.bobs_project.pk),
                "memberships-1-role": "r",
            }
        )
        response = self.client.post(self.url, data)
        self.assertContains(
            response, "This user owns this project and already has full access."
        )
        self.assertFalse(
            Member.objects.filter(user=self.bob, project=self.bobs_project).exists()
        )

    @override_settings(
        AUTHENTICATION_BACKENDS=[
            "hc.accounts.backends.EmailBackend",
            "django.contrib.auth.backends.ModelBackend",
        ]
    )
    def test_user_editor_permission_does_not_grant_membership_permission(self) -> None:
        self.charlie.is_staff = True
        self.charlie.save()
        self.charlie.user_permissions.add(
            Permission.objects.get(
                codename="change_user", content_type__app_label="auth"
            )
        )
        self.client.login(username="charlie@example.org", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["inline_admin_formsets"], [])
        data = self.user_data()
        data["memberships-0-role"] = "m"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.bobs_membership.refresh_from_db()
        self.assertEqual(self.bobs_membership.role, "w")

    @override_settings(
        AUTHENTICATION_BACKENDS=[
            "hc.accounts.backends.EmailBackend",
            "django.contrib.auth.backends.ModelBackend",
        ]
    )
    def test_view_membership_permission_does_not_allow_changes(self) -> None:
        self.charlie.is_staff = True
        self.charlie.save()
        self.charlie.user_permissions.add(
            Permission.objects.get(
                codename="change_user", content_type__app_label="auth"
            ),
            Permission.objects.get(
                codename="view_member", content_type__app_label="accounts"
            ),
        )
        self.client.login(username="charlie@example.org", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        inline = response.context["inline_admin_formsets"][0]
        self.assertFalse(inline.has_add_permission)
        self.assertFalse(inline.has_change_permission)
        self.assertFalse(inline.has_delete_permission)
        data = self.user_data()
        data["memberships-0-role"] = "m"
        self.client.post(self.url, data)
        self.bobs_membership.refresh_from_db()
        self.assertEqual(self.bobs_membership.role, "w")

    def test_it_denies_access_to_non_staff_users(self) -> None:
        self.client.login(username="bob@example.org", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_default_backend_denies_non_superuser_admin_model_access(self) -> None:
        self.charlie.is_staff = True
        self.charlie.save()
        self.charlie.user_permissions.add(
            Permission.objects.get(
                codename="change_user", content_type__app_label="auth"
            )
        )
        self.client.login(username="charlie@example.org", password="password")
        self.assertEqual(self.client.get(self.url).status_code, 403)
