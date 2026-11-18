# tests/test_layout_switch.py — خيار تبديل لغة الكيبورد بعد التحويل.
#
# يغطي: اكتشاف لغة النص، اختيار تخطيط ويندوز المناسب من المثبَّت،
# وربط الخيار بالاختصارين (Shift × 2 / Caps Lock × 2) دون لمس لغة الجهاز فعلاً.

import time

import pytest

import layout_switch
import main
import settings_store
from converter import detect_language
from test_pipeline import FakeIO, double_tap

AR_101 = 0x00000401
AR_EGYPT = 0x00000C01
EN_US = 0x00000409
EN_UK = 0x00000809
FRENCH = 0x0000040C


@pytest.fixture(autouse=True)
def _fresh_features(monkeypatch):
    main.set_features(settings_store.DEFAULTS)
    main.guard.release_now()
    main.buffer.reset()
    yield
    main.set_features(settings_store.DEFAULTS)
    main.guard.release_now()
    main.buffer.reset()


def features(**overrides):
    values = dict(settings_store.DEFAULTS)
    values.update(overrides)
    return values


def type_text(text):
    for ch in text:
        main.handle_key_event(ch, True)
        main.handle_key_event(ch, False)


def record_switches(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main.layout_switch,
        "switch_to_text_language",
        lambda text: calls.append(text) or True,
    )
    return calls


# ── اكتشاف اللغة ───────────────────────────────────────────────
def test_detect_language():
    assert detect_language("مرحبا") == "ar"
    assert detect_language("lvpfh") == "en"
    assert detect_language("مرحبا 123") == "ar"
    assert detect_language("hello 123") == "en"


def test_detect_language_unknown_or_tied():
    assert detect_language("") is None
    assert detect_language("123 --") is None
    assert detect_language("aا") is None      # تعادل 50/50


# ── اختيار تخطيط لوحة المفاتيح ─────────────────────────────────
def test_language_of_layout_variants():
    assert layout_switch.language_of_layout(AR_101) == "ar"
    assert layout_switch.language_of_layout(AR_EGYPT) == "ar"
    assert layout_switch.language_of_layout(EN_US) == "en"
    assert layout_switch.language_of_layout(EN_UK) == "en"
    assert layout_switch.language_of_layout(FRENCH) is None
    assert layout_switch.language_of_layout(None) is None


def test_choose_layout_prefers_matching_current_layout():
    assert layout_switch.choose_layout([AR_101, EN_US], "en", AR_101) == EN_US
    assert layout_switch.choose_layout([AR_101, EN_US], "en", EN_US) == EN_US
    # لا نبدّل بين لهجتين عربيتين بلا داعٍ
    assert layout_switch.choose_layout([AR_101, AR_EGYPT], "ar", AR_EGYPT) == AR_EGYPT
    assert layout_switch.choose_layout([AR_101, AR_EGYPT], "ar", EN_US) == AR_101


def test_choose_layout_returns_none_when_language_missing():
    assert layout_switch.choose_layout([AR_101], "en") is None
    assert layout_switch.choose_layout([], "ar") is None
    assert layout_switch.choose_layout([AR_101, EN_US], "fr") is None


def test_switch_to_language_rejects_unknown_language():
    assert layout_switch.switch_to_language("fr") is False


# ── تفعيل الخيار لكل اختصار ───────────────────────────────────
def test_layout_switch_off_by_default():
    assert main.layout_switch_active("selection") is False
    assert main.layout_switch_active("word") is False


def test_layout_switch_requires_master():
    main.set_features(features(switch_layout=True))
    assert main.layout_switch_active("selection") is True
    assert main.layout_switch_active("word") is True

    main.set_features(features(switch_layout=False))
    assert main.layout_switch_active("selection") is False
    assert main.layout_switch_active("word") is False


# ── الربط الفعلي بالتحويل ─────────────────────────────────────
def test_selection_switches_layout_when_enabled(monkeypatch):
    calls = record_switches(monkeypatch)
    io = FakeIO()
    io.selected = "مرحبا"
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)
    main.set_features(features(switch_layout=True, layout_double_shift=True))

    double_tap("shift")
    main.guard.release_now()

    assert calls == ["lvpfh"]          # لغة النتيجة (إنجليزي)
    assert io.paste_calls == 1


def test_selection_skips_layout_when_master_off(monkeypatch):
    calls = record_switches(monkeypatch)
    io = FakeIO()
    io.selected = "مرحبا"
    monkeypatch.setattr(main, "IO", io)

    double_tap("shift")
    main.guard.release_now()

    assert io.paste_calls == 1
    assert calls == []


def test_word_switches_layout_and_follows_toggle(monkeypatch):
    calls = record_switches(monkeypatch)
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.set_features(features(switch_layout=True))

    type_text("مرحبا")
    double_tap("shift")
    main.guard.release_now()          # إغلاق فوري بدل مهلة الحارس الواقعية
    assert calls == ["lvpfh"]

    # التوجيل للخلف يعيد النص العربي -> تعود اللغة إلى العربية
    double_tap("shift")
    main.guard.release_now()
    assert calls == ["lvpfh", "مرحبا"]


def test_layout_switch_failure_does_not_break_conversion(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.set_features(features(switch_layout=True))

    def boom(_text):
        raise RuntimeError("layout api unavailable")

    monkeypatch.setattr(main.layout_switch, "switch_to_text_language", boom)

    type_text("مرحبا")
    double_tap("shift")
    time.sleep(0.25)                  # مهلة إطفاء الحارس الواقعية (150ms)

    assert io.written == ["lvpfh"]
    assert main.buffer.current_word == "lvpfh"
    assert main.guard.active is False   # الخطأ في التبديل لم يعطّل التحويل
