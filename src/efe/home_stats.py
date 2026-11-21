"""Home page summary tiles: watched this week, watch streak and upcoming episodes."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.apps import apps
from django.utils import timezone

from app.models import Episode, MediaTypes, Status
from efe import notification_center
from events.models import Event

UPCOMING_DAYS = 7
STREAK_LOOKBACK_DAYS = 400

# Media whose progress date counts as activity (episodes are counted by watch date).
ACTIVITY_MODELS = (
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
)


@dataclass
class HomeStats:
    """Numbers shown in the home page tiles."""

    week_episodes: int = 0
    week_movies: int = 0
    last_week_total: int = 0
    week_days: list = field(default_factory=list)  # [(date, count, is_today, is_future)]
    streak: int = 0
    best_streak: int = 0
    streak_at_risk: bool = False
    recent_days: list = field(default_factory=list)  # [(date, active)] last 7 days
    upcoming_count: int = 0
    upcoming_days: int = UPCOMING_DAYS
    upcoming_next: object = None

    @property
    def week_total(self):
        """Episodes and movies watched this week."""
        return self.week_episodes + self.week_movies

    @property
    def week_change(self):
        """Difference to the same days of last week."""
        return self.week_total - self.last_week_total

    @property
    def week_peak(self):
        """Highest daily count this week (at least 1, for bar heights)."""
        return max([count for _, count, _, _ in self.week_days] + [1])


def _week_start(user, today):
    if getattr(user, "week_start_day", "monday") == "sunday":
        return today - timedelta(days=(today.weekday() + 1) % 7)
    return today - timedelta(days=today.weekday())


def _aware(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def _local_day(value):
    return timezone.localtime(value).date()


def _episode_dates(user, since, until):
    return Episode.objects.filter(
        related_season__user=user,
        end_date__gte=since,
        end_date__lt=until,
    ).values_list("end_date", flat=True)


def _movie_dates(user, since, until):
    movie = apps.get_model("app", MediaTypes.MOVIE.value)
    return movie.objects.filter(
        user=user,
        status=Status.COMPLETED.value,
        end_date__gte=since,
        end_date__lt=until,
    ).values_list("end_date", flat=True)


def _activity_days(user, since, until):
    """Local dates on which something was watched, finished or played to the end.

    Only real watch/finish dates count: progress dates are not used, because imports
    and edits set them to the day of the change, not the day something was watched.
    """
    days = {_local_day(value) for value in _episode_dates(user, since, until)}
    for media_type in ACTIVITY_MODELS:
        model = apps.get_model("app", media_type)
        for value in model.objects.filter(user=user, end_date__gte=since, end_date__lt=until).values_list(
            "end_date",
            flat=True,
        ):
            days.add(_local_day(value))
    return days


def _streaks(days, today):
    """Return (current streak, best streak, current streak needs today)."""
    if not days:
        return 0, 0, False
    anchor = today if today in days else today - timedelta(days=1)
    current = 0
    day = anchor
    while day in days:
        current += 1
        day -= timedelta(days=1)
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return current, max(best, current), current > 0 and today not in days


def build(user, now=None):
    """Compute the home page tiles for a user."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    tomorrow = _aware(today + timedelta(days=1))
    stats = HomeStats()

    # This week, per day, compared with the same days of last week
    start = _week_start(user, today)
    week_from = _aware(start)
    counts = {}
    for value in [*_episode_dates(user, week_from, tomorrow), *_movie_dates(user, week_from, tomorrow)]:
        day = _local_day(value)
        counts[day] = counts.get(day, 0) + 1
    stats.week_episodes = _episode_dates(user, week_from, tomorrow).count()
    stats.week_movies = _movie_dates(user, week_from, tomorrow).count()
    stats.week_days = [
        (day, counts.get(day, 0), day == today, day > today)
        for day in (start + timedelta(days=offset) for offset in range(7))
    ]
    last_from = _aware(start - timedelta(days=7))
    last_until = _aware(today - timedelta(days=6))
    stats.last_week_total = (
        _episode_dates(user, last_from, last_until).count()
        + _movie_dates(user, last_from, last_until).count()
    )

    # Streak of days with any activity
    days = _activity_days(user, _aware(today - timedelta(days=STREAK_LOOKBACK_DAYS)), tomorrow)
    stats.streak, stats.best_streak, stats.streak_at_risk = _streaks(days, today)
    stats.recent_days = [
        (today - timedelta(days=offset), (today - timedelta(days=offset)) in days)
        for offset in range(6, -1, -1)
    ]

    # Upcoming episodes and releases of tracked media in the next days
    center = notification_center.build(user, now)
    # The notification center only looks as far ahead as the user's setting.
    stats.upcoming_days = max(1, min(UPCOMING_DAYS, getattr(user, "notification_center_days", UPCOMING_DAYS)))
    horizon = now + timedelta(days=stats.upcoming_days)
    upcoming = [
        *[entry for entry in center.today if not entry.is_released],
        *[entry for entry in center.upcoming if entry.event.datetime <= horizon],
    ]
    upcoming.sort(key=lambda entry: entry.event.datetime)
    stats.upcoming_count = len(upcoming)
    stats.upcoming_next = upcoming[0] if upcoming else None
    return stats


TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value)


def weekly_summary(user, now=None):
    """Summarize the last full Monday–Sunday week (sent every Monday at midnight)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    week_end = today - timedelta(days=today.weekday())  # this Monday, exclusive
    week_start = week_end - timedelta(days=7)
    since, until = _aware(week_start), _aware(week_end)

    # TV shows with at least one new episode during that week (seasons count once).
    released = {}
    for event in Event.objects.get_user_events(
        user,
        week_start,
        week_end - timedelta(days=1),
    ):
        item = event.item
        if since <= event.datetime < until and item.media_type in TV_TYPES:
            released.setdefault((item.media_id, item.source), item.title)

    return {
        "week_start": week_start.isoformat(),
        "episodes": _episode_dates(user, since, until).count(),
        "movies": _movie_dates(user, since, until).count(),
        "new_releases": len(released),
        "release_titles": sorted(released.values())[:5],
    }
