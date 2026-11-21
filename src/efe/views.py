import logging
import re
from urllib.parse import quote, urlencode
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from simple_history.utils import bulk_update_with_history

from app.models import Episode, Item, MediaTypes, Season, Sources
from events.models import Event
from app.providers import services
from app.templatetags.app_tags import media_type_readable, media_url
from app import helpers as app_helpers
from efe import bulk_watch as efe_bulk
from efe import dock as efe_dock
from efe import status_panel as efe_status_panel
from efe import show_length as efe_show_length
from efe import undo as efe_undo
from efe import poster_wall as efe_poster_wall
from efe import show_card as efe_show_card
from efe import universe as efe_universe
from efe import trailers as efe_trailers
from efe import film_series, home_stats, notification_center, person as efe_person, rating_chart as efe_rating_chart, random_pick, show_status, task_control, tasks, watch_dates
from efe.templatetags.efe_tags import ef_countdown
from users.models import ThemeChoices

logger = logging.getLogger(__name__)

SERIES_FILTERS = ("all", "in_progress", "completed")
MAX_SCORE = 10
MAX_NOTES = 2000


# --- Notification center -------------------------------------------------


@require_GET
def notifications(request):
    """Display the notification center and mark its entries as read."""
    center = notification_center.build(request.user)
    context = {
        "center": center,
        "unread_count": center.unread_count,
        "days_ahead": request.user.notification_center_days,
    }
    response = render(request, "efe/notifications.html", context)
    notification_center.mark_read(request.user, center)
    return response


@require_GET
def notifications_menu(request):
    """Return the bell dropdown content (htmx)."""
    center = notification_center.build(request.user)
    return render(
        request,
        "efe/components/notifications_menu.html",
        {
            "center": center,
            "entries": center.dropdown_entries(),
            "unread_count": center.unread_count,
        },
    )


@require_POST
def notifications_mark_read(request):
    """Mark every notification center entry as read."""
    center = notification_center.build(request.user)
    notification_center.mark_read(request.user, center)
    if request.headers.get("HX-Request"):
        center = notification_center.build(request.user)
        response = render(
            request,
            "efe/components/notifications_menu.html",
            {
                "center": center,
                "entries": center.dropdown_entries(),
                "unread_count": 0,
            },
        )
        response["HX-Trigger"] = "efe-notifications-read"
        return response
    return redirect("efe_notifications")


@require_GET
def notifications_feed(request):
    """Return the notification center as JSON (badge and browser notifications)."""
    center = notification_center.build(request.user)
    watched = notification_center.watched_episodes(request.user, center.entries)
    entries = [
        {
            "id": f"{entry.event.id}-{entry.kind}",
            "event_id": entry.event.id,
            "can_watch": entry.can_watch,
            "watched": entry.event.id in watched,
            "title": str(entry.item),
            "episode": entry.event.readable_content_number,
            "media_type": media_type_readable(entry.item.media_type),
            "image": entry.item.image,
            "url": media_url(entry.item),
            "released": entry.is_released,
            "unread": entry.unread,
            "when": ef_countdown(entry),
            "datetime": entry.event.datetime.isoformat(),
        }
        for entry in center.entries
    ]
    return JsonResponse({"unread": center.unread_count, "entries": entries})


def parse_score(raw):
    """Return an optional 0-10 score with at most one decimal ("8", "8.5", "8,5").

    An empty value means no score (None); anything else invalid raises ValueError.
    """
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        score = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(raw) from error
    if not score.is_finite() or not (0 <= score <= MAX_SCORE) or score != score.quantize(Decimal("0.1")):
        raise ValueError(raw)
    return score


