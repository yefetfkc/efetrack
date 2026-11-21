"""efeTrack Telegram bot: weekly preview text and chat commands.

The bot token and the allowed chat come from the user's existing Telegram
notification address (``tgram://<token>/<chat id>`` in notification settings),
so nothing secret is stored in code or settings. Only chats listed there are
answered; messages from anyone else are ignored.
"""

import logging
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from itertools import zip_longest

import requests
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import formats, timezone, translation

from app.models import Episode, MediaTypes, Status
from efe import dock, home_stats, random_pick, tg_komutlar
from events.models import Event

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
TGRAM = re.compile(r"^tgram://(?P<token>\d+:[\w-]+)/(?P<chats>[^?#]+)", re.IGNORECASE)
TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value)
MAX_LINES = 40

COMMANDS = [
    ("bugun", "Bugün çıkan bölümler ve yayınlar"),
    ("yarin", "Yarın çıkacaklar"),
    ("hafta", "Önümüzdeki 7 günün yayınları"),
    ("devam", "Yarım kalan dizilerin ve son izlediğin bölüm"),
    ("nezaman", "Bir dizinin sıradaki bölümü ne zaman? (/nezaman Dizi Adı)"),
    ("izledim", "Bir dizinin sıradaki bölümünü izledim yap (/izledim Dizi Adı)"),
    ("son", "Son izlediğin 8 bölüm ve film"),
    ("gecmis", "O gün ne izledin? (/gecmis dün, /gecmis 12 Ekim)"),
    ("nerede", "Hangi platformda izlenir? (/nerede Film ya da Dizi Adı)"),
    ("takvim", "Bu ayın afişli takvim görseli"),
    ("oyuncu", "Bir oyuncunun izlediğin yapımları (/oyuncu Ad)"),
    ("ozet", "Bu haftanın sayıları ve izleme serin"),
    ("istatistik", "Sayıların grafik resmi olarak"),
    ("rapor", "Geçen ayın PDF raporu (/rapor bu ay)"),
    ("rastgele", "Planlananlardan rastgele bir öneri"),
    ("kisa", "100 dakikadan kısa planlanan filmler"),
    ("karsilastir", "İki diziyi yan yana koy (/karsilastir Dizi 1 | Dizi 2)"),
    ("sunucu", "Disk, bellek ve bekçi durumu"),
    ("ayarlar", "Bildirim tercihlerini düğmelerle aç/kapat"),
    ("yardim", "Bu liste"),
]
LINK_HELP = "IMDb, Letterboxd ya da TMDB bağlantısı gönderirsen ne yapmak istediğini sorup listene eklerim."


# MARK: Telegram address of a user


def credentials(user):
    """(bot token, [chat ids]) from the user's tgram:// notification address, or None."""
    for line in (user.notification_urls or "").splitlines():
        match = TGRAM.match(line.strip())
        if match:
            chats = [part for part in match["chats"].split("/") if re.fullmatch(r"-?\d+", part)]
            if chats:
                return match["token"], chats
    return None


def chat_owners():
    """{bot token: {chat id: user}} for every user with a Telegram address."""
    owners = {}
    # Oldest account first: the bot serves the owner's token even if others add one.
    users = get_user_model().objects.exclude(notification_urls="").filter(notification_urls__contains="tgram://")
    for user in users.order_by("id"):
        creds = credentials(user)
        if creds is None:
            continue
        token, chats = creds
        for chat in chats:
            owners.setdefault(token, {}).setdefault(chat, user)  # the oldest account keeps the chat
    return owners


def api(token, method, http_timeout=15, **payload):
    """Call the Bot API; return the result or None (errors are logged without the token)."""
    try:
        response = requests.post(API.format(token=token, method=method), json=payload, timeout=http_timeout)
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning("Telegram %s failed: %s", method, type(error).__name__)
        return None
    if not data.get("ok"):
        logger.warning("Telegram %s refused: %s", method, data.get("description", "?"))
        return None
    return data.get("result")


# MARK: Release lists


def _excluded(user):
    return set(user.notification_excluded_items.values_list("id", flat=True))


def _visible(user, events, excluded=None):
    """Drop events of items the user muted in notification settings."""
    excluded = _excluded(user) if excluded is None else excluded
    return [event for event in events if event.item_id not in excluded]


def releases(user, first_day, last_day, *, mute=False):
    """Tracked releases between two local days (inclusive), in air order."""
    events = list(Event.objects.get_user_events(user, first_day, last_day))
    return _visible(user, events) if mute else events


def _line(event):
    if event.is_sentinel_time:
        return f"▸ {event}"
    return f"▸ {event} · {timezone.localtime(event.datetime):%H:%M}"


def day_list(user, first_day, days, *, mute=False, empty=None):
    """Releases grouped by day, e.g. for /hafta and the Monday preview."""
    last_day = first_day + timedelta(days=days - 1)
    events = releases(user, first_day, last_day, mute=mute)
    if not events:
        return empty or "Bu günlerde takip ettiklerinden yayın yok."
    by_day = {}
    for event in events:
        by_day.setdefault(timezone.localtime(event.datetime).date(), []).append(event)
    lines = []
    with translation.override(user.language or "tr"):
        for day in sorted(by_day):
            if lines:
                lines.append("")
            lines.append(formats.date_format(day, "l, j F"))
            lines.extend(_line(event) for event in by_day[day])
    lines.append("")
    lines.append(f"Toplam {len(events)} yayın")
    return _clip(lines)


def _clip(lines):
    if len(lines) > MAX_LINES:
        lines = [*lines[: MAX_LINES - 1], f"… ve {len(lines) - MAX_LINES + 1} satır daha (sitede Takvim)"]
    return "\n".join(lines)


def week_title(user, first_day):
    """'🗓️ Bu hafta · 17–23 Kasım' style title."""
    last_day = first_day + timedelta(days=6)
    with translation.override(user.language or "tr"):
        if first_day.month == last_day.month:
            span = f"{first_day.day}–{formats.date_format(last_day, 'j F')}"
        else:
            span = f"{formats.date_format(first_day, 'j F')} – {formats.date_format(last_day, 'j F')}"
    return f"🗓️ Bu hafta · {span}"


