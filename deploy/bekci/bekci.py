#!/usr/bin/env python3
"""efeTrack sunucu bekçisi: sorun olunca Telegram'a bir kez yazar, düzelince haber verir.

Sunucuda (Docker'ın dışında) systemd zamanlayıcısıyla 5 dakikada bir çalışır; site
çökse de çalışır. Yalnız Python standart kütüphanesi kullanır.

Telegram bilgisi efeTrack veritabanındaki bildirim adresinden (tgram://...) okunur;
okunamazsa son bilinen kopya (/var/lib/efetrack-bekci, yalnız root) kullanılır.

Ortam: BEKCI_DRY=1 → göndermez, yazdırır. `bekci.py --deneme` → tek deneme mesajı.
"""

import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DB = "/opt/efeTrack/db/db.sqlite3"
BACKUPS = "/opt/efeTrack/backups/efetrack_*.tar.gz"
# Public URL checked for reachability, e.g. https://track.example.com/static/favicon/apple-touch-icon.png
# Empty: the site check is skipped.
SITE = os.environ.get("BEKCI_SITE_URL", "")
SITE_NAME = urllib.parse.urlsplit(SITE).hostname or "Site"
STATE_DIR = "/var/lib/efetrack-bekci"
STATE = os.path.join(STATE_DIR, "durum.json")
CREDS = os.path.join(STATE_DIR, "telegram.json")
# Telegram'daki /sunucu komutu için kısa özet: uygulama kabı veritabanı klasörünü görür.
# İçinde gizli bilgi yok (yalnız zaman ve sorun açıklamaları).
SUMMARY = os.path.join(os.path.dirname(DB), "bekci-ozet.json")

DISK_LIMIT = int(os.environ.get("BEKCI_DISK_LIMIT", "85"))  # yüzde
MEM_MIN_MB = int(os.environ.get("BEKCI_MEM_MIN_MB", "200"))
BACKUP_MAX_H = 27
TASK_MAX_MIN = 30
CONFIRM_RUNS = 2  # geçici aksaklıklar (güncelleme sırasında yeniden başlama) uyarı olmasın
REMIND_H = 12
DRY = os.environ.get("BEKCI_DRY") == "1"
if DRY:  # denemeler gerçek durumu bozmasın
    STATE = "/tmp/efetrack-bekci-deneme.json"
TGRAM = re.compile(r"^tgram://(\d+:[\w-]+)/(-?\d+)")
RECOVERED = {
    "app": "efeTrack uygulaması yeniden sağlıklı",
    "bot": "Telegram botu yeniden çalışıyor",
    "site": f"{SITE_NAME} yeniden açılıyor",
    "yedek": "Gecelik yedek yeniden alınıyor",
    "disk": "Diskte yeniden yer var",
    "bellek": "Bellek rahatladı",
    "gorevler": "Arka plan görevleri yeniden çalışıyor",
    "vt": "Veritabanı yeniden okunabiliyor",
}


def run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def db():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)


# MARK: kontroller → {anahtar: açıklama}


