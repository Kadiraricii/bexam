"""
cloud_sync.py için birim testleri:
Referans Kodu ve PIN üretimi, veri serileştirme ve Vercel bulut senkronizasyon
yöneticisinin davranışlarını doğrular.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import cloud_sync
import common


def test_generate_ref_code_format():
    ref = cloud_sync.generate_ref_code()
    assert ref.startswith("REF-")
    assert len(ref) == 8  # "REF-" (4) + 4 char alphanumeric


def test_generate_pin_format():
    pin = cloud_sync.generate_pin()
    assert len(pin) == cloud_sync.PIN_LENGTH
    assert pin.isdigit()


def test_serialize_download_overview(tmp_path):
    course_dir = tmp_path / "BST020"
    exam_dir = course_dir / "Final"
    exam_dir.mkdir(parents=True)

    # Roster
    (course_dir / common.STUDENT_ROSTER_CSV_FILENAME).write_text(
        "Ad Soyad;Öğrenci Numarası\r\nMEHMET KADİR ARICI;2420191035\r\nAYŞE YILMAZ;2420171001\r\n",
        encoding="utf-8-sig",
    )
    stem = common.format_student_pdf_stem("Final", "MEHMET KADİR ARICI", "2420191035")
    (exam_dir / f"{stem}.pdf").write_bytes(b"%PDF-1.4" + b"x" * common.MIN_VALID_PDF_BYTES)

    payload = cloud_sync.serialize_download_overview(tmp_path)
    assert "summary" in payload
    assert "courses" in payload
    assert len(payload["courses"]) == 1
    assert payload["courses"][0]["name"] == "BST020"
    assert len(payload["courses"][0]["exams"]) == 1
    
    exam_data = payload["courses"][0]["exams"][0]
    assert exam_data["name"] == "Final"
    assert exam_data["captured"] == 1
    assert exam_data["total"] == 2
    assert exam_data["missing"] == [{"name": "AYŞE YILMAZ", "no": "2420171001"}]


def test_cloud_sync_manager_start_stop(tmp_path):
    manager = cloud_sync.CloudSyncManager(tmp_path, vercel_url="http://localhost:9999")
    assert not manager.is_running
    assert manager.ref_code.startswith("REF-")
    assert len(manager.pin) == 4

    with patch.object(manager, "sync_once", return_value=(True, "OK")) as mock_sync:
        manager.start()
        assert manager.is_running
        manager.stop()
        assert not manager.is_running
        assert mock_sync.called


def test_cloud_sync_regenerate_credentials(tmp_path):
    manager = cloud_sync.CloudSyncManager(tmp_path)
    old_ref = manager.ref_code
    old_pin = manager.pin

    with patch.object(manager, "sync_once", return_value=(True, "OK")):
        new_ref, new_pin = manager.regenerate_credentials()
        assert new_ref != old_ref or new_pin != old_pin
        assert manager.ref_code == new_ref
        assert manager.pin == new_pin