def weekly_preview(user, today=None):
    """(title, body) of the Monday message: this week's releases."""
    today = today or timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    with translation.override(user.language or "tr"):
        body = day_list(user, monday, 7, mute=True, empty="Bu hafta takip ettiklerinden yeni bir şey yayınlanmıyor.")
    return week_title(user, monday), body


# MARK: Shows of the user


def fold(text):
    """Compare titles without case or Turkish/other accents: "Şahsiyet" ~ "sahsiyet", "İ"/"ı" ~ "i"."""
    text = (text or "").replace("İ", "i").replace("I", "i").replace("ı", "i").casefold()
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).strip()


def find_shows(user, query, limit=6):
    """The user's TV shows whose title contains the query (in progress first).

    Matching is done in Python: SQLite's case-insensitive search knows only ASCII letters.
    """
    query = fold(query)
    if not query:
        return []
    tv_model = apps.get_model("app", "tv")
    shows = [tv for tv in tv_model.objects.filter(user=user).select_related("item") if query in fold(tv.item.title)]
    exact = [tv for tv in shows if fold(tv.item.title) == query]
    if len(exact) == 1:
        return exact
    if exact:  # same title twice (e.g. US and UK versions): let the user choose
        shows = exact
    order = {Status.IN_PROGRESS.value: 0, Status.PLANNING.value: 1, Status.PAUSED.value: 2}
    shows.sort(key=lambda tv: (order.get(tv.status, 3), tv.item.title))
    return shows[:limit]


def _last_watched(user, tv):
    return (
        Episode.objects.filter(
            related_season__user=user,
            related_season__item__media_id=tv.item.media_id,
            related_season__item__source=tv.item.source,
        )
        .select_related("item")
        .order_by(F("item__season_number").desc(), F("item__episode_number").desc())
        .first()
    )


def next_release(tv):
    """The next upcoming event of a show (any of its seasons), or None."""
    return (
        Event.objects.filter(
            item__media_id=tv.item.media_id,
            item__source=tv.item.source,
            item__media_type=MediaTypes.SEASON.value,
            datetime__gte=timezone.now(),
        )
        .select_related("item")
        .order_by("datetime")
        .first()
    )


def when_text(user, tv):
    """Answer for /nezaman."""
    event = next_release(tv)
    last = _last_watched(user, tv)
    seen = f"\nSon izlediğin: S{last.item.season_number}B{last.item.episode_number}" if last else ""
    if event is None:
        return f"{tv.item.title}: takvimde yaklaşan bölüm yok (dizi bitmiş ya da tarih açıklanmamış).{seen}"
    local = timezone.localtime(event.datetime)
    with translation.override(user.language or "tr"):
        day = formats.date_format(local.date(), "j F l")
    days = (local.date() - timezone.localdate()).days
    soon = "bugün" if days == 0 else "yarın" if days == 1 else f"{days} gün sonra"
    hour = "" if event.is_sentinel_time else f" {local:%H:%M}"
    return f"{event}\n{day}{hour} ({soon}){seen}"


class ProviderError(Exception):
    """The show's season data could not be loaded."""


def next_to_watch(user, tv):
    """(season, episode, aired) of the first episode after the last one watched, or None.

    Uses the provider's season data (cached), like the season page.
    """
    from efe import bulk_watch  # noqa: PLC0415 - provider imports are heavy

    last = _last_watched(user, tv)
    season_number, after = (last.item.season_number, last.item.episode_number) if last else (1, 0)
    today = timezone.localdate()
    for number in (season_number, season_number + 1):
        try:
            episodes = bulk_watch._season_episodes(tv.item.media_id, tv.item.source, number)  # noqa: SLF001
        except Exception as error:
            if number == season_number:
                # The current season must be known; never jump ahead on a provider error.
                raise ProviderError from error
            continue  # a missing next season is normal
        start = after if number == season_number else 0
        for episode in episodes:
            if episode["episode_number"] > start:
                aired = bulk_watch._air_date(episode)  # noqa: SLF001
                return number, episode["episode_number"], aired is not None and aired <= today
    return None


def mark_watched(user, tv, season_number, episode_number):
    """Watch up to S/E now; return (count, undo token). Raises BulkWatchError."""
    from efe import bulk_watch  # noqa: PLC0415
    from efe import undo as efe_undo  # noqa: PLC0415

    before = efe_undo.before_state(user, tv.item.media_id, tv.item.source, season_number)
    created = bulk_watch.mark_up_to(user, tv.item.media_id, tv.item.source, season_number, episode_number)
    token = efe_undo.remember(
        user,
        created,
        f"{tv.item.title} S{season_number}B{episode_number} izlendi (Telegram)",
        before=before,
    )
    return len([episode for episode in created if episode is not None]), token


# MARK: Other answers


def continuing_text(user):
    shows = dock.continuing(user, limit=10)
    if not shows:
        return "Yarım kalan dizin yok."
    lines = ["Devam ettiklerin (son izlenen bölüm):"]
    lines.extend(f"▸ {show['title']} · {show['last']}" for show in shows)
    return "\n".join(lines)


def recent_text(user, limit=8):
    movie_model = apps.get_model("app", "movie")
    rows = [
        (episode.end_date, f"{episode.item.title} S{episode.item.season_number}B{episode.item.episode_number}")
        for episode in Episode.objects.filter(related_season__user=user, end_date__isnull=False)
        .select_related("item")
        # Same minute (bulk "watched up to"): newest episode first.
        .order_by("-end_date", "-item__season_number", "-item__episode_number")[:limit]
    ]
    rows += [
        (movie.end_date, f"🎬 {movie.item.title}")
        for movie in movie_model.objects.filter(user=user, end_date__isnull=False)
        .select_related("item")
        .order_by("-end_date")[:limit]
    ]
    rows.sort(key=lambda row: row[0], reverse=True)
    if not rows:
        return "Henüz izleme kaydın yok."
    lines = ["Son izlediklerin:"]
    with translation.override(user.language or "tr"):
        for when, title in rows[:limit]:
            lines.append(f"▸ {title} · {formats.date_format(timezone.localtime(when), 'j M H:i')}")
    return "\n".join(lines)


