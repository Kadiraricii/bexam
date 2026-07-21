# Blackboard Sınav PDF Yakalayıcı

İstinye Blackboard'daki (`istinye.blackboard.com`) sınav "Değerlendirme Geri Bildirimi"
sayfalarını PDF olarak indirir ve sayfadaki **GÖNDERİM TARİHİ** ile **ONAY** kodunu
otomatik ayıklayıp kaydeder.

## Nasıl çalışır

1. `capture.py` çalıştırıldığında görünür (headed) bir Chrome penceresi açılır.
2. Kullanıcı bu pencerede SSO ile giriş yapar ve PDF alınacak sınav sonucu sayfasına
   manuel olarak gider.
3. Terminalde ENTER'a basılınca script:
   - Sayfayı sonuna kadar otomatik kaydırıp gizli/lazy-load içeriği yükletir,
   - Sayfa metninden başlığı, gönderim tarihini ve onay kodunu regex ile çıkarır,
   - Sayfayı `output/{sınav_başlığı}_{onay_kodu}.pdf` olarak kaydeder,
   - `.state/captures.json` dosyasına bir kayıt ekler (başlık, tarih, onay, pdf yolu).
4. Aynı oturumda tarayıcı kapanmadan istenildiği kadar farklı sayfa yakalanabilir
   (her seferinde başka bir sınav/öğrenciye gidip tekrar ENTER).

Oturum bilgisi `.state/profile` klasöründe (kalıcı Chrome profili) tutulur, bu sayede
her çalıştırmada yeniden SSO girişi gerekmeyebilir.

## Kurulum

