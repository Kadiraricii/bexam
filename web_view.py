"""
Salt-okunur, PIN korumalı bir HTTP "durum sayfası": İndirme sayfasındaki
AYNI kart/tamamlanma verisini (bkz. common.collect_download_overview,
common.summarize_download_overview) BAŞKA bir cihazdan (ör. hocanın
kendi bilgisayarında GUI açıkken, lab'daki 2. bir bilgisayardan) tarayıcı
ile görüntülemek için.

BİLİNÇLİ OLARAK HİÇBİR AKSİYON TETİKLEMEZ: dosya/klasör açma, silme,
tarama başlatma gibi hiçbir eylem endpoint'i YOK - sadece görüntüleme.
Eksik öğrenci listeleri JavaScript'siz, saf HTML <details>/<summary> ile
genişletilip daraltılabiliyor (bu bir "tıklama eylemi" değil, sadece o
kartın içindeki metni açıp kapatan yerel bir görüntü değişikliği -
sunucuya hiçbir istek gitmiyor).

GÜVENLİK MODELİ - bunu net anlamak önemli:
- Bu bir kimlik doğrulama sistemi DEĞİL, aynı yerel ağ/lab ortamındaki
  YETKİSİZ göz atmaya karşı hafif bir engel (6 haneli PIN + IP başına
  deneme sınırlaması).
- PIN gönderimi HTTP POST metodu ile yapılır, şifre alanı gizlenmiştir ve
  URL adres çubuğunda/geçmişinde görünmez.
- Sunucu 0.0.0.0'a bağlanıyor (ikinci bilgisayardan erişim için bu
  GEREKLİ) - bu yüzden SADECE güvendiğin bir ağda (ör. üniversite iç
  ağı, ev Wi-Fi'si) kullan, herkese açık/paylaşımlı bir Wi-Fi'de ASLA.
- Trafik ŞİFRELENMEZ (düz HTTP, TLS yok) - aynı ağdaki biri teorik
  olarak trafiği dinleyebilir. Öğrenci PDF'lerinin İÇERİĞİ hiç
  sunulmuyor (sadece ad/numara/tamamlanma durumu), ama bu veri de
  kişisel olduğu için (bkz. README "Gizlilik notu") bilerek/istenerek
  açılmalı, varsayılan olarak KAPALI başlar.
"""

import html
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from common import DownloadOverview, collect_download_overview, summarize_download_overview

DEFAULT_PORT = 8899
PORT_SEARCH_ATTEMPTS = 10
PIN_LENGTH = 6
SESSION_COOKIE_NAME = "bbview_session"
AUTO_REFRESH_SECONDS = 5
MAX_PIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
SESSION_TTL_SECONDS = 86400  # Oturumlar 24 saat geçerlidir
MISSING_LIST_COLLAPSE_THRESHOLD = 8