def stats_text(user):
    stats = home_stats.build(user)
    change = stats.week_change
    trend = "geçen haftanın aynı günleriyle aynı" if change == 0 else (
        f"geçen haftadan {abs(change)} {'fazla' if change > 0 else 'az'}"
    )
    lines = [
        f"Bu hafta: {stats.week_episodes} bölüm, {stats.week_movies} film ({trend})",
        f"İzleme serisi: {stats.streak} gün (en iyi {stats.best_streak})",
    ]
    if stats.streak_at_risk:
        lines.append("Bugün bir şey izlemezsen seri bozulacak.")
    if stats.upcoming_next is not None:
        lines.append(f"Sıradaki yayın: {stats.upcoming_next.event}")
    return "\n".join(lines)


def random_text(user):
    chosen, _reel, count = random_pick.pick(user, random_pick.available_media_types(user))
    if chosen is None:
        return "Planlananlar listen boş."
    kind = {MediaTypes.MOVIE.value: "Film", MediaTypes.TV.value: "Dizi"}.get(chosen.item.media_type, chosen.item.media_type)
    return f"🎲 {chosen.item.title} ({kind})\n{count} planlanan arasından seçildi. Başka biri için tekrar /rastgele yaz."


# MARK: /gecmis (what was watched on a day)

MONTHS = {
    fold_name: number
    for number, fold_name in enumerate(
        "ocak subat mart nisan mayis haziran temmuz agustos eylul ekim kasim aralik".split(), start=1
    )
}
DATE_EXAMPLES = "/gecmis bugün · /gecmis dün · /gecmis 12 Ekim · /gecmis 12.10.2025 · /gecmis 12/10"


def _make_date(day, month, year, today):
    """A date or None. Without a year: this year, or last year when that day is still ahead."""
    try:
        if year is None:
            found = date(today.year, month, day)
            return found if found <= today else date(today.year - 1, month, day)
        return date(year + 2000 if year < 100 else year, month, day)  # noqa: PLR2004
    except ValueError:
        return None


def parse_date(text, today=None):
    """Turkish day text → date, or None: bugün, dün, 12 Ekim [2025], 12.10[.2025], 12/10, 2025-10-12."""
    today = today or timezone.localdate()
    text = fold(text)
    if text in ("bugun", "today"):
        return today
    if text in ("dun", "yesterday"):
        return today - timedelta(days=1)
    if text in ("evvelsi gun", "evvelki gun", "onceki gun"):
        return today - timedelta(days=2)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return _make_date(int(match[3]), int(match[2]), int(match[1]), today)
    match = re.fullmatch(r"(\d{1,2})\s*[./-]\s*(\d{1,2})(?:\s*[./-]\s*(\d{2}|\d{4}))?", text)
    if match:
        return _make_date(int(match[1]), int(match[2]), int(match[3]) if match[3] else None, today)
    match = re.fullmatch(r"(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?", text)
    if match and match[2] in MONTHS:
        return _make_date(int(match[1]), MONTHS[match[2]], int(match[3]) if match[3] else None, today)
    return None


def history_text(user, day):
    """Episodes and films the user watched on a local day, with times, grouped."""
    if not 1900 <= day.year <= 9000:  # noqa: PLR2004 - far dates would overflow below
        return "Bu tarihte izleme kaydı olamaz. Örnek: /gecmis 12 Ekim"
    start = timezone.make_aware(datetime.combine(day, time.min))
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
    movie_model = apps.get_model("app", "movie")
    episodes = (
        Episode.objects.filter(related_season__user=user, end_date__gte=start, end_date__lt=end)
        .select_related("item")
        .order_by("end_date", "item__season_number", "item__episode_number")
    )
    movies = movie_model.objects.filter(user=user, end_date__gte=start, end_date__lt=end).select_related("item").order_by("end_date")
    with translation.override(user.language or "tr"):
        title = formats.date_format(day, "j F Y, l")
    if not episodes and not movies:
        return f"{title}\nBu gün izleme kaydın yok."
    lines = [f"🗓️ {title}"]
    if episodes:
        lines.extend(["", "📺 Bölümler"])
        lines.extend(
            f"▸ {ep.item.title} S{ep.item.season_number}B{ep.item.episode_number} · {timezone.localtime(ep.end_date):%H:%M}"
            for ep in episodes
        )
    if movies:
        lines.extend(["", "🎬 Filmler"])
        lines.extend(f"▸ {movie.item.title} · {timezone.localtime(movie.end_date):%H:%M}" for movie in movies)
    lines.extend(["", f"Toplam {len(episodes)} bölüm, {len(movies)} film"])
    return _clip(lines)


def history_command(user, argument):
    if not argument:
        return history_text(user, timezone.localdate()), None
    day = parse_date(argument)
    if day is None:
        return f"Tarihi anlayamadım. Şöyle yazabilirsin:\n{DATE_EXAMPLES}", None
    return history_text(user, day), None


# MARK: /nerede (where to watch, Turkey)

WHERE_REGION = "TR"
OFFER_TEXT = {
    "flatrate": "Abonelikle",
    "free": "Ücretsiz",
    "ads": "Reklamlı ücretsiz",
    "rent": "Kirala",
    "buy": "Satın al",
}
KIND_NAME = {MediaTypes.MOVIE.value: "Film", MediaTypes.TV.value: "Dizi"}
WHERE_TMDB_PER_TYPE = 3
WHERE_MAX_CHOICES = 6


def _tmdb_search(media_type, query):
    from app.providers import tmdb  # noqa: PLC0415

    return tmdb.search(media_type, query, 1)["results"]


