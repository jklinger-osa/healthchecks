from __future__ import annotations

from datetime import timedelta as td

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from hc.accounts.models import Member, Project
from hc.api.models import Channel, Check
from hc.test import BaseTestCase


class IndexTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.c1 = Check.objects.create(project=self.project)
        self.c2 = Check.objects.create(project=self.project)
        self.c3 = Check.objects.create(project=self.project)

    def test_it_shows_projects(self) -> None:
        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/")

        self.assertContains(r, "Alices Project")
        self.assertContains(r, "3 checks")
        self.assertContains(r, "status ic-up")
        self.assertContains(r, "favicon.svg")

    def test_it_shows_overall_down_status(self) -> None:
        self.c1.status = "down"
        self.c1.save()

        self.client.login(username="alice@example.org", password="password")
        r = self.client.get("/")
        self.assertContains(r, "status ic-down")
        self.assertContains(r, "favicon_down.svg")


class ProblemDashboardTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.login(username="alice@example.org", password="password")
        self.membership = Member.objects.create(
            user=self.alice, project=self.bobs_project, role="r"
        )
        self.down = Check.objects.create(
            project=self.project,
            name="Failed job",
            status="down",
            last_ping=now() - td(hours=2),
        )
        self.grace = Check.objects.create(
            project=self.bobs_project,
            name="Late job",
            status="up",
            last_ping=now() - td(minutes=61),
            timeout=td(hours=1),
            grace=td(minutes=5),
        )
        self.healthy = Check.objects.create(
            project=self.project, name="Healthy job", status="up", last_ping=now()
        )
        Check.objects.create(project=self.project, name="Never run", status="new")
        Check.objects.create(project=self.project, name="Paused job", status="paused")
        self.private = Check.objects.create(
            project=self.charlies_project, name="Private job", status="down"
        )
        self.empty = Project.objects.create(
            owner=self.alice, name="Empty project", badge_key="empty-project"
        )
        self.own_channel = Channel.objects.create(
            project=self.project, name="Own integration", kind="webhook"
        )
        self.other_channel = Channel.objects.create(
            project=self.bobs_project, name="Other integration", kind="email"
        )
        self.down.channel_set.add(self.own_channel)
        self.grace.channel_set.add(self.other_channel)

    def test_only_down_and_grace_services_appear_with_their_projects(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only projects with warnings or errors")
        self.assertContains(response, "Services with warnings or errors")
        self.assertEqual(
            [row["check"].pk for row in response.context["problem_rows"]],
            [self.down.pk, self.grace.pk],
        )
        self.assertContains(response, "Failed job")
        self.assertContains(response, "Late job")
        self.assertNotContains(response, "Healthy job")
        self.assertNotContains(response, "Private job")
        self.assertTemplateUsed(response, "front/check_row.html")
        self.assertNotContains(
            response,
            'id="update-cron-submit" type="submit" class="btn btn-primary" disabled',
        )
        self.assertNotContains(
            response,
            'id="update-oncalendar-submit" type="submit" class="btn btn-primary" disabled',
        )
        self.assertContains(response, f'data-code="{self.own_channel.code}"')
        self.assertContains(response, f'data-code="{self.other_channel.code}"')

    def test_controls_follow_each_projects_permissions(self) -> None:
        data = self.client.get("/?refresh=1&problems=1").json()
        rows = {row["code"]: row for row in data["rows"]}
        self.assertTrue(rows[str(self.down.code)]["editable"])
        self.assertIn('class="btn pause"', rows[str(self.down.code)]["html"])
        self.assertFalse(rows[str(self.grace.code)]["editable"])
        self.assertNotIn('class="btn pause"', rows[str(self.grace.code)]["html"])
        self.assertIn(
            f"/projects/{self.bobs_project.code}/checks/",
            rows[str(self.grace.code)]["html"],
        )
        response = self.client.post(
            f"/checks/{self.grace.code}/name/",
            {"name": "Changed", "return_to": "projects"},
        )
        self.assertEqual(response.status_code, 403)
        self.membership.role = "m"
        self.membership.save()
        data = self.client.get("/?refresh=1&problems=1").json()
        row = next(row for row in data["rows"] if row["code"] == str(self.grace.code))
        self.assertTrue(row["editable"])
        self.assertIn('class="btn pause"', row["html"])

    def test_superusers_keep_the_normal_project_list_scope(self) -> None:
        self.alice.is_superuser = True
        self.alice.save()
        response = self.client.get("/?refresh=1&problems=1")
        self.assertNotContains(response, "Private job")
        self.assertNotIn(str(self.charlies_project.code), response.json()["projects"])

    def test_live_refresh_adds_new_failures_and_removes_recoveries(self) -> None:
        before = self.client.get("/?refresh=1&problems=1").json()
        self.assertNotIn(
            str(self.healthy.code), [row["code"] for row in before["rows"]]
        )
        self.healthy.status = "down"
        self.healthy.save()
        self.down.status = "up"
        self.down.last_ping = now()
        self.down.save()
        after = self.client.get("/?refresh=1&problems=1").json()
        codes = [row["code"] for row in after["rows"]]
        self.assertIn(str(self.healthy.code), codes)
        self.assertNotIn(str(self.down.code), codes)
        self.assertEqual(after["projects"][str(self.empty.code)]["status"], "up")

    def test_refresh_removes_projects_and_checks_after_access_is_revoked(self) -> None:
        self.membership.delete()
        data = self.client.get("/?refresh=1&problems=1").json()
        self.assertNotIn(str(self.bobs_project.code), data["projects"])
        self.assertNotIn(str(self.grace.code), [row["code"] for row in data["rows"]])

    def test_legacy_project_refresh_contract_is_preserved(self) -> None:
        data = self.client.get("/?refresh=1").json()
        self.assertEqual(data[str(self.project.code)]["status"], "down")
        self.assertEqual(data[str(self.bobs_project.code)]["status"], "grace")
        self.assertNotIn("rows", data)
        self.assertNotIn("projects", data)

    def test_name_and_schedule_edits_return_to_main_view(self) -> None:
        response = self.client.post(
            f"/checks/{self.down.code}/name/",
            {
                "name": "Renamed job",
                "slug": "renamed-job",
                "tags": "ops",
                "desc": "Notes",
                "return_to": "projects",
            },
        )
        self.assertRedirects(response, "/")
        self.down.refresh_from_db()
        self.assertEqual(self.down.name, "Renamed job")
        response = self.client.post(
            f"/checks/{self.down.code}/timeout/",
            {"kind": "simple", "timeout": 3600, "grace": 300, "return_to": "projects"},
        )
        self.assertRedirects(response, "/")
        self.down.refresh_from_db()
        self.assertEqual(self.down.grace, td(minutes=5))

    def test_ping_url_ambiguity_includes_healthy_services_in_the_same_project(
        self,
    ) -> None:
        self.project.show_slugs = True
        self.project.save()
        Check.objects.filter(pk__in=[self.down.pk, self.healthy.pk]).update(
            slug="duplicate"
        )
        response = self.client.get("/")
        self.assertContains(response, "(not unique)")

    def test_no_problems_state_and_anonymous_access(self) -> None:
        Check.objects.filter(project__in=[self.project, self.bobs_project]).update(
            status="paused", last_start=None
        )
        response = self.client.get("/")
        self.assertEqual(response.context["problem_rows"], [])
        self.assertContains(response, "No services currently have warnings or errors.")
        self.client.logout()
        response = self.client.get("/?refresh=1&problems=1")
        self.assertEqual(response.status_code, 302)

    def test_database_query_count_does_not_grow_per_service(self) -> None:
        self.client.get("/")
        urls = ("/", "/?refresh=1&problems=1")
        counts = []
        for url in urls:
            with CaptureQueriesContext(connection) as queries:
                self.client.get(url)
            counts.append(len(queries))
        for number in range(15):
            Check.objects.create(
                project=self.project, name=f"Failure {number}", status="down"
            )
        for url, count in zip(urls, counts, strict=True):
            with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(queries), count)
