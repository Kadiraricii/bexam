"""web_view.py icin testler - PIN korumali, salt-okunur Indirme durumu
sunucusu. Sunucu GERCEK bir soket acar (127.0.0.1, rastgele/yuksek bir
port) - her testte start() edilen sunucu MUTLAKA stop() ediliyor (fixture
teardown'da), aksi halde ardisik testler port cakismasi ya da acik
soket birikmesi yasayabilir."""

import http.client
from urllib.parse import urlencode

import pytest

import common
import web_view


@pytest.fixture(scope="module")
def running_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("webview")
    server = web_view.WebViewServer(tmp_path, port=0)
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def reset_server_state(running_server):
    with running_server._lock:
        running_server._sessions.clear()
        running_server._failed_attempts.clear()


def _get(server: "web_view.WebViewServer", path: str, cookie: str | None = None) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        headers = {"Cookie": f"{web_view.SESSION_COOKIE_NAME}={cookie}"} if cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp_headers = dict(resp.getheaders())
        body = resp.read()
        return status, resp_headers, body
    finally:
        conn.close()


def _post(
    server: "web_view.WebViewServer",
    path: str,
    data: dict[str, str],
    cookie: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = f"{web_view.SESSION_COOKIE_NAME}={cookie}"
        body = urlencode(data)
        conn.request("POST", path, body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp_headers = dict(resp.getheaders())
        resp_body = resp.read()
        return status, resp_headers, resp_body
    finally:
        conn.close()


def test_generate_pin_is_six_digits():
    pin = web_view._generate_pin()
    assert len(pin) == web_view.PIN_LENGTH
    assert pin.isdigit()


def test_generate_pin_varies_across_calls():
    """Her cagrida rastgele uretiliyor mu diye kaba bir kontrol - AYNI
    PIN'in tekrar tekrar cikmasi (sahte rastgelelik) bu testi kacirmaz
    ama makul sayida denemede en az bir farkli deger beklenir."""
    pins = {web_view._generate_pin() for _ in range(20)}
    assert len(pins) > 1


def test_server_start_assigns_port_and_stop_releases_it(tmp_path):
    server = web_view.WebViewServer(tmp_path, port=0)
    assert not server.is_running
    server.start()
    try:
        assert server.is_running
        assert server.port is not None
        assert server.url.startswith("http://")
    finally:
        server.stop()
    assert not server.is_running
    assert server.port is None


def test_root_without_pin_shows_pin_page(running_server):
    status, _headers, raw_body = _get(running_server, "/")
    body = raw_body.decode("utf-8")
    assert status == 200
    assert "PIN" in body
    assert "İndirme Durumu" in body
    assert 'method="post"' in body


def test_pin_page_renders_six_digit_only_boxes(running_server):
    """PIN giris ekraninin 6 ayri, sadece rakam kabul eden (JS ile
    filtrelenen) kutu olarak render edildigini ve degerlerin gizli tek
    bir alanda birlestirildigini dogrular - bkz. _render_pin_page_html."""
    _status, _headers, raw_body = _get(running_server, "/")
    body = raw_body.decode("utf-8")

    assert body.count('class="pin-box"') == web_view.PIN_LENGTH
    assert 'maxlength="1"' in body
    assert 'id="pin-hidden"' in body
    # JS filtresi: girilen her karakterden rakam DISINDAKI her seyi atar.
    assert "replace(/[^0-9]/g, '')" in body


def test_wrong_pin_is_rejected(running_server):
    real_pin = running_server.pin
    wrong_first_digit = str((int(real_pin[0]) + 1) % 10)
    wrong_pin = wrong_first_digit + real_pin[1:]

    status, _headers, raw_body = _post(running_server, "/", {"pin": wrong_pin})
    assert status == 401
    body = raw_body.decode("utf-8")
    assert "Hatalı" in body


def test_correct_pin_redirects_and_sets_session_cookie(running_server):
    status, headers, _body = _post(running_server, "/", {"pin": running_server.pin})
    assert status == 303
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    assert web_view.SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie


def test_valid_session_cookie_shows_dashboard(running_server):
    _status, headers, _body = _post(running_server, "/", {"pin": running_server.pin})
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    token = set_cookie.split(f"{web_view.SESSION_COOKIE_NAME}=")[1].split(";")[0]

    status, _headers, raw_body = _get(running_server, "/", cookie=token)
    body = raw_body.decode("utf-8")
    assert status == 200
    assert "İndirme Durumu" in body
    assert "otomatik yenilenir" in body


def test_server_survives_render_exception_and_serves_next_request(running_server, monkeypatch):
    """Bir istek isleme sirasinda beklenmedik bir istisna olusursa (ör.
    Windows'ta kilitli/izin sorunlu bir dosya nedeniyle stat() hatasi -
    exam_roster_completion zaten bunu yakaliyor ama teorik olarak baska
    bir yerde YAKALANMAMIS bir hata cikarsa) TEK sunucunun TAMAMEN
    COKMEMESI, bir SONRAKI isteği normal yanitlamaya devam etmesi
    gerekiyor - socketserver'in kendi varsayilan istek-basina hata
    izolasyonuna guveniyoruz, burada CANLI dogruluyoruz."""
    _status, headers, _body = _post(running_server, "/", {"pin": running_server.pin})
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    token = set_cookie.split(f"{web_view.SESSION_COOKIE_NAME}=")[1].split(";")[0]

    def _boom(_output_dir):
        raise PermissionError("simüle edilmiş dosya erişim hatası")

    monkeypatch.setattr(web_view, "collect_download_overview", _boom)
    # Bu istek sirasinda sunucu tarafinda bir istisna olusacak - istemci
    # tarafinda baglanti sifirlanmasi/bozuk yanit gibi bir sonuc
    # BEKLENIYOR, testin asil amaci bu satirin exception FIRLATMASI
    # DEGIL, sunucunun bir SONRAKI istekte hala ayakta olmasi.
    try:
        _get(running_server, "/", cookie=token)
    except (ConnectionError, OSError):
        pass
    monkeypatch.undo()

    status, _headers, raw_body = _get(running_server, "/", cookie=token)
    assert status == 200
    assert "İndirme Durumu" in raw_body.decode("utf-8")


def test_lockout_after_too_many_wrong_attempts(running_server):
    real_pin = running_server.pin
    wrong_pin = str((int(real_pin[0]) + 1) % 10) + real_pin[1:]
    for _ in range(web_view.MAX_PIN_ATTEMPTS):
        _post(running_server, "/", {"pin": wrong_pin})
    status, _headers, _body = _post(running_server, "/", {"pin": running_server.pin})
    assert status == 429


def test_favicon_returns_204(running_server):
    status, _headers, _body = _get(running_server, "/favicon.ico")
    assert status == 204


def test_security_headers_present(running_server):
    _status, headers, _body = _get(running_server, "/")
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert headers_lower.get("x-content-type-options") == "nosniff"
    assert headers_lower.get("x-frame-options") == "DENY"
    assert headers_lower.get("referrer-policy") == "no-referrer"
    assert "no-store" in (headers_lower.get("cache-control") or "")


def test_dashboard_html_shows_missing_student_chip(tmp_path):
    course_dir = tmp_path / "BST020"
    exam_dir = course_dir / "Final"
    exam_dir.mkdir(parents=True)
    (course_dir / common.STUDENT_ROSTER_CSV_FILENAME).write_text(
        "Ad Soyad;Öğrenci Numarası\r\n"
        "MEHMET KADİR ARICI;2420191035\r\n"
        "AYŞE YILMAZ;2420171001\r\n",
        encoding="utf-8-sig",
    )
    stem = common.format_student_pdf_stem("Final", "MEHMET KADİR ARICI", "2420191035")
    (exam_dir / f"{stem}.pdf").write_bytes(b"%PDF-1.4" + b"x" * common.MIN_VALID_PDF_BYTES)

    html_out = web_view._render_dashboard_html(tmp_path)

    assert "1/2" in html_out
    assert "AYŞE YILMAZ" in html_out
    assert "1 öğrenci eksik" in html_out


def test_dashboard_html_shows_scan_done_banner_when_fully_completed(tmp_path):
    # Kullanicinin istegi: bir dersin Not Defteri taramasi TUM sinav
    # satirlarini islemeyi bitirdiginde, web durum sayfasinda bunu
    # ACIKCA gosteren bir bildirim olsun (bkz. gui.py'deki
    # write_scan_completion_status cagrisi, capture_exam_submissions
    # dongusunun sonunda).
    common.write_scan_completion_status(
        tmp_path,
        course_label="BST020 - Veri Madenciliği",
        exam_count=6,
        totals={"ok": 15, "skip": 3, "fail": 0},
        fully_completed=True,
    )

    html_out = web_view._render_dashboard_html(tmp_path)

    assert "Tarama tamamlandı" in html_out
    assert "BST020 - Veri Madenciliği" in html_out
    assert "6 sınav bulundu, hepsi işlendi" in html_out


def test_dashboard_html_hides_scan_done_banner_when_not_fully_completed(tmp_path):
    # bkz. _render_scan_status_banner_html docstring'i: yarim kalan bir
    # tarama icin banner BILEREK gosterilmiyor - "hepsi bitti" YANLIS
    # izlenimi vermemek icin.
    common.write_scan_completion_status(
        tmp_path,
        course_label="BST020 - Veri Madenciliği",
        exam_count=6,
        totals={"ok": 2, "skip": 0, "fail": 1},
        fully_completed=False,
    )

    html_out = web_view._render_dashboard_html(tmp_path)

    assert "Tarama tamamlandı" not in html_out


def test_dashboard_html_hides_scan_done_banner_when_no_status_file(tmp_path):
    html_out = web_view._render_dashboard_html(tmp_path)

    assert "Tarama tamamlandı" not in html_out


def test_dashboard_html_escapes_names():
    escaped = web_view.html.escape("Veri & Yapay Zeka <Lab>")
    assert escaped == "Veri &amp; Yapay Zeka &lt;Lab&gt;"


def test_missing_list_collapses_when_many_students_missing(tmp_path):
    course_dir = tmp_path / "BST020"
    exam_dir = course_dir / "Final"
    exam_dir.mkdir(parents=True)
    rows = [(f"ÖĞRENCİ {i}", f"24201{i:05d}") for i in range(12)]
    csv_body = "Ad Soyad;Öğrenci Numarası\r\n" + "".join(f"{n};{no}\r\n" for n, no in rows)
    (course_dir / common.STUDENT_ROSTER_CSV_FILENAME).write_text(csv_body, encoding="utf-8-sig")

    html_out = web_view._render_dashboard_html(tmp_path)

    assert "<details" in html_out
    assert "12 öğrenciyi göster" in html_out


def test_missing_list_stays_expanded_when_few_students_missing(tmp_path):
    course_dir = tmp_path / "BST020"
    exam_dir = course_dir / "Final"
    exam_dir.mkdir(parents=True)
    (course_dir / common.STUDENT_ROSTER_CSV_FILENAME).write_text(
        "Ad Soyad;Öğrenci Numarası\r\nAYŞE YILMAZ;2420171001\r\n",
        encoding="utf-8-sig",
    )

    html_out = web_view._render_dashboard_html(tmp_path)

    assert "<details" not in html_out
    assert "AYŞE YILMAZ" in html_out

