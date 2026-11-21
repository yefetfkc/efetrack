"""More Telegram commands: /takvim, /oyuncu, /kisa, /sunucu and /ayarlar.

Kept apart from efe/telegram.py (which dispatches to these) so that file stays readable.
"""

import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import formats, timezone, translation

from app.models import MediaTypes, Sources, Status

logger = logging.getLogger(__name__)


def _data_dir():
    """The folder of the database (a Docker volume): small state files live next to it."""
    return Path(settings.DATABASES["default"]["NAME"]).parent


# MARK: /ayarlar — notification preferences

# (key, button text, user field or None). Keys without a field are kept in a small JSON file
# (no database change needed); every preference is on by default.
PREFS = [
    ("yeni_bolum", "🔔 Yeni bölüm bildirimi", "release_notifications_enabled"),
    ("gunluk", "🗞️ Günlük özet", "daily_digest_enabled"),
    ("haftalik", "🗓️ Pazartesi haftalık ön izleme", None),
    ("sabah", "☀️ 11:00 izleme önerisi", None),
    ("kapsul", "⏳ Zaman kapsülü", None),
    ("aylik", "📄 Aylık PDF rapor", None),
    ("fragman", "🎞️ Yeni fragman haberi", None),
    ("seri", "🎬 Film serisi haberi", None),
    ("sezon", "📺 Yeni sezon öncesi özet", None),
]
PREF_KEYS = {key for key, _label, _field in PREFS}
# Which master switch each extra notice also needs (they were tied to these before /ayarlar).
NEEDS = {
    "haftalik": "gunluk", "sabah": "gunluk", "kapsul": "gunluk", "aylik": "gunluk",
    "fragman": "yeni_bolum", "seri": "yeni_bolum", "sezon": "yeni_bolum",
}


def _prefs_path():
    return _data_dir() / "efe-telegram-ayarlar.json"


