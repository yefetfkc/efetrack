import json
import os
from datetime import timedelta
from unittest import mock

from celery import states
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django_celery_results.models import TaskResult

from efe import task_control


def make_task(task_id, status, name="Import from Yamtrack", kwargs="", args="()", created=None):
    task = TaskResult.objects.create(
        task_id=task_id,
        task_name=name,
        status=status,
        task_args=args,
        task_kwargs=kwargs,
    )
    if created:
        TaskResult.objects.filter(id=task.id).update(date_created=created)
    return task


@mock.patch("efe.task_control.current_app.control.revoke")
class CancelTaskTests(TestCase):
    """Queued and running tasks can be cancelled from the import history and the calendar."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        self.kwargs = f"{{'file': <InMemoryUploadedFile: a.csv>, 'user_id': {self.user.id}, 'mode': 'new'}}"

    def _cancel(self, task_id, next_url=""):
        return self.client.post(reverse("efe_cancel_task", args=[task_id]), {"next": next_url})

    def test_cancel_queued_task(self, revoke):
        make_task("t1", states.PENDING, kwargs=self.kwargs)
        response = self._cancel("t1", reverse("import_data"))
        self.assertRedirects(response, reverse("import_data"), fetch_redirect_response=False)
        revoke.assert_called_once_with("t1", terminate=True, signal="SIGTERM")
        task = TaskResult.objects.get(task_id="t1")
        self.assertEqual(task.status, states.REVOKED)
        self.assertIn(task_control.CANCELLED, task.result)

    def test_stop_running_task(self, revoke):
        make_task("t2", states.STARTED, name="Reload calendar", args=f"(<SimpleLazyObject: <User: {self.user.username}>>,)")
        self._cancel("t2", reverse("calendar"))
        revoke.assert_called_once_with("t2", terminate=True, signal="SIGTERM")
        self.assertEqual(TaskResult.objects.get(task_id="t2").status, states.REVOKED)

    def test_finished_and_foreign_tasks_are_left_alone(self, revoke):
        make_task("done", states.SUCCESS, kwargs=self.kwargs)
        other = get_user_model().objects.create_user(username="other", password="pw")
        make_task("theirs", states.PENDING, kwargs=f"{{'user_id': {other.id}, 'mode': 'new'}}")
        self._cancel("done")
        self._cancel("theirs")
        self._cancel("missing")
        revoke.assert_not_called()
        self.assertEqual(TaskResult.objects.get(task_id="done").status, states.SUCCESS)
        self.assertEqual(TaskResult.objects.get(task_id="theirs").status, states.PENDING)

    def test_cancel_still_closes_task_when_queue_is_gone(self, revoke):
        revoke.side_effect = OSError("no broker")
        make_task("t3", states.PENDING, kwargs=self.kwargs)
        self._cancel("t3")
        self.assertEqual(TaskResult.objects.get(task_id="t3").status, states.REVOKED)

    def test_unsafe_next_url_is_ignored(self, revoke):
        make_task("t4", states.PENDING, kwargs=self.kwargs)
        response = self._cancel("t4", "https://example.com/")
        self.assertRedirects(response, reverse("import_data"), fetch_redirect_response=False)

    def test_started_task_with_json_args_and_turkish_username(self, revoke):
        self.user.username = "efe_şık"
        self.user.save()
        args = json.dumps(f"(<SimpleLazyObject: <User: {self.user.username}>>,)")
        self.assertIn("\\u", args)
        task = make_task("t6", states.STARTED, name="Reload calendar", args=args)
        self.assertTrue(task_control.owns(task, self.user))
        task.task_args = json.dumps("(1,)")
        self.assertEqual(task_control.owns(task, self.user), self.user.id == 1)

    def test_get_is_not_allowed(self, revoke):
        self.assertEqual(self.client.get(reverse("efe_cancel_task", args=["t1"])).status_code, 405)

    def test_import_history_shows_cancel_and_cancelled(self, revoke):
        make_task("t5", states.PENDING, kwargs=self.kwargs)
        response = self.client.get(reverse("import_data"))
        self.assertContains(response, reverse("efe_cancel_task", args=["t5"]))
        self._cancel("t5")
        response = self.client.get(reverse("import_data"))
        self.assertNotContains(response, reverse("efe_cancel_task", args=["t5"]))
        self.assertContains(response, "efe-badge-cancelled")

    def test_calendar_shows_running_refresh_and_blocks_duplicates(self, revoke):
        args = f"(<SimpleLazyObject: <User: {self.user.username}>>,)"
        make_task("cal", states.STARTED, name="Reload calendar", args=args)
        response = self.client.get(reverse("calendar"))
        self.assertContains(response, reverse("efe_cancel_task", args=["cal"]))
        self.assertContains(response, reverse("reload_calendar_status"))
        with mock.patch("events.tasks.reload_calendar.delay") as delay:
            self.client.post(reverse("reload_calendar"))
            delay.assert_not_called()
        self.assertEqual(self.client.get(reverse("reload_calendar_status")).status_code, 204)
        self._cancel("cal")
        status = self.client.get(reverse("reload_calendar_status"))
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status["HX-Refresh"], "true")


class InterruptedTaskTests(TestCase):
    """The desktop app's queue lives in memory: a restart closes what it left behind."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")

    def test_close_interrupted(self):
        now = timezone.now()
        make_task("old-pending", states.PENDING, created=now - timedelta(hours=2))
        make_task("old-running", states.STARTED, created=now - timedelta(hours=1))
        make_task("old-done", states.SUCCESS, created=now - timedelta(hours=1))
        make_task("new-pending", states.PENDING, created=now + timedelta(seconds=5))
        with mock.patch.dict(os.environ, {"EFETRACK_BROKER_STARTED": str(now.timestamp())}):
            self.assertEqual(task_control.close_interrupted(), 2)
        statuses = dict(TaskResult.objects.values_list("task_id", "status"))
        self.assertEqual(statuses["old-pending"], states.REVOKED)
        self.assertEqual(statuses["old-running"], states.REVOKED)
        self.assertEqual(statuses["old-done"], states.SUCCESS)
        self.assertEqual(statuses["new-pending"], states.PENDING)
        self.assertTrue(task_control.is_interrupted(TaskResult.objects.get(task_id="old-pending")))

    def test_nothing_happens_outside_the_desktop_app(self):
        make_task("old", states.PENDING, created=timezone.now() - timedelta(days=1))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EFETRACK_BROKER_STARTED", None)
            self.assertEqual(task_control.close_interrupted(), 0)
            self.assertFalse(task_control.retry_failed_calendar_refresh())
        self.assertEqual(TaskResult.objects.get(task_id="old").status, states.PENDING)

    @mock.patch("events.tasks.reload_calendar.delay")
    def test_failed_calendar_refresh_is_retried(self, delay):
        env = {"EFETRACK_BROKER_STARTED": str(timezone.now().timestamp())}
        with mock.patch.dict(os.environ, env):
            make_task("ok", states.SUCCESS, name="Reload calendar")
            self.assertFalse(task_control.retry_failed_calendar_refresh())
            make_task("bad", states.FAILURE, name="Reload calendar", created=timezone.now() + timedelta(seconds=1))
            self.assertTrue(task_control.retry_failed_calendar_refresh())
            delay.assert_called_once_with(self.user)

    @mock.patch("events.tasks.reload_calendar.delay")
    def test_cancelled_calendar_refresh_is_not_retried(self, delay):
        task = make_task("mine", states.REVOKED, name="Reload calendar")
        TaskResult.objects.filter(id=task.id).update(result=json.dumps({"exc_message": [task_control.CANCELLED]}))
        with mock.patch.dict(os.environ, {"EFETRACK_BROKER_STARTED": "1"}):
            self.assertFalse(task_control.retry_failed_calendar_refresh())
        delay.assert_not_called()
