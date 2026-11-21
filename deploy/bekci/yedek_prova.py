#!/usr/bin/env python3
"""efeTrack yedek provası: son gecelik yedeği deneme ortamında açıp sağlam mı diye bakar.

Her ayın 1'inde systemd zamanlayıcısıyla çalışır (efetrack-prova.timer), sonucu Telegram'a
yazar. Canlı veritabanına ve çalışan uygulamaya dokunmaz:

1. son yedek (backups/efetrack_*.tar.gz) var mı, taze mi;
2. arşiv açılıyor mu, veritabanı boyutu açıklama dosyasıyla (manifest.json) aynı mı;
3. SQLite bütünlük kontrolü (integrity_check) "ok" mu;
4. kayıt sayıları (kullanıcı, dizi, bölüm, film) canlı veritabanına yakın mı;
5. efeTrack imajından internete kapalı, geçici bir konteyner açılır; yedeğin KOPYASI
   üzerinde göçler (migrate) çalışır ve uygulama veriyi okuyabiliyor mu bakılır.

Geçici dosyalar /var/tmp altında açılır ve iş bitince silinir.
Ortam: BEKCI_DRY=1 → Telegram'a göndermez, yazdırır.
"""

import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bekci  # noqa: E402 - Telegram bilgisi ve gönderimi bekçiyle ortak

BACKUPS = "/opt/efeTrack/backups/efetrack_*.tar.gz"
LIVE_DB = "/opt/efeTrack/db/db.sqlite3"
IMAGE = os.environ.get("PROVA_IMAGE", "efetrack:latest")
MAX_AGE_H = 30
CONTAINER_TIMEOUT = 600
TABLES = (("users_user", "kullanıcı"), ("app_tv", "dizi"), ("app_episode", "bölüm"), ("app_movie", "film"))
# Yedek, canlıdan en çok birkaç saat eski: kayıtların bundan fazlası eksikse şüpheli.
MIN_SHARE = 0.9
MIN_ROWS = 20  # az kayıtlı tablolarda (ör. kullanıcı) bir-iki kayıt farkı normal
CHECK_SCRIPT = (
    "from django.contrib.auth import get_user_model\n"
    "from app.models import TV, Movie, Episode\n"
    "import json\n"
    "print('PROVA ' + json.dumps({'users': get_user_model().objects.count(), 'tv': TV.objects.count(),"
    " 'movies': Movie.objects.count(), 'episodes': Episode.objects.count()}))\n"
)


class ProvaError(Exception):
    """Provanın durduğu adım (mesajı Telegram'a gider)."""


def _number(value):
    return f"{value:,}".replace(",", ".")


def latest_backup():
    files = glob.glob(BACKUPS)
    if not files:
        raise ProvaError("Hiç gecelik yedek bulunamadı.")
    path = max(files, key=os.path.getmtime)
    age_h = (time.time() - os.path.getmtime(path)) / 3600
    if age_h > MAX_AGE_H:
        raise ProvaError(f"Son yedek {age_h:.0f} saat önce alınmış (gecelik yedek durmuş olabilir).")
    return path


def extract(path, work):
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = set(archive.getnames())
            if "db/db.sqlite3" not in names or "manifest.json" not in names:
                raise ProvaError("Yedek arşivinde veritabanı ya da manifest.json eksik.")
            for name in ("db/db.sqlite3", "manifest.json"):
                archive.extract(name, work, filter="data")
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ProvaError(f"Yedek arşivi açılamadı ({type(error).__name__}).") from error
    db = os.path.join(work, "db", "db.sqlite3")
    try:
        with open(os.path.join(work, "manifest.json")) as file:
            manifest = json.load(file)
    except (OSError, ValueError) as error:
        raise ProvaError("manifest.json okunamadı.") from error
    expected = manifest.get("database_size_bytes")
    if expected and os.path.getsize(db) != expected:
        raise ProvaError(f"Veritabanı boyutu tutmuyor ({os.path.getsize(db)} ≠ {expected} bayt).")
    return db, manifest


def counts(path, read_only=True):
    uri = f"file:{path}?mode=ro" if read_only else f"file:{path}"
    with sqlite3.connect(uri, uri=True, timeout=10) as conn:
        return {table: conn.execute(f"select count(*) from {table}").fetchone()[0] for table, _label in TABLES}  # noqa: S608