def _load_prefs():
    try:
        return json.loads(_prefs_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_prefs(data):
    path = _prefs_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def pref_on(user, key):
    """Whether the user wants this notice (the extra ones default to on)."""
    field = dict((k, f) for k, _label, f in PREFS).get(key)
    if field:
        return bool(getattr(user, field, True))
    return _load_prefs().get(str(user.id), {}).get(key, True) is not False


def set_pref(user, key, on):
    field = dict((k, f) for k, _label, f in PREFS).get(key)
    if field:
        setattr(user, field, on)
        user.save(update_fields=[field])
        return
    data = _load_prefs()
    data.setdefault(str(user.id), {})[key] = bool(on)
    _save_prefs(data)


def settings_reply(user):
    """(text, keyboard): one button per preference; pressing it flips it."""
    lines = ["⚙️ Bildirim tercihlerin", "Değiştirmek için düğmeye dokun."]
    off_masters = [key for key in ("gunluk", "yeni_bolum") if not pref_on(user, key)]
    if off_masters:
        names = {"gunluk": "Günlük özet", "yeni_bolum": "Yeni bölüm bildirimi"}
        lines.append("")
        lines.extend(
            f"Not: {names[key]} kapalıyken ona bağlı olanlar (gri) da gelmez." for key in off_masters
        )
    rows = []
    for key, label, _field in PREFS:
        on = pref_on(user, key)
        blocked = NEEDS.get(key) in off_masters
        mark = "✅" if on and not blocked else ("⏸" if on else "⬜️")
        rows.append([{"text": f"{mark} {label}", "callback_data": f"ay:{key}"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def toggle_pref(user, key):
    """Flip one preference and return the refreshed (text, keyboard)."""
    if key in PREF_KEYS:
        set_pref(user, key, not pref_on(user, key))
    return settings_reply(user)


# MARK: /kisa — short planned films

SHORT_MINUTES = 100
MAX_SHORT_LINES = 30


def short_films_text(user):
    from efe import planned, planned_meta, tasks_planned  # noqa: PLC0415

    films = [entry for entry in planned_meta.attach(planned.collect(user)) if entry["kind"] == MediaTypes.MOVIE.value]
    if not films:
        return "Planladığın film yok."
    short = sorted(
        (entry for entry in films if entry["minutes"] and entry["minutes"] < SHORT_MINUTES),
        key=lambda entry: (entry["minutes"], entry["title"].casefold()),
    )
    unknown = sum(1 for entry in films if not entry["minutes"])
    if unknown:
        try:
            tasks_planned.start_fill(user)
        except Exception:  # noqa: BLE001 - no broker: answer with what is known
            logger.warning("planned facts task could not be queued", exc_info=True)
    if not short:
        text = f"Planladığın filmler arasında {SHORT_MINUTES} dakikadan kısa olan yok."
    else:
        lines = [f"⏱ {SHORT_MINUTES} dakikadan kısa planlanan filmler ({len(short)}):"]
        for entry in short[:MAX_SHORT_LINES]:
            extra = [f"{entry['minutes']} dk"]
            if entry["year"]:
                extra.insert(0, str(entry["year"]))
            if entry["score"]:
                extra.append(f"★ {entry['score']:.1f}")
            lines.append(f"▸ {entry['title']} · {' · '.join(extra)}")
        if len(short) > MAX_SHORT_LINES:
            lines.append(f"… ve {len(short) - MAX_SHORT_LINES} film daha (sitede Planlananlar → Süre)")
        text = "\n".join(lines)
    if unknown:
        text += f"\n\n{unknown} filmin süresi henüz bilinmiyor; birkaç dakika sonra tekrar /kisa yazarsan onlar da hesaba katılır."
    return text


# MARK: /oyuncu — the user's titles with an actor

PERSON_CHOICES = 5
WATCHED_STATUSES = {
    Status.COMPLETED.value: "izledin",
    Status.IN_PROGRESS.value: "izliyorsun",
    Status.PAUSED.value: "ara verdin",
    Status.DROPPED.value: "bıraktın",
}
MAX_ACTOR_LINES = 30


def search_people(query):
    """TMDB people matching the name, most popular first: [{id, name, known}]."""
    from app.providers import services, tmdb  # noqa: PLC0415

    response = services.api_request(
        Sources.TMDB.value, "GET", f"{tmdb.base_url}/search/person",
        params={**tmdb.base_params, "query": query, "include_adult": "false"},
    )
    people = []
    for hit in response.get("results") or []:
        known = [entry.get("title") or entry.get("name") for entry in hit.get("known_for") or []]
        people.append({"id": hit["id"], "name": hit.get("name") or "", "known": [k for k in known if k][:2],
                       "department": hit.get("known_for_department") or "", "popularity": hit.get("popularity") or 0})
    return people


def actor_command(user, argument):
    """(text, keyboard) for /oyuncu Ad."""
    if not argument:
        return "Oyuncunun adını da yaz: /oyuncu Pedro Pascal", None
    try:
        people = search_people(argument)
    except Exception:  # noqa: BLE001 - provider down or refused
        logger.warning("person search failed", exc_info=True)
        return "TMDB'ye şu an ulaşılamadı, biraz sonra tekrar dene.", None
    if not people:
        return f"\"{argument}\" adında bir oyuncu bulamadım.", None
    actors = [person for person in people if person["department"] == "Acting"] or people
    exact = [person for person in actors if person["name"].casefold() == argument.casefold()]
    if len(exact) == 1 or len(actors) == 1:
        return actor_text(user, (exact or actors)[0]["id"]), None
    buttons = []
    for person in actors[:PERSON_CHOICES]:
        known = f" ({', '.join(person['known'])})" if person["known"] else ""
        buttons.append([{"text": f"{person['name']}{known}"[:60], "callback_data": f"oy:{person['id']}"}])
    return "Hangisi? Dokun:", {"inline_keyboard": buttons}


def actor_text(user, person_id):
    from efe import person as efe_person  # noqa: PLC0415

    try:
        profile = efe_person.fetch(person_id)
    except Exception:  # noqa: BLE001
        logger.warning("person fetch failed", exc_info=True)
        return "TMDB'ye şu an ulaşılamadı, biraz sonra tekrar dene."
    credits = [credit for credit in efe_person.mark(user, profile["credits"]) if credit["acting"]]
    watched = [credit for credit in credits if credit["status"] in WATCHED_STATUSES]
    planned = [credit for credit in credits if credit["status"] == Status.PLANNING.value]
    name = profile["name"]
    if not watched:
        text = f"🎭 {name}: oynadığı yapımlardan izlediğin yok."
        if planned:
            text += f"\nPlanladıkların: {', '.join(credit['title'] for credit in planned[:5])}"
        return text
    lines = [f"🎭 {name} · izlediğin {len(watched)} yapım"]
    for credit in watched[:MAX_ACTOR_LINES]:
        kind = "Dizi" if credit["media_type"] == MediaTypes.TV.value else "Film"
        parts = [f"{credit['title']}" + (f" ({credit['year']})" if credit["year"] else ""), kind]
        if credit["character"]:
            parts.append(credit["character"][:40])
        parts.append(WATCHED_STATUSES[credit["status"]])
        lines.append("▸ " + " · ".join(parts))
    if len(watched) > MAX_ACTOR_LINES:
        lines.append(f"… ve {len(watched) - MAX_ACTOR_LINES} yapım daha")
    if planned:
        lines.append("")
        lines.append(f"📌 Planladıkların: {', '.join(credit['title'] for credit in planned[:5])}")
    return "\n".join(lines)


# MARK: /sunucu — disk, memory and the watchdog

BEKCI_FILE = "bekci-ozet.json"  # written by deploy/bekci/bekci.py every 5 minutes
BEKCI_STALE_MIN = 15


def _disk_line():
    try:
        usage = shutil.disk_usage(_data_dir())
    except OSError:
        return "💽 Disk: okunamadı"
    percent = usage.used * 100 / usage.total
    warn = " ⚠️" if percent >= 85 else ""  # noqa: PLR2004 - the watchdog's limit
    return f"💽 Disk: %{percent:.0f} dolu · boş {usage.free / 1e9:.1f} GB / {usage.total / 1e9:.0f} GB{warn}"


def _memory_line():
    try:
        with open("/proc/meminfo") as handle:  # noqa: PTH123
            info = dict(line.split(":", 1) for line in handle)
        total = int(info["MemTotal"].split()[0]) // 1024
        available = int(info["MemAvailable"].split()[0]) // 1024
    except (OSError, KeyError, ValueError):
        return "🧠 Bellek: bu sistemde okunamıyor"
    warn = " ⚠️" if available < 200 else ""  # noqa: PLR2004 - the watchdog's limit
    return f"🧠 Bellek: {available} MB boş / {total} MB{warn}"


def _watchdog_lines(now=None):
    now = now or time.time()
    try:
        data = json.loads((_data_dir() / BEKCI_FILE).read_text())
        checked = float(data["zaman"])
        problems = dict(data.get("sorunlar") or {})
    except (OSError, ValueError, KeyError, TypeError):
        return ["🛡️ Bekçi: bilgi yok (sunucudaki bekçi güncellenmemiş ya da hiç çalışmamış)"]
    minutes = int((now - checked) // 60)
    if minutes > BEKCI_STALE_MIN:
        return [f"🛡️ Bekçi: ⚠️ {minutes} dakikadır çalışmadı"]
    when = "az önce" if minutes < 1 else f"{minutes} dk önce"
    if not problems:
        return [f"🛡️ Bekçi: sorun yok ✓ (son kontrol {when})"]
    return [f"🛡️ Bekçi: {len(problems)} sorun (son kontrol {when})", *[f"   • {text}" for text in problems.values()]]


def _task_lines():
    from django_celery_beat.models import PeriodicTask  # noqa: PLC0415

    names = {
        "Send release notifications": "Yeni bölüm kontrolü",
        "Send daily digest": "Günlük özet",
        "Send weekly preview": "Haftalık ön izleme",
        "Check film series news": "Film serisi haberleri",
        "Check new trailers": "Yeni fragman kontrolü",
    }
    lines = []
    for task in PeriodicTask.objects.filter(task__in=names).order_by("task"):
        when = "hiç çalışmadı" if task.last_run_at is None else timezone.localtime(task.last_run_at).strftime("%d.%m %H:%M")
        lines.append(f"   ▸ {names[task.task]}: son {when}")
    return lines


def server_text():
    lines = ["🖥️ efeTrack sunucu durumu", f"Uygulama çalışıyor ✓ · sürüm {settings.VERSION}", ""]
    lines += [_disk_line(), _memory_line(), *_watchdog_lines()]
    tasks = _task_lines()
    if tasks:
        lines += ["", "⚙️ Arka plan görevleri:", *tasks]
    return "\n".join(lines)


# MARK: /takvim — this month's releases as a picture with posters

POSTERS_PER_DAY = 2


def _month_events(user, today):
    from efe import telegram  # noqa: PLC0415

    first = today.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    events = telegram.releases(user, first, last, mute=True)
    by_day = {}
    for event in events:
        by_day.setdefault(timezone.localtime(event.datetime).date(), []).append(event)
    return first, last, by_day, len(events)


def calendar_image(user, today=None):
    """(png bytes, caption) of this month's calendar: day grid, up to two posters a day."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    from efe import report  # noqa: PLC0415

    today = today or timezone.localdate()
    first, last, by_day, total = _month_events(user, today)
    with translation.override(user.language or "tr"):
        month_name = formats.date_format(first, "F Y")

    # Posters of each day (one per title), fetched in parallel from the poster cache
    wanted = {}
    for day, events in by_day.items():
        urls = []
        for event in events:
            url = getattr(event.item, "image", "") or ""
            if url.startswith("https://") and "none.svg" not in url and url not in urls:
                urls.append(url)
        wanted[day] = urls
    unique = {url for urls in wanted.values() for url in urls[:POSTERS_PER_DAY]}
    cell_w, cell_h, gap, pad = 136, 200, 8, 40
    poster_size = (58, 87)
    with ThreadPoolExecutor(max_workers=8) as pool:
        posters = dict(zip(unique, pool.map(lambda url: report._poster(url, poster_size), unique)))  # noqa: SLF001

    lead = first.weekday()  # Monday first
    weeks = (lead + last.day + 6) // 7
    W = pad * 2 + 7 * cell_w + 6 * gap
    top = 200
    H = top + 44 + weeks * (cell_h + gap) + 70
    image = Image.new("RGB", (W, H), report.PAGE)
    draw = ImageDraw.Draw(image)
    report._header(draw, W, "Takvim", month_name)  # noqa: SLF001
    for index, name in enumerate(report.WEEKDAYS):
        x = pad + index * (cell_w + gap) + cell_w / 2
        report._text(draw, (x, top + 16), name, 20, report.MUTED, bold=True, anchor="mm")  # noqa: SLF001
    for offset in range(weeks * 7):
        day = first + timedelta(days=offset - lead)
        col, row = offset % 7, offset // 7
        x0 = pad + col * (cell_w + gap)
        y0 = top + 44 + row * (cell_h + gap)
        box = (x0, y0, x0 + cell_w, y0 + cell_h)
        if day.month != first.month:
            report._rounded(draw, box, 14, report.CHROME)  # noqa: SLF001
            report._text(draw, (x0 + 12, y0 + 10), str(day.day), 18, report.RAISED)  # noqa: SLF001
            continue
        report._rounded(draw, box, 14, report.CARD)  # noqa: SLF001
        if day == today:
            draw.rounded_rectangle(box, radius=14, outline=report.ACCENT_LIGHT, width=3)
        events = by_day.get(day, [])
        colour = report.ACCENT_LIGHT if day == today else (report.FG if events else report.MUTED)
        report._text(draw, (x0 + 12, y0 + 10), str(day.day), 22, colour, bold=True)  # noqa: SLF001
        if not events:
            continue
        report._text(draw, (x0 + cell_w - 12, y0 + 14), f"{len(events)} yayın", 15, report.MUTED, anchor="ra")  # noqa: SLF001
        urls = wanted.get(day, [])[:POSTERS_PER_DAY]
        shown = [posters.get(url) for url in urls if posters.get(url) is not None]
        if shown:
            total_w = len(shown) * poster_size[0] + (len(shown) - 1) * 8
            px = x0 + (cell_w - total_w) // 2
            for poster in shown:
                image.paste(poster, (int(px), y0 + 46))
                px += poster_size[0] + 8
            title_y = y0 + 46 + poster_size[1] + 10
        else:
            title_y = y0 + 52
        title = str(events[0].item.title)
        report._text(draw, (x0 + 10, title_y), report._fit(draw, title, 15, cell_w - 20), 15, report.FG)  # noqa: SLF001
        others = len({event.item_id for event in events}) - 1
        if others > 0:
            report._text(draw, (x0 + 10, title_y + 22), f"+{others} yapım daha", 14, report.ACCENT_LIGHT)  # noqa: SLF001
    footer = f"Bu ay {total} yayın" if total else "Bu ay takip ettiklerinden yayın yok"
    report._text(draw, (W / 2, H - 36), f"{footer} · efeTrack", 20, report.MUTED, anchor="mm")  # noqa: SLF001
    caption = f"📅 {month_name} takvimin · {total} yayın" if total else f"📅 {month_name}: takip ettiklerinden yayın yok"
    return report._png(image), caption  # noqa: SLF001