def _local_lan_ip() -> str:
    """Bu makinenin yerel ağdaki IP adresini tahmin eder."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _generate_pin() -> str:
    return f"{secrets.randbelow(10 ** PIN_LENGTH):0{PIN_LENGTH}d}"


class WebViewServer:
    """PIN korumalı, salt-okunur İndirme durumu sunucusu."""

    def __init__(self, output_dir: Path, port: int = DEFAULT_PORT) -> None:
        self.output_dir = output_dir
        self.pin = _generate_pin()
        self.lan_ip = _local_lan_ip()
        self.port: int | None = None
        self._requested_port = port
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}  # token -> timestamp
        self._failed_attempts: dict[str, list[float]] = {}
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.lan_ip}:{self.port}" if self.port is not None else ""

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self.is_running:
            return
        with self._lock:
            self._sessions.clear()
            self._failed_attempts.clear()
        handler_cls = _make_handler(self)
        last_error: OSError | None = None
        httpd: HTTPServer | None = None
        chosen_port: int | None = None
        for attempt in range(PORT_SEARCH_ATTEMPTS):
            candidate_port = self._requested_port + attempt
            try:
                httpd = HTTPServer(("0.0.0.0", candidate_port), handler_cls)
            except OSError as exc:
                last_error = exc
                continue
            chosen_port = httpd.server_address[1]
            break
        if httpd is None or chosen_port is None:
            raise RuntimeError(
                f"Web görünümü için uygun bir port bulunamadı "
                f"({self._requested_port}-{self._requested_port + PORT_SEARCH_ATTEMPTS - 1} "
                "arası hepsi meşgul)."
            ) from last_error
        self._httpd = httpd
        self.port = chosen_port
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._lock:
            self._sessions.clear()
            self._failed_attempts.clear()
        self._httpd = None
        self._thread = None
        self.port = None

    # ---- PIN/oturum yardımcıları ----

    def check_pin(self, candidate: str, client_ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = [t for t in self._failed_attempts.get(client_ip, []) if now - t < LOCKOUT_SECONDS]
            if len(attempts) >= MAX_PIN_ATTEMPTS:
                self._failed_attempts[client_ip] = attempts
                return False
            if secrets.compare_digest(candidate, self.pin):
                self._failed_attempts.pop(client_ip, None)
                return True
            attempts.append(now)
            self._failed_attempts[client_ip] = attempts
            return False

    def is_locked_out(self, client_ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = [t for t in self._failed_attempts.get(client_ip, []) if now - t < LOCKOUT_SECONDS]
            return len(attempts) >= MAX_PIN_ATTEMPTS

    def new_session(self) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._sessions[token] = now
        return token

    def has_session(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            created_at = self._sessions.get(token)
            if created_at is None:
                return False
            if now - created_at > SESSION_TTL_SECONDS:
                self._sessions.pop(token, None)
                return False
            return True


def _make_handler(server: WebViewServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "IndirmeGorunumu/1.0"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

        def _write_html(self, status: int, body: str, extra_headers: dict[str, str] | None = None) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)
            self.close_connection = True

        def _redirect_to_dashboard(self, session_token: str) -> None:
            self.send_response(303)
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE_NAME}={session_token}; Path=/; HttpOnly; SameSite=Strict",
            )
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def _session_token(self) -> str | None:
            cookie_header = self.headers.get("Cookie", "")
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(f"{SESSION_COOKIE_NAME}="):
                    return part[len(SESSION_COOKIE_NAME) + 1:]
            return None

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            if server.has_session(self._session_token()):
                self._write_html(200, _render_dashboard_html(server.output_dir))
                return

            query = parse_qs(parsed.query)
            pin_candidate = (query.get("pin") or [""])[0].strip()
            client_ip = self.client_address[0]

            if not pin_candidate:
                self._write_html(200, _render_pin_page_html())
                return

            if server.is_locked_out(client_ip):
                self._write_html(429, _render_pin_page_html(
                    f"Çok fazla yanlış deneme yapıldı. {LOCKOUT_SECONDS} saniye sonra tekrar dene."
                ))
                return

            if server.check_pin(pin_candidate, client_ip):
                self._redirect_to_dashboard(server.new_session())
                return

            self._write_html(401, _render_pin_page_html("Hatalı PIN, tekrar dene."))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            client_ip = self.client_address[0]
            if server.is_locked_out(client_ip):
                self._write_html(429, _render_pin_page_html(
                    f"Çok fazla yanlış deneme yapıldı. {LOCKOUT_SECONDS} saniye sonra tekrar dene."
                ))
                return

            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                post_data = parse_qs(body)
            except Exception:
                post_data = {}

            pin_candidate = (post_data.get("pin") or [""])[0].strip()

            if not pin_candidate:
                self._write_html(400, _render_pin_page_html("Lütfen PIN girin."))
                return

            if server.check_pin(pin_candidate, client_ip):
                self._redirect_to_dashboard(server.new_session())
                return

            self._write_html(401, _render_pin_page_html("Hatalı PIN, tekrar dene."))

    return Handler


_PAGE_STYLE = """
  :root {
    --bg: #F8FAFC; --card: #FFFFFF; --border: #E2E8F0; --text: #0F172A;
    --muted: #64748B; --accent: #2563EB; --success: #16A34A;
    --success-soft: #DCFCE7; --warning: #B45309; --warning-soft: #FEF3C7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }
"""


def _render_pin_page_html(error: str | None = None) -> str:
    error_html = (
        f'<p class="error">⚠ {html.escape(error)}</p>' if error else ""
    )
    # 6 ayri kutu (OTP/2FA giris ekranlarindaki tanidik desen) - her kutu
    # SADECE 1 rakam tutuyor, yazilinca otomatik bir sonrakine geciyor.
    # Sunucu tarafinda TEK bir "pin" alani bekleniyor (bkz. do_POST), bu
    # yuzden 6 kutunun degeri gizli bir input'ta birlestiriliyor - kutular
    # kendileri form ile GONDERILMIYOR (name'siz), sadece gorsel/girdi
    # katmani.
    boxes_html = "".join(
        f'<input class="pin-box" type="text" inputmode="numeric" autocomplete="one-time-code" '
        f'maxlength="1" data-index="{i}">'
        for i in range(PIN_LENGTH)
    )
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Giriş — İndirme Durumu</title>
<style>{_PAGE_STYLE}
  .wrap {{ min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }}
  .box {{
    background: var(--card); border: 1px solid var(--border); border-radius: 18px;
    padding: 36px 32px; max-width: 380px; width: 100%; text-align: center;
    box-shadow: 0 1px 2px rgba(15,23,42,.04), 0 12px 28px -14px rgba(15,23,42,.18);
  }}
  .lock {{
    width: 52px; height: 52px; border-radius: 50%; background: var(--accent);
    display: grid; place-items: center; margin: 0 auto 16px; font-size: 24px;
  }}
  h1 {{ font-size: 19px; margin: 0 0 6px; }}
  p.hint {{ color: var(--muted); font-size: 13.5px; margin: 0 0 24px; line-height: 1.5; }}
  p.error {{
    color: #B91C1C; background: #FEE2E2; border-radius: 8px; padding: 10px 12px;
    font-size: 13.5px; margin: 0 0 16px; text-align: left;
  }}
  .pin-row {{ display: flex; gap: 8px; justify-content: center; margin-bottom: 22px; }}
  .pin-box {{
    width: 42px; height: 52px; font-size: 24px; font-weight: 700; text-align: center;
    border-radius: 10px; border: 1.5px solid var(--border); color: var(--text);
    -moz-appearance: textfield;
  }}
  .pin-box:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37,99,235,.15); }}
  .pin-box::-webkit-outer-spin-button, .pin-box::-webkit-inner-spin-button {{
    -webkit-appearance: none; margin: 0;
  }}
  button {{
    width: 100%; padding: 13px; border-radius: 10px; border: none;
    background: var(--accent); color: white; font-size: 15px; font-weight: 700; cursor: pointer;
  }}
  button:disabled {{ background: var(--border); color: var(--muted); cursor: not-allowed; }}
  /* Dar telefon ekranlarinda (ör. 375px genislikte bir iPhone) 6 kutu +
     aralarindaki bosluklar .box'in ic dolgusuyla birlikte gorunur
     genisligi asip yatay tasmaya (kutularin kirpilmasina) yol
     acabiliyordu - CANLI HESAPLANDI: varsayilan olculerle 292px
     gerekiyor ama 375px'lik bir ekranda .wrap+.box dolgusu dusuldukten
     sonra sadece ~263px kalabiliyor. Dar ekranlarda kutu/bosluk/dolgu
     kucultuluyor.*/
  @media (max-width: 400px) {{
    .wrap {{ padding: 16px; }}
    .box {{ padding: 28px 18px; }}
    .pin-row {{ gap: 6px; }}
    .pin-box {{ width: 34px; height: 46px; font-size: 20px; }}
  }}
</style></head>
<body><div class="wrap"><div class="box">
  <div class="lock">🔒</div>
  <h1>İndirme Durumu</h1>
  <p class="hint">Devam etmek için hocanın ekranında gösterdiği<br>{PIN_LENGTH} haneli PIN'i gir.</p>
  {error_html}
  <form method="post" action="/" id="pin-form">
    <div class="pin-row">{boxes_html}</div>
    <input type="hidden" name="pin" id="pin-hidden">
    <button type="submit" id="pin-submit" disabled>Giriş</button>
  </form>
</div></div>
<script>
  // Sadece rakam kabul eden, otomatik ileri/geri odaklanan 6 kutulu PIN
  // girisi. Hicbir harici kutuphane/istek yok - sadece bu sayfaya ozel,
  // gomulu (inline) bir davranis katmani.
  (function () {{
    var boxes = Array.prototype.slice.call(document.querySelectorAll('.pin-box'));
    var hidden = document.getElementById('pin-hidden');
    var submitBtn = document.getElementById('pin-submit');

    function syncHiddenAndButton() {{
      var value = boxes.map(function (b) {{ return b.value; }}).join('');
      hidden.value = value;
      submitBtn.disabled = value.length !== {PIN_LENGTH};
    }}

    boxes.forEach(function (box, i) {{
      box.addEventListener('input', function () {{
        box.value = box.value.replace(/[^0-9]/g, '').slice(0, 1);
        if (box.value && i < boxes.length - 1) {{ boxes[i + 1].focus(); }}
        syncHiddenAndButton();
      }});
      box.addEventListener('keydown', function (e) {{
        if (e.key === 'Backspace' && !box.value && i > 0) {{ boxes[i - 1].focus(); }}
      }});
      box.addEventListener('paste', function (e) {{
        e.preventDefault();
        var text = (e.clipboardData || window.clipboardData).getData('text')
          .replace(/[^0-9]/g, '').slice(0, {PIN_LENGTH});
        text.split('').forEach(function (ch, idx) {{ if (boxes[idx]) {{ boxes[idx].value = ch; }} }});
        var next = Math.min(text.length, boxes.length - 1);
        boxes[next].focus();
        syncHiddenAndButton();
      }});
    }});

    if (boxes.length) {{ boxes[0].focus(); }}
  }})();
</script>
</body></html>"""


def _render_dashboard_html(output_dir: Path) -> str:
    data: DownloadOverview = collect_download_overview(output_dir)
    summary = summarize_download_overview(data)

    parts: list[str] = []
    if summary is not None:
        stat_cells = [
            (str(summary["total_exams"]), "sınav"),
            (f"{summary['captured_sum']}/{summary['roster_total_sum']}", "toplam yakalama"),
            (str(summary["exams_with_missing"]), "sınavda eksik var"),
        ]
        if summary["top_student"] is not None:
            name, count = summary["top_student"]
            stat_cells.append((name, f"en çok eksik kalan öğrenci ({count} sınav)"))
        cells_html = "".join(
            f'<div class="stat"><div class="stat-num">{html.escape(num)}</div>'
            f'<div class="stat-label">{html.escape(label)}</div></div>'
            for num, label in stat_cells
        )
        parts.append(f'<div class="stat-strip">{cells_html}</div>')

    if not data:
        parts.append('<p class="empty">Henüz hiç ders klasörü yok.</p>')

    for course_dir, exams in data:
        parts.append(f'<div class="course-heading">📁 {html.escape(course_dir.name)}</div>')
        if not exams:
            parts.append('<p class="empty">Bu derste henüz sınav klasörü yok.</p>')
            continue
        parts.append('<div class="card-grid">')
        for exam_dir, completion, item_count in exams:
            parts.append(_render_exam_card_html(exam_dir, completion, item_count))
        parts.append("</div>")

    body_html = "".join(parts)
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="{AUTO_REFRESH_SECONDS}">
<title>İndirme Durumu</title>
<style>{_PAGE_STYLE}
  .page {{ max-width: 1080px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin: 0 0 20px; }}
  .stat-strip {{
    display: flex; flex-wrap: wrap; background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden; margin-bottom: 20px;
  }}
  .stat {{ flex: 1; min-width: 140px; padding: 14px 18px; border-right: 1px solid var(--border); }}
  .stat:last-child {{ border-right: none; }}
  .stat-num {{ font-size: 20px; font-weight: 700; }}
  .stat-label {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
  .course-heading {{ font-size: 15px; font-weight: 700; margin: 22px 0 10px; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }}
  .exam-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 16px; }}
  .exam-card-head {{ display: flex; align-items: center; gap: 12px; }}
  .ring {{
    --pct: 100; width: 52px; height: 52px; border-radius: 50%; flex-shrink: 0;
    background: conic-gradient(var(--ring-color) calc(var(--pct) * 1%), var(--border) 0);
    display: grid; place-items: center;
  }}
  .ring-inner {{
    width: 40px; height: 40px; border-radius: 50%; background: var(--card);
    display: grid; place-items: center; font-size: 11px; font-weight: 700;
  }}
  .exam-title {{ font-weight: 700; font-size: 14px; }}
  .exam-sub {{ font-size: 12px; margin-top: 2px; }}
  .exam-sub.ok {{ color: var(--success); }}
  .exam-sub.warn {{ color: var(--warning); }}
  .missing-list {{ margin-top: 10px; }}
  .chip {{
    display: inline-block; font-size: 11px; padding: 4px 9px; border-radius: 999px;
    background: var(--warning-soft); color: #92400E; margin: 0 6px 6px 0;
  }}
  details.missing-list summary {{ font-size: 12px; color: var(--muted); cursor: pointer; margin-bottom: 6px; }}
  .empty {{ color: var(--muted); font-size: 14px; }}
