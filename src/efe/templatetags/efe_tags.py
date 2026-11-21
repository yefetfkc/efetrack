import logging
from datetime import timedelta

from django import template
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from efe import notification_center, vocabulary

register = template.Library()
logger = logging.getLogger(__name__)


@register.filter
def ef_term(value):
    """Translate media data returned in English by providers."""
    return vocabulary.translate(value)


@register.simple_tag
def notification_unread_count(user):
    """Return the unread notification center entries of a user."""
    if not getattr(user, "is_authenticated", False):
        return 0
    try:
        return notification_center.build(user).unread_count
    except Exception:  # noqa: BLE001 - the badge must never break a page
        return 0


@register.filter
def ef_relative_day(value):
    """Return a friendly relative day ("Today", "Tomorrow", "in 3 days")."""
    if value is None:
        return ""
    day = value.date() if hasattr(value, "date") else value
    today = timezone.localdate()
    delta = (day - today).days
    if delta == 0:
        return _("Today")
    if delta == 1:
        return _("Tomorrow")
    if delta == -1:
        return _("Yesterday")
    if delta > 1:
        return ngettext("in %(count)d day", "in %(count)d days", delta) % {
            "count": delta,
        }
    return ngettext("%(count)d day ago", "%(count)d days ago", -delta) % {
        "count": -delta,
    }


