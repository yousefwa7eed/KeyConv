# tests/test_controls.py — إعدادات لوحة التحكم وربطها الفوري بالخدمة.
#
# يغطي: سلامة ملف الإعدادات (تالف/قيم غريبة)، وأن تعطيل ميزة من اللوحة
# يمنع فعلها فعلاً دون إعادة تشغيل، وأن إعادة تفعيلها تُرجع السلوك.

import time

import pytest

import main
import settings_store
from test_pipeline import FakeIO, double_tap


@pytest.fixture(autouse=True)
def _restore_features():
    yield
    main.set_features(settings_store.DEFAULTS)
    main.guard.release_now()   # لا يبقى حارس الحقن مفعّلاً للاختبار التالي


def make_io(features=None):
    main.buffer.reset()
    main._modifiers.clear()
    main.guard.release_now()
    values = dict(settings_store.DEFAULTS)
    values.update(features or {})
    main.set_features(values)
    io = FakeIO()
    main.IO = io
    return io


def type_text(text):
    for ch in text:
        main.handle_key_event(ch, True)
        main.handle_key_event(ch, False)


# ── ملف الإعدادات ─────────────────────────────────────────────
EXPECTED_DEFAULTS = {
    "double_shift": True,
    "switch_layout": False,
    "slots_enabled": False,
}


def test_defaults_are_all_on(tmp_path):
    missing = tmp_path / "settings.json"
    assert settings_store.load(str(missing)) == EXPECTED_DEFAULTS


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    broken = tmp_path / "settings.json"
    broken.write_text("{ليس JSON", encoding="utf-8")
    assert settings_store.load(str(broken)) == EXPECTED_DEFAULTS


def test_wrong_types_are_ignored(tmp_path):
    weird = tmp_path / "settings.json"
    weird.write_text('{"double_shift": "no", "caps": 0}', encoding="utf-8")
    assert settings_store.load(str(weird)) == EXPECTED_DEFAULTS


def test_save_round_trip(tmp_path):
    target = tmp_path / "settings.json"
    settings_store.save({"double_shift": False, "switch_layout": True}, str(target))
    values = settings_store.load(str(target))
    assert values["double_shift"] is False
    assert values["switch_layout"] is True
    assert values["slots_enabled"] is False
    assert settings_store.mtime(str(target)) is not None


# ── الإيقاف الفوري للميزات ────────────────────────────────────
def test_shift_disabled_ignores_gesture():
    io = make_io({"double_shift": False, "switch_layout": True})
    io.selected = "مرحبا"
    double_tap("shift")
    assert io.copy_calls == 0
    assert io.paste_calls == 0


def test_word_conversion_disabled_with_same_feature():
    io = make_io({"double_shift": False})
    type_text("lvpfh")
    double_tap("shift")
    assert io.written == []
    assert io.backspaces_total == 0


def test_enabling_again_restores_behaviour():
    io = make_io({"double_shift": False})
    main.set_features({"double_shift": True})
    type_text("lvpfh")
    double_tap("shift")
    assert io.written == ["مرحبا"]


# ── التطبيق الفوري بلا إعادة تشغيل ───────────────────────────
def test_watcher_applies_file_changes_live(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    settings_store.save({"double_shift": True, "switch_layout": False}, str(target))
    monkeypatch.setenv("KEYCONV_SETTINGS", str(target))

    main.set_features(settings_store.load())
    stop = main.start_settings_watcher(interval=0.05)
    try:
        settings_store.save({"double_shift": False, "switch_layout": True}, str(target))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and main.feature_enabled("double_shift"):
            time.sleep(0.03)
        assert main.feature_enabled("double_shift") is False
        assert main.feature_enabled("switch_layout") is True
    finally:
        stop.set()