</style></head>
<body><div class="page">
  <h1>📥 İndirme Durumu</h1>
  <p class="sub">Salt okunur canlı görünüm - {AUTO_REFRESH_SECONDS} saniyede bir otomatik yenilenir.</p>
  {body_html}
</div></body></html>"""


def _render_exam_card_html(
    exam_dir: Path,
    completion: tuple[int, list[tuple[str, str]]] | None,
    item_count: int,
) -> str:
    name = html.escape(exam_dir.name)
    if completion is None:
        return f"""<div class="exam-card">
  <div class="exam-card-head">
    <div style="font-size:26px;">📁</div>
    <div><div class="exam-title">{name}</div>
    <div class="exam-sub">{item_count} öğe</div></div>
  </div>
</div>"""

    total, missing = completion
    captured = total - len(missing)
    complete = not missing
    ring_color = "var(--success)" if complete else "var(--warning)"
    pct = round((captured / total) * 100) if total else 0
    sub_text = "tamamlandı" if complete else f"{len(missing)} öğrenci eksik"
    sub_class = "ok" if complete else "warn"

    missing_html = ""
    if missing:
        chips = "".join(
            f'<span class="chip">⚠ {html.escape(display_name)} ({html.escape(student_no)})</span>'
            for display_name, student_no in missing
        )
        if len(missing) > MISSING_LIST_COLLAPSE_THRESHOLD:
            missing_html = (
                f'<details class="missing-list"><summary>{len(missing)} öğrenciyi göster</summary>'
                f"{chips}</details>"
            )
        else:
            missing_html = f'<div class="missing-list">{chips}</div>'

    return f"""<div class="exam-card">
  <div class="exam-card-head">
    <div class="ring" style="--pct:{pct}; --ring-color:{ring_color};">
      <div class="ring-inner">{captured}/{total}</div>
    </div>
    <div><div class="exam-title">{name}</div>
    <div class="exam-sub {sub_class}">{sub_text}</div></div>
  </div>
  {missing_html}
</div>"""
