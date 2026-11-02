from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from hc.api.models import Check, Flip, MaintenanceWindow
from hc.test import BaseTestCase


class MaintenanceViewsTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Database backup")
        self.url = f"/projects/{self.project.code}/maintenance/"
        self.payload = {
            "name": "Nightly",
            "kind": "weekly",
            "enabled": "on",
            "tz": "Europe/Berlin",
            "start_time": "05:00",
            "end_time": "05:30",
            "weekdays": [str(day) for day in range(7)],
            "checks": [self.check.id],
        }
        self.client.login(username="alice@example.org", password="password")

    def test_it_creates_and_lists_a_recurring_window(self) -> None:
        response = self.client.post(self.url, self.payload, follow=True)
        self.assertRedirects(response, self.url)
        self.assertContains(response, "Maintenance window saved.")
        window = MaintenanceWindow.objects.get()
        self.assertEqual(window.weekdays, list(range(7)))
        self.assertEqual(list(window.checks.all()), [self.check])
        response = self.client.get(self.url)
        self.assertContains(response, "Nightly")
        self.assertContains(response, "Europe/Berlin")

    def test_it_creates_a_one_time_window_in_selected_timezone(self) -> None:
        self.payload.update(
            kind="one_time", start_date="2026-11-02", end_date="2026-11-02"
        )
        response = self.client.post(self.url, self.payload)
        self.assertRedirects(response, self.url)
        window = MaintenanceWindow.objects.get()
        self.assertEqual(window.starts_at, datetime(2026, 11, 2, 4, tzinfo=UTC))
        self.assertEqual(window.ends_at, datetime(2026, 11, 2, 4, 30, tzinfo=UTC))

    def test_it_rejects_reverse_one_time_dates(self) -> None:
        self.payload.update(
            kind="one_time", start_date="2026-11-03", end_date="2026-11-02"
        )
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_it_rejects_invalid_timezone(self) -> None:
        self.payload["tz"] = "invalid/zone"
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)

    def test_it_rejects_checks_from_another_project(self) -> None:
        foreign = Check.objects.create(project=self.bobs_project)
        self.payload["checks"] = [foreign.id]
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_read_only_members_can_view_but_not_create(self) -> None:
        self.bobs_membership.role = "r"
        self.bobs_membership.save()
        self.client.login(username="bob@example.org", password="password")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)

    def test_non_members_have_no_access(self) -> None:
        self.client.login(username="charlie@example.org", password="password")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 404)

    def test_edit_and_delete_wake_pending_notifications(self) -> None:
        self.client.post(self.url, self.payload)
        window = MaintenanceWindow.objects.get()
        due = datetime(2027, 1, 1, tzinfo=UTC)
        flip = Flip.objects.create(
            owner=self.check,
            old_status="up",
            new_status="down",
            created=due,
            deferred_until=due,
        )
        self.payload.update(window=str(window.code), name="Changed")
        self.assertRedirects(self.client.post(self.url, self.payload), self.url)
        flip.refresh_from_db()
        assert flip.deferred_until is not None
        self.assertLess(flip.deferred_until, due)
        self.assertContains(
            self.client.get(self.url + f"?edit={window.code}"), "Changed"
        )
        self.assertRedirects(
            self.client.post(
                self.url, {"window": str(window.code), "action": "delete"}
            ),
            self.url,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_csrf_is_required(self) -> None:
        self.csrf_client.login(username="alice@example.org", password="password")
        self.assertEqual(self.csrf_client.post(self.url, self.payload).status_code, 403)

    def test_project_wide_scope_does_not_require_selected_services(self) -> None:
        self.payload["checks"] = []
        self.payload["all_checks"] = "on"
        self.assertRedirects(self.client.post(self.url, self.payload), self.url)
        self.assertTrue(MaintenanceWindow.objects.get().all_checks)

    def test_empty_service_scope_is_rejected(self) -> None:
        self.payload["checks"] = []
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_service_is_preselected_when_opening_from_its_page(self) -> None:
        response = self.client.get(self.url + f"?check={self.check.code}")
        self.assertEqual(response.context["form"].initial["checks"], [self.check.pk])

    def test_preselection_cannot_reference_another_project(self) -> None:
        foreign = Check.objects.create(project=self.bobs_project)
        self.assertEqual(
            self.client.get(self.url + f"?check={foreign.code}").status_code, 404
        )

    def test_service_details_show_configured_windows_outside_active_time(self) -> None:
        self.client.post(self.url, self.payload)
        response = self.client.get(f"/checks/{self.check.code}/details/")
        self.assertContains(response, "Nightly")
        self.assertContains(response, "Add Maintenance")

    def test_service_modal_preselects_service_and_returns_to_it(self) -> None:
        response = self.client.get(
            self.url
            + f"?modal=1&check={self.check.code}&return_check={self.check.code}"
        )
        self.assertTemplateUsed(response, "front/maintenance_modal.html")
        self.assertEqual(response.context["form"].initial["checks"], [self.check.pk])
        self.payload["return_check"] = str(self.check.code)
        response = self.client.post(
            self.url, self.payload, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertJSONEqual(
            response.content, {"redirect": f"/checks/{self.check.code}/details/"}
        )

    def test_modal_validation_errors_are_returned_inline(self) -> None:
        self.payload["name"] = ""
        response = self.client.post(
            self.url, self.payload, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "front/maintenance_modal.html")
        self.assertContains(response, "This field is required", status_code=400)
        self.assertNotContains(response, "navbar-brand", status_code=400)

    def test_return_destination_cannot_point_to_another_project(self) -> None:
        foreign = Check.objects.create(project=self.bobs_project)
        self.payload["return_check"] = str(foreign.code)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 404)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_read_only_members_cannot_load_edit_modal(self) -> None:
        self.bobs_membership.role = "r"
        self.bobs_membership.save()
        self.client.login(username="bob@example.org", password="password")
        self.assertEqual(self.client.get(self.url + "?modal=1").status_code, 403)

    def test_service_creation_without_javascript_returns_to_service(self) -> None:
        self.payload["return_check"] = str(self.check.code)
        self.assertRedirects(
            self.client.post(self.url, self.payload),
            f"/checks/{self.check.code}/details/",
        )

    def test_checks_list_shows_configured_and_active_maintenance(self) -> None:
        self.client.post(self.url, self.payload)
        window = MaintenanceWindow.objects.get()
        window.kind = "one_time"
        window.starts_at = now() + timedelta(hours=1)
        window.ends_at = now() + timedelta(hours=2)
        window.save()
        list_url = f"/projects/{self.project.code}/checks/"
        response = self.client.get(list_url)
        self.assertContains(response, 'title="Maintenance configured"')
        self.assertNotContains(
            response, 'class="maintenance-indicator maintenance-active"'
        )
        window = MaintenanceWindow.objects.get()
        window.kind = "one_time"
        window.starts_at = now() - timedelta(minutes=1)
        window.ends_at = now() + timedelta(minutes=1)
        window.save()
        response = self.client.get(list_url)
        self.assertContains(
            response, 'class="maintenance-indicator maintenance-active"'
        )
        self.assertContains(response, "Maintenance active - notifications muted")
        window.ends_at = now() - timedelta(seconds=1)
        window.save()
        response = self.client.get(list_url)
        self.assertContains(response, 'title="Maintenance configured"')
        self.assertNotContains(
            response, 'class="maintenance-indicator maintenance-active"'
        )

    def test_maintenance_indicator_updates_in_status_polling(self) -> None:
        window = MaintenanceWindow.objects.create(
            project=self.project,
            name="Project-wide",
            kind="one_time",
            all_checks=True,
            starts_at=now() - timedelta(minutes=1),
            ends_at=now() + timedelta(minutes=1),
        )
        status_url = f"/projects/{self.project.code}/checks/status/"
        response = self.client.get(status_url)
        self.assertIn(
            "maintenance-active", response.json()["details"][0]["maintenance"]
        )
        window.enabled = False
        window.save()
        response = self.client.get(status_url)
        self.assertIn(
            "Maintenance configured", response.json()["details"][0]["maintenance"]
        )
        self.assertNotIn(
            "maintenance-active", response.json()["details"][0]["maintenance"]
        )
        window.delete()
        response = self.client.get(status_url)
        self.assertEqual(response.json()["details"][0]["maintenance"], "")

    def test_maintenance_indicator_is_scoped_to_the_service(self) -> None:
        other = Check.objects.create(project=self.project, name="Other service")
        self.client.post(self.url, self.payload)
        response = self.client.get(f"/projects/{self.project.code}/checks/status/")
        details = {
            item["code"]: item["maintenance"] for item in response.json()["details"]
        }
        self.assertIn("Maintenance configured", details[str(self.check.code)])
        self.assertEqual(details[str(other.code)], "")

    def test_maintenance_indicator_queries_do_not_grow_per_service(self) -> None:
        self.client.post(self.url, self.payload)
        urls = [
            f"/projects/{self.project.code}/checks/",
            f"/projects/{self.project.code}/checks/status/",
        ]
        self.client.get(urls[0])
        counts = []
        for url in urls:
            with CaptureQueriesContext(connection) as queries:
                self.client.get(url)
            counts.append(len(queries))
        for index in range(10):
            Check.objects.create(project=self.project, name=f"Service {index}")
        for url, count in zip(urls, counts, strict=True):
            with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                self.client.get(url)
            self.assertEqual(len(queries), count)


class GlobalMaintenanceViewsTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice.is_superuser = True
        self.alice.save()
        self.client.login(username="alice@example.org", password="password")
        self.url = f"/projects/{self.project.code}/maintenance/"
        self.other_url = f"/projects/{self.bobs_project.code}/maintenance/"
        self.payload: dict[str, str | list[str]] = {
            "name": "Healthchecks update",
            "kind": "one_time",
            "enabled": "on",
            "all_projects": "on",
            "tz": "UTC",
            "start_date": "2026-11-02",
            "start_time": "05:00",
            "end_date": "2026-11-02",
            "end_time": "06:00",
        }

    def test_superusers_can_create_and_edit_global_windows_from_another_project(
        self,
    ) -> None:
        self.assertRedirects(self.client.post(self.url, self.payload), self.url)
        window = MaintenanceWindow.objects.get()
        self.assertIsNone(window.project_id)
        self.assertFalse(window.checks.exists())
        self.assertContains(self.client.get(self.other_url), "Healthchecks update")
        self.payload.update(window=str(window.code), name="Updated server maintenance")
        self.assertRedirects(
            self.client.post(self.other_url, self.payload), self.other_url
        )
        window.refresh_from_db()
        self.assertIsNone(window.project_id)
        self.assertEqual(window.name, "Updated server maintenance")
        self.assertRedirects(
            self.client.post(
                self.other_url, {"action": "delete", "window": str(window.code)}
            ),
            self.other_url,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_global_scope_is_hidden_and_rejected_for_project_owners(self) -> None:
        self.alice.is_superuser = False
        self.alice.save()
        response = self.client.get(self.url + "?modal=1")
        self.assertNotContains(response, 'name="all_projects"')
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Only superusers", status_code=400)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_global_windows_cannot_be_recurring(self) -> None:
        self.payload.update(kind="weekly", weekdays=["0"])
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, "All-project maintenance must be one-time", status_code=400
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_project_members_can_see_but_cannot_change_global_maintenance(self) -> None:
        self.client.post(self.url, self.payload)
        window = MaintenanceWindow.objects.get()
        check = Check.objects.create(project=self.project, name="Example")
        self.client.login(username="bob@example.org", password="password")
        for url in (self.url, f"/checks/{check.code}/details/"):
            response = self.client.get(url)
            self.assertContains(response, "Healthchecks update")
            self.assertContains(response, "All projects")
            self.assertNotContains(response, f"edit={window.code}")
        self.assertEqual(
            self.client.get(self.url + f"?modal=1&edit={window.code}").status_code, 403
        )
        self.payload.update(window=str(window.code), all_projects="")
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url, {"action": "delete", "window": str(window.code)}
            ).status_code,
            403,
        )
        window.refresh_from_db()
        self.assertIsNone(window.project_id)

    def test_global_scope_changes_wake_pending_notifications_in_every_project(
        self,
    ) -> None:
        self.client.post(self.url, self.payload)
        window = MaintenanceWindow.objects.get()
        other = Check.objects.create(project=self.bobs_project, status="down")
        flip = Flip.objects.create(
            owner=other,
            created=now(),
            old_status="up",
            new_status="down",
            deferred_until=now() + timedelta(days=1),
        )
        self.payload.update(window=str(window.code), all_projects="", all_checks="on")
        self.assertRedirects(self.client.post(self.url, self.payload), self.url)
        flip.refresh_from_db()
        assert flip.deferred_until is not None
        self.assertLess(flip.deferred_until, now())
        window.refresh_from_db()
        self.assertEqual(window.project_id, self.project.pk)
        self.payload["all_projects"] = "on"
        self.assertRedirects(self.client.post(self.url, self.payload), self.url)
        flip.deferred_until = now() + timedelta(days=1)
        flip.save()
        self.client.post(self.url, {"action": "delete", "window": str(window.code)})
        flip.refresh_from_db()
        assert flip.deferred_until is not None
        self.assertLess(flip.deferred_until, now())

    def test_global_maintenance_appears_in_every_services_live_indicator(self) -> None:
        self.payload.update(
            start_date=now().date().isoformat(),
            end_date=(now() + timedelta(days=1)).date().isoformat(),
            start_time="00:00",
            end_time="23:59",
        )
        self.client.post(self.url, self.payload)
        for project in (self.project, self.bobs_project):
            check = Check.objects.create(project=project)
            response = self.client.get(f"/projects/{project.code}/checks/status/")
            detail = next(
                item
                for item in response.json()["details"]
                if item["code"] == str(check.code)
            )
            self.assertIn("maintenance-active", detail["maintenance"])