def where_candidates(user, query):
    """[(media type, TMDB id, title)]: the user's library first, else the best TMDB results."""
    needle = fold(query)
    found = []
    for media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        model = apps.get_model("app", media_type)
        seen = set()
        for row in model.objects.filter(user=user, item__source="tmdb").select_related("item"):
            if needle in fold(row.item.title) and row.item.media_id not in seen:
                seen.add(row.item.media_id)
                found.append((media_type, row.item.media_id, row.item.title))
    if found:
        exact = [row for row in found if fold(row[2]) == needle]
        if len(exact) == 1:
            return exact
        return (exact or found)[:WHERE_MAX_CHOICES]
    results = []
    for media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        try:
            hits = _tmdb_search(media_type, query)[:WHERE_TMDB_PER_TYPE]
        except Exception:  # noqa: BLE001 - one type failing still leaves the other
            logger.warning("Where: TMDB search failed for %s", media_type)
            continue
        results.append([(media_type, str(hit["media_id"]), _title_year(hit)) for hit in hits])
    # Interleave so the best movie and the best show come first.
    merged = [row for pair in zip_longest(*results) for row in pair if row]
    return merged[:WHERE_MAX_CHOICES]


def _title_year(hit):
    year = hit.get("release_year")
    return f"{hit['title']} ({year})" if year else hit["title"]


def where_text(media_type, media_id):
    """Platforms in Turkey for one title, grouped by offer type."""
    from app.providers import tmdb  # noqa: PLC0415
    from efe import watch_providers  # noqa: PLC0415

    try:
        metadata = services_metadata(media_type, media_id, "tmdb")
    except Exception:  # noqa: BLE001
        return "Platform bilgisine şu an ulaşılamadı, biraz sonra tekrar dene."
    title = metadata.get("title") or ""
    decorated = watch_providers.decorate(tmdb.group_providers(metadata.get("providers"), WHERE_REGION), title)
    head = f"{'🎬' if media_type == MediaTypes.MOVIE.value else '📺'} {title} ({KIND_NAME.get(media_type, media_type)}) · Türkiye"
    justwatch = watch_providers.justwatch_search_url(title, WHERE_REGION)
    if not decorated or not decorated["has_any"]:
        return f"{head}\nŞu an Türkiye'de bir platformda görünmüyor.\nJustWatch: {justwatch}"
    lines = [head]
    for offer in decorated["offers"]:
        names = ", ".join(provider["provider_name"] for provider in offer["providers"])
        lines.append(f"▸ {OFFER_TEXT.get(offer['key'], offer['key'])}: {names}")
    lines.append(f"Ayrıntı: {decorated['link'] or justwatch}")
    return "\n".join(lines)


def where_command(user, argument):
    if not argument:
        return "Film ya da dizi adını da yaz: /nerede Dune", None
    candidates = where_candidates(user, argument)
    if not candidates:
        return f"\"{argument}\" bulunamadı. JustWatch'ta ara: {_justwatch(argument)}", None
    if len(candidates) > 1:
        rows = [
            [{"text": f"{'🎬' if kind == MediaTypes.MOVIE.value else '📺'} {title}"[:40], "callback_data": f"nr:{kind}:{media_id}"}]
            for kind, media_id, title in candidates
        ]
        return SHOW_PICK, {"inline_keyboard": rows}
    return where_text(candidates[0][0], candidates[0][1]), None


def _justwatch(query):
    from efe import watch_providers  # noqa: PLC0415

    return watch_providers.justwatch_search_url(query, WHERE_REGION)


def help_text():
    lines = ["efeTrack komutları:"]
    lines.extend(f"/{name} · {text}" for name, text in COMMANDS)
    lines.extend(["", f"🔗 {LINK_HELP}"])
    lines.append("")
    lines.append("Dizi adının bir kısmını yazman yeter: /nezaman severance")
    lines.append("Tarih örnekleri: /gecmis dün · /gecmis 12 Ekim · /gecmis 12.10.2025")
    lines.append("Oyuncu: /oyuncu Pedro Pascal")
    lines.append("Başka bir sohbette @efeTrackbot dizi adı yazarak dizini paylaşabilirsin.")
    return "\n".join(lines)


# MARK: Dispatch

SHOW_PICK = "Hangisi? Dokun:"


def _pick_buttons(action, shows):
    return {
        "inline_keyboard": [
            [{"text": tv.item.title[:40], "callback_data": f"{action}:{tv.id}"}] for tv in shows
        ],
    }


def _show_for(user, tv_id):
    tv_model = apps.get_model("app", "tv")
    return tv_model.objects.filter(user=user, id=tv_id).select_related("item").first()


def _watch_offer(user, tv):
    """Reply (text, keyboard) offering to watch the next episode."""
    try:
        found = next_to_watch(user, tv)
    except ProviderError:
        return f"{tv.item.title}: bölüm bilgisine şu an ulaşılamadı, biraz sonra tekrar dene.", None
    if found is None:
        return f"{tv.item.title}: sıradaki bölümü bulamadım.", None
    season, episode, aired = found
    if not aired:
        return f"{tv.item.title} S{season}B{episode} henüz yayınlanmadı. /nezaman {tv.item.title}", None
    button = {"text": f"✓ S{season}B{episode} izledim", "callback_data": f"izle:{tv.id}:{season}:{episode}"}
    return f"{tv.item.title} · sıradaki bölüm S{season}B{episode}", {"inline_keyboard": [[button]]}


def handle_command(user, text):
    """Answer a chat message: (reply text, optional inline keyboard)."""
    with translation.override(user.language or "tr"):
        return _handle_command(user, text)


