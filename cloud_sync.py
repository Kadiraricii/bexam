"""
Vercel Bulut Senkronizasyon Modülü:
Yerel cihazdaki indirme/tamamlanma verilerini (output_dir) Referans Kodu (ref_code)
ve PIN Kodu ile şifreli/güvenli olarak Vercel üzerinde yayınlanan bulut paneline
senkronize eder.

Kullanım:
    sync_manager = CloudSyncManager(output_dir, vercel_url="https://metib-dashboard.vercel.app")
    sync_manager.start()
    ...
    sync_manager.stop()
"""

import json
import random
import secrets
import string
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import collect_download_overview, summarize_download_overview

DEFAULT_VERCEL_URL = "https://metib-dashboard.vercel.app"
SYNC_INTERVAL_SECONDS = 10
REF_CODE_LENGTH = 6
PIN_LENGTH = 4


def generate_ref_code() -> str:
    """Rastgele 6 haneli benzersiz Referans Kodu üretir (ör: REF-8492 veya REF-A7B9)."""
    chars = string.ascii_uppercase + string.digits
    rand_str = "".join(secrets.choice(chars) for _ in range(4))
    return f"REF-{rand_str}"


def generate_pin() -> str:
    """Rastgele 4 haneli PIN kodu üretir (ör: 4829)."""
    return f"{secrets.randbelow(10000):04d}"


def serialize_download_overview(output_dir: Path) -> dict[str, Any]:
    """collect_download_overview çıktısını Vercel/Web paneline uygun
    JSON-serializable formata dönüştürür."""
    data = collect_download_overview(output_dir)
    summary = summarize_download_overview(data)
    courses = []

    for course_dir, exams in data:
        exam_list = []
        for exam_dir, completion, item_count in exams:
            if completion is not None:
                total, missing = completion
                captured = total - len(missing)
                missing_students = [{"name": name, "no": no} for name, no in missing]
            else:
                total = None
                captured = None
                missing_students = None

            exam_list.append({
                "name": exam_dir.name,
                "item_count": item_count,
                "total": total,
                "captured": captured,
                "missing": missing_students,
            })

        courses.append({
            "name": course_dir.name,
            "exams": exam_list,
        })

    return {
        "summary": summary,
        "courses": courses,
    }


class CloudSyncManager:
    """Vercel / Cloud yayın senkronizasyon yöneticisi."""

    def __init__(
        self,
        output_dir: Path,
        vercel_url: str = DEFAULT_VERCEL_URL,
        ref_code: str | None = None,
        pin: str | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.vercel_url = vercel_url.rstrip("/")
        self.ref_code = ref_code or generate_ref_code()
        self.pin = pin or generate_pin()

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.last_sync_time: float | None = None
        self.last_error: str | None = None
        self.sync_count = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def regenerate_credentials(self) -> tuple[str, str]:
        """Yeni bir Referans Kodu ve PIN üretir."""
        with self._lock:
            self.ref_code = generate_ref_code()
            self.pin = generate_pin()
            self.last_sync_time = None
            self.last_error = None
            return self.ref_code, self.pin

    def start(self) -> None:
        """Arka plan senkronizasyon thread'ini başlatır."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Senkronizasyon thread'ini durdurur."""
        if not self.is_running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def sync_once(self) -> tuple[bool, str]:
        """Anlık tek bir senkronizasyon POST isteği gönderir."""
        payload = {
            "ref_code": self.ref_code,
            "pin": self.pin,
            "timestamp": time.time(),
            "data": serialize_download_overview(self.output_dir),
        }

        endpoint = f"{self.vercel_url}/api/sync"
        try:
            json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=json_bytes,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "BB-Sinav-Yakalayici/2.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp_text = resp.read().decode("utf-8", errors="replace")
                with self._lock:
                    self.last_sync_time = time.time()
                    self.last_error = None
                    self.sync_count += 1
                return True, "Senkronizasyon başarılı"
        except Exception as exc:
            err_msg = f"Bağlantı hatası: {exc}"
            with self._lock:
                self.last_error = err_msg
            return False, err_msg

    def _sync_loop(self) -> None:
        """Arka planda periyodik senkronizasyon döngüsü."""
        # Başlangıçta hemen ilk gönderimi yap
        self.sync_once()

        while not self._stop_event.is_set():
            # SYNC_INTERVAL_SECONDS kadar bekle (stop_event tetiklenirse hemen çıkar)
            if self._stop_event.wait(SYNC_INTERVAL_SECONDS):
                break
            self.sync_once()
