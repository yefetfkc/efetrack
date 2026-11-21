"""Status panel: background tasks, calendar refresh, backups, offline copy and errors."""

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone
from django_celery_results.models import TaskResult

# Scheduled tasks (config/settings.py CELERY_BEAT_SCHEDULE) with Turkish names
# and how often they should run (seconds); late if not run for twice as long.
SCHEDULED = (
    ("Reload calendar", "Takvim ve bölüm tarihlerini güncelleme", 24 * 3600),
    ("Send release notifications", "Yeni bölüm bildirimleri", 10 * 60),
    ("Send daily digest", "Günlük özet", 24 * 3600),
    ("Cleanup user messages", "Eski mesajları temizleme", 24 * 3600),
    ("Send weekly preview", "Haftalık ön izleme (pazartesi)", 7 * 24 * 3600),
    ("Check release date changes", "Yayın tarihi değişikliği kontrolü", 6 * 3600),
    ("Check new seasons", "Yeni sezon kontrolü", 24 * 3600),
    ("Check completed shows", "Biten dizi kartı", 10 * 60),
    ("Send morning suggestion", "Sabah önerisi (11:00)", 24 * 3600),
    ("Send monthly report", "Aylık PDF raporu", 31 * 24 * 3600),
    ("Check film series news", "Film serisi haberleri", 24 * 3600),
    ("Check new trailers", "Yeni fragman kontrolü", 24 * 3600),
)
FINISHED = ("SUCCESS", "FAILURE", "REVOKED")
ACTIVE = ("PENDING", "RECEIVED", "STARTED", "RETRY")
ERROR_LINE = re.compile(r"\b(ERROR|CRITICAL|Traceback)\b")
TAIL_BYTES = 256 * 1024
MAX_ERRORS = 12


@dataclass
class TaskRow:
    name: str
    last_run: datetime | None
    ok: bool | None
    late: bool
    detail: str = ""
    off: str = ""  # why the task has nothing to do (then it is never "late")
    running: bool = False


@dataclass
class FolderInfo:
    path: str
    count: int = 0
    size: int = 0
    newest: datetime | None = None


@dataclass
class Status:
    tasks: list = field(default_factory=list)
    running: list = field(default_factory=list)
    calendar_last: datetime | None = None
    backups: FolderInfo | None = None
    offline: FolderInfo | None = None
    database_size: int | None = None
    errors: list = field(default_factory=list)
    log_dir: str | None = None
    tmdb_ok: bool | None = None

    @property
    def problems(self):
        """Short list of things that need attention."""
        items = [f"{row.name} gecikti" for row in self.tasks if row.late]
        items += [f"{row.name} son çalışmada hata verdi" for row in self.tasks if row.ok is False]
        if self.tmdb_ok is False:
            items.append("TMDB'ye ulaşılamıyor (çevrimdışı mod devrede)")
        if self.backups is not None and (
            self.backups.newest is None or (timezone.now() - self.backups.newest).days > 8
        ):
            items.append("Son yedek 8 günden eski")
        return items


def _digest_off():
    """The daily digest only sends to external notification addresses (Apprise)."""
    from users.models import User  # noqa: PLC0415

    has_target = User.objects.filter(daily_digest_enabled=True).exclude(notification_urls="").exclude(
        notification_urls__isnull=True
    )
    return "" if has_target.exists() else "Kapalı: bildirim adresi (Telegram, Discord…) tanımlı değil"


def _cleanup_off(now):
    """Nothing to clean while there are no old messages."""
    from app.models import UserMessage  # noqa: PLC0415

    cutoff = now - timedelta(days=getattr(settings, "USER_MESSAGE_RETENTION_DAYS", 30) + 1)
    return "" if UserMessage.objects.filter(created_at__lt=cutoff).exists() else "Gerekmedi: silinecek eski mesaj yok"


def _off_reason(task_name, now):
    if task_name in ("Send daily digest", "Send weekly preview", "Send morning suggestion", "Send monthly report"):
        return _digest_off()
    if task_name == "Cleanup user messages":
        return _cleanup_off(now)
    return ""


def _tasks(now):
    rows = []
    for task_name, label, every in SCHEDULED:
        results = TaskResult.objects.filter(task_name=task_name)
        # Queued/running results are written on publish: judge by the last finished run.
        last = results.filter(status__in=FINISHED).order_by("-date_done").first()
        running = results.filter(status__in=ACTIVE).exists()
        when = last.date_done if last else None
        off = _off_reason(task_name, now)
        # A weekly task that never ran is not late yet (first Monday may still be ahead).
        never_ok = when is None and every >= 7 * 24 * 3600
        late = not off and not running and not never_ok and (when is None or (now - when).total_seconds() > every * 2)
        ok = None if last is None else last.status == "SUCCESS"
        detail = ""
        if last is not None and not ok:
            detail = (last.result or "")[:300]
        rows.append(TaskRow(name=label, last_run=when, ok=ok, late=late, detail=detail, off=off, running=running))
    return rows


def _running():
    return list(
        TaskResult.objects.filter(status__in=ACTIVE)
        .order_by("-date_created")
        .values_list("task_name", "date_created")[:10],
    )


def _folder(path, pattern="*"):
    if not path:
        return None
    folder = Path(path)
    info = FolderInfo(path=str(folder))
    if not folder.is_dir():
        return info
    newest = None
    for item in folder.glob(pattern):
        if not item.is_file():
            continue
        stat = item.stat()
        info.count += 1
        info.size += stat.st_size
        if newest is None or stat.st_mtime > newest:
            newest = stat.st_mtime
    info.newest = datetime.fromtimestamp(newest, tz=UTC) if newest else None
    return info


def _errors(log_dir):
    """Latest error lines of the server logs, newest first."""
    if not log_dir or not Path(log_dir).is_dir():
        return []
    found = []
    for log in sorted(Path(log_dir).glob("*.log")):
        try:
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - TAIL_BYTES))
                lines = handle.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        found += [(log.stem, line.strip()[:400]) for line in lines if ERROR_LINE.search(line)]
    return found[-MAX_ERRORS:][::-1]


def _tmdb_reachable():
    try:
        response = requests.get(
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": settings.TMDB_API},
            timeout=3,
        )
    except requests.exceptions.RequestException:
        return False
    return response.status_code == requests.codes.ok


def build(check_network=True):
    """Collect everything the status panel shows."""
    now = timezone.now()
    data_dir = getattr(settings, "DATA_DIR", None)
    log_dir = os.environ.get("EFETRACK_LOG_DIR")
    tasks = _tasks(now)
    database = Path(settings.DATABASES["default"]["NAME"])
    return Status(
        tasks=tasks,
        running=_running(),
        calendar_last=tasks[0].last_run,
        backups=_folder(Path(data_dir) / "Yedekler", "efetrack-yedek-*.sqlite3") if data_dir else None,
        offline=_folder(getattr(settings, "EFE_OFFLINE_DIR", None), "*.djcache"),
        database_size=database.stat().st_size if database.is_file() else None,
        errors=_errors(log_dir),
        log_dir=log_dir,
        tmdb_ok=_tmdb_reachable() if check_network else None,
    )
