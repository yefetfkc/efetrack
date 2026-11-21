"""What's new: shown once per device after an update (see templates/efe/components/whats_new.html).

Add a new entry at the top for every release; ``id`` must change. The pop-up appears only
for ``SHOW_DAYS`` after the entry's date, and only until "Tamam" is pressed on that device.

Version: every new release entry gets the next ``surum`` (1.0.0 → 1.0.1 → 1.0.2 …). The newest
one is the app's version (settings.VERSION, Ayarlar → Hakkında); nothing else needs changing.
"""

from datetime import date

SHOW_DAYS = 21

ENTRIES = [
    {
        "id": "2026-11-20-1.0.2",
        "surum": "1.0.2",
        "date": date(2026, 11, 20),
        "title": "efeTrack 1.0.2: ülkeler, ilerleme halkası, ay kolajı",
        "items": [
            ("🌍", "Ülke sayfaları: planladıklarından bir ülkenin yapımları (ör. Britanya). Planlananlar'da ülke seçimi; yapım sayfasındaki \"Britanya Yapımı\" yazısı da oraya götürür."),
            ("⭕", "Oyuncu ve yönetmen sayfalarında ilerleme halkası: çıkmış işlerinden kaçını izlediğin (Nolan: 8/12)."),
            ("🔲", "Listelerin ızgara görünümünde yoğunluk: Sıkı, Rahat, Galeri. Bu cihazda hatırlanır."),
            ("🖼️", "Poster Duvarı → Ay kolajı: bir ayda izlediklerin tek resimde; Instagram hikâye, gönderi, kare, yatay, telefon ve masaüstü duvar kâğıdı boyutları."),
        ],
    },
    {
        "id": "2026-11-19c-1.0.1",
        "surum": "1.0.1",
        "date": date(2026, 11, 19),
        "title": "efeTrack 1.0.1: afiş seçimi ve oturum adları",
        "items": [
            ("🖼️", "Afişi değiştir: yapım sayfasında afişin üzerindeki düğmeyle TMDB ya da MyAnimeList'teki diğer afişlerden birini seç; her yerde o görünür. \"Varsayılan\" ile geri dönersin."),
            ("🏷️", "Ayarlar → Oturumlar: her oturuma ad verebilirsin (ör. \"Salon TV'si\")."),
            ("🔍", "Arama kutusundaki tür seçimi (\"Kutu oyunları\" gibi uzun adlar) artık tek satırda."),
            ("🛠️", "Düzeltme: uygulamanın kendi açtığı oturumlar \"Bilinmeyen cihaz\" yerine cihaz adıyla görünür."),
        ],
    },
    {
        "id": "2026-11-19b",
        "surum": "1.0.0",
        "date": date(2026, 11, 19),
        "title": "efeTrack 1.0.0: mevsim efektleri, yeni Telegram komutları",
        "items": [
            ("🍂", "Mevsim efektleri: sonbahar yaprakları, bahar çiçekleri ve Cadılar Bayramı yarasaları. Mevsiminde kendiliğinden açılır; Tercihler → Ortam efektleri'nden açıp kapatabilirsin."),
            ("🖼️", "Afiş indirme: yapım sayfasında afişin üzerindeki düğmeyle yüksek çözünürlüklü afişi indir."),
            ("📅", "Telegram: /takvim (bu ayın afişli takvimi), /oyuncu Ad (oyuncunun izlediğin yapımları), /kisa (100 dakikadan kısa planlanan filmler)."),
            ("🖥️", "Telegram: /sunucu (disk, bellek ve bekçi durumu; /durum'un yerini aldı), /ayarlar (bildirimleri düğmelerle aç/kapat)."),
            ("🎲", "Rastgele öneride süre, tür ve puan seçimi yenilendi; türler Türkçe."),
            ("⬇️", "Planlananlar ve Poster Duvarı aynı tür resim kaydeder: her afişin altında adı, \"Hepsi\"nde türü de."),
            ("🔳", "Planlananlar karekodu artık listenin yazısını verir; siteye giriş gerekmez."),
        ],
    },
    {
        "id": "2026-11-19",
        "date": date(2026, 11, 19),
        "title": "Planlananlar ve yeni Telegram komutları",
        "items": [
            ("🕒", "Planlananlar (kenar menüsü): tüm türler bir arada; toplam süre, puan/yıl/süre sıralaması, karekod."),
            ("⬇️", "Planlananlar'ı resim olarak kaydet: yalnız seçili tür, İndirilenler'e iner."),
            ("🎬", "Dizi ve film sayfalarında Ekip sekmesi: yönetmen, yaratıcı, senarist; kişi sayfasında işleri."),
            ("🎲", "Rastgele öneride süre, tür ve en düşük puan süzgeçleri."),
            ("🤖", "Telegram: /nerede Film (Türkiye'de hangi platformda), /gecmis 12 Ekim (o gün ne izledin)."),
            ("🔔", "Telegram'a film serine yeni film eklenince ve planladığın yapıma yeni fragman çıkınca haber."),
            ("🔐", "Ayarlar → Oturumlar: açık cihazlar ve uzaktan çıkış; yeni cihazdan girişte Telegram uyarısı."),
        ],
    },
    {
        "id": "2026-11-14s",
        "date": date(2026, 11, 14),
        "title": "Kitaptan uyarlamalar",
        "items": [
            ("📖", "Bir dizi ya da film kitaptan uyarlandıysa sayfasında kitabı görürsün; \"Kitap listeme ekle\" ile Planlanan olarak eklenir."),
            ("📚", "Kitap serisine dayanan yapımlarda (ör. Game of Thrones) seri gösterilir ve kitap aramasına gidilir."),
        ],
    },
    {
        "id": "2026-11-14r",
        "date": date(2026, 11, 14),
        "title": "Yeni sezon öncesi özet",
        "items": [
            ("📺", "Takip ettiğin bir dizinin yeni sezonu yarın başlıyorsa, 18:00'de önceki sezonun özet videosu Telegram'a gelir; video yoksa yazılı özet."),
            ("⏱", "Dizi sayfalarında Ayrıntılar → Süre artık toplam izleme süresini gösteriyor."),
        ],
    },
    {
        "id": "2026-11-14p",
        "date": date(2026, 11, 14),
        "title": "Ortam efektleri",
        "items": [
            ("🌌", "Kar'ın yanına Yağmur, Yıldızlar, Ateş böcekleri, Kuzey ışıkları ve Film greni: Tercihler → Ortam efektleri. Birlikte de açılabilir."),
            ("🎬", "Sinema modunda öneriler arasında geçerken ses paketine uygun yumuşak bir kaydırma sesi; ← → oklarıyla da geçilir."),
            ("🏠", "Ana sayfanın üst kısmı düzenlendi: başlık solda, düğmeler sağda tek sırada."),
        ],
    },
    {
        "id": "2026-11-14o",
        "date": date(2026, 11, 14),
        "title": "Sinema modu ve yeni Telegram özellikleri",
        "items": [
            ("⏱", "Dizi, sezon ve film sayfalarında toplam süre (saat ve dakika); izlemeye başladıysan kalan süre de."),
            ("🌍", "Yapımın ülkesi başlığın altında: \"Britanya Yapımı\", \"Güney Kore Yapımı\"…"),
            ("🎬", "Sinema modu: ana sayfada tek dev afiş ve \"Şimdi bunu izle\". Tercihler'den ya da ana sayfadaki düğmeden aç/kapat."),
            ("🎨", "Tercihler sayfası yenilendi: Görünüm, Sesler ve Bildirimler ayrı bölümlerde."),
            ("🔗", "Bota IMDb, Letterboxd ya da TMDB bağlantısı gönder: ne yapmak istediğini sorup listene ekler."),
            ("⚖️", "/karsilastir Dizi 1 | Dizi 2: süre, puan ve ilerleme yan yana."),
            ("⏳", "Zaman kapsülü: geçmiş yıllarda bugün ne izlediğin her sabah 10:30'da Telegram'da."),
            ("🏆", "Bir diziyi bitirince ya da film izleyince paylaşılabilir \"Bitirdim\" resmi gelir; İşlemler kutusundan da indirilebilir."),
            ("🛟", "Her ayın 1'inde son yedek deneme ortamında açılıp kontrol edilir, sonucu Telegram'a yazılır."),
        ],
    },
    {
        "id": "2026-11-14b",
        "date": date(2026, 11, 14),
        "title": "Kasım güncellemesi",
        "items": [
            ("🌐", "efeTrack artık sunucuda: Mac, telefon ve site aynı veriyi kullanıyor."),
            ("🤖", "Telegram botu: /yardim ile bütün komutlar; /istatistik grafik, /rapor aylık PDF gönderir."),
            ("☀️", "Her gün 11:00'de \"bugün ne izlesem\" önerisi; ayın 1'inde aylık rapor."),
            ("🖼️", "Yeni bölüm bildirimleri afişli geliyor; tarih değişince ve yeni sezon duyurulunca haber veriliyor."),
            ("🎬", "Dizi ve film sayfalarında Fragman ve Evren sekmeleri; 2. sezondan itibaren önceki sezonun özeti."),
            ("🎴", "Dizi kartı (İşlemler kutusunda): hikâye boyutunda paylaşılabilir kart."),
            ("🧱", "Poster Duvarı: izlediklerinin afiş kolajı, resim olarak kaydedilebilir."),
            ("📅", "Takvimde yazı yerine afişler."),
            ("🌙", "Gece teması: OLED ekranlar için tam siyah (Tercihler → koyu tonlar)."),
            ("📴", "Telefonda internetsiz görüntüleme: açtığın sayfalar kayıtlı kopyadan açılır."),
            ("❄️", "Kar efekti: Tercihler'den istediğin zaman aç/kapat; 1 Aralık – 6 Ocak arası kendiliğinden yağar."),
            ("👁", "Misafir hesabı: kütüphaneni yalnız görüntüleyebilen bir hesap açılabiliyor."),
        ],
    },
]


SURUM = next((entry["surum"] for entry in ENTRIES if entry.get("surum")), "1.0.0")


def latest(today=None):
    """The newest entry while it is fresh enough to pop up, else None."""
    today = today or date.today()
    if not ENTRIES:
        return None
    entry = ENTRIES[0]
    return entry if 0 <= (today - entry["date"]).days <= SHOW_DAYS else None
