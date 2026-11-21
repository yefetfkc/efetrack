"""Notification center: upcoming and newly released media the user tracks."""

from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from app.models import Episode, MediaTypes, Season, Status
from app.providers import services
from efe.models import NotificationKind, NotificationRead
from events.models import Event, SentinelDatetime

# How many days back newly released content stays in the notification center
RELEASED_DAYS_BACK = 7
# Upcoming releases this close count towards the unread badge
UNREAD_UPCOMING_DAYS = 7
MAX_DROPDOWN_ENTRIES = 8


@dataclass
class Entry:
    """A notification center entry built from a calendar event."""

    event: Event
    kind: str
    unread: bool

    @property
    def item(self):
        """Return the event item."""
        return self.event.item

    @property
    def is_released(self):
        """Return whether the content is already out."""
        return self.kind == NotificationKind.RELEASED.value

    @property
    def can_watch(self):
        """Return whether this is a released TV episode that can be marked as watched."""
        return (
            self.is_released
            and self.item.media_type == MediaTypes.SEASON.value
            and self.event.content_number is not None
        )

    @property
    def has_time(self):
        """Return whether the release time of day is known."""
        return not self.event.is_sentinel_time

    @property
    def local_datetime(self):
        """Return the release datetime in the active timezone."""
        return timezone.localtime(self.event.datetime)


@dataclass
class NotificationCenter:
    """Grouped notification center entries for a user."""

    released: list = field(default_factory=list)
    today: list = field(default_factory=list)
    upcoming: list = field(default_factory=list)

    @property
    def entries(self):
        """Return every entry, most relevant first."""
        return [*self.today, *self.released, *self.upcoming]

    @property
    def unread_count(self):
        """Return how many entries are unread."""
        return sum(1 for entry in self.entries if entry.unread)

    @property
    def upcoming_by_day(self):
        """Return upcoming entries grouped by local release date."""
        days = {}
        for entry in self.upcoming:
            days.setdefault(entry.local_datetime.date(), []).append(entry)
        return days

    def dropdown_entries(self):
        """Return the entries shown in the top bar bell menu."""
        unread = [entry for entry in self.entries if entry.unread]
        read = [entry for entry in self.entries if not entry.unread]
        return [*unread, *read][:MAX_DROPDOWN_ENTRIES]


def _window_events(user, now):
    """Return the user's events inside the notification center window."""
    today = timezone.localdate(now)
    first_day = today - timedelta(days=RELEASED_DAYS_BACK)
    last_day = today + timedelta(days=user.notification_center_days)
    events = Event.objects.get_user_events(user, first_day, last_day)
    muted = user.notification_excluded_items.values_list("id", flat=True)
    return [
        event
        for event in events.exclude(item_id__in=list(muted))
        if event.datetime != SentinelDatetime.max_datetime()
    ]


def build(user, now=None):
    """Build the notification center for a user."""
    now = now or timezone.now()
    events = _window_events(user, now)
    read = set(
        NotificationRead.objects.filter(
            user=user,
            event__in=events,
        ).values_list("event_id", "kind"),
    )
    today = timezone.localdate(now)
    unread_until = now + timedelta(days=UNREAD_UPCOMING_DAYS)

    center = NotificationCenter()
    for event in events:
        released = event.datetime <= now
        kind = (
            NotificationKind.RELEASED.value
            if released
            else NotificationKind.UPCOMING.value
        )
        counts_as_new = released or event.datetime <= unread_until
        entry = Entry(
            event=event,
            kind=kind,
            unread=counts_as_new and (event.id, kind) not in read,
        )
        if timezone.localtime(event.datetime).date() == today:
            center.today.append(entry)
        elif released:
            center.released.append(entry)
        else:
            center.upcoming.append(entry)

    center.released.sort(key=lambda entry: entry.event.datetime, reverse=True)
    return center


def mark_read(user, center):
    """Mark every unread entry of the notification center as read."""
    NotificationRead.objects.bulk_create(
        [
            NotificationRead(user=user, event=entry.event, kind=entry.kind)
            for entry in center.entries
            if entry.unread
        ],
        ignore_conflicts=True,
    )
    user.notification_center_seen_at = timezone.now()
    user.save(update_fields=["notification_center_seen_at"])


def watched_episodes(user, entries):
    """Return the event ids of watchable entries whose episode the user already watched."""
    watchable = [entry for entry in entries if entry.can_watch]
    if not watchable:
        return set()
    done = set(
        Episode.objects.filter(
            related_season__user=user,
            related_season__item__in=[entry.item for entry in watchable],
        ).values_list("related_season__item_id", "item__episode_number"),
    )
    return {
        entry.event.id
        for entry in watchable
        if (entry.item.id, entry.event.content_number) in done
    }


WATCH_LOCK_SECONDS = 60


def watch(user, event_id, now=None, score=None, notes=""):
    """Mark the episode of a released notification as watched and the entry as read.

    Only entries currently in the user's notification center can be marked, so a
    request cannot touch other users' or untracked media. An optional score (0-10)
    and note are stored on the episode; if it was already watched they are added to
    the latest watch. Returns "watched", "already" (watched before, nothing new to
    save), "updated" (watched before, score/note saved), "pending" (the same
    request is still running) or None (no such watchable entry).
    """
    now = now or timezone.now()
    entry = next((e for e in build(user, now).entries if e.event.id == event_id), None)
    if entry is None or not entry.can_watch:
        return None

    # Two clicks at once must not record the episode twice (the server runs several workers).
    lock = f"efe-watch:{user.id}:{event_id}"
    if not cache.add(lock, 1, WATCH_LOCK_SECONDS):
        return "pending"
    try:
        item = entry.item
        # Fetch the metadata the episode save needs before writing anything: if the
        # provider is unreachable nothing is changed, and the database is not held
        # locked while waiting on the network (the results are cached).
        for media_type in (MediaTypes.SEASON.value, "tv_with_seasons"):
            services.get_media_metadata(media_type, item.media_id, item.source, [item.season_number])

        number = entry.event.content_number
        with transaction.atomic():
            season = Season.objects.filter(item=item, user=user).first()
            if season is None:
                season = Season.objects.create(
                    item=item,
                    user=user,
                    score=None,
                    status=Status.IN_PROGRESS.value,
                    notes="",
                )
            latest = (
                Episode.objects.filter(related_season=season, item__episode_number=number)
                .order_by(F("end_date").desc(nulls_last=True), "-created_at")
                .first()
            )
            if latest is None:
                Episode.objects.create(
                    related_season=season,
                    item=season.get_episode_item(number),
                    end_date=now,
                    notes=notes,
                    score=score,
                )
                result = "watched"
            elif score is not None or notes:
                changes = {}
                if notes:
                    # Never overwrite an earlier note: add the new one below it.
                    changes["notes"] = f"{latest.notes}\n{notes}" if latest.notes else notes
                if score is not None:
                    changes["score"] = score
                Episode.objects.filter(id=latest.id).update(**changes)
                result = "updated"
            else:
                result = "already"
            NotificationRead.objects.get_or_create(user=user, event=entry.event, kind=entry.kind)
    finally:
        cache.delete(lock)
    return result