def _handle_command(user, text):
    command, _, argument = (text or "").strip().partition(" ")
    command = command.split("@", 1)[0].lstrip("/").casefold()
    argument = argument.strip()
    today = timezone.localdate()

    if command in ("start", "help", "yardim", "yardım"):
        return help_text(), None
    if command in ("bugun", "bugün"):
        return day_list(user, today, 1, empty="Bugün takip ettiklerinden yeni bir şey yayınlanmıyor."), None
    if command in ("yarin", "yarın"):
        return day_list(user, today + timedelta(days=1), 1, empty="Yarın takip ettiklerinden yayın yok."), None
    if command == "hafta":
        return day_list(user, today, 7), None
    if command == "devam":
        return continuing_text(user), None
    if command == "son":
        return recent_text(user), None
    if command in ("ozet", "özet"):
        return stats_text(user), None
    if command in ("gecmis", "geçmiş", "gecmiş", "geçmis"):
        return history_command(user, argument)
    if command == "nerede":
        return where_command(user, argument)
    if command == "rastgele":
        return random_text(user), None
    if command in ("sunucu", "durum"):  # /durum: the old name, now part of /sunucu
        return tg_komutlar.server_text(), None
    if command == "takvim":
        data, caption = tg_komutlar.calendar_image(user)
        return FileReply("photo", "efeTrack-takvim.png", data, caption), None
    if command == "oyuncu":
        return tg_komutlar.actor_command(user, argument)
    if command in ("kisa", "kısa"):
        return tg_komutlar.short_films_text(user), None
    if command == "ayarlar":
        return tg_komutlar.settings_reply(user)
    if command == "istatistik":
        from efe import report  # noqa: PLC0415

        return FileReply("photo", "efeTrack-istatistik.png", report.stats_image(user), "📊 efeTrack istatistiğin"), None
    if command == "rapor":
        from efe import report  # noqa: PLC0415

        today = timezone.localdate()
        year, month = (today.year, today.month) if "bu" in argument.casefold() else report.previous_month(today)
        name, data, caption = report.monthly_pdf(user, year, month)
        return FileReply("document", name, data, caption), None
    if command in ("karsilastir", "karşılaştır", "karsilastır", "karşilastir"):
        return compare_command(user, argument)
    if command in ("nezaman", "izledim"):
        if not argument:
            return f"Dizi adını da yaz: /{command} Dizi Adı", None
        shows = find_shows(user, argument)
        if not shows:
            return f"\"{argument}\" adında takip ettiğin bir dizi bulamadım.", None
        if len(shows) > 1:
            return SHOW_PICK, _pick_buttons("ne" if command == "nezaman" else "iz", shows)
        if command == "nezaman":
            return when_text(user, shows[0]), None
        if getattr(user, "is_demo", False):
            return "Demo hesabı yalnız görüntüleyebilir.", None
        return _watch_offer(user, shows[0])
    return "Bu komutu tanımıyorum. /yardim yazarsan listeyi gösteririm.", None


def handle_callback(user, data):
    """Answer an inline button: (reply text, optional keyboard)."""
    with translation.override(user.language or "tr"):
        return _handle_callback(user, data)


def _handle_callback(user, data):
    from efe import bulk_watch  # noqa: PLC0415
    from efe import undo as efe_undo  # noqa: PLC0415

    action, _, rest = (data or "").partition(":")
    if action == "ay":
        if getattr(user, "is_demo", False):
            return "Demo hesabı yalnız görüntüleyebilir.", None
        return tg_komutlar.toggle_pref(user, rest)
    if action == "oy":
        if not rest.isdigit():
            return "Bu düğme artık geçerli değil.", None
        return tg_komutlar.actor_text(user, int(rest)), None
    if action == "oneri":
        return random_text(user), None
    if action == "ekle":
        from efe import link_add  # noqa: PLC0415

        parts = rest.split(":")
        if len(parts) != 3:  # noqa: PLR2004
            return "Bu düğme artık geçerli değil.", None
        if getattr(user, "is_demo", False) and parts[2] != "x":
            return "Demo hesabı yalnız görüntüleyebilir.", None
        try:
            return link_add.apply(user, *parts), None
        except link_add.LinkError as error:
            return str(error), None
    if action == "nr":
        kind, _, media_id = rest.partition(":")
        if kind not in KIND_NAME or not media_id.isdigit():
            return "Bu düğme artık geçerli değil.", None
        return where_text(kind, media_id), None
    if action == "kars":
        ids = [int(part) for part in rest.split(":") if part.isdigit()]
        shows = [tv for tv in (_show_for(user, tv_id) for tv_id in ids) if tv is not None]
        if len(shows) != 2:  # noqa: PLR2004
            return "Bu düğme artık geçerli değil.", None
        return compare_text(user, shows[0], shows[1]), None
    if action == "geri":
        count = efe_undo.undo(user, rest)
        if count is None:
            return "Geri alınamadı: süre (2 dakika) dolmuş.", None
        return "↩︎ Geri alındı, izleme kaydı silindi.", None
    parts = rest.split(":")
    if not parts or not parts[0].isdigit():
        return "Bu düğme artık geçerli değil.", None
    tv = _show_for(user, int(parts[0]))
    if tv is None:
        return "Bu dizi artık listende değil.", None
    if action == "ne":
        return when_text(user, tv), None
    if getattr(user, "is_demo", False):
        return "Demo hesabı yalnız görüntüleyebilir.", None
    if action == "iz":
        return _watch_offer(user, tv)
    if action == "izle" and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        season, episode = int(parts[1]), int(parts[2])
        try:
            count, token = mark_watched(user, tv, season, episode)
        except bulk_watch.BulkWatchError as error:
            return str(error), None
        extra = "" if count <= 1 else f" (arada kalan {count - 1} bölümle birlikte)"
        text = f"✓ {tv.item.title} S{season}B{episode} izlendi{extra}."
        keyboard = (
            {"inline_keyboard": [[{"text": "↩︎ Geri al (2 dk)", "callback_data": f"geri:{token}"}]]} if token else None
        )
        return text, keyboard
    return "Bu düğme artık geçerli değil.", None


