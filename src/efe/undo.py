"""Short-lived "Undo" for tracking changes (web toast and Mac notification).

Three kinds of tokens:
* "watch": episodes just marked as watched (deleted again),
* "unwatch": a watch just removed with the home page "−" (written back as it was),
* "media": the progress of another media type changed with "+"/"−" (restored).

An undo token lists exactly the watches the action created; undoing deletes only
those (through Episode.delete) and puts the season and show back the way they
were: their earlier status, or removed when the action itself created them and
they are empty again. Tokens belong to one user and expire after a few minutes.
"""

import secrets

from django.core.cache import cache
from django.db import transaction
from simple_history.utils import bulk_update_with_history

from django.apps import apps

from app.models import TV, Episode, Season

MEDIA_FIELDS = ("progress", "status", "start_date", "end_date")

UNDO_SECONDS = 120
SESSION_KEY = "efe_undo"


def _key(token):
    return f"efe-undo-{token}"


def before_state(user, media_id, source, season_number):
    """Season and show of the user as they are before an action ([id, status] or None)."""
    season = Season.objects.filter(
        item__media_id=media_id,
        item__source=source,
        item__season_number=season_number,
        item__episode_number=None,
        user=user,
    ).first()
    tv = TV.objects.filter(item__media_id=media_id, item__source=source, item__season_number=None, user=user).first()
    return {
        "season": [season.id, season.status] if season else None,
        "tv": [tv.id, tv.status] if tv else None,
    }


def _store(data, label, count, request):
    token = secrets.token_urlsafe(12)
    cache.set(_key(token), {**data, "label": label}, UNDO_SECONDS)
    if request is not None and hasattr(request, "session"):
        request.session[SESSION_KEY] = {"token": token, "label": label, "count": count}
    return token


def remember_unwatch(user, episode, label, before=None):
    """Store an undo token that writes a just-removed watch back."""
    season = episode.related_season
    return _store(
        {
            "kind": "unwatch",
            "user": user.id,
            "watches": [
                {
                    "season": season.id,
                    "item": episode.item_id,
                    "end_date": episode.end_date,
                    "notes": episode.notes,
                    "score": episode.score,
                },
            ],
            "before": before,
            "after": {"season": season.id, "tv": season.related_tv_id},
        },
        label,
        1,
        None,
    )


def media_snapshot(media):
    """The fields "+"/"−" can change on a non-episode media."""
    return {field: getattr(media, field, None) for field in MEDIA_FIELDS}


def remember_media(user, media, snapshot, label):
    """Store an undo token that puts a media's progress back."""
    return _store(
        {
            "kind": "media",
            "user": user.id,
            "model": media._meta.label,  # noqa: SLF001
            "id": media.id,
            "fields": snapshot,
        },
        label,
        1,
        None,
    )


def remember(user, episodes, label, request=None, before=None):
    """Store an undo token for the created watches; show a toast on the next page."""
    episodes = [episode for episode in episodes if episode is not None]
    if not episodes:
        return None
    season = episodes[0].related_season
    return _store(
        {
            "kind": "watch",
            "user": user.id,
            "episodes": [episode.id for episode in episodes],
            "before": before,
            "after": {"season": season.id, "tv": season.related_tv_id},
        },
        label,
        len(episodes),
        request,
    )


def pop_toast(request):
    """Return (and forget) the toast to show on this page, if any."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return session.pop(SESSION_KEY, None)


def _restore(user, data):
    """Put the season and show back the way they were before the action."""
    before, after = data.get("before"), data.get("after") or {}
    if before is None:
        return
    for model, key, empty in (
        (Season, "season", lambda obj: not Episode.objects.filter(related_season=obj).exists()),
        (TV, "tv", lambda obj: not Season.objects.filter(related_tv=obj).exists()),
    ):
        obj = model.objects.filter(id=after.get(key), user=user).first()
        if obj is None:
            continue
        if before.get(key) is None:
            if empty(obj):  # created by the action and empty again
                obj.delete()
        elif obj.status != before[key][1]:
            obj.status = before[key][1]
            bulk_update_with_history([obj], model, fields=["status"], default_user=user)


def undo(user, token):
    """Delete the watches of a token; return how many, or None if expired/foreign.

    All or nothing: if something fails, nothing is deleted and the token stays valid.
    """
    data = cache.get(_key(token))
    if not data or data.get("user") != user.id:
        return None
    kind = data.get("kind", "watch")
    with transaction.atomic():
        if kind == "unwatch":
            count = _rewatch(user, data)
        elif kind == "media":
            count = _restore_media(user, data)
        else:
            count = _delete_watches(user, data)
        _restore(user, data)
    cache.delete(_key(token))
    return count


def _delete_watches(user, data):
    episodes = Episode.objects.filter(id__in=data["episodes"], related_season__user=user).select_related(
        "related_season__related_tv", "item"
    )
    count = 0
    # Latest episodes first, so parent statuses step back cleanly.
    for episode in sorted(episodes, key=lambda e: e.item.episode_number or 0, reverse=True):
        episode.delete()
        count += 1
    return count


def _rewatch(user, data):
    count = 0
    for watch in data["watches"]:
        season = Season.objects.filter(id=watch["season"], user=user).first()
        if season is None:
            continue
        Episode.objects.create(
            related_season=season,
            item_id=watch["item"],
            end_date=watch["end_date"],
            notes=watch["notes"] or "",
            score=watch["score"],
        )
        count += 1
    return count


def _restore_media(user, data):
    model = apps.get_model(data["model"])
    media = model.objects.filter(id=data["id"], user=user).first()
    if media is None:
        return 0
    fields = [field for field in data["fields"] if hasattr(media, field)]
    for field in fields:
        setattr(media, field, data["fields"][field])
    bulk_update_with_history([media], model, fields=fields, default_user=user)
    return 1


def latest_watch(season, episode_number):
    """The watch just created for an episode of a season."""
    return (
        Episode.objects.filter(related_season=season, item__episode_number=episode_number)
        .order_by("-created_at", "-id")
        .first()
    )
