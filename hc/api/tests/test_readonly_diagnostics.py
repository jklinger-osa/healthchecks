from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils.timezone import now

from hc.api.models import Channel, Check, Ping
from hc.lib.s3 import GetObjectError
from hc.test import BaseTestCase


@override_settings(SITE_ROOT="http://testserver")
class ReadOnlyDiagnosticsTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project.api_key_readonly = "R" * 32
        self.project.save()
        self.check = Check.objects.create(
            project=self.project, name="Backup", n_pings=3
        )
        for number in range(1, 4):
            Ping.objects.create(
                owner=self.check,
                n=number,
                created=now() - timedelta(minutes=3 - number),
                body_raw=b"Latest diagnostic output" if number == 3 else None,
                ua="monitoring-agent",
            )
        self.other = Check.objects.create(
            project=self.bobs_project, name="Private", n_pings=1
        )
        Ping.objects.create(
            owner=self.other, n=1, body_raw=b"Private diagnostic output"
        )
        self.channel = Channel.objects.create(
            project=self.project,
            name="Operations",
            kind="webhook",
            value='{"url":"https://example.org/private-token"}',
        )
        Channel.objects.create(
            project=self.bobs_project, name="Private integration", kind="email"
        )

    def test_agent_can_discover_checks_read_pings_and_follow_body_urls(self) -> None:
        for hashed in (False, True):
            key = self.project.set_api_key_readonly() if hashed else "R" * 32
            if not hashed:
                self.project.api_key_readonly = key
            self.project.save()
            for version in (1, 2, 3):
                with self.subTest(hashed=hashed, version=version):
                    response = self.client.get(
                        f"/api/v{version}/checks/?fields=name,status,unique_key",
                        HTTP_X_API_KEY=key,
                    )
                    self.assertEqual(response.status_code, 200)
                    check = response.json()["checks"][0]
                    self.assertNotIn("uuid", check)
                    self.assertEqual(check["unique_key"], self.check.unique_key)
                    url = f"/api/v{version}/checks/{check['unique_key']}/pings/"
                    with self.assertNumQueries(5):
                        response = self.client.get(url, HTTP_X_API_KEY=key)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, str(self.check.code))
                    self.assertEqual(response["Access-Control-Allow-Origin"], "*")
                    pings = response.json()["pings"]
                    self.assertEqual([ping["n"] for ping in pings], [3, 2, 1])
                    self.assertIsNone(pings[1]["body_url"])
                    body_url = pings[0]["body_url"]
                    self.assertEqual(
                        body_url,
                        f"http://testserver/api/v{version}/checks/{self.check.unique_key}/pings/3/body",
                    )
                    body = self.client.get(body_url, HTTP_X_API_KEY=key)
                    self.assertEqual(body.status_code, 200)
                    self.assertEqual(body["Content-Type"], "text/plain")
                    self.assertEqual(body.content, b"Latest diagnostic output")

    def test_readonly_uuid_requests_also_return_unique_key_body_urls(self) -> None:
        response = self.client.get(
            f"/api/v3/checks/{self.check.code}/pings/", HTTP_X_API_KEY="R" * 32
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(self.check.code))
        self.assertIn(self.check.unique_key, response.json()["pings"][0]["body_url"])

    def test_readwrite_unique_key_requests_preserve_uuid_body_urls(self) -> None:
        response = self.client.get(
            f"/api/v3/checks/{self.check.unique_key}/pings/", HTTP_X_API_KEY="X" * 32
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["pings"][0]["body_url"],
            f"http://testserver/api/v3/checks/{self.check.code}/pings/3/body",
        )
        response = self.client.get(
            f"/api/v3/checks/{self.check.unique_key}/pings/3/body",
            HTTP_X_API_KEY="X" * 32,
        )
        self.assertEqual(response.content, b"Latest diagnostic output")

    def test_readonly_access_cannot_cross_projects(self) -> None:
        for version in (1, 2, 3):
            for identifier, status in (
                (str(self.other.code), 403),
                (self.other.unique_key, 404),
            ):
                for suffix in ("pings/", "pings/1/body"):
                    with self.subTest(
                        version=version, identifier=identifier, suffix=suffix
                    ):
                        response = self.client.get(
                            f"/api/v{version}/checks/{identifier}/{suffix}",
                            HTTP_X_API_KEY="R" * 32,
                        )
                        self.assertEqual(response.status_code, status)
                        self.assertNotContains(
                            response, "Private diagnostic output", status_code=status
                        )

    def test_unknown_and_malformed_unique_keys_return_404(self) -> None:
        for identifier in ("0" * 40, "not-a-valid-identifier"):
            for suffix in ("pings/", "pings/3/body"):
                response = self.client.get(
                    f"/api/v3/checks/{identifier}/{suffix}", HTTP_X_API_KEY="R" * 32
                )
                self.assertEqual(response.status_code, 404)

    def test_new_routes_require_a_valid_key_and_only_accept_get(self) -> None:
        for suffix in ("pings/", "pings/3/body"):
            url = f"/api/v3/checks/{self.check.unique_key}/{suffix}"
            for key in ("", "Z" * 32):
                self.assertEqual(
                    self.client.get(url, HTTP_X_API_KEY=key).status_code, 401
                )
            for method in (self.client.post, self.client.delete, self.client.patch):
                self.assertEqual(method(url, HTTP_X_API_KEY="R" * 32).status_code, 405)
            options = self.client.options(url)
            self.assertEqual(options.status_code, 204)
            self.assertIn("GET", options["Access-Control-Allow-Methods"])

    def test_readonly_ping_and_body_access_enforces_retention(self) -> None:
        self.profile.ping_log_limit = 1
        self.profile.save()
        response = self.client.get(
            f"/api/v3/checks/{self.check.unique_key}/pings/", HTTP_X_API_KEY="R" * 32
        )
        self.assertEqual([ping["n"] for ping in response.json()["pings"]], [3])
        Ping.objects.filter(owner=self.check, n=2).update(body_raw=b"Outside retention")
        response = self.client.get(
            f"/api/v3/checks/{self.check.unique_key}/pings/2/body",
            HTTP_X_API_KEY="R" * 32,
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_body_and_external_storage_failures_are_preserved(self) -> None:
        response = self.client.get(
            f"/api/v3/checks/{self.check.unique_key}/pings/2/body",
            HTTP_X_API_KEY="R" * 32,
        )
        self.assertEqual(response.status_code, 404)
        Ping.objects.filter(owner=self.check, n=3).update(object_size=100)
        with patch("hc.api.models.get_object", side_effect=GetObjectError):
            response = self.client.get(
                f"/api/v3/checks/{self.check.unique_key}/pings/3/body",
                HTTP_X_API_KEY="R" * 32,
            )
        self.assertEqual(response.status_code, 503)

    def test_readonly_keys_can_list_only_their_projects_integration_metadata(
        self,
    ) -> None:
        for version in (1, 2, 3):
            response = self.client.get(
                f"/api/v{version}/channels/", HTTP_X_API_KEY="R" * 32
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {
                    "channels": [
                        {
                            "id": str(self.channel.code),
                            "name": "Operations",
                            "kind": "webhook",
                        }
                    ]
                },
            )
            self.assertNotContains(response, "private-token")
            self.assertNotContains(response, "Private integration")

    def test_readonly_keys_still_cannot_mutate_checks(self) -> None:
        for suffix in ("", "pause", "resume"):
            url = f"/api/v3/checks/{self.check.code}" + (f"/{suffix}" if suffix else "")
            self.assertEqual(
                self.client.post(
                    url,
                    {"name": "Modified"},
                    content_type="application/json",
                    HTTP_X_API_KEY="R" * 32,
                ).status_code,
                401,
            )
        self.assertEqual(
            self.client.delete(
                f"/api/v3/checks/{self.check.code}", HTTP_X_API_KEY="R" * 32
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v3/checks/",
                {"name": "New"},
                content_type="application/json",
                HTTP_X_API_KEY="R" * 32,
            ).status_code,
            401,
        )
        self.check.refresh_from_db()
        self.assertEqual(self.check.name, "Backup")
        self.assertEqual(Check.objects.filter(project=self.project).count(), 1)