def process_update(update, owners):
    """Handle one update from getUpdates. ``owners``: {chat id: user}. Returns the reply or None."""
    message = update.get("message") or update.get("edited_message")
    callback = update.get("callback_query")
    inline = update.get("inline_query")
    if inline:
        # Private chat id == Telegram user id: only the owner can use inline mode.
        user = owners.get(str((inline.get("from") or {}).get("id", "")))
        if user is None:
            return None
        return {"inline": inline.get("id"), "results": inline_results(user, inline.get("query") or "")}
    if message:
        chat = str(message.get("chat", {}).get("id", ""))
        user = owners.get(chat)
        text = message.get("text") or message.get("caption") or ""
        if user is None:
            return None
        if not text.startswith("/"):
            return link_reply(user, chat, text)
        reply, keyboard = handle_command(user, text)
        if isinstance(reply, FileReply):
            return {"chat_id": chat, "file": reply}
        return {"chat_id": chat, "text": reply, "keyboard": keyboard}
    if callback:
        chat = str((callback.get("message") or {}).get("chat", {}).get("id", ""))
        user = owners.get(chat)
        if user is None:
            return None
        reply, keyboard = handle_callback(user, callback.get("data"))
        answer = {"chat_id": chat, "text": reply, "keyboard": keyboard, "callback_id": callback.get("id")}
        if (callback.get("data") or "").startswith("ay:"):
            # Settings: update the same message instead of sending a new one per press.
            answer["edit"] = (callback.get("message") or {}).get("message_id")
        if (callback.get("data") or "").startswith("ekle:"):
            # The question is answered: remove its buttons so it cannot be pressed twice.
            answer["clear_buttons"] = (callback.get("message") or {}).get("message_id")
        return answer
    return None



# MARK: links (IMDb / Letterboxd / TMDB) and /karsilastir


def link_reply(user, chat, text):
    """Reply to a plain message: a known link → "what do you want to do?", otherwise None."""
    from efe import link_add  # noqa: PLC0415

    url = link_add.find_link(text)
    if url is None:
        if "http" in text.casefold():
            return {"chat_id": chat, "text": f"Bu bağlantıyı tanımadım. {LINK_HELP}", "keyboard": None}
        return None
    try:
        with translation.override(user.language or "tr"):
            poster, caption, keyboard = link_add.offer(user, url)
    except link_add.LinkError as error:
        return {"chat_id": chat, "text": str(error), "keyboard": None}
    return {"chat_id": chat, "text": caption, "keyboard": keyboard, "photo": poster}


COMPARE_SPLIT = re.compile(r"\s*(?:\||\bvs\.?\b|\bile\b|,|/|;)\s*", re.IGNORECASE)


def compare_command(user, argument):
    """/karsilastir A | B → comparison text (or buttons when a name matches several shows)."""
    argument = argument or ""
    # With "|" only that separates (titles may contain commas or "/": "Love, Death & Robots", "Fate/Zero").
    parts = argument.split("|") if "|" in argument else COMPARE_SPLIT.split(argument)
    names = [name for name in parts if name.strip()]
    if len(names) != 2:  # noqa: PLR2004
        return "İki dizi adı yaz, araya | koy: /karsilastir Breaking Bad | Better Call Saul", None
    found = []
    for name in names:
        shows = find_shows(user, name)
        if not shows:
            return f"\"{name.strip()}\" adında takip ettiğin bir dizi bulamadım.", None
        found.append(shows)
    first, second = found
    if len(first) == 1 and len(second) == 1:
        if first[0].id == second[0].id:
            return "İki farklı dizi yaz; ikisi de aynı diziye çıktı.", None
        return compare_text(user, first[0], second[0]), None
    # Ambiguous: offer every pair (at most a few buttons).
    rows = []
    for a in first[:3]:
        for b in second[:3]:
            if a.id != b.id:
                rows.append([{"text": f"{a.item.title[:28]} ⟷ {b.item.title[:28]}", "callback_data": f"kars:{a.id}:{b.id}"}])
    if not rows:
        return "İki farklı dizi yaz; ikisi de aynı diziye çıktı.", None
    return "Hangilerini karşılaştırayım? Dokun:", {"inline_keyboard": rows[:6]}


def _compare_facts(user, tv):
    from efe import media_facts  # noqa: PLC0415

    item = tv.item
    facts = {"title": item.title, "score": tv.score, "status": tv.get_status_display()}
    metadata = {}
    if item.source == "tmdb":
        try:
            metadata = services_metadata(MediaTypes.TV.value, item.media_id, item.source)
        except Exception:  # noqa: BLE001 - the line shows "?"
            metadata = {}
    details = metadata.get("details") or {}
    facts["tmdb"] = metadata.get("score")
    facts["seasons"] = details.get("seasons")
    facts["episodes"] = details.get("episodes")
    start = str(details.get("first_air_date") or "")[:4]
    end = str(details.get("last_air_date") or "")[:4]
    facts["years"] = (start if not end or end == start else f"{start}–{end}") if start else None
    country = media_facts.country(metadata) if metadata else None
    facts["country"] = country["text"].replace(" Yapımı", "") if country else None
    facts["length"] = None
    if item.source == "tmdb":
        try:
            facts["length"] = media_facts.show_total(item.media_id, user)
        except Exception:  # noqa: BLE001
            logger.warning("Compare: no total length for %s", item.media_id)
    watched = (
        Episode.objects.filter(related_season__related_tv=tv, end_date__isnull=False)
        .values("item__season_number", "item__episode_number")
        .distinct()
        .count()
    )
    total = (facts["length"] or {}).get("episodes") or facts["episodes"]
    facts["watched"], facts["total"] = watched, total
    return facts


def services_metadata(media_type, media_id, source):
    from app.providers import services  # noqa: PLC0415

    return services.get_media_metadata(media_type, media_id, source)


def _num(value):
    if value is None:
        return "–"
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.1f}".replace(".", ",")