def integrity(path):
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as conn:
            result = conn.execute("pragma integrity_check").fetchone()[0]
    except sqlite3.Error as error:
        raise ProvaError(f"Yedekteki veritabanı açılamadı ({error}).") from error
    if result != "ok":
        raise ProvaError(f"Bütünlük kontrolü başarısız: {result[:200]}")


def compare(backup, live):
    notes = []
    for table, label in TABLES:
        if (live.get(table) or 0) >= MIN_ROWS and backup[table] < live[table] * MIN_SHARE:
            notes.append(f"{label}: yedekte {_number(backup[table])}, canlıda {_number(live[table])}")
    if notes:
        raise ProvaError("Yedekte beklenenden az kayıt var — " + "; ".join(notes))


def trial_container(work):
    """Göçler + veri okuma, internete kapalı geçici konteynerde (yedeğin kopyasıyla)."""
    os.chmod(os.path.join(work, "db"), 0o777)  # noqa: S103 - geçici klasör; konteyner kullanıcısı yazabilsin
    os.chmod(os.path.join(work, "db", "db.sqlite3"), 0o666)  # noqa: S103
    name = f"efetrack-prova-{os.getpid()}"
    command = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "-e", "SECRET=yedek-provasi-gecici", "-e", "REDIS_URL=redis://127.0.0.1:6399", "-e", "TZ=Europe/Istanbul",
        "-v", f"{os.path.join(work, 'db')}:/yamtrack/db",
        IMAGE, "sh", "-c",
        "python manage.py migrate --noinput >/tmp/migrate.log 2>&1 || { tail -5 /tmp/migrate.log; exit 3; }; "
        f"python manage.py shell -c \"{CHECK_SCRIPT}\"",
    ]
    started = time.time()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=CONTAINER_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        # Only the docker client was stopped; remove the container too.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60, check=False)
        raise ProvaError("Deneme ortamı zamanında açılmadı (10 dk).") from error
    except OSError as error:
        raise ProvaError(f"Docker çalıştırılamadı ({error}).") from error
    seconds = time.time() - started
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 3:  # noqa: PLR2004
        raise ProvaError("Deneme ortamında göçler (migrate) başarısız: " + output.strip()[-300:])
    line = next((row for row in output.splitlines() if row.startswith("PROVA ")), None)
    if result.returncode != 0 or line is None:
        raise ProvaError("Deneme ortamında uygulama veriyi okuyamadı: " + output.strip()[-300:])
    return json.loads(line[6:]), seconds


def run():
    path = latest_backup()
    name = os.path.basename(path)
    work = tempfile.mkdtemp(prefix="efetrack-prova-", dir="/var/tmp")
    try:
        db, _manifest = extract(path, work)
        integrity(db)
        backup = counts(db)
        try:
            live = counts(LIVE_DB)
        except sqlite3.Error:
            live = {}
        compare(backup, live)
        seen, seconds = trial_container(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    size = os.path.getsize(path) / 1e6
    summary = " · ".join(f"{_number(backup[table])} {label}" for table, label in TABLES)
    return (
        "✅ Yedek provası başarılı\n"
        f"Yedek: {name} ({size:.1f} MB)\n"
        f"Bütünlük kontrolü: tamam\n"
        f"İçinde: {summary}\n"
        f"Deneme ortamında açıldı: göçler ve veri okuma tamam ({seconds:.0f} sn; "
        f"uygulama {_number(seen['tv'])} dizi, {_number(seen['movies'])} film gördü).\n"
        "Gerekirse bu yedekten geri dönülebilir."
    )


def main():
    try:
        text = run()
        ok = True
    except ProvaError as error:
        text, ok = f"❌ Yedek provası BAŞARISIZ\n{error}\n\nCanlı sisteme dokunulmadı; yedekleri kontrol etmek gerek.", False
    except Exception as error:  # noqa: BLE001 - beklenmeyen hata da bildirilsin
        text, ok = f"❌ Yedek provası beklenmeyen bir hatayla durdu ({type(error).__name__}: {error})", False
    print(text)
    bekci.send(text)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