@require_POST
def set_episode_score(request, episode_id):
    """Set or clear the score of one of the user's watched episodes (episode card ★)."""
    if getattr(request.user, "is_demo", False):
        return JsonResponse({"ok": False, "error": "demo"}, status=403)
    try:
        score = parse_score(request.POST.get("score"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_score"}, status=400)
    episode = get_object_or_404(Episode, id=episode_id, related_season__user=request.user)
    episode.score = score
    bulk_update_with_history([episode], Episode, fields=["score"])
    return JsonResponse({"ok": True, "score": None if score is None else str(score)})


@require_POST
def notifications_watch(request, event_id):
    """Mark a released episode from the notification center as watched (Mac menu bar).

    Optional POST fields: ``score`` (0-10, one decimal) and ``notes``.
    """
    try:
        score = parse_score(request.POST.get("score"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_score"}, status=400)
    notes = (request.POST.get("notes") or "").strip()[:MAX_NOTES]
    event = Event.objects.select_related("item").filter(id=event_id).first()
    before = (
        efe_undo.before_state(request.user, event.item.media_id, event.item.source, event.item.season_number)
        if event is not None
        else None
    )
    try:
        result = notification_center.watch(request.user, event_id, score=score, notes=notes)
    except Exception:
        logger.exception("Could not mark notification event %s as watched", event_id)
        return JsonResponse({"ok": False, "error": "failed"}, status=502)
    if result is None:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    token = None
    if result == "watched":
        season = Season.objects.filter(item=event.item, user=request.user).first()
        if season is not None:
            token = efe_undo.remember(
                request.user,
                [efe_undo.latest_watch(season, event.content_number)],
                f"{event.item.title} S{event.item.season_number}B{event.content_number} izlendi",
                before=before,
            )
    return JsonResponse({"ok": True, "result": result, "undo": token})


@require_POST
def undo_watch(request, token):
    """Undo the watches just marked (toast button or Mac notification action)."""
    count = efe_undo.undo(request.user, token)
    if count is None:
        return JsonResponse({"ok": False, "error": "expired"}, status=410)
    return JsonResponse({"ok": True, "count": count})


@require_GET
def person_page(request, person_id):
    """In-app actor page: filmography with what the user watched marked."""
    try:
        profile = efe_person.fetch(person_id)
    except Exception:
        logger.exception("Could not load TMDB person %s", person_id)
        return render(request, "efe/person.html", {"error": True, "person_id": person_id}, status=502)
    credits = efe_person.mark(request.user, profile["credits"])
    context = {
        "person": profile,
        "credits": credits,
        "movie_count": sum(1 for c in credits if c["media_type"] == MediaTypes.MOVIE.value),
        "tv_count": sum(1 for c in credits if c["media_type"] == MediaTypes.TV.value),
        "watched_count": sum(1 for c in credits if c["watched"]),
        "tracked_count": sum(1 for c in credits if c["status"]),
        "rings": efe_person.progress_rings(credits, profile.get("crew_first")),
    }
    return render(request, "efe/person.html", context)


@require_GET
def rating_chart_view(request, media_id):
    """Episode rating heatmap of a TMDB show, loaded lazily by the details page (htmx)."""
    try:
        season = int(request.GET["sezon"]) if request.GET.get("sezon") else None
    except ValueError:
        season = None
    chart = None
    try:
        tv_metadata = services.get_media_metadata(MediaTypes.TV.value, media_id, Sources.TMDB.value)
        chart = efe_rating_chart.build(
            request.user, media_id, Sources.TMDB.value, tv_metadata, current_season=season
        )
    except Exception:
        logger.exception("Could not build the rating chart of %s", media_id)
        return render(request, "efe/components/rating_chart_body.html", {"error": True})
    return render(request, "efe/components/rating_chart_body.html", {"rating_chart": chart})


@require_GET
def show_endings(request):
    """Status of the tracked shows; the Mac app notifies when one gets canceled or ends."""
    return JsonResponse({"shows": show_status.tracked_statuses(request.user)})


@require_GET
def dock_continuing(request):
    """TV shows in progress for the Mac app's Dock menu."""
    return JsonResponse({"shows": efe_dock.continuing(request.user)})


def _bulk_done(request, created, label, before):
    efe_undo.remember(request.user, created, label, request=request, before=before)
    messages.success(request, f"{label}: {len(created)} bölüm izlendi olarak işaretlendi.")


def _bulk_lock(request, media_id, season_number):
    """One bulk action per season at a time (a double click must not mark twice)."""
    key = f"efe-bulk:{request.user.id}:{media_id}:{season_number}"
    if cache.add(key, 1, 120):
        return key
    messages.info(request, "Bu sezon için işaretleme zaten sürüyor.")
    return None


@require_POST
def season_mark_upto(request, source, media_id, season_number, episode_number):
    """Mark every unwatched, aired episode up to this one as watched (now)."""
    if getattr(request.user, "is_demo", False):
        return HttpResponse(_("This section is view-only for demo accounts."), status=403)
    lock = _bulk_lock(request, media_id, season_number)
    if lock is None:
        return app_helpers.redirect_back(request)
    try:
        before = efe_undo.before_state(request.user, media_id, source, season_number)
        created = efe_bulk.mark_up_to(request.user, media_id, source, season_number, episode_number)
    except efe_bulk.BulkWatchError as error:
        messages.error(request, str(error))
    else:
        _bulk_done(request, created, f"S{season_number}B{episode_number}'e kadar", before)
    finally:
        cache.delete(lock)
    return app_helpers.redirect_back(request)


@require_POST
def season_spread(request, source, media_id, season_number):
    """Mark the season's unwatched episodes with dates spread over a past period."""
    if getattr(request.user, "is_demo", False):
        return HttpResponse(_("This section is view-only for demo accounts."), status=403)
    if request.POST.get("onay") != "1":
        messages.error(request, "Onay kutusunu işaretlemeden toplu işaretleme yapılmaz.")
        return app_helpers.redirect_back(request)
    lock = _bulk_lock(request, media_id, season_number)
    if lock is None:
        return app_helpers.redirect_back(request)
    try:
        before = efe_undo.before_state(request.user, media_id, source, season_number)
        start = watch_dates.parse_day(request.POST.get("start")) if request.POST.get("start") else None
        end = watch_dates.parse_day(request.POST.get("end")) if request.POST.get("end") else None
        first_day, last_day = efe_bulk.period_range(request.POST.get("period"), start=start, end=end)
        created = efe_bulk.spread(request.user, media_id, source, season_number, first_day, last_day)
    except (ValueError, efe_bulk.BulkWatchError) as error:
        messages.error(request, str(error) if isinstance(error, efe_bulk.BulkWatchError) else "Tarih okunamadı.")
    else:
        _bulk_done(
            request,
            created,
            f"{season_number}. sezon {first_day:%d.%m.%Y}–{last_day:%d.%m.%Y} arasına",
            before,
        )
    finally:
        cache.delete(lock)
    return app_helpers.redirect_back(request)


@require_GET
def show_length(request, media_id):
    """Seasons, episodes and approximate watch time of a show (search results, htmx)."""
    try:
        length = efe_show_length.summary(media_id)
    except Exception:  # noqa: BLE001 - a missing line must not break the search page
        logger.exception("Could not load the length of show %s", media_id)
        length = None
    return render(request, "efe/components/show_length.html", {"length": length})


@require_GET
def media_length(request, media_id):
    """Total watch time chip of a whole show (detail page, htmx; all seasons are loaded)."""
    from efe import media_facts  # noqa: PLC0415

    if not media_id.isdigit():
        return HttpResponse(status=404)
    try:
        length = media_facts.show_total(media_id, request.user)
    except Exception:  # noqa: BLE001 - the chip simply stays away
        logger.exception("Could not load the total length of show %s", media_id)
        length = None
    if request.GET.get("yalin"):
        # Details box line: only the total ("52 sa 58 dk"), or "Bilinmiyor".
        if not length:
            return HttpResponse(_("Unknown"))
        return HttpResponse(("≈ " if length["estimated"] else "") + length["total"])
    return render(request, "efe/components/media_length.html", {"length": length})


@require_GET
def cinema(request):
    """Cinema mode picture of the home page (htmx; loaded only when the mode is on)."""
    from efe import cinema as efe_cinema  # noqa: PLC0415

    try:
        picks = efe_cinema.picks(request.user)
    except Exception:  # noqa: BLE001 - the home page falls back to its usual sections
        logger.exception("Could not build the cinema mode picks")
        picks = None
    return render(request, "efe/components/cinema_body.html", {"picks": picks})


@require_GET
def book_source_view(request, media_type, media_id):
    """"Kitaptan uyarlama" chip of a show or film (htmx, lazy; Wikidata)."""
    from efe import book_source  # noqa: PLC0415

    if media_type not in (MediaTypes.TV.value, MediaTypes.MOVIE.value) or not media_id.isdigit():
        return HttpResponse(status=404)
    try:
        books = book_source.lookup(media_type, media_id)
    except Exception:  # noqa: BLE001 - Wikidata down: the chip simply stays away
        logger.warning("Book source lookup failed for %s %s", media_type, media_id)
        books = []
    for book in books:
        owned = book_source.in_library(request.user, book["title"]) or (
            book_source.in_library(request.user, book["search_title"]) if book["search_title"] else None
        )
        book["owned_url"] = media_url(owned.item) if owned is not None else ""
    return render(
        request,
        "efe/components/book_source.html",
        {"books": books, "can_add": not getattr(request, "efe_guest", False) and not getattr(request.user, "is_demo", False)},
    )


@require_POST
def book_source_add(request):
    """Add the adapted book to the user's book list (planned)."""
    from efe import book_source  # noqa: PLC0415

    if getattr(request, "efe_guest", False) or getattr(request.user, "is_demo", False):
        return HttpResponse(status=403)
    title = (request.POST.get("title") or "").strip()[:300]
    search_title = (request.POST.get("search_title") or "").strip()[:300]
    author = (request.POST.get("author") or "").strip()[:200]
    try:
        book, created = book_source.add(request.user, title, author, search_title)
    except Exception:  # noqa: BLE001 - the user gets the search link instead
        logger.exception("Could not add the book %s", title)
        book, created = None, False
    return render(
        request,
        "efe/components/book_source_result.html",
        {
            "book": book,
            "created": created,
            "url": media_url(book.item) if book is not None else "",
            "search_url": reverse("search") + "?" + urlencode({"q": search_title or title, "media_type": MediaTypes.BOOK.value}),
            "books_hidden": not getattr(request.user, "book_enabled", True),
        },
    )


@require_GET
def status_page(request):
    """Status panel: background tasks, calendar refresh, backups, offline copy and errors."""
    if getattr(request.user, "is_demo", False):
        return HttpResponse(_("This section is view-only for demo accounts."), status=403)
    return render(request, "efe/status.html", {"status": efe_status_panel.build()})


@require_GET
def weekly_summary(request):
    """Return last week's watch summary as JSON (Mac weekly notification)."""
    return JsonResponse(home_stats.weekly_summary(request.user))


@require_POST
def notifications_toggle_item(request, item_id):
    """Mute or unmute the notifications of an item."""
    item = get_object_or_404(Item, id=item_id)
    excluded = request.user.notification_excluded_items
    if excluded.filter(id=item.id).exists():
        excluded.remove(item)
        muted = False
    else:
        excluded.add(item)
        muted = True
    if request.headers.get("HX-Request"):
        return JsonResponse({"muted": muted})
    return redirect(request.headers.get("referer") or "efe_notifications")


# --- Random pick ----------------------------------------------------------


@require_GET
def random_suggestion(request):
    """Display the random suggestion page."""
    media_types = random_pick.available_media_types(request.user)
    counts = {
        media_type: len(random_pick.planning_media(request.user, [media_type]))
        for media_type in media_types
    }
    # A few planned posters for the idle reel (the same strip that spins on "Pick for me").
    import random as _random  # noqa: PLC0415

    posters = [
        media for media in random_pick.planning_media(request.user, media_types)
        if (media.item.image or "").startswith("http") and "none.svg" not in media.item.image
    ]
    preview = _random.sample(posters, min(len(posters), 9))
    # Advanced filters read the cached facts; a background task fills what is missing.
    from efe import planned_meta, tasks_planned  # noqa: PLC0415

    computing = False
    entries = [random_pick.item_entry(media) for media in random_pick.planning_media(request.user, media_types)]
    if planned_meta.missing(entries):
        try:
            tasks_planned.start_fill(request.user)
        except Exception:  # noqa: BLE001 - e.g. no broker: the page still works without filters
            logger.warning("planned facts task could not be queued", exc_info=True)
        computing = tasks_planned.is_filling(request.user)
    context = {
        "durations": random_pick.DURATIONS,
        "min_scores": random_pick.MIN_SCORES,
        "genres": random_pick.genre_choices(request.user),
        "computing": computing,
        "media_types": [media_type for media_type in media_types if counts.get(media_type)],
        "counts": counts,
        "total": sum(counts.values()),
        "lists": random_pick.user_lists(request.user),
        "preview": preview,
    }
    return render(request, "efe/random.html", context)


@require_POST
def random_suggestion_roll(request):
    """Pick a random planned media and return the result card (htmx)."""
    allowed = random_pick.available_media_types(request.user)
    media_types = [
        media_type
        for media_type in request.POST.getlist("media_types")
        if media_type in allowed
    ] or allowed
    list_id = request.POST.get("list_id") or None
    if list_id is not None and not str(list_id).isdigit():
        list_id = None
    filters = random_pick.clean_filters(
        request.POST.get("duration", ""), request.POST.get("genre", ""), request.POST.get("min_score", ""),
    )
    stats = {}
    chosen, reel, candidates = random_pick.pick(
        request.user,
        media_types,
        list_id=list_id,
        exclude_item_id=request.POST.get("exclude"),
        filters=filters,
        stats=stats,
    )
    response = render(
        request,
        "efe/components/random_result.html",
        {"chosen": chosen, "reel": reel, "candidates": candidates},
    )
    if stats.get("unknown"):
        response.content += format_html(
            '<p class="text-xs text-gray-400 mt-3">{} yapımın bilgisi henüz yok, süzgeç yüzünden elendi.</p>',
            stats["unknown"],
        ).encode()
    return response


# --- Film series ----------------------------------------------------------


@require_GET
def series(request):
    """Display the film series of the user's movies."""
    status_filter = request.GET.get("filter", "all")
    if status_filter not in SERIES_FILTERS:
        status_filter = "all"
    unchecked = film_series.unchecked_movie_ids(request.user)
    scanning = tasks.is_scanning(request.user)
    if unchecked and not scanning:
        scanning = tasks.start_scan(request.user)
        # Eager mode (no worker) finishes immediately
        scanning = tasks.is_scanning(request.user)
        unchecked = film_series.unchecked_movie_ids(request.user)
    context = {
        "filters": [
            ("all", _("All")),
            ("in_progress", _("In progress")),
            ("completed", _("Completed")),
        ],
        "series_list": film_series.user_series(request.user, status_filter),
        "status_filter": status_filter,
        "unchecked_count": len(unchecked),
        "scanning": scanning,
    }
    template = (
        "efe/components/series_list.html"
        if request.headers.get("HX-Request")
        else "efe/series.html"
    )
    return render(request, template, context)


@require_POST
def series_scan(request):
    """Start looking for the film series of the user's movies."""
    started = tasks.start_scan(request.user)
    if request.headers.get("HX-Request"):
        return JsonResponse({"started": started})
    return redirect("efe_series")


# --- Preferences ----------------------------------------------------------


@require_POST
def set_theme(request):
    """Save the interface theme."""
    theme = request.POST.get("theme")
    if theme not in ThemeChoices.values:
        return HttpResponse(_("Invalid theme."), status=400)
    request.user.theme = theme
    request.user.save(update_fields=["theme"])
    return HttpResponse(status=204)


@require_POST
def set_sound(request):
    """Save the sound effects preferences."""
    user = request.user
    update_fields = []
    if "enabled" in request.POST:
        user.sound_effects_enabled = request.POST.get("enabled") in ("1", "true", "on")
        update_fields.append("sound_effects_enabled")
    if "volume" in request.POST:
        try:
            volume = int(request.POST.get("volume"))
        except (TypeError, ValueError):
            return HttpResponse(_("Invalid volume."), status=400)
        user.sound_volume = max(0, min(100, volume))
        update_fields.append("sound_volume")
    if update_fields:
        user.save(update_fields=update_fields)
    return HttpResponse(status=204)


@require_POST
def set_sidebar_media(request):
    """Show or hide a media type in the sidebar (macOS context menu)."""
    if getattr(request.user, "is_demo", False):
        return HttpResponse(_("This section is view-only for demo accounts."), status=403)
    media_type = request.POST.get("media_type")
    allowed = [value for value in MediaTypes.values if value != MediaTypes.EPISODE.value]
    if media_type not in allowed:
        return HttpResponse(_("Invalid media type."), status=400)
    field = f"{media_type}_enabled"
    if not hasattr(request.user, field):
        return HttpResponse(_("Invalid media type."), status=400)
    setattr(request.user, field, request.POST.get("enabled") in ("1", "true", "on"))
    request.user.save(update_fields=[field])
    return HttpResponse(status=204)


# --- Watch dates ---------------------------------------------------------


@require_POST
def set_watch_date(request):
    """Change the watch date shown on a media card; return the refreshed badge."""
    if getattr(request.user, "is_demo", False):
        return HttpResponse(_("This section is view-only for demo accounts."), status=403)
    media_type = request.POST.get("media_type", "")
    try:
        instance_id = int(request.POST.get("instance_id", ""))
        day = watch_dates.parse_day(request.POST.get("date"))
        media = watch_dates.set_watch_date(request.user, media_type, instance_id, day)
    except (ValueError, LookupError):
        # LookupError: unknown model; ObjectDoesNotExist is handled below
        return HttpResponse(_("The date could not be saved."), status=400)
    except ObjectDoesNotExist:
        return HttpResponse(_("The date could not be saved."), status=404)
    logger.info("Watch date of %s %s changed to %s", media_type, instance_id, day)
    return render(
        request,
        "efe/components/watch_date_badge.html",
        {"media": media, "user": request.user},
    )


# --- Background tasks ----------------------------------------------------


@require_POST
def cancel_task(request, task_id):
    """Cancel a queued or running background task of the user."""
    if getattr(request.user, "is_demo", False):
        return HttpResponse(_("This section is view-only for demo accounts."), status=403)
    try:
        task_control.cancel(request.user, task_id)
    except task_control.TaskControlError:
        messages.warning(request, _("This task has already finished or cannot be cancelled."))
    else:
        messages.success(request, _("The task was cancelled."))
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("import_data")
    return redirect(next_url)


TRAILER_TYPES = (MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value)


@require_GET
def trailers_view(request, media_type, media_id):
    """Trailers of a TMDB movie/show/season, loaded when the "Fragman" tab opens (htmx)."""
    if media_type not in TRAILER_TYPES or not media_id.isdigit():
        return HttpResponse(status=404)
    try:
        season = int(request.GET["sezon"]) if request.GET.get("sezon") else None
    except ValueError:
        season = None
    try:
        sections = efe_trailers.sections(media_type, media_id, season)
    except Exception:
        logger.exception("Could not load the trailers of %s %s", media_type, media_id)
        return render(request, "efe/components/trailers_body.html", {"error": True})
    recap = None
    if media_type == MediaTypes.SEASON.value:
        try:
            recap = efe_trailers.recap(media_id, season)
        except Exception:  # noqa: BLE001 - the trailers still show
            logger.exception("Could not load the recap of %s season %s", media_id, season)
    return render(request, "efe/components/trailers_body.html", {"sections": sections, "recap": recap})


@require_GET
def poster_wall(request):
    """Posters of everything the user watched/read/played, filterable by year and kind."""
    kinds = dict(efe_poster_wall.KINDS)
    kind = request.GET.get("tur")
    kind = kind if kind in kinds else None
    try:
        year = int(request.GET["yil"]) if request.GET.get("yil") else None
    except ValueError:
        year = None
    enabled = set(request.user.get_enabled_media_types())
    context = {
        "collage": efe_poster_wall.month_data(request.user),
        "entries": efe_poster_wall.collect(request.user, year, kind),
        "years": efe_poster_wall.years(request.user),
        "kinds": [(key, label) for key, label in efe_poster_wall.KINDS if key in enabled],
        "year": year,
        "kind": kind,
        "kind_label": kinds.get(kind, ""),
    }
    return render(request, "efe/poster_wall.html", context)


def poster_choice_view(request, item_id):
    """GET: the posters offered by the title's source (modal). POST: use the chosen one."""
    from app.providers import services  # noqa: PLC0415
    from efe import poster_choice  # noqa: PLC0415

    item = poster_choice.tracked_item(request.user, item_id)
    if item is None or not poster_choice.supported(item.source, item.media_type):
        return HttpResponse("Bu yapımın afişi değiştirilemez.", status=404, content_type="text/plain; charset=utf-8")
    seasons = [item.season_number] if item.media_type == MediaTypes.SEASON.value else None
    try:
        default = services.get_media_metadata(item.media_type, item.media_id, item.source, seasons).get("image")
        choices = poster_choice.alternatives(item.source, item.media_type, item.media_id, item.season_number)
    except Exception:  # noqa: BLE001 - provider down: say so in the modal
        logger.warning("poster choices failed", exc_info=True)
        default, choices = None, None
    if request.method == "POST":
        if getattr(request.user, "is_demo", False):
            return HttpResponse(status=403)
        if choices is None or not poster_choice.choose(item, request.POST.get("url", ""), default):
            return HttpResponse("Bu afiş seçilemedi.", status=400, content_type="text/plain; charset=utf-8")
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response
    return render(request, "efe/components/poster_choice.html", {
        "item": item, "choices": choices, "default": default,
        "default_listed": any(choice["url"] == default for choice in choices or []),
    })


@require_GET
def poster_download(request):
    """The poster in its highest resolution, as a file to save (media page → "HD afiş")."""
    from django.core.exceptions import SuspiciousFileOperation  # noqa: PLC0415
    from django.utils.http import content_disposition_header  # noqa: PLC0415
    from django.utils.text import get_valid_filename  # noqa: PLC0415

    title = (request.GET.get("ad") or "").strip()[:120]
    try:
        safe = get_valid_filename(title.replace("/", " ")) if title else ""
    except SuspiciousFileOperation:  # a title of only punctuation or emoji ("?", "★")
        safe = ""
    result = efe_poster_wall.full_size(request.GET.get("u", ""))
    if result is None:
        return HttpResponse("Afiş indirilemedi.", status=404, content_type="text/plain; charset=utf-8")
    data, content_type, extension = result
    name = f"{safe or 'afis'} - efeTrack.{extension}"
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(as_attachment=True, filename=name)
    response["Cache-Control"] = "private, max-age=86400"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def poster_proxy(request):
    """Same-origin copy of an allowed cover image (for drawing the wall on a canvas)."""
    result = efe_poster_wall.proxy(request.GET.get("u", ""))
    if result is None:
        return HttpResponse(status=404)
    data, content_type = result
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, max-age=604800"
    # An image only: never run anything inside it, never guess another type.
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def show_card_view(request, tv_id):
    """Story-size shareable card of one of the user's shows."""
    from django.apps import apps  # noqa: PLC0415

    tv = get_object_or_404(apps.get_model("app", "tv").objects.select_related("item"), id=tv_id, user=request.user)
    return render(request, "efe/show_card.html", {"card": efe_show_card.data(request.user, tv), "tv": tv})


@require_GET
def finish_card_view(request, media_type, media_id):
    """"Bitirdim" picture (PNG) of one of the user's finished shows or films."""
    from django.apps import apps  # noqa: PLC0415

    from efe import finish_card  # noqa: PLC0415

    if media_type not in (MediaTypes.TV.value, MediaTypes.MOVIE.value):
        return HttpResponse(status=404)
    media = get_object_or_404(
        apps.get_model("app", media_type).objects.select_related("item"), id=media_id, user=request.user,
    )
    data, _caption = finish_card.build(request.user, media)
    response = HttpResponse(data, content_type="image/png")
    name = re.sub(r"[^\w-]+", "-", media.item.title, flags=re.UNICODE).strip("-") or "kart"
    disposition = "attachment" if request.GET.get("indir") else "inline"
    response["Content-Disposition"] = f"{disposition}; filename*=UTF-8''efeTrack-{quote(name)}.png"
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def service_worker(request):
    """The offline service worker (served at /sw.js so it can cover the whole site)."""
    import json  # noqa: PLC0415

    from django.conf import settings  # noqa: PLC0415

    precache = ["/", reverse("calendar"), reverse("efe_poster_wall"), reverse("lists")]
    if request.user.is_authenticated:
        for media_type in (MediaTypes.TV.value, MediaTypes.MOVIE.value):
            try:
                precache.append(reverse("medialist", args=[request.user.username, media_type]))
            except Exception:  # noqa: BLE001 - a disabled media type has no list
                pass
    response = render(
        request,
        "efe/sw.js",
        {"version": settings.VERSION, "precache": json.dumps(precache)},
        content_type="application/javascript; charset=utf-8",
    )
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def universe_view(request, media_type, media_id):
    """Prequels, sequels, spin-offs and same-world titles, loaded when the "Evren" tab opens."""
    if media_type not in efe_universe.TMDB_PROPERTY or not media_id.isdigit():
        return HttpResponse(status=404)
    try:
        links = efe_universe.lookup(request.user, media_type, media_id)
    except Exception:
        logger.exception("Could not load the universe of %s %s", media_type, media_id)
        return render(request, "efe/components/universe_body.html", {"error": True})
    return render(request, "efe/components/universe_body.html", {"links": links})


@require_GET
def whats_new(request):
    """All "Yenilikler" entries."""
    from efe import yenilikler  # noqa: PLC0415

    return render(request, "efe/whats_new.html", {"entries": yenilikler.ENTRIES})


@require_GET
def planned_view(request, country=None):
    """All planned media together, filterable by type and country (Planlananlar, /ulke/gb)."""
    from efe import planned  # noqa: PLC0415

    kind = request.GET.get("tur") or None
    if kind not in dict(efe_poster_wall.KINDS):
        kind = None
    sort = request.GET.get("sira", "yeni")
    if sort not in dict(planned.SORTS):
        sort = "yeni"
    from efe import tasks_planned  # noqa: PLC0415

    country = (country or request.GET.get("ulke") or "").lower()[:40] or None
    data = planned.view_data(request.user, kind, sort, country)
    computing = False
    if data["pending"]:
        # Facts of the titles come from the cache; a background task fills what is missing.
        try:
            tasks_planned.start_fill(request.user)
        except Exception:  # noqa: BLE001 - e.g. no broker: the page still works without the facts
            logger.warning("planned facts task could not be queued", exc_info=True)
        computing = tasks_planned.is_filling(request.user)
        if not computing:  # eager mode (no worker) finishes at once
            data = planned.view_data(request.user, kind, sort, country)
    kind_label, kind_slug, verb = planned.labels(kind)
    context = {
        **data, "kind": kind, "sort": sort, "sorts": planned.SORTS, "computing": computing,
        "kind_label": kind_label, "kind_slug": kind_slug, "verb": verb, "country": country,
    }
    return render(request, "efe/planned.html", context)


def country_page(request, key):
    """A country's titles among the planned ones (🇬🇧 Britanya yapımları)."""
    return planned_view(request, country=key)


def countries_page(request):
    """Every country of the planned titles, with counts."""
    from efe import planned, planned_meta  # noqa: PLC0415

    entries = planned_meta.attach(planned.collect(request.user))
    return render(request, "efe/countries.html", {
        "countries": planned.countries_of(entries),
        "unknown": sum(1 for entry in entries if not entry.get("country")),
        "pending": bool(planned_meta.missing(entries)),
    })
