"""Undo and episode rating for the home page "+"/"−" progress buttons.

``before`` runs before the progress change, ``after`` after it; ``after`` returns
the htmx trigger events: "efe-undo" (the Undo toast) and, when "+" watched an
episode, "efe-rate" (the rating popup).
"""

from django.urls import reverse

from app.models import Episode, MediaTypes
from efe import undo


def before(user, media, media_type, operation):
    """What the change may touch, captured before it happens."""
    if media_type != MediaTypes.SEASON.value:
        return {"snapshot": undo.media_snapshot(media)}
    item = media.item
    state = {
        "season_state": undo.before_state(user, item.media_id, item.source, item.season_number),
        "episode_ids": set(Episode.objects.filter(related_season=media).values_list("id", flat=True)),
    }
    if operation == "decrease" and media.progress:
        # Season.unwatch removes the latest watch of the current episode.
        state["removed"] = (
            Episode.objects.filter(related_season=media, item__episode_number=media.progress)
            .select_related("item", "related_season")
            .order_by("-end_date")
            .first()
        )
    return state


def _label(media, number):
    return f"{media.item.title} S{media.item.season_number}B{number}"


def after(user, media, media_type, operation, state):
    """Undo token (and rating popup) for the change just made; {} if nothing changed."""
    events = {}
    if media_type != MediaTypes.SEASON.value:
        snapshot = state["snapshot"]
        if undo.media_snapshot(media) == snapshot:
            return events
        word = "artırıldı" if operation == "increase" else "azaltıldı"
        token = undo.remember_media(user, media, snapshot, f"{media.item.title}: ilerleme {word}")
        events["efe-undo"] = {"token": token, "label": f"{media.item.title}: ilerleme {word}", "count": 1}
        return events

    current = Episode.objects.filter(related_season=media).select_related("item", "related_season")
    if operation == "increase":
        new = [episode for episode in current if episode.id not in state["episode_ids"]]
        if not new:
            return events
        episode = new[0]
        label = f"{_label(media, episode.item.episode_number)} izlendi"
        token = undo.remember(user, [episode], label, before=state["season_state"])
        events["efe-undo"] = {"token": token, "label": label, "count": 1}
        events["efe-rate"] = {
            "url": reverse("efe_episode_score", args=[episode.id]),
            "label": _label(media, episode.item.episode_number),
        }
    else:
        removed = state.get("removed")
        if removed is None or current.filter(id=removed.id).exists():
            return events
        label = f"{_label(media, removed.item.episode_number)} izleme kaydı silindi"
        token = undo.remember_unwatch(user, removed, label, before=state["season_state"])
        events["efe-undo"] = {"token": token, "label": label, "count": 1}
    return events
