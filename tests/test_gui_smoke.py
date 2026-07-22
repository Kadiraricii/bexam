"""gui.py icin duman (smoke) testleri.

Gercek bir Tk penceresi acar; sayfalarin hatasiz kuruldugunu, kompakt
modun, onboarding akisinin ve bu oturumda bulunup duzeltilen spesifik
hatalarin (Chrome kapanma algisi, acilir/kapanir uyari bandi, onboarding
tasmasi) DUZELTILMIS haliyle calistigini dogrular.

NOT: "cursor='pointinghand'" gibi SADECE belirli bir widget kurulurken
tetiklenen, platforma-ozel cokme turu hatalar bu testlerle (widget
agacinin GERCEKTEN kurulmasiyla) yakalanir - salt modul ice aktarma
testleri BUNU kacirir. En gercekci dogrulama icin ayrica bkz.
test_app_launches.py (gercek `python gui.py` alt-surec calistirmasi).
"""

import tkinter as tk

import pytest

import gui as gui_module
from conftest import (
    _fake_sync_playwright,
    find_button_with_text,
    find_labels_containing,
    find_widgets,
    is_currently_packed,
)


# ---------- _emoji_font (Windows'ta gri/monokrom emoji hatasi) ----------
# Windows'ta Tk, emoji karakterlerini VARSAYILAN olarak "Segoe UI Symbol"
# fontuyla render ediyordu - macOS'taki renkli emoji gorunumunun aksine
# gri/tek renk, silik bir sonuc (CANLI DOGRULANDI, ekran goruntusuyle).
# "Segoe UI Emoji" ACIKCA istenirse Tk dogru, renkli glif setini seciyor.


def test_emoji_font_uses_segoe_ui_emoji_on_windows(monkeypatch):
    monkeypatch.setattr(gui_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gui_module, "_EMOJI_FONT_FAMILY", "Segoe UI Emoji")

    assert gui_module._emoji_font(20) == ("Segoe UI Emoji", 20)
    assert gui_module._emoji_font(20, "bold") == ("Segoe UI Emoji", 20, "bold")


def test_emoji_font_uses_system_default_on_non_windows(monkeypatch):
    monkeypatch.setattr(gui_module, "_EMOJI_FONT_FAMILY", "")

    assert gui_module._emoji_font(18) == ("", 18)


def test_onboarding_screen_builds_and_shows_start_button(gui_app):
    button = find_button_with_text(gui_app.container, "Başla →")

    assert button is not None


def test_finish_onboarding_reveals_app_shell_with_scan_button(gui_app):
    gui_app._finish_onboarding()
    gui_app.root.update_idletasks()

    assert hasattr(gui_app, "scan_button")


@pytest.mark.parametrize("page_key", ["home", "download", "outputs", "logs", "settings", "help"])
def test_every_nav_page_builds_without_error(gui_app, page_key):
    gui_app._finish_onboarding()

    gui_app._show_page(page_key)
    gui_app.root.update_idletasks()

    assert gui_app.current_page == page_key


def test_compact_mode_enter_and_exit_round_trip(gui_app):
    gui_app._finish_onboarding()

    gui_app._enter_compact_mode()
    gui_app.root.update_idletasks()
    assert gui_app._compact_mode is True

    gui_app._exit_compact_mode()
    gui_app.root.update_idletasks()
    assert gui_app._compact_mode is False


def test_chrome_flag_notice_not_shown_during_onboarding(gui_app):
    labels = find_labels_containing(gui_app.container, "Beklenen Bir Tarayıcı")

    assert labels == []


