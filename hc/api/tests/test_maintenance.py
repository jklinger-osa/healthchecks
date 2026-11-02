from __future__ import annotations

from datetime import UTC, datetime, time
from unittest.mock import Mock

import time_machine
from django.core.exceptions import ValidationError

from hc.accounts.models import Project
from hc.api.management.commands.sendalerts import Command, notify
from hc.api.models import Check, Flip, MaintenanceWindow
from hc.test import BaseTestCase


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


class MaintenanceTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.check = Check.objects.create(project=self.project, status="down")
        self.window = MaintenanceWindow.objects.create(
            project=self.project,
            name="Nightly",
            kind="weekly",
            tz="UTC",
            start_time=time(5),
            end_time=time(5, 30),
            weekdays=list(range(7)),
        )
        self.window.checks.add(self.check)

    def test_boundaries_are_start_inclusive_and_end_exclusive(self) -> None:
        self.assertIsNone(self.check.maintenance_until(instant("2026-11-02T04:59:59")))
        self.assertEqual(
            self.check.maintenance_until(instant("2026-11-02T05:00:00")),
            instant("2026-11-02T05:30:00"),
        )
        self.assertIsNone(self.check.maintenance_until(instant("2026-11-02T05:30:00")))

    def test_disabled_window_does_not_mute(self) -> None:
        self.window.enabled = False
        self.window.save()
        self.assertIsNone(self.check.maintenance_until(instant("2026-11-02T05:10:00")))

    def test_multiple_checks_can_share_a_window(self) -> None:
        second = Check.objects.create(project=self.project)
        self.window.checks.add(second)
        self.assertIsNotNone(second.maintenance_until(instant("2026-11-02T05:10:00")))

    def test_overlapping_windows_extend_suppression(self) -> None:
        second = MaintenanceWindow.objects.create(
            project=self.project,
            name="Longer",
            kind="one_time",
            starts_at=instant("2026-11-02T05:20:00"),
            ends_at=instant("2026-11-02T06:00:00"),
        )
        second.checks.add(self.check)
        self.assertEqual(
            self.check.maintenance_until(instant("2026-11-02T05:25:00")),
            instant("2026-11-02T06:00:00"),
        )

    def test_overnight_window_uses_start_days_weekday(self) -> None:
        self.window.start_time, self.window.end_time = time(23), time(1)
        self.window.weekdays = [0]
        self.window.save()
        self.assertIsNotNone(
            self.check.maintenance_until(instant("2026-11-03T00:30:00"))
        )
        self.assertIsNone(self.check.maintenance_until(instant("2026-11-04T00:30:00")))

    def test_timezone_is_applied(self) -> None:
        self.window.tz = "Europe/Berlin"
        self.window.save()
        self.assertEqual(
            self.check.maintenance_until(instant("2026-11-02T04:10:00")),
            instant("2026-11-02T04:30:00"),
        )

    def test_repeated_dst_hour_remains_muted_in_both_occurrences(self) -> None:
        self.window.tz = "Europe/Berlin"
        self.window.start_time, self.window.end_time = time(2), time(3)
        self.window.save()
        for value in ("2026-10-25T00:30:00", "2026-10-25T01:30:00"):
            self.assertEqual(
                self.check.maintenance_until(instant(value)),
                instant("2026-10-25T02:00:00"),
            )

    def test_spring_dst_gap_does_not_extend_window_past_end(self) -> None:
        self.window.tz = "Europe/Berlin"
        self.window.start_time, self.window.end_time = time(1), time(4)
        self.window.save()
        self.assertEqual(
            self.check.maintenance_until(instant("2026-03-29T01:30:00")),
            instant("2026-03-29T02:00:00"),
        )
        self.assertIsNone(self.check.maintenance_until(instant("2026-03-29T02:00:00")))

    def test_cross_project_assignment_cannot_mute_a_check(self) -> None:
        self.window.project = self.bobs_project
        self.window.save()
        self.assertIsNone(self.check.maintenance_until(instant("2026-11-02T05:10:00")))

    def test_invalid_schedule_is_rejected(self) -> None:
        self.window.end_time = self.window.start_time
        with self.assertRaises(ValidationError):
            self.window.full_clean()

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_failure_is_durable_and_sent_at_window_end_without_a_ping(self) -> None:
        flip = Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T05:10:00"),
            old_status="up",
            new_status="down",
        )
        worker = Command()
        worker.executor = Mock()
        self.assertTrue(worker.process_one_flip())
        flip.refresh_from_db()
        self.assertIsNone(flip.processed)
        self.assertEqual(flip.deferred_until, instant("2026-11-02T05:30:00"))
        self.assertFalse(worker.process_one_flip())
        worker.executor.submit.assert_not_called()
        with time_machine.travel("2026-11-02T05:30:00Z", tick=False):
            restarted_worker = Command()
            restarted_worker.executor = Mock()
            self.assertTrue(restarted_worker.process_one_flip())
            restarted_worker.executor.submit.assert_called_once()
        flip.refresh_from_db()
        self.assertIsNotNone(flip.processed)
        self.assertIsNone(flip.deferred_until)
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, "down")

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_failure_and_recovery_inside_window_send_nothing(self) -> None:
        down = Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T05:10:00"),
            old_status="up",
            new_status="down",
        )
        worker = Command()
        worker.executor = Mock()
        worker.process_one_flip()
        self.check.status, self.check.last_ping = "up", instant("2026-11-02T05:20:00")
        self.check.save()
        up = Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T05:20:00"),
            old_status="down",
            new_status="up",
        )
        with time_machine.travel("2026-11-02T05:20:00Z", tick=False):
            worker.process_one_flip()
        with time_machine.travel("2026-11-02T05:30:00Z", tick=False):
            worker.process_one_flip()
            worker.process_one_flip()
        worker.executor.submit.assert_not_called()
        for flip in (down, up):
            flip.refresh_from_db()
            self.assertIsNotNone(flip.processed)

    @time_machine.travel("2026-11-02T05:20:00Z", tick=False)
    def test_previously_notified_incident_gets_deferred_recovery(self) -> None:
        Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T04:50:00"),
            processed=instant("2026-11-02T04:50:00"),
            old_status="up",
            new_status="down",
        )
        self.check.status, self.check.last_ping = "up", instant("2026-11-02T05:20:00")
        self.check.save()
        Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T05:20:00"),
            old_status="down",
            new_status="up",
        )
        worker = Command()
        worker.executor = Mock()
        worker.process_one_flip()
        with time_machine.travel("2026-11-02T05:30:00Z", tick=False):
            worker.process_one_flip()
        worker.executor.submit.assert_called_once()

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_dispatch_rechecks_maintenance_before_sending(self) -> None:
        flip = Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T05:10:00"),
            processed=instant("2026-11-02T05:10:00"),
            old_status="up",
            new_status="down",
        )
        self.assertIsNone(notify(flip))
        flip.refresh_from_db()
        self.assertIsNone(flip.processed)
        self.assertEqual(flip.deferred_until, instant("2026-11-02T05:30:00"))

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_down_reminders_are_muted_during_maintenance(self) -> None:
        self.check.last_ping = instant("2026-11-02T05:00:00")
        self.check.save()
        self.assertFalse(self.profile.send_report(nag=True))
        with time_machine.travel("2026-11-02T05:30:00Z", tick=False):
            self.assertTrue(self.profile.send_report(nag=True))

    def test_project_wide_window_covers_new_checks_without_assignment(self) -> None:
        self.window.all_checks = True
        self.window.save()
        self.window.checks.clear()
        second = Check.objects.create(project=self.project, name="New service")
        self.assertEqual(
            second.maintenance_until(instant("2026-11-02T05:10:00")),
            instant("2026-11-02T05:30:00"),
        )
        foreign = Check.objects.create(project=self.bobs_project)
        self.assertIsNone(foreign.maintenance_until(instant("2026-11-02T05:10:00")))

    def test_selected_service_window_does_not_mute_other_services(self) -> None:
        other = Check.objects.create(project=self.project, name="Unrelated service")
        self.assertIsNotNone(
            self.check.maintenance_until(instant("2026-11-02T05:10:00"))
        )
        self.assertIsNone(other.maintenance_until(instant("2026-11-02T05:10:00")))


class GlobalMaintenanceTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.check = Check.objects.create(
            project=self.project,
            status="down",
            last_ping=instant("2026-11-02T05:00:00"),
        )
        self.other = Check.objects.create(project=self.bobs_project, status="down")
        self.window = MaintenanceWindow.objects.create(
            name="Server update",
            kind="one_time",
            starts_at=instant("2026-11-02T05:00:00"),
            ends_at=instant("2026-11-02T06:00:00"),
        )

    def test_it_covers_current_and_future_projects_and_checks(self) -> None:
        project = Project.objects.create(owner=self.alice, badge_key="new-project")
        future = Check.objects.create(project=project)
        for check in (self.check, self.other, future):
            self.assertEqual(
                check.maintenance_until(instant("2026-11-02T05:10:00")),
                self.window.ends_at,
            )
            self.assertIsNone(check.maintenance_until(instant("2026-11-02T06:00:00")))

    def test_it_survives_project_deletion(self) -> None:
        self.project.delete()
        self.window.refresh_from_db()
        self.assertEqual(
            self.other.maintenance_until(instant("2026-11-02T05:10:00")),
            self.window.ends_at,
        )

    def test_global_windows_must_be_one_time(self) -> None:
        self.window.kind = "weekly"
        with self.assertRaises(ValidationError):
            self.window.full_clean()

    def test_global_and_service_windows_combine(self) -> None:
        local = MaintenanceWindow.objects.create(
            project=self.project,
            name="Longer service update",
            kind="one_time",
            starts_at=instant("2026-11-02T05:30:00"),
            ends_at=instant("2026-11-02T07:00:00"),
        )
        local.checks.add(self.check)
        self.assertEqual(
            self.check.maintenance_until(instant("2026-11-02T05:45:00")), local.ends_at
        )
        self.assertEqual(
            self.other.maintenance_until(instant("2026-11-02T05:45:00")),
            self.window.ends_at,
        )

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_global_alerts_are_deferred_durably_and_sent_after_restart(self) -> None:
        flips = [
            Flip.objects.create(
                owner=check,
                created=instant("2026-11-02T05:10:00"),
                old_status="up",
                new_status="down",
            )
            for check in (self.check, self.other)
        ]
        worker = Command()
        worker.executor = Mock()
        worker.process_one_flip()
        worker.process_one_flip()
        worker.executor.submit.assert_not_called()
        for flip in flips:
            flip.refresh_from_db()
            self.assertIsNone(flip.processed)
            self.assertEqual(flip.deferred_until, self.window.ends_at)
        with time_machine.travel("2026-11-02T06:00:00Z", tick=False):
            restarted = Command()
            restarted.executor = Mock()
            restarted.process_one_flip()
            restarted.process_one_flip()
            self.assertEqual(restarted.executor.submit.call_count, 2)
        for flip in flips:
            flip.refresh_from_db()
            self.assertIsNotNone(flip.processed)

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_dispatch_sees_global_windows_created_after_initial_check(self) -> None:
        self.window.enabled = False
        self.window.save()
        self.assertIsNone(self.check.maintenance_until())
        self.window.enabled = True
        self.window.save()
        flip = Flip.objects.create(
            owner=self.check,
            created=instant("2026-11-02T05:10:00"),
            old_status="up",
            new_status="down",
            processed=instant("2026-11-02T05:10:00"),
        )
        self.assertIsNone(notify(flip))
        flip.refresh_from_db()
        self.assertIsNone(flip.processed)
        self.assertEqual(flip.deferred_until, self.window.ends_at)

    @time_machine.travel("2026-11-02T05:10:00Z", tick=False)
    def test_global_windows_suppress_down_reminders(self) -> None:
        self.assertFalse(self.profile.send_report(nag=True))
        self.window.enabled = False
        self.window.save()
        self.assertTrue(self.profile.send_report(nag=True))