def check_containers(problems):
    health = run("docker", "inspect", "-f", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", "efetrack-app")
    if health != "running healthy":
        problems["app"] = f"efeTrack uygulaması sağlıksız ({health or 'bulunamadı'})"
    bot = run("docker", "inspect", "-f", "{{.State.Status}}", "efetrack-bot")
    if bot != "running":
        problems["bot"] = f"Telegram bot servisi çalışmıyor ({bot or 'bulunamadı'})"


def check_site(problems):
    if not SITE:
        return
    try:
        # Cloudflare, tarayıcı kimliği olmayan (Python-urllib) istekleri 403 ile reddediyor.
        request = urllib.request.Request(SITE, headers={"User-Agent": "efeTrack-bekci/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - sabit adres
            if response.status != 200:
                problems["site"] = f"{SITE_NAME} yanıt vermiyor (kod {response.status})"
    except Exception as error:  # noqa: BLE001
        problems["site"] = f"{SITE_NAME} adresine ulaşılamıyor ({type(error).__name__})"


def check_backup(problems):
    files = glob.glob(BACKUPS)
    if not files:
        problems["yedek"] = "Hiç gecelik yedek yok"
        return
    age_h = (time.time() - max(os.path.getmtime(f) for f in files)) / 3600
    if age_h > BACKUP_MAX_H:
        problems["yedek"] = f"Son gecelik yedek {age_h:.0f} saat önce alınmış"


def check_disk_memory(problems):
    usage = shutil.disk_usage("/")
    percent = usage.used * 100 / usage.total
    if percent >= DISK_LIMIT:
        problems["disk"] = f"Disk %{percent:.0f} dolu (boş {usage.free / 1e9:.1f} GB)"
    try:
        with open("/proc/meminfo") as f:
            info = dict(line.split(":", 1) for line in f)
        available = int(info["MemAvailable"].split()[0]) // 1024
        if available < MEM_MIN_MB:
            problems["bellek"] = f"Boş bellek çok az ({available} MB)"
    except (OSError, KeyError, ValueError):
        pass


def check_tasks(problems):
    try:
        with db() as conn:
            row = conn.execute(
                "select last_run_at from django_celery_beat_periodictask where task = 'Send release notifications'"
            ).fetchone()
    except sqlite3.Error as error:
        problems["vt"] = f"Veritabanı okunamadı ({error})"
        return
    if not row or not row[0]:
        return
    last = datetime.fromisoformat(str(row[0]).replace(" ", "T"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
    if minutes > TASK_MAX_MIN:
        problems["gorevler"] = f"Arka plan görevleri durmuş (yeni bölüm kontrolü {minutes:.0f} dk'dır çalışmadı)"


# MARK: Telegram


def credentials():
    try:
        with db() as conn:
            for (urls,) in conn.execute("select notification_urls from users_user where notification_urls like '%tgram://%' order by id"):
                for line in (urls or "").splitlines():
                    match = TGRAM.match(line.strip())
                    if match:
                        creds = {"token": match[1], "chat": match[2]}
                        save(CREDS, creds)
                        return creds
    except sqlite3.Error:
        pass
    return load(CREDS, None)


def send(text):
    if DRY:
        print("[göndermeden]", text)
        return True
    creds = credentials()
    if not creds:
        print("Telegram adresi yok", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": creds["chat"], "text": text}).encode()
    try:
        url = f"https://api.telegram.org/bot{creds['token']}/sendMessage"
        with urllib.request.urlopen(url, data=data, timeout=20) as response:  # noqa: S310
            return response.status == 200
    except Exception as error:  # noqa: BLE001
        print(f"Telegram gönderilemedi: {type(error).__name__}", file=sys.stderr)
        return False


# MARK: durum dosyası


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def write_summary(problems):
    """/sunucu komutunun okuduğu özet (herkes okuyabilir: 0644)."""
    if DRY:
        return
    # Bekçi root olarak çalışır, klasöre ise uygulama kabı da yazabilir: geçici dosya rastgele adla
    # ve O_EXCL ile açılır (bırakılmış bir bağlantı/symlink izlenmez), izin dosyanın kendisine verilir.
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".bekci-ozet-", suffix=".tmp", dir=os.path.dirname(SUMMARY))
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump({"zaman": time.time(), "sorunlar": problems}, f, ensure_ascii=False)
        os.replace(tmp, SUMMARY)
        tmp = None
    except OSError as error:
        print(f"Özet yazılamadı: {error}", file=sys.stderr)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def main():
    if "--deneme" in sys.argv:
        ok = send("🧪 efeTrack bekçisi çalışıyor. Sunucuda sorun olursa buraya yazacağım.")
        sys.exit(0 if ok else 1)

    problems = {}
    for check in (check_containers, check_site, check_backup, check_disk_memory, check_tasks):
        try:
            check(problems)
        except Exception as error:  # noqa: BLE001 - bir kontrol diğerlerini durdurmasın
            problems[check.__name__] = f"Kontrol hatası: {type(error).__name__}"

    write_summary(problems)

    state = load(STATE, {})  # {anahtar: {"text", "seen", "alerted"}}
    now = time.time()
    new_alerts, still = [], []
    for key, text in problems.items():
        entry = state.get(key, {"seen": 0, "alerted": 0})
        entry["seen"] += 1
        entry["text"] = text
        if entry["seen"] >= CONFIRM_RUNS and (
            not entry["alerted"] or now - entry["alerted"] > REMIND_H * 3600
        ):
            (still if entry["alerted"] else new_alerts).append(text)
            entry["alerted"] = now
        state[key] = entry

    fixed_keys = [key for key, entry in state.items() if key not in problems and entry.get("alerted")]
    fixed = [RECOVERED.get(key, state[key]["text"]) for key in fixed_keys]

    lines = []
    if new_alerts:
        lines += ["⚠️ efeTrack sunucu uyarısı", *[f"• {t}" for t in new_alerts]]
    if still:
        lines += ["⏳ Hâlâ sürüyor", *[f"• {t}" for t in still]]
    if fixed:
        if lines:
            lines.append("")
        lines += ["✅ Düzeldi", *[f"• {t}" for t in fixed]]
    sent = send("\n".join(lines)) if lines else True
    if sent:
        state = {key: entry for key, entry in state.items() if key in problems}
    else:
        # Gönderilemedi: uyarılar ve "düzeldi" haberleri bir sonraki çalışmada yeniden denensin.
        for key, entry in state.items():
            if key in problems and entry["text"] in new_alerts + still:
                entry["alerted"] = 0
        state = {key: entry for key, entry in state.items() if key in problems or key in fixed_keys}
    save(STATE, state)
    if DRY:
        print("sorunlar:", problems or "yok")


if __name__ == "__main__":
    main()
