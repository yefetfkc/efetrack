"""In-app actor page: a person's filmography from TMDB, marked with what the user tracks."""

import requests
from django.core.cache import cache
from django.urls import reverse
from django.utils.text import slugify

from app.models import TV, MediaTypes, Movie, Sources, Status
from app.providers import services, tmdb
from efe import offline

# Talk shows and news: appearances "as themselves" that clutter a filmography.
NOISE_GENRES = {10763, 10767}

STATUS_LABELS = {
    Status.COMPLETED.value: ("İzledin", "#16a34a"),
    Status.IN_PROGRESS.value: ("İzliyorsun", "#4f46e5"),
    Status.PLANNING.value: ("Planladın", "#6b7280"),
    Status.PAUSED.value: ("Ara verdin", "#d97706"),
    Status.DROPPED.value: ("Bıraktın", "#dc2626"),
}


def _credit(entry):
    media_type = entry.get("media_type")
    date = entry.get("release_date") or entry.get("first_air_date") or ""
    return {
        "id": str(entry.get("id")),
        "media_type": media_type,
        "title": entry.get("title") or entry.get("name") or "",
        "characters": [entry["character"]] if entry.get("character") else [],
        "date": date,
        "year": date[:4],
        "image": tmdb.get_image_url(entry.get("poster_path")),
        "episode_count": entry.get("episode_count") or 0,
        "popularity": entry.get("popularity") or 0,
        "acting": True,
        "crew_roles": [],
    }


def _credits(cast, crew=()):
    """Movies and TV shows the person worked on, one entry each, newest first.

    ``acting`` marks titles they acted in; ``crew_roles`` lists their duties behind
    the camera (Turkish, in ``tmdb.CREW_ROLE_ORDER`` order). A title is listed once
    even if both apply, so the page counts stay right.
    """
    merged = {}
    for entry in cast:
        if entry.get("media_type") not in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
            continue
        if NOISE_GENRES.intersection(entry.get("genre_ids") or []):
            continue
        credit = _credit(entry)
        key = (credit["media_type"], credit["id"])
        if key in merged:
            # A show lists one entry per role: join them.
            known = merged[key]
            for character in credit["characters"]:
                if character not in known["characters"]:
                    known["characters"].append(character)
            known["episode_count"] += credit["episode_count"]
        else:
            merged[key] = credit
    for entry in crew:
        role = tmdb.CREW_JOB_ROLES.get(entry.get("job"))
        if role is None or entry.get("media_type") not in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
            continue
        if NOISE_GENRES.intersection(entry.get("genre_ids") or []):
            continue
        credit = _credit(entry)
        key = (credit["media_type"], credit["id"])
        known = merged.get(key)
        if known is None:
            credit["acting"] = False
            credit["characters"] = []
            known = merged[key] = credit
        if role not in known["crew_roles"]:
            known["crew_roles"].append(role)
    for credit in merged.values():
        credit["crew_roles"].sort(key=tmdb.CREW_ROLE_ORDER.index)
    # Undated (announced) titles first, then newest to oldest.
    return sorted(merged.values(), key=lambda c: (c["date"] == "", c["date"]), reverse=True)


# TMDB "known for" department -> the main duty shown first on the page.
DEPARTMENT_ROLES = {
    "Directing": "Yönetmen",
    "Writing": "Senarist",
    "Production": "Yapımcı",
    "Camera": "Görüntü yönetmeni",
    "Sound": "Müzik",
    "Creator": "Yaratıcı",
}


def _crew_roles(credits, department):
    """Duties behind the camera that appear in the credits, the person's main one first."""
    present = {role for credit in credits for role in credit["crew_roles"]}
    main = DEPARTMENT_ROLES.get(department)
    return sorted(
        (role for role in tmdb.CREW_ROLE_ORDER if role in present),
        key=lambda role: role != main,
    )


def _subtitle(department, crew_roles):
    """"Yönetmen · Senarist" for crew people, "Oyuncu · Yönetmen" for actors who also direct."""
    main = DEPARTMENT_ROLES.get(department)
    others = [r for r in crew_roles if r != main and r != "Yapımcı"][:2]
    if main:
        return " · ".join([main, *others])
    if department == "Acting":
        return " · ".join(["Oyuncu", *others[:1]])
    return department or ""