**En kolay yol — hazır scriptler** (macOS, Windows ve Linux'ta çalışır):

| Platform | Kurulum (bir kere) | Başlatma (her seferinde) |
|---|---|---|
| macOS / Linux | `./setup.sh` | `./start.sh` |
| Windows | `setup.bat`'a çift tıkla | `start.bat`'a çift tıkla |

Bu scriptler otomatik olarak: Python kurulu mu diye kontrol eder, `.venv`
sanal ortamını oluşturur, `requirements.txt`'teki bağımlılıkları kurar,
Playwright'in tarayıcı bileşenlerini kurar ve **gerçek Google Chrome**'un
kurulu olup olmadığını kontrol edip yoksa uyarır (program Playwright'in
kendi test tarayıcısını değil, gerçek Chrome'u kullanıyor — bkz. aşağıdaki
"Chrome for Testing" bölümü). Windows'ta `setup.bat` sırasında Python
kurulumunda **"Add python.exe to PATH"** kutucuğunu işaretlemeyi unutma.

**Elle kurulum** (macOS/Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

**Elle kurulum** (Windows, PowerShell/CMD):

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Kullanım

Çoğu kullanıcı için asıl arayüz `gui.py` (bkz. "Grafik arayüz" bölümü) —
`./start.sh` ya da `start.bat` ile açılır. Terminal tabanlı `capture.py`
ise tek sayfa yakalama ve hata ayıklama için hâlâ mevcut:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate.bat
python3 capture.py          # Windows: python capture.py
```

- ENTER: mevcut sayfayı yakala
- `q`: çık

## Windows uyumluluğu

Proje başlangıçta macOS'ta geliştirildi; Windows'ta da **aynı özelliklerin**
sorunsuz çalışması için şunlar kod seviyesinde ele alındı:

- **Dosya/klasör açma**: macOS'a özel `open` komutu yerine artık
  `common.py::open_in_file_manager()` kullanılıyor — Windows'ta
  `os.startfile`, macOS'ta `open`, Linux'ta `xdg-open` çağırıyor
  (Çıktı Klasörünü Aç, Log Dosyasını Aç, İndirme sayfasındaki ağaçta
  çift tıklama — hepsi bunu kullanıyor).
- **Yasak dosya adı karakterleri**: `sanitize_filename` zaten Windows'un
  yasakladığı `< > : " / \ | ? *` karakterlerinin tamamını temizliyordu;
  ayrıca artık **`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`** gibi
  Windows'a özel ayrılmış isimlere denk gelirse otomatik bir ek ekliyor,
  ve dosya adı SONUNDA nokta/boşluk bırakmıyor (Windows bunları da
  reddediyor).
- **Uzun yol (MAX_PATH) sorunu**: Windows'ta varsayılan olarak tam dosya
  yolu 260 karaktere sınırlı (uzun yol desteği çoğu makinede kapalı).
  Derin bir çıktı klasörü + uzun ders/sınav/öğrenci adları bu sınırı
  aşabileceği için: klasör adları artık daha tutucu bir sınırla
  (`DEFAULT_FOLDER_MAX_CHARS`, 60 karakter) oluşturuluyor, ayrıca her
  PDF yazılmadan hemen önce `ensure_safe_full_path()` TAM yolu ölçüp
  gerekirse dosya adının açıklayıcı kısmını kırpıyor — ONAY kodu (kimlik
  belirleyici kısım) her zaman korunuyor.
- **`channel="chrome"`**: Playwright'in bu özelliği (Playwright'in kendi
  tarayıcısı yerine sistemde kurulu gerçek Chrome'u sürmek) Windows'ta da
  aynı şekilde destekleniyor, ek bir değişiklik gerekmedi.
- **Yollar**: Tüm dosya yolları `pathlib.Path` ile oluşturuluyor (asla
  elle `/` ile birleştirilmiyor), bu da Windows'un `\` ayracıyla otomatik
  uyumlu çalışmasını sağlıyor.

## Otomatik ders tarama (scan_course.py)

Tek tek sayfaya gidip ENTER'a basmak yerine, bir dersin **Not Defteri**
sayfasına gidip tüm sınavların (Blackboard'ın resmi adıyla **Test** —
"Tests, Surveys, and Pools" aracı) otomatik yakalanmasını sağlar:

```bash
source .venv/bin/activate
python3 scan_course.py
```

1. Tarayıcı açılır, SSO ile giriş yap.
2. Taranacak dersin **Not Defteri** sayfasına git.
3. Terminale dönüp ENTER'a bas.
4. Script sırayla **"Görüntüle" butonu olan TÜM satırları** dener,
   `capture.py` ile aynı yakalama mantığını (scroll + görsel yükleme
   bekleme + PDF + ONAY/GÖNDERİM TARİHİ çıkarımı) çalıştırır, sonra bir
   sonrakine geçer.

**Sınav tespiti isimden değil, içerikten yapılır**: İlk sürümde hangi
satırların gerçekten sınav olduğu satır adına bakılarak (adında "sınav"
geçiyor mu diye) tahmin ediliyordu. Bu kırılgan çıktı — hocalar sınavı
"Quiz", "Vize", "Final", "Ara Sınav", "Midterm" gibi çok farklı
adlandırabiliyor, sabit bir anahtar kelime listesi hiçbir zaman tam
kapsayamaz. Artık isim filtresi YOK: her "Görüntüle" satırı denenir; bir
satır açıldığında **ONAY kodu** görünmüyorsa (10 saniye içinde) bu satır
sessizce PDF'e çevrilmeye çalışılmaz — "sınav/quiz değilmiş ya da
gönderilmemiş" diye **atlanan** (hata değil) olarak işaretlenir ve bir
sonraki satıra geçilir. Böylece hem hiçbir isimlendirme kaçırılmaz hem de
ödev/tartışma forumu gibi alakasız satırlar sonuç PDF'lerini kirletmez.

**Klasör yapısı**: `output/{ders adı}/{sınav adı}/{sınav_başlığı}_{onay_kodu}.pdf`
Ders adı sekme başlığından (`page.title()`), sınav klasörü de satır adından
türetilir. Bu yapı bilinçli olarak hoca (instructor) sürümüyle aynı şekilde
kurgulandı: hoca hesabına geçince aynı `{ders}/{sınav}/` klasörünün içine
her öğrenci için `{öğrenci_adı}_{onay_kodu}.pdf` düşecek — yani "Kısa Sınav 1"
klasörüne girildiğinde o sınava giren **tüm öğrencilerin** dosyaları aynı
yerde birikecek. Öğrenci hesabıyla test ederken zaten tek dosya düşüyor,
ama klasör iskeleti şimdiden bu çoklu-öğrenci senaryosuna hazır.

**Idempotent çalışma**: `.state/captures.json`'da kaydı olan bir sınav
(aynı başlık) tekrar karşına çıktığında, script önce o kaydın PDF dosyasının
**hâlâ diskte olup olmadığını** kontrol eder — sadece log'a değil, dosyanın
varlığına bakar. Dosya duruyorsa atlar; silinmiş/taşınmışsa yeniden yakalar.
Bu sayede scripti güvenle defalarca çalıştırabilir, yarıda kalan bir
taramayı kaldığı yerden devam ettirebilir, kazayla silinen bir PDF'i de
kaybetmemiş olursun. Sonunda "Yakalanan / atlanan / hatalı" sayaç özeti
gösterilir.

**Overlay kapatma**: Sınav sonucu overlay'i, tıklama sonrası bir SPA
route'a (`.../grades/student-submission-view?contentId=...`) karşılık
geldiği için `Escape` yerine tarayıcı geçmişi (`go_back`) ile kapatılır;
bu başarısız olursa Not Defteri URL'ine doğrudan gidilerek kurtarma
yapılır. Bir satır tamamen hata verirse döngü çökmez, otomatik kurtarıp
sıradaki sınava geçer.

Not: Satır/buton eşleştirmesi mevcut DOM yapısına göre yazıldı; ilk
çalıştırmada "Hiç sınav satırı bulunamadı" uyarısı çıkarsa sayfa yapısı
farklı demektir — ekran görüntüsü veya sayfa HTML'i paylaşılırsa
selector'lar düzeltilir.

## Blackboard REST API araştırması (sonuç: kullanmıyoruz)

Resmi bir Blackboard REST API var ama iki nedenden dolayı bu proje için
uygun değil:

1. **Kurumsal admin onayı şart** — App ID'nin okulun Blackboard sistem
   yöneticisi tarafından Admin Panel'den elle kaydedilmesi gerekiyor,
   hoca kendi başına açamıyor.
2. **ONAY/submission-receipt kodu API şemasında yok** — sadece Ultra
   arayüzünde (UI) gösteriliyor, dokümante edilmiş gradebook/attempt
   endpoint alanlarında karşılığı bulunamadı.

Bu yüzden tarayıcı otomasyonu (Playwright) ile devam ediyoruz.

## Dosyalar

| Dosya | Açıklama |
|---|---|
| `capture.py` | Tek sayfa yakalama — tarayıcıyı açar, manuel/tekrarlı yakalama döngüsü |
| `scan_course.py` | Bir dersin Not Defteri'ndeki tüm sınavları otomatik bulup yakalar (öğrenci hesabı) |
| `scan_grade_center.py` | Bir sınavdaki tüm öğrencileri otomatik bulup yakalar (hoca hesabı, terminal) |
| `gui.py` | Hoca için grafik arayüz — ders/sınav seçip toplu tarama başlatma |
| `common.py` | Sabitler, dosya adı temizleme, ONAY/GÖNDERİM TARİHİ regex çıkarımı |
| `setup.sh` / `setup.bat` | Kurulum: sanal ortam + bağımlılıklar + Playwright + Chrome kontrolü (macOS-Linux / Windows) |
| `start.sh` / `start.bat` | `gui.py`'yi sanal ortamı otomatik aktive ederek başlatır (macOS-Linux / Windows) |
| `requirements.txt` | Python bağımlılıkları (tek dış paket: Playwright) |
| `output/` | Üretilen PDF'ler |
| `.state/profile` | Kalıcı Chrome oturumu (cookie/login) |
| `.state/captures.json` | Yakalanan her sayfanın metadata kaydı |

## Hoca hesabı: scan_grade_center.py

Bir hoca ekran görüntüsü sayesinde yazıldı: Grade Center'da bir öğrencinin
sınav sonucunu açtığında sol tarafta tüm sınıfın listelendiği ("Öğrenciler"
sekmesi) bir panel çıkıyor. Script bu paneldeki her öğrenciyi sırayla tıklar.

```bash
source .venv/bin/activate
python3 scan_grade_center.py
```

1. Tarayıcı açılır, SSO ile giriş yap.
2. Grade Center'dan herhangi bir öğrencinin sınav sonucunu aç (sol tarafta
   "Öğrenciler" listesi görünmeli — ekran görüntüsündeki gibi).
3. Terminale dönüp ENTER'a bas.
4. Script soldaki listede adı+notu geçen ("10 / 100" gibi) her satırı bulur,
   tek tek tıklar, ONAY kodunun değiştiğini doğrulayıp (yani gerçekten o
   öğrencinin sayfası yüklendiğinden emin olup) yakalar.
5. Çıktı: `output/{sınav adı}/{öğrenci adı}_{onay kodu}.pdf` — tam
   `ornekyapi.md`'de anlaşılan yapı.

**Ders adı**: Şu an klasör adı sadece sınav adından (`page.title()`)
türetiliyor, ayrı bir ders klasörü katmanı yok — gerekirse
`scan_course.py`'deki gibi `output/{ders}/{sınav}/` şeklinde
derinleştirilir.

## Grafik arayüz: gui.py

Hoca için terminal yerine tıklamalı bir kontrol paneli:

```bash
source .venv/bin/activate
python3 gui.py
```

**Tasarım**: **Bul → Onayla → İndir**, 2 aşamalı. Manuel liste tutma,
ders adı yazma yok — sayfaya git, tıkla, ne bulunduğunu gör, onaylarsan
indir.

Akış:

0. **Çıktı klasörü** (isteğe bağlı, en üstte): varsayılan proje
   içindeki `output/` klasörüdür; **"Klasör Seç"** ile taramaya
   başlamadan önce istediğin başka bir klasörü (ör. Masaüstü'nde bir
   klasör) seçebilirsin.
1. **"Tarayıcıyı Aç"**: açılan pencerede SSO ile normal şekilde giriş
   yapılır (2FA dahil). **Şifre programda hiçbir yerde toplanmaz/
   saklanmaz** — giriş tamamen o tarayıcı penceresinde olur. Bağlanınca
   durum "bağlı ✓" olur ve "Bul ve Tara" butonu aktifleşir.
2. Tarayıcıda herhangi bir sayfaya gidilir, **"Bul ve Tara"**ya basılır.
   Bu adım **SADECE tarar, henüz indirmez**. Önce **giriş kontrolü**
   yapılır (sayfanın yerleşmesine ~3 saniye fırsat tanıyarak — SSO'nun
   son geçiş anını yanlışlıkla "giriş yok" sanmamak için). Domain
   doğruysa GUI sayfanın **türünü kendisi anlar** ve bulduklarını
   log'da listeler:
   - Dersin **Not Defteri** sayfasıysa → o derste bulunan tüm sınavlar
     (`scan_course.py` mantığı — öğrenci hesabı).
   - Bir sınavın **öğrenci listesi** sayfasıysa → o sınava giren tüm
     öğrenciler (`scan_grade_center.py` mantığı — hoca hesabı).
   - Hiçbiri değilse, log alanında açık bir uyarı gösterilir.
3. Bulunanlar uygunsa **"PDF Olarak İndir"**e basılır — ancak o zaman
   gerçek yakalama/indirme başlar. Bitince log alanının üstünde
   **"Yakalanan / Atlanan / Hatalı"** sayıları gösterilir, ayrıca
   `output/indirme_log.txt` dosyasına zaman damgalı bir özet **eklenir**
   (üzerine yazılmaz — zamanla birikimli bir geçmiş oluşur). Başka bir
   ders/sınava gidip tekrarlanabilir. **"Çıktı Klasörünü Aç"** Finder'da
   seçili çıktı klasörünü açar.
4. **"Güvenli Çıkış"**: tarayıcıyı (ve Playwright arka plan sürecini)
   düzgünce kapatıp öyle pencereyi kapatır — arkada asılı Chrome süreci
   bırakmaz. Pencerenin sağ üst köşesindeki normal kapatma (X) düğmesi
   de aynı güvenli kapanışı tetikler. **Aktif bir tarama sürerken**
   basılırsa taramayı ANINDA kesmez (o sırada işlenen tek öğrenci/sınavı
   güvenle bitirip durur — en kötü ~30 saniye), sonra kapanır; bu sayede
   90 öğrencilik bir taramanın ortasında "Güvenli Çıkış"a basmak artık
   ya taramanın tamamının bitmesini beklemek ya da Chrome'u/oturum
   kilidini yarım bırakma riski arasında seçim yaptırmıyor.

**Not (giriş doğrulaması sınırı)**: Kontrol sadece "hâlâ Blackboard
domaininde miyiz" bakıyor — SSO'nun gerçekten başarılı kimlik doğrulaması
yaptığını değil. Bu, "SSO ekranında takılı kaldın" gibi bariz durumları
yakalar ama Blackboard domaininde göründüğü hâlde oturumun geçersiz
olduğu nadir bir durumu yakalamayabilir; öyle bir durumda sayfa türü
zaten tanınamayacağı için yine açık bir uyarı verilir.

Arka planda `scan_course.py` ve `scan_grade_center.py`'deki **aynı**
fonksiyonlar (dolayısıyla aynı doğrulama katmanları, kaydır-topla,
rastgele gecikme, devre kesici) kullanılıyor — GUI sadece ikisini
otomatik seçip üstüne bir kontrol paneli.

**Threading notu**: Playwright'ın senkron API'si tek bir thread'den
kullanılmak zorunda olduğu için, tüm Playwright işlemleri tek bir kalıcı
arka plan thread'inde sırayla işleniyor; GUI ile arasında komut/sonuç
kuyrukları var. Hiçbir buton doğrudan yeni bir thread açıp Playwright'a
dokunmuyor (bu, çökme riski taşırdı). "Güvenli Çıkış" da bu thread'in
gerçekten bitmesini (`join`) bekler — ilk sürümde pencereyi kapatmak
tarayıcının kapanmasını beklemiyordu, bu düzeltildi.

**Pencere boyutu notu**: Başta tarayıcıyı ekran çözünürlüğüne göre tam
ekran açmayı denedik, ama Blackboard'un kendi sayfası sabit/dar bir
genişlikte kaldığı için pencereyi zorlamak sadece etrafta büyük boş alan
bıraktı (gerçek bir hata değildi, Blackboard'un kendi tasarımı). Bunun
yerine sabit, standart bir boyut (**1440×900**, `common.py` içinde
`browser_launch_kwargs()`) kullanılıyor.

**Sekme takibi notu (önemli düzeltme)**: Microsoft/Azure AD SSO akışı
girişi **yeni bir sekmede** açabiliyor. İlk sürümde script tarayıcı
açılır açılmaz yakaladığı **tek** sekmeyi hep aynı referans olarak
kullanıyordu; kullanıcı SSO'yu farklı bir sekmede tamamlayıp
Blackboard'a geçtiğinde script hâlâ eski/boş sekmede kalıyor, "giriş
yapılmamış" diye yanlış uyarı veriyordu — adres çubuğunda Blackboard
görünse bile. Artık her "Bul ve Tara" (ve CLI script'lerinde her
ENTER'a basışta) `resolve_active_page()` ile açık sekmeler arasından
gerçekten Blackboard'da olan taranıyor, tek bir sekme referansına
güvenilmiyor.

**Asıl kök neden (Chrome for Testing → gerçek Chrome geçişi)**: Yukarıdaki
sekme takibi düzeltmesi gerçek ve faydalı bir sağlamlaştırmaydı, ama asıl
sorunun sebebi değildi. Uzun bir hata ayıklama sürecinde (canlı "izlenen
sayfa" barı, sekme sayısı/URL listeleme, "Güvenli Çıkış" ile hangi
pencerenin gerçekten kontrol edildiğini test etme) kesin olarak
belirlendi: script'in açtığı tarayıcı (Playwright'ın varsayılan olarak
kullandığı özel "Chrome for Testing" derlemesi) Microsoft/Azure AD SSO'da
**gerçekten donuyordu** — aynı SAMLRequest URL'inde dakikalarca hiç
ilerlemiyordu. Bu, otomasyon araçlarıyla (Selenium/Playwright) sürülen
tarayıcıların Azure AD tarafından tespit edilip giriş akışının
yönlendirme döngüsüne sokulması şeklinde **bilinen, dokümante edilmiş
bir kısıtlama** — bizim kodumuzdaki bir hata değil.

Çözüm: `browser_launch_kwargs()` artık `channel="chrome"` ile Chrome for
Testing yerine **gerçekten kurulu Google Chrome'u** sürüyor (kendi ayrı
`.state/profile` klasörümüzü kullanmaya devam ediyoruz, Chrome'un
varsayılan/ana profiliyle bir ilgisi yok), ayrıca
`--disable-blink-features=AutomationControlled` ve
`ignore_default_args=["--enable-automation"]` ile otomasyon parmak izi
azaltılıyor. Otomatik testle doğrulandı: artık gerçek Chrome açılıyor
(`/Applications/Google Chrome.app`) ve `navigator.webdriver` artık
`False` dönüyor (öncesinde `True` idi). Bu, kullanıcının kişisel
Chrome'undaki oturumu miras almıyor — otomasyon kendi ayrı, temiz
profilinde yine sıfırdan giriş yapılmasını gerektiriyor, ama bu sefer
(umulur ki) SSO döngüye girmeden tamamlanabiliyor.

## Sağlamlık / felaket senaryoları

Hoca hesabında 90 öğrenciye kadar çıkabilecek büyük sınıflarda "hepsini
kaçırmadan, karıştırmadan" yakalamak kritik olduğu için, gerçek ortamda
test etmeden önce en tehlikeli senaryolara karşı kod seviyesinde savunma
eklendi (tam detay: `~/.claude/plans/streamed-painting-hanrahan.md`).

**Ele alınanlar:**

1. **Yanlış öğrenciye yanlış PDF (mislabeling)** — en kritik risk. Bir
   öğrenciye tıklandıktan sonra PDF üretilmeden önce **üç ayrı sinyal**
   birden doğrulanır: (a) ONAY kodu sayfada var mı, (b) tıklanan
   öğrencinin adı "GÖNDERİM TARİHİ" bloğuna yakın metinde gerçekten
   geçiyor mu, (c) sayfadaki not, sidebar'da o öğrenci için görünen notla
   eşleşiyor mu. Üçü de tutmadan PDF üretilmez, açık hata olarak
   raporlanır.
2. **Virtualized liste (kalabalık sınıf)** — soldaki öğrenci paneli
   adım adım kaydırılıp her adımda görünenler toplanır, panel
   sabitlenene (kaydırma sonuna gelinene) kadar devam eder.
3. **Aynı isimli öğrenciler** — tıklama isimle değil DOM sırasına
   (kaçıncı görünen aynı-isimli satır) göre yapılır; dosya adına
   `(2)`, `(3)` gibi ayırt edici ek eklenir.
4. **Oturum düşmesi (uzun taramada)** — madde 1'in doğrulaması zaten
   bunu "içerik doğrulanamadı" hatası olarak yakalar; ayrıca art arda
   5 hata olursa tarama erken durur (boşuna onlarca hata + gecikme
   harcamamak için).
5. **`captures.json` bozulması** (Ctrl+C tam yazarken kesilirse) —
   log artık geçici dosya + `rename` ile atomic yazılıyor; bozuk JSON
   bulunursa anlaşılır bir hata mesajı verir.
6. **Bot/otomasyon tespiti** — her öğrenci tıklaması arasına 1–3 saniye
   rastgele gecikme, her 20 öğrencide bir 20 saniyelik ekstra mola.
7. **Eksik/kesik PDF** — otomatik kaydırma artık "sabitlendi mi yoksa
   süre dolduğu için mi durdu" bilgisini döndürüyor; sabitlenmediyse
   (içerik hâlâ büyürken vazgeçildiyse) PDF **üretilmez**, hata verilir.
   Ayrıca kaydırma sonunda artık başa dönülmüyor (virtualized içerikte
   geri dönmenin, daha önce yüklenen alt kısmı DOM'dan düşürüp PDF'i
   eksik bırakma riski vardı — Chrome'un yazdırma motoru zaten mevcut
   scroll pozisyonundan bağımsız çalışır). Üretilen PDF şüpheli derecede
   küçükse (< 3KB) de dosya silinip hata verilir.

**İkinci tur — 10 yeni felaket senaryosu** (detaylı plan:
`~/.claude/plans/streamed-painting-hanrahan.md`):

1. **İki farklı dersin aynı isimli sınavı** — idempotency kontrolü artık
   ders adını da içeren bir anahtarla (`f"{ders} - {sınav}"`) yapılıyor;
   önceden sadece sınav adına bakıldığı için başka bir dersin aynı adlı
   sınavı "zaten yakalanmış" sanılıp sessizce atlanabiliyordu.
2. **Birden fazla Blackboard sekmesi açık** — "Bul ve Tara" artık kaç
   sekmenin Blackboard'da olduğunu kontrol ediyor; birden fazlaysa
   hangisinin taranacağı belirsiz olduğu için sessizce tahmin etmek
   yerine açıkça uyarıp duruyor.
3. **Notlandırılmamış/muaf öğrenciler** — öğrenci listesi artık sadece
   sayısal not ("50/100") gösteren satırlarla sınırlı değil; panel
   içindeki tüm satırlar toplanıyor, böylece henüz notlandırılmamış ya
   da muaf öğrenciler de listeye giriyor.
4. **Disk dolu / yazma izni yok** — taramaya başlamadan önce çıktı
   klasörüne küçük bir yazma testi yapılıyor; PDF yazma sırasında disk
   hatası olursa da anlaşılır bir Türkçe mesaja çevriliyor.
5. **Bulut-senkron klasör (iCloud/Dropbox/OneDrive)** — böyle bir klasör
   seçilirse GUI log'unda bilgilendirici bir uyarı gösteriliyor.
6. **Tarayıcı taramanın ortasında kapanır/çöker** — bu artık ayrı
   tanınıyor; devre kesicinin 5 denemeyi tüketmesini beklemeden anında
   durup net bir mesaj veriyor, bağlantı durumunu sıfırlıyor.
7. **İkinci uygulama örneği açılırsa** — profil kilidi hatası net bir
   "program zaten açık olabilir" mesajına çevriliyor.
8. **Önceki çökmeden kalan kilit dosyaları** — "Tarayıcıyı Aç"tan hemen
   önce yetim `SingletonLock` vb. dosyalar (kullanımda değillerse
   kayıpsız şekilde) temizleniyor.
9. **Çok uzun dosya adı** — `sanitize_filename` artık 120 karakterle
   sınırlı; ONAY kodu bu sınırlamadan etkilenmiyor (her zaman korunuyor).
10. **Oturum süresi taramanın ortasında dolarsa** — devre kesici
    tetiklendiğinde son sayfa artık Blackboard'da değilse, uyarı
    mesajına "oturumun süresi dolmuş olabilir" ipucu ekleniyor.
11. **Kurtarma başarısız olursa zincirleme yanlış-sayfa hatası** — bazı
    Not Defteri satırları (ör. "Yoklama") ONAY göstermeden farklı bir
    sayfa yapısına düşüp `return_to_grades_list`'in geri dönmesini
    engelleyebiliyordu; eskiden bu durumda script sessizce devam edip
    SONRAKİ satırları (gerçek sınavlar dahil) yanlış sayfa durumundan
    tıklamaya çalışıyordu. Artık kurtarma doğrulanamazsa tarama o an
    güvenle durduruluyor, yanlış satırlara asla tıklanmıyor.

**Ödev sayfalarında yanlış sekme yakalanması (içerik doğruluğu hatası)**:
Bazı ödev "Değerlendirme Geri Bildirimi" sayfalarında iki sekme oluyor —
sabit **"Yönergeler"** (hocanın ödev açıklaması) ve öğrencinin gönderdiği
dosyanın kendi adıyla etiketli sekmesi (ör. "odev2.pdf"). Sayfa
varsayılan olarak "Yönergeler" sekmesiyle açılıyor; script bunu
yakalarsa PDF'e **hocanın ödev talimatı** girer, öğrencinin gerçekte
gönderdiği içerik DEĞİL — ONAY/not/tarih sekmeden bağımsız üstte olduğu
için bunlar yine de doğru çıkıyordu, bu yüzden fark edilmesi gecikti.
`switch_to_submission_tab` (`capture.py`) artık "Yönergeler" ilk sekme
olarak bulunursa son sekmeye (gönderilen dosya) geçip öyle yakalıyor;
sınav sayfalarında (bu sekme yapısı hiç görülmedi) hiçbir şey
değişmiyor. Bilinen sınırlama: öğrenci birden fazla dosya yüklediyse şu
an sadece son sekme yakalanıyor.

**Gizlilik notu**: Bu PDF'ler öğrenci kişisel verisi (ad, sınav
cevapları, not) içerir. `output/` ve `.state/` klasörleri `.gitignore`'a
eklendi; bunları senkronize bir bulut klasörüne (iCloud/Dropbox vb.)
koymaktan kaçının (GUI artık bu durumu da tespit edip uyarıyor — bkz.
madde 5).