def compare_text(user, first, second):
    """Side-by-side facts of two shows (A / B blocks, the bigger value marked)."""
    a, b = _compare_facts(user, first), _compare_facts(user, second)
    lines = ["⚖️ Karşılaştırma", f"🅰 {a['title']}", f"🅱 {b['title']}"]

    def block(icon, label, left, right, better=None):
        lines.extend(["", f"{icon} {label}"])
        mark_a = mark_b = ""
        if better is not None and better[0] is not None and better[1] is not None and better[0] != better[1]:
            mark_a, mark_b = (" ◀" if better[0] > better[1] else ""), (" ◀" if better[1] > better[0] else "")
        lines.append(f"🅰 {left}{mark_a}")
        lines.append(f"🅱 {right}{mark_b}")

    def length(facts):
        line = facts["length"]
        return (("≈ " if line["estimated"] else "") + line["total"]) if line else "?"

    block("⏱", "Toplam süre", length(a), length(b))
    block(
        "📺", "Sezon · bölüm",
        f"{a['seasons'] or '?'} sezon · {a['total'] or '?'} bölüm",
        f"{b['seasons'] or '?'} sezon · {b['total'] or '?'} bölüm",
    )
    block("⭐", "TMDB puanı", _num(a["tmdb"]), _num(b["tmdb"]), (a["tmdb"], b["tmdb"]))
    block("★", "Puanım", _num(a["score"]), _num(b["score"]),
          (float(a["score"]) if a["score"] is not None else None, float(b["score"]) if b["score"] is not None else None))

    def progress(facts):
        if not facts["total"]:
            return f"{facts['watched']} bölüm izledin · {facts['status']}"
        percent = round(100 * min(facts["watched"], facts["total"]) / facts["total"])
        return f"{facts['watched']}/{facts['total']} (%{percent}) · {facts['status']}"

    block("✅", "İlerlemen", progress(a), progress(b))
    def remaining(facts):
        line = facts["length"] or {}
        if line.get("remaining"):
            return line["remaining"]
        if facts["total"] and facts["watched"] >= facts["total"]:
            return "bitti"
        if not facts["watched"] and line.get("total"):
            return line["total"] + " (hiç başlamadın)"
        return "–"

    block("⏳", "Kalan süre", remaining(a), remaining(b))
    if a["years"] or b["years"]:
        block("🗓", "Yayın yılları", a["years"] or "?", b["years"] or "?")
    if a["country"] or b["country"]:
        block("🌍", "Yapım", a["country"] or "?", b["country"] or "?")
    return "\n".join(lines)


# MARK: time capsule (a year ago today)


def time_capsule(user, today=None):
    """(poster url or None, text) of what the user watched on this day in earlier years, or None."""
    from efe import on_this_day  # noqa: PLC0415

    years = on_this_day.build(user, today)
    if not years:
        return None
    lines = ["⏳ Zaman kapsülü"]
    poster = None
    for year in years:
        lines.extend(["", f"{year.heading} ({formats.date_format(year.day, 'j F Y')}):"])
        for entry in year.entries:
            lines.append(f"▸ {entry.title}: {entry.text}")
            image = entry.image or ""
            if poster is None and image.startswith("https://") and "none.svg" not in image:
                poster = image
    return poster, "\n".join(lines)


def send_time_capsules(today=None):
    """Task: the time capsule to every user with a Telegram address (daily digest on)."""
    from events.notifications import send_user_notification  # noqa: PLC0415

    sent = 0
    for user in get_user_model().objects.exclude(notification_urls="").filter(daily_digest_enabled=True):
        if not tg_komutlar.pref_on(user, "kapsul"):
            continue
        try:
            with translation.override(user.language or "tr"):
                capsule = time_capsule(user, today)
        except Exception:  # one user's error must not stop the others
            logger.exception("Time capsule failed for %s", user.username)
            continue
        if capsule is None:
            continue
        poster, text = capsule
        creds = credentials(user)
        delivered = False
        if creds:
            token, chats = creds
            for chat in chats:
                if poster and len(text) <= CAPTION_LIMIT:
                    result = api(token, "sendPhoto", chat_id=chat, photo=poster, caption=text)
                    if result is None:
                        result = api(token, "sendMessage", chat_id=chat, text=text)
                else:
                    result = api(token, "sendMessage", chat_id=chat, text=text)
                delivered = delivered or result is not None
        urls = [u.strip() for u in user.notification_urls.splitlines() if u.strip()]
        others = urls if not delivered else [u for u in urls if not TGRAM.match(u)]
        if others:
            send_user_notification(user, others, "⏳ Zaman kapsülü", text.split("\n", 1)[-1].strip())
        sent += 1
    return f"Time capsule sent to {sent} user(s)"


# MARK: release notifications with posters

CAPTION_LIMIT = 1024  # Telegram photo caption limit
ALBUM_LIMIT = 10


def _poster_url(event):
    image = getattr(event.item, "image", "") or ""
    return image if image.startswith("https://") and "none.svg" not in image else None


def send_release_photos(urls, title, body, releases):
    """Send release notifications to Telegram addresses as poster photo(s) with the text.

    One release: one photo with the message as caption. 2–10: an album, the message under
    it. Returns the addresses still to be sent as plain text (non-Telegram, too long, failed).
    """
    caption = f"{title}\n\n{body}"
    posters = []
    for event in releases:
        poster = _poster_url(event)
        if poster and poster not in posters:
            posters.append(poster)
    if not posters or len(posters) > ALBUM_LIMIT or len(caption) > CAPTION_LIMIT:
        return list(urls)
    remaining = []
    for url in urls:
        match = TGRAM.match(url.strip())
        if not match:
            remaining.append(url)
            continue
        token = match["token"]
        chats = [part for part in match["chats"].split("/") if re.fullmatch(r"-?\d+", part)]
        ok = bool(chats)
        for chat in chats:
            if len(posters) == 1:
                result = api(token, "sendPhoto", chat_id=chat, photo=posters[0], caption=caption)
            else:
                media = [{"type": "photo", "media": poster} for poster in posters]
                media[0]["caption"] = caption
                result = api(token, "sendMediaGroup", chat_id=chat, media=media)
            ok = ok and result is not None
        if not ok:
            remaining.append(url)  # a photo could not be sent: fall back to text
    return remaining



# MARK: files (photos, PDF)


class FileReply:
    """A photo or document to send instead of a text reply."""

    def __init__(self, kind, name, data, caption=""):
        self.kind, self.name, self.data, self.caption = kind, name, data, caption


