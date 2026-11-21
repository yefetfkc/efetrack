"""Mark several episodes of a season at once (efeTrack).

* "Up to here": every unwatched, aired episode up to and including one episode,
  watched now.
* "Spread": every unwatched, aired episode of the season, with watch dates spread
  evenly over a past period (last week, last month…), so statistics and weekly
  summaries are not piled onto today.

Everything is written in one transaction and offered for "Undo".
"""

from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from app.models import Episode, Item, MediaTypes, Season, Sources, Status
from app.providers import services

EVENING = time(21, 0)

PERIODS = (
    ("last_week", "Geçen hafta"),
    ("last_month", "Geçen ay"),
    ("last_3_months", "Son 3 ay"),
    ("last_year", "Geçen yıl"),
    ("custom", "Tarih aralığı seç"),
)


class BulkWatchError(ValueError):
    """A request that cannot be done (shown to the user)."""


def period_range(period, today=None, start=None, end=None):
    """Return (first day, last day) of a named past period."""
    today = today or timezone.localdate()
    if period == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if period == "last_3_months":
        return today - timedelta(days=90), today - timedelta(days=1)
    if period == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if period == "custom":
        if not start or not end:
            raise BulkWatchError("Başlangıç ve bitiş tarihini seç.")
        if start > end:
            raise BulkWatchError("Başlangıç tarihi bitişten sonra olamaz.")
        if end > today:
            raise BulkWatchError("Gelecekteki bir tarih seçilemez.")
        return start, end
    raise BulkWatchError("Geçersiz dönem.")


def _to_date(value):
    """A date from a TMDB date string, a date or a datetime; None if unknown."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value or "")
    except (TypeError, ValueError):
        return None


def _air_date(episode):
    return _to_date(episode.get("air_date"))


def season_for(user, media_id, source, season_number):
    """The user's season, created (in progress) if it is not tracked yet."""
    season = Season.objects.filter(
        item__media_id=media_id,
        item__source=source,
        item__season_number=season_number,
        item__episode_number=None,
        user=user,
    ).first()
    if season is not None:
        return season
    metadata = services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])
    season_metadata = metadata[f"season/{season_number}"]
    item, _ = Item.objects.get_or_create(
        media_id=media_id,
        source=source if source == Sources.MANUAL.value else Sources.TMDB.value,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
        defaults={"title": metadata["title"], "image": season_metadata["image"]},
    )
    return Season.objects.create(item=item, user=user, score=None, status=Status.IN_PROGRESS.value, notes="")


def _season_episodes(media_id, source, season_number):
    metadata = services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])
    episodes = metadata[f"season/{season_number}"].get("episodes") or []
    return sorted(
        (e for e in episodes if e.get("episode_number") is not None),
        key=lambda e: e["episode_number"],
    )


def _watched_numbers(season):
    if season is None:
        return set()
    return set(Episode.objects.filter(related_season=season).values_list("item__episode_number", flat=True))


def _unwatched_aired(episodes, watched, until):
    """Episode numbers not watched yet that aired on or before ``until``."""
    numbers = []
    for episode in episodes:
        aired = _air_date(episode)
        if episode["episode_number"] in watched or aired is None or aired > until:
            continue
        numbers.append(episode["episode_number"])
    return numbers


def upto_counts(episodes, today=None):
    """For a season page: {episode number: how many episodes "up to here" would mark}."""
    today = today or timezone.localdate()
    counts = {}
    pending = 0
    for episode in sorted(episodes, key=lambda e: e["episode_number"]):
        aired = _air_date(episode)
        if not episode.get("history") and aired is not None and aired <= today:
            pending += 1
        counts[episode["episode_number"]] = pending
    return counts


def _write(season, plan):
    """Create the watches [(episode number, aware datetime)] in one transaction."""
    created = []
    with transaction.atomic():
        for number, when in plan:
            season.watch(number, when)
            created.append(
                Episode.objects.filter(related_season=season, item__episode_number=number)
                .order_by("-created_at", "-id")
                .first(),
            )
    return created


def mark_up_to(user, media_id, source, season_number, episode_number, now=None):
    """Watch every unwatched, aired episode up to ``episode_number`` (now)."""
    now = now or timezone.now()
    episodes = _season_episodes(media_id, source, season_number)
    existing = Season.objects.filter(
        item__media_id=media_id, item__source=source, item__season_number=season_number, user=user
    ).first()
    numbers = [
        n for n in _unwatched_aired(episodes, _watched_numbers(existing), timezone.localdate(now))
        if n <= episode_number
    ]
    if not numbers:
        raise BulkWatchError("İşaretlenecek izlenmemiş bölüm yok.")
    season = existing or season_for(user, media_id, source, season_number)
    # A second apart keeps the order in history and statistics.
    plan = [(number, now - timedelta(seconds=len(numbers) - index)) for index, number in enumerate(numbers)]
    return _write(season, plan)


def spread_plan(numbers, first_day, last_day, air_dates=None, now=None):
    """Evening watch times spread evenly over the days, in episode order (never in the future)."""
    air_dates = air_dates or {}
    now = now or timezone.now()
    days = (last_day - first_day).days + 1
    plan = []
    previous = None
    for index, number in enumerate(numbers):
        day = first_day + timedelta(days=(index * days) // len(numbers))
        aired = air_dates.get(number)
        if aired and day < aired:
            day = aired  # never before the episode aired
        when = timezone.make_aware(datetime.combine(day, EVENING)) + timedelta(minutes=index)
        if previous and when <= previous:
            when = previous + timedelta(minutes=1)
        plan.append((number, when))
        previous = when
    # A period ending today: evening times may still be ahead; keep them just before now.
    total = len(plan)
    return [
        (number, when if when <= now else now - timedelta(minutes=total - index))
        for index, (number, when) in enumerate(plan)
    ]


def spread(user, media_id, source, season_number, first_day, last_day):
    """Watch every unwatched episode that aired by ``last_day``, spread over the period."""
    episodes = _season_episodes(media_id, source, season_number)
    existing = Season.objects.filter(
        item__media_id=media_id, item__source=source, item__season_number=season_number, user=user
    ).first()
    numbers = _unwatched_aired(episodes, _watched_numbers(existing), last_day)
    if not numbers:
        raise BulkWatchError("Bu dönemde yayınlanmış izlenmemiş bölüm yok.")
    air_dates = {e["episode_number"]: _air_date(e) for e in episodes}
    season = existing or season_for(user, media_id, source, season_number)
    return _write(season, spread_plan(numbers, first_day, last_day, air_dates))