## Şu anki durum / sırada ne var

- ✅ Tek öğrenci hesabıyla tek sınav sayfası için uçtan uca akış kuruldu.
- ✅ Blackboard REST API seçeneği araştırıldı, kullanılmamasına karar verildi.
- ✅ `scan_course.py` gerçek Not Defteri sayfasında test edildi ve başarılı
  çalışıyor; ders bazlı klasörleme + idempotent atlama + hata kurtarma
  eklendi.
- ✅ `scan_grade_center.py` yazıldı ve yukarıdaki felaket senaryolarına
  karşı sağlamlaştırıldı (bir hoca ekran görüntüsüne dayanarak) —
  gerçek Grade Center'da henüz test edilmedi, sırada bu var.
- ✅ GUI, öğrenci hesabıyla uçtan uca gerçek testte çalıştı: giriş → SSO
  sorunları giderildi (gerçek Chrome + otomasyon gizleme, çoklu sekme
  takibi, tekrar-deneyen giriş kontrolü) → "Bul ve Tara" / "PDF Olarak
  İndir" iki aşamalı akışa geçildi → `indirme_log.txt` eklendi.
- ✅ GUI baştan tasarlandı: ilk açılışta adım adım anlatan bir onboarding
  ekranı ("Nasıl Çalışır?" ile tekrar açılabilir), kart tabanlı düzenli
  bir ana ekran, tarama sırasında canlı ilerleme sayacı, bitince sesli/
  görsel uyarı (`root.bell()` + pencereyi öne getirme).
