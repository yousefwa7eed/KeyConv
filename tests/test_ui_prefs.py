# tests/test_ui_prefs.py — حفظ تفضيل المظهر وسلامته عند ملف تالف.

import ui_prefs


def test_save_load_round_trip(tmp_path, monkeypatch):
    target = tmp_path / "ui.json"
    monkeypatch.setenv("KEYCONV_UI_PREFS", str(target))

    ui_prefs.save_dark(True)
    assert ui_prefs.load_dark() is True

    ui_prefs.save_dark(False)
    assert ui_prefs.load_dark() is False


def test_corrupt_file_follows_system(tmp_path, monkeypatch):
    target = tmp_path / "ui.json"
    target.write_text("{ليس JSON", encoding="utf-8")
    monkeypatch.setenv("KEYCONV_UI_PREFS", str(target))
    monkeypatch.setattr(ui_prefs, "system_prefers_dark", lambda: True)

    assert ui_prefs.load_dark() is True


def test_wrong_type_follows_system(tmp_path, monkeypatch):
    target = tmp_path / "ui.json"
    target.write_text('{"dark": "yes"}', encoding="utf-8")
    monkeypatch.setenv("KEYCONV_UI_PREFS", str(target))
    monkeypatch.setattr(ui_prefs, "system_prefers_dark", lambda: False)

    assert ui_prefs.load_dark() is False