def fetch(person_id):
    """Return the person's TMDB profile and filmography (cached)."""
    cache_key = f"{Sources.TMDB.value}_person_{person_id}"
    data = cache.get(cache_key)
    if data is not None and "crew_roles" in data:  # older copies lack the crew part
        return data
    disk_key = f"efe-offline-person-{person_id}"
    try:
        profile = _fetch_profile(person_id)
    except offline.OFFLINE_ERRORS:
        # Offline: the saved copy, but not into the memory cache, so the page is
        # fetched again as soon as the internet is back.
        profile = offline.load(disk_key)
        if profile is None:
            raise
        return profile
    offline.save(disk_key, profile)
    cache.set(cache_key, profile)
    return profile


def _fetch_profile(person_id):
    url = f"{tmdb.base_url}/person/{person_id}"
    try:
        response = services.api_request(
            Sources.TMDB.value,
            "GET",
            url,
            params={**tmdb.base_params, "append_to_response": "combined_credits"},
        )
        biography = response.get("biography") or ""
        if not biography:
            # Many people have no Turkish biography: fall back to English.
            english = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params={**tmdb.base_params, "language": "en-US"},
            )
            biography = english.get("biography") or ""
    except requests.exceptions.HTTPError as error:
        tmdb.handle_error(error)

    credits = _credits(
        (response.get("combined_credits") or {}).get("cast") or [],
        (response.get("combined_credits") or {}).get("crew") or [],
    )
    department = response.get("known_for_department")
    crew_roles = _crew_roles(credits, department)
    data = {
        "id": person_id,
        "name": response.get("name") or "",
        "image": tmdb.get_image_url(response.get("profile_path")),
        "biography": biography,
        "birthday": response.get("birthday"),
        "deathday": response.get("deathday"),
        "place_of_birth": response.get("place_of_birth"),
        "department": department,
        "credits": credits,
        "acting_count": sum(1 for credit in credits if credit["acting"]),
        "crew_roles": crew_roles,
        "crew_first": department in DEPARTMENT_ROLES,
        "subtitle": _subtitle(department, crew_roles),
        "source_url": f"https://www.themoviedb.org/person/{person_id}",
    }
    return data


WATCHED_FOR_RING = {Status.COMPLETED.value, Status.IN_PROGRESS.value}
RING_ROLES = (("acting", "Oyunculuk"), ("Yönetmen", "Yönetmenlik"))


def progress_rings(marked, crew_first=False, today=None):
    """[{label, done, total, percent, dash}] — how many of the person's released works the user
    watched, once for acting and once for directing.

    Announced or unreleased titles are left out, so the ring can actually be completed. The
    person's main role always gets a ring; the other one only if the user watched some of it
    (a director's cameos "as himself" would otherwise show a sad "0/38").
    """
    import datetime  # noqa: PLC0415

    today = (today or datetime.date.today()).isoformat()
    rings = []
    for role, label in RING_ROLES:
        works = [
            credit for credit in marked
            if (credit["acting"] if role == "acting" else role in credit["crew_roles"])
            and credit["date"] and credit["date"] <= today
        ]
        if not works:
            continue
        done = sum(1 for credit in works if credit["status"] in WATCHED_FOR_RING)
        main = (role != "acting") == bool(crew_first)
        if not done and not main:
            continue
        percent = round(done * 100 / len(works))
        rings.append({"key": role, "label": label, "done": done, "total": len(works), "percent": percent,
                      "dash": f"{done * 100 / len(works):.1f}"})
    if crew_first:
        rings.sort(key=lambda ring: ring["key"] == "acting")
    return rings


def mark(user, credits):
    """Add the in-app link and the user's tracking status to each credit."""
    ids = {
        MediaTypes.MOVIE.value: [c["id"] for c in credits if c["media_type"] == MediaTypes.MOVIE.value],
        MediaTypes.TV.value: [c["id"] for c in credits if c["media_type"] == MediaTypes.TV.value],
    }
    statuses = {}
    for media_type, model in ((MediaTypes.MOVIE.value, Movie), (MediaTypes.TV.value, TV)):
        if not ids[media_type]:
            continue
        for media_id, status in model.objects.filter(
            user=user,
            item__source=Sources.TMDB.value,
            item__media_id__in=ids[media_type],
        ).order_by("created_at").values_list("item__media_id", "status"):
            # Several entries of one movie are possible: the newest one wins.
            statuses[(media_type, media_id)] = status

    marked = []
    for credit in credits:
        status = statuses.get((credit["media_type"], credit["id"]))
        label, colour = STATUS_LABELS.get(status, (None, None))
        marked.append(
            {
                **credit,
                "character": ", ".join(credit["characters"]),
                "url": reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_type": credit["media_type"],
                        "media_id": credit["id"],
                        "title": slugify(credit["title"]) or "x",
                    },
                ),
                "status": status,
                "status_label": label,
                "status_colour": colour,
                "watched": status == Status.COMPLETED.value,
            },
        )
    return marked