@register.filter
def ef_countdown(entry):
    """Return a short countdown for a notification center entry."""
    now = timezone.now()
    released_at = entry.event.datetime
    if released_at <= now:
        return _("Out now")
    if not entry.has_time:
        return ef_relative_day(timezone.localtime(released_at))
    remaining = released_at - now
    if remaining < timedelta(hours=1):
        minutes = max(1, int(remaining.total_seconds() // 60))
        return ngettext(
            "in %(count)d minute",
            "in %(count)d minutes",
            minutes,
        ) % {"count": minutes}
    if remaining < timedelta(hours=24):
        hours = int(remaining.total_seconds() // 3600)
        return ngettext("in %(count)d hour", "in %(count)d hours", hours) % {
            "count": hours,
        }
    return ef_relative_day(timezone.localtime(released_at))


@register.filter
def ef_long_date(value):
    """Return a long localized date (e.g. "Friday, 6 November")."""
    if value is None:
        return ""
    return formats.date_format(value, "l, j F")


@register.filter
def ef_status_class(status):
    """Return Tailwind classes for a media status chip."""
    return {
        "Completed": "bg-emerald-600 text-white",
        "In progress": "bg-indigo-600 text-white",
        "Planning": "bg-sky-600 text-white",
        "Paused": "bg-amber-600 text-white",
        "Dropped": "bg-red-600 text-white",
    }.get(status, "bg-gray-600 text-ef-fg")


@register.filter
def get_item(mapping, key):
    """Return mapping[key] (or None) in templates."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag
def efe_home_upcoming(user, limit=12):
    """Return today's and upcoming notification center entries for the home page."""
    if not getattr(user, "is_authenticated", False):
        return []
    try:
        center = notification_center.build(user)
    except Exception:  # noqa: BLE001 - the home page must never break
        return []
    return [*center.today, *center.upcoming][:limit]


@register.simple_tag
def efe_home_stats(user):
    """Return the home page summary tiles (watched this week, streak, upcoming)."""
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        from efe import home_stats  # noqa: PLC0415

        return home_stats.build(user)
    except Exception:  # noqa: BLE001 - the home page must never break
        logger.exception("Could not build the home page tiles")
        return None


@register.simple_tag
def efe_watch_date_editable(media, viewer, owner):
    """Whether the viewer may change the watch date shown on this media card."""
    if media is None or not getattr(viewer, "is_authenticated", False) or viewer != owner:
        return False
    if getattr(viewer, "is_demo", False):
        return False
    from efe import watch_dates  # noqa: PLC0415

    try:
        return watch_dates.is_editable(media)
    except Exception:  # noqa: BLE001 - a card must never break
        logger.exception("Could not check the watch date of %s", media)
        return False


@register.simple_tag
def efe_end_state(item=None, metadata=None):
    """Final / canceled badge state of a TV show or season (cached data only)."""
    try:
        from efe import show_status  # noqa: PLC0415

        if metadata is not None:
            return show_status.state(metadata)
        return show_status.for_item(item)
    except Exception:  # noqa: BLE001 - a badge must never break a page
        logger.exception("Could not read the show status")
        return None


@register.simple_tag
def efe_on_this_day(user):
    """Return the "on this day in earlier years" home card data."""
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        from efe import on_this_day  # noqa: PLC0415

        return on_this_day.build(user)
    except Exception:  # noqa: BLE001 - the home page must never break
        logger.exception("Could not build the on-this-day card")
        return None


@register.simple_tag(takes_context=True)
def efe_undo_toast(context):
    """Return (and consume) the pending "Undo" toast of this request."""
    request = context.get("request")
    if request is None or not getattr(request.user, "is_authenticated", False):
        return None
    from efe import undo  # noqa: PLC0415

    return undo.pop_toast(request)


@register.filter
def efe_poster(url, size="w185"):
    """Smaller TMDB poster (calendar, walls); other images unchanged."""
    from efe.poster_wall import small  # noqa: PLC0415

    return small(url, size)


@register.filter
def efe_has_poster(url):
    """Whether the media has a real poster (not the "none" placeholder)."""
    return bool(url) and url.startswith("http") and "none.svg" not in url


@register.simple_tag
def efe_festive():
    """New Year's season (1 December – 6 January, local time): snow and the festive icon."""
    today = timezone.localdate()
    return today.month == 12 or (today.month == 1 and today.day <= 6)  # noqa: PLR2004


# Seasonal effects and the days they turn on by themselves (month, day) — inclusive.
SEASONS = (
    ("blossoms", (3, 20), (5, 31)),   # spring
    ("leaves", (9, 22), (11, 30)),    # autumn
    ("bats", (10, 24), (11, 1)),      # Halloween week
)


@register.simple_tag
def efe_season(today=None):
    """Space-separated seasonal effects in season (local date), e.g. "leaves bats"."""
    today = today or timezone.localdate()
    key = (today.month, today.day)
    return " ".join(name for name, start, end in SEASONS if start <= key <= end)


@register.simple_tag
def efe_shown_poster(media, current_instance=None):
    """The poster to show on a media page: the user's chosen one (Item) when tracked."""
    image = getattr(getattr(current_instance, "item", None), "image", "") or ""
    if image.startswith("http") and "none.svg" not in image:
        return image
    return media.get("image") if hasattr(media, "get") else ""


@register.simple_tag
def efe_poster_choosable(media):
    """Whether "Afişi değiştir" can offer other posters for this title's source."""
    from efe import poster_choice  # noqa: PLC0415

    return poster_choice.supported(media.get("source"), media.get("media_type"))


@register.simple_tag
def efe_whats_new():
    """The "Yenilikler" entry to pop up once per device (fresh releases only)."""
    from efe import yenilikler  # noqa: PLC0415

    return yenilikler.latest(timezone.localdate())


@register.simple_tag
def efe_media_facts(media, user=None, show=None):
    """Country and total watch time under the title (show pages load the time lazily).

    ``show``: the show's metadata on a season page (the country comes from it).
    """
    facts = {"country": None, "length": None, "lazy": False, "book": None}
    try:
        from efe import media_facts  # noqa: PLC0415

        media_type = media.get("media_type")
        if media_type not in (MEDIA_TV, MEDIA_SEASON, MEDIA_MOVIE) or media.get("source") != "tmdb":
            return facts
        facts["country"] = media_facts.country(show if media_type == MEDIA_SEASON and show else media)
        if str(media.get("media_id") or "").isdigit():
            facts["book"] = (MEDIA_MOVIE if media_type == MEDIA_MOVIE else MEDIA_TV, str(media["media_id"]))
        if media_type == MEDIA_MOVIE:
            facts["length"] = media_facts.movie_total(media)
        elif media_type == MEDIA_SEASON:
            facts["length"] = media_facts.season_total(media, user)
        else:
            facts["lazy"] = True
    except Exception:  # noqa: BLE001 - extra facts must never break a page
        logger.exception("Could not build the media facts")
    return facts


MEDIA_TV, MEDIA_SEASON, MEDIA_MOVIE = "tv", "season", "movie"