- ✅ PDF'lerde bazen fotoğrafların "açılmamış" (boş) çıkması giderildi:
  yazdırmadan önce artık sayfadaki tüm görsellerin gerçekten yüklenip
  decode edilmesi bekleniyor; süre dolarsa PDF üretilmiyor (tekrar
  denenir), tarayıcının "bitti ama gelmedi" dediği gerçekten bozuk
  görseller ise PDF'i durdurmadan ayrı bir uyarı olarak loglanıyor.
- ✅ Sınav tespiti isim tabanlı anahtar kelimeden içerik tabanlı (ONAY
  kodu var mı) tespite geçirildi — bkz. yukarıdaki "Sınav tespiti"
  notu.
- ✅ Yakalama sırasında sayfaya enjekte edilen `FORCE_VISIBLE_CSS`
  (`* { overflow: visible !important; ... }`) artık işlem bitince
  (başarılı ya da hatalı fark etmeksizin) sayfadan kaldırılıyor. Öncesinde
  bu agresif CSS sayfada kalıcı kalabiliyordu; Blackboard Ultra bir SPA
  olduğu için aynı sekmede başka bir derse/sayfaya geçildiğinde (tam
  sayfa yenilenmeden) o yeni sayfa da bozuk görünebiliyordu (dropdown/
  panel gibi overflow'a dayanan UI öğeleri bozuluyordu) — düzeltildi.

### Keşfedilen yeni sayfa türü — "Gönderimler" listesi (henüz kod yazılmadı)

Hocadan gelen bir ekran görüntüsünde, bir sınava (ör.
`BST020-KısaSınav1`) tıklandığında önce **"Gönderimler (N)"** sekmesi
açık bir liste sayfasına düşüldüğü görüldü: tüm öğrenciler tek tabloda
(Öğrenci, Öğrenci Durumu, Not Verme Durumu, Not, Gönder), ve üstte
kritik bir bilgi — **"15/20 GÖNDERİLDİ"** gibi net bir **toplam sayı**.

Bu, `scan_grade_center.py`'nin şu an varsaydığı "Öğrenciler" sidebar'lı
tek-öğrenci-açık görünümünden **farklı, muhtemelen bir önceki adım**.
Hipotez (henüz doğrulanmadı): bu listede bir öğrenci satırına tıklamak,
zaten desteklediğimiz sidebar'lı görünüme götürüyor olabilir.

Bu sayfa doğrulanıp desteklenirse iki fayda sağlar:
1. Hoca için daha doğal bir başlangıç noktası (tek öğrenci açmaya
   gerek kalmadan doğrudan sınava tıklamak yeterli olur).
2. **Toplam sayı doğrulaması** — uzun zamandır aranan "90 öğrencide
   kaçını gerçekten yakaladık" sorusuna cevap: yakalanan sayı ile bu
   sayfadaki "X/Y GÖNDERİLDİ" karşılaştırılıp eksik varsa açıkça
   uyarılabilir.

**Kasıtlı olarak henüz kod yazılmadı** — bir öğrenci satırına
tıklandığında gerçekte ne olduğunu gösteren bir ekran görüntüsü
bekleniyor; kör tahminle selector yazmak daha önce defalarca yanlış
yönlendirmişti (bkz. SSO/sekme takibi hikayesi yukarıda), aynı hataya
düşülmeyecek.