def test_chrome_flag_notice_shown_exactly_once_on_home_page(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("home")
    gui_app.root.update_idletasks()

    labels = find_labels_containing(gui_app.container, "Beklenen Bir Tarayıcı")

    assert len(labels) == 1


def test_chrome_flag_notice_starts_collapsed_on_home_page(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("home")
    gui_app.root.update_idletasks()

    detail = find_labels_containing(gui_app.container, "Chrome açılışta aşağıdaki")[0]

    # detail.master ("body" cercevesi) pack_forget()/pack() ile
    # gizlenip/gosteriliyor - detail'in KENDI winfo_manager() durumu
    # (kendi dogrudan ebeveyni olan body icinde hala "pack" kalir) bunu
    # yansitmaz, bu yuzden dogrudan body'yi kontrol ediyoruz.
    assert not is_currently_packed(detail.master)


def test_chrome_flag_notice_starts_expanded_on_help_page(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("help")
    gui_app.root.update_idletasks()

    detail = find_labels_containing(gui_app.container, "Chrome açılışta aşağıdaki")[0]

    assert is_currently_packed(detail.master)


def test_chrome_flag_notice_chevron_expands_and_collapses_on_click(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("home")
    gui_app.root.update_idletasks()

    chevron = find_widgets(gui_app.container, lambda w: isinstance(w, gui_module.ChevronToggle))[0]
    detail = find_labels_containing(gui_app.container, "Chrome açılışta aşağıdaki")[0]

    # chevron._on_click() DOGRUDAN cagriliyor - event_generate("<Button-1>")
    # DEGIL: tk_root fixture'i pencereyi withdraw() ile gizliyor (testler
    # sirasinda ekranda pencere yanip sonmesin diye), ve CANLI DOGRULANDI
    # ki bu durumda Canvas widget'larda sentetik <Button-1> olayi dogru
    # yonlenmiyor (fiziksel buton basma olaylari gercek, haritalanmis
    # ekran koordinatlarina ihtiyac duyuyor). Isleyiciyi dogrudan cagirmak
    # AYNI kod yolunu (set_expanded + command cagrisi) sinar, pencere
    # gorunurlugunden bagimsiz sekilde.
    chevron._on_click()
    gui_app.root.update_idletasks()
    assert is_currently_packed(detail.master)

    chevron._on_click()
    gui_app.root.update_idletasks()
    assert not is_currently_packed(detail.master)


def test_browser_lost_message_visually_disables_scan_and_download_buttons(gui_app):
    gui_app._finish_onboarding()
    gui_app._connection_state = "connected"
    gui_app._scan_enabled = True
    gui_app._download_enabled = True
    gui_app._apply_tracked_state()

    gui_app._handle_message("browser_lost", None)

    assert gui_app.scan_button._state == "disabled"
    assert gui_app.download_button._state == "disabled"
    assert gui_app.open_browser_button._state == "normal"


def test_onboarding_start_button_stays_within_window_bounds_when_content_overflows(monkeypatch):
    """Sag panel icerigi (adimlar + notlar) pencereden UZUN olsa bile
    "Başla" butonu her zaman pencere sinirlari icinde kalmali - bu
    oturumda bulunan gercek bir bug: buton eskiden ekran disina
    itilebiliyordu."""
    monkeypatch.setattr(gui_module, "has_seen_onboarding", lambda: False)
    monkeypatch.setattr(gui_module, "mark_onboarding_seen", lambda: None)
    monkeypatch.setattr(gui_module, "sync_playwright", _fake_sync_playwright)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk penceresi oluşturulamadı: {exc}")

    try:
        root.geometry("1160x650")  # kucuk yukseklik - tasma senaryosunu zorluyor
        app = gui_module.BlackboardGUI(root)
        root.update_idletasks()

        button = find_button_with_text(app.container, "Başla →")
        assert button is not None

        window_bottom = root.winfo_rooty() + root.winfo_height()
        button_bottom = button.winfo_rooty() + button.winfo_height()
        assert button_bottom <= window_bottom + 5

        app.command_queue.put(("quit", None))
        app.worker_thread.join(timeout=10)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_download_tree_page_handles_missing_output_directory(gui_app, tmp_path):
    gui_app._finish_onboarding()
    gui_app.output_dir = tmp_path / "hic-olusmamis"

    gui_app._show_page("download")
    gui_app.root.update_idletasks()

    assert gui_app.current_page == "download"