def send_file(token, chat, reply):
    """Upload a FileReply (multipart); returns the API result or None."""
    method, fieldname, mime = (
        ("sendPhoto", "photo", "image/png") if reply.kind == "photo" else ("sendDocument", "document", "application/pdf")
    )
    try:
        response = requests.post(
            API.format(token=token, method=method),
            data={"chat_id": chat, "caption": reply.caption[:CAPTION_LIMIT]},
            files={fieldname: (reply.name, reply.data, mime)},
            timeout=60,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning("Telegram %s failed: %s", method, type(error).__name__)
        return None
    if not data.get("ok"):
        logger.warning("Telegram %s refused: %s", method, data.get("description", "?"))
        return None
    return data.get("result")


# MARK: inline mode (@efeTrackbot name, in any chat)


def _share_caption(user, media, kind):
    item = media.item
    lines = [f"{'📺' if kind == MediaTypes.TV.value else '🎬'} {item.title}"]
    if media.score is not None:
        lines.append(f"★ Puanım: {float(media.score):.1f}".replace(".", ","))
    status = media.get_status_display()
    if kind == MediaTypes.TV.value:
        watched = (
            Episode.objects.filter(related_season__related_tv=media)
            .values("item__season_number", "item__episode_number")
            .distinct()
            .count()
        )
        status += f" · {watched} bölüm izledim"
    lines.append(status)
    if item.source == "tmdb":
        lines.append(f"https://www.themoviedb.org/{'tv' if kind == MediaTypes.TV.value else 'movie'}/{item.media_id}")
    lines.append("— efeTrack")
    return "\n".join(lines)


def inline_results(user, query, limit=12):
    """Photo results (poster + caption) of the user's shows and films matching the query."""
    needle = fold(query)
    found = []
    for kind in (MediaTypes.TV.value, MediaTypes.MOVIE.value):
        model = apps.get_model("app", kind)
        rows = model.objects.filter(user=user).select_related("item")
        if not needle:  # empty query: recently changed ones
            rows = rows.order_by("-id")[:limit]
        for media in rows:
            if needle and needle not in fold(media.item.title):
                continue
            image = media.item.image or ""
            if not image.startswith("https://") or "none.svg" in image:
                continue
            found.append((kind, media))
    found = found[:limit]
    from efe.poster_wall import small  # noqa: PLC0415

    return [
        {
            "type": "photo",
            "id": f"{kind}-{media.id}",
            "photo_url": small(media.item.image, "w500"),
            "thumbnail_url": small(media.item.image, "w185"),
            "title": media.item.title,
            "caption": _share_caption(user, media, kind)[:CAPTION_LIMIT],
        }
        for kind, media in found
    ]


# MARK: morning suggestion (11:00)


def morning_suggestion(user):
    """(poster url or None, caption, keyboard) — next episodes of recent shows, else a planned title."""
    tv_model = apps.get_model("app", MediaTypes.TV.value)
    options = []
    for show in dock.continuing(user, limit=6):
        tv = tv_model.objects.filter(user=user, item__title=show["title"], status=Status.IN_PROGRESS.value).select_related("item").first()
        if tv is None:
            continue
        try:
            found = next_to_watch(user, tv)
        except ProviderError:
            continue
        if found and found[2]:
            options.append((tv, found[0], found[1]))
        if len(options) >= 3:  # noqa: PLR2004
            break
    planned, _reel, _count = random_pick.pick(user, random_pick.available_media_types(user))
    lines = ["☀️ Günaydın! Bugün ne izlesem?", ""]
    keyboard_rows = []
    poster = None
    if options:
        tv, season, episode = options[0]
        lines.append(f"▶︎ {tv.item.title} · S{season}B{episode} (kaldığın yerden)")
        poster = tv.item.image
        keyboard_rows.append([{"text": f"✓ S{season}B{episode} izledim", "callback_data": f"izle:{tv.id}:{season}:{episode}"}])
        extra = [f"▸ {o.item.title} · S{s}B{e}" for o, s, e in options[1:]]
        if planned is not None:
            extra.append(f"▸ 🎲 {planned.item.title} (planladıkların arasından)")
        if extra:
            lines += ["", "Ya da:", *extra]
    elif planned is not None:
        lines.append(f"🎲 {planned.item.title} (planladıkların arasından)")
        poster = planned.item.image
    else:
        return None
    keyboard_rows.append([{"text": "🎲 Başka öner", "callback_data": "oneri:"}])
    if poster and (not poster.startswith("https://") or "none.svg" in poster):
        poster = None
    return poster, "\n".join(lines), {"inline_keyboard": keyboard_rows}


def send_morning_suggestions():
    """Task: the 11:00 suggestion to every user with a Telegram address (daily digest on)."""
    from events.notifications import send_user_notification  # noqa: PLC0415

    sent = 0
    for user in get_user_model().objects.exclude(notification_urls="").filter(daily_digest_enabled=True):
        if not tg_komutlar.pref_on(user, "sabah"):
            continue
        with translation.override(user.language or "tr"):
            suggestion = morning_suggestion(user)
        if suggestion is None:
            continue
        poster, caption, keyboard = suggestion
        creds = credentials(user)
        delivered = False
        if creds:
            token, chats = creds
            for chat in chats:
                payload = {"chat_id": chat, "reply_markup": keyboard}
                if poster:
                    result = api(token, "sendPhoto", photo=poster, caption=caption[:CAPTION_LIMIT], **payload)
                else:
                    result = api(token, "sendMessage", text=caption, **payload)
                delivered = delivered or result is not None
        others = [u.strip() for u in user.notification_urls.splitlines() if u.strip() and not TGRAM.match(u.strip())]
        if not delivered:
            others = [u.strip() for u in user.notification_urls.splitlines() if u.strip()]
        if others:
            send_user_notification(user, others, "☀️ Bugün ne izlesem?", caption)
        sent += 1
    return f"Morning suggestion sent to {sent} user(s)"
