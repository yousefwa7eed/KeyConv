# tests/test_slots_hook.py — الحافظة متعددة الفتحات (Ctrl+C+رقم / Ctrl+رقم+V).
#
# يغطي: آلة القرار (SlotChord) بلا ويندوز: التسلح بعد C وبعد Ctrl+رقم،
# احتجاز الرقم وV عند اختيار فتحة، بقاء Ctrl+V لصقاً طبيعياً، انتهاء المهل،
# تجاهل الأحداث المحقونة والتركيبات مع Alt/Win، ومخزن الفتحات المؤقت وملف العرض.

import json

import pytest

import slots_hook
from slots_hook import SlotChord

VK_C = slots_hook.VK_C
VK_V = slots_hook.VK_V
VK_CONTROL = slots_hook.VK_CONTROL
VK_0 = 0x30
VK_1 = 0x31
VK_2 = 0x32
VK_3 = 0x33
VK_5 = 0x35
VK_9 = 0x39
VK_NUMPAD5 = 0x65
VK_X = 0x58
VK_SHIFT = 0x10


class FakeTimer:
    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class Env:
    """بيئة قابلة للتغيير أثناء الاختبار (مفاتيح مضغوطة/زمن/تفعيل)."""

    def __init__(self):
        self.enabled = True
        self.ctrl = False
        self.mod = False
        self.t = 0.0
        self.seq = 100


@pytest.fixture()
def chord_env():
    env = Env()
    events = []
    sent = []
    timers = []

    def arm_timer(delay, fn):
        timer = FakeTimer(delay, fn)
        timers.append(timer)
        return timer

    chord = SlotChord(
        on_capture=lambda slot, before_seq: events.append(("capture", slot, before_seq)),
        on_paste=lambda slot: events.append(("paste", slot)),
        enabled=lambda: env.enabled,
        ctrl_down=lambda: env.ctrl,
        modifier_down=lambda: env.mod,
        seq=lambda: env.seq,
        now=lambda: env.t,
        arm_timer=arm_timer,
    )
    return chord, env, events, sent, timers


def down(chord, vk):
    return chord.handle(vk, True)


def up(chord, vk):
    return chord.handle(vk, False)


# ── نسخ إلى فتحة: Ctrl+C ثم رقم ─────────────────────────────
def test_copy_flow_captures_digit_slot(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    assert down(chord, VK_C) == "pass"            # النسخ العادي يجري
    assert down(chord, VK_1) == "swallow"          # الرقم لا يصل للتطبيق
    assert up(chord, VK_1) == "pass"
    assert events == [("capture", 1, 100)]


def test_capture_all_slots_in_sequence(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)
    assert down(chord, VK_1) == "swallow"
    down(chord, VK_C)                              # نضغط C من جديد لكل فتحة
    assert down(chord, VK_3) == "swallow"
    assert [e[1] for e in events] == [1, 3]


def test_capture_window_expiry_then_digit_arms_slot(chord_env):
    """فات وقت النسخ: الرقم لم يعد يحفظ، لكنه يصلح لتسلح لصق فتحة."""
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)
    env.t = SlotChord.CAPTURE_WINDOW_S + 0.1
    assert down(chord, VK_1) == "swallow"
    assert events == []                            # لا حفظ
    assert chord.armed is not None                 # بل انتظار V
    assert down(chord, VK_V) == "swallow"
    assert events == [("paste", 1)]


def test_capture_cancelled_when_ctrl_released(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)
    up(chord, VK_CONTROL)
    env.ctrl = False
    assert down(chord, VK_1) == "pass"             # يُكتب '1' طبيعياً
    assert events == []


def test_capture_records_seq_before_copy(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    env.seq = 777
    down(chord, VK_C)
    down(chord, VK_9)
    assert events == [("capture", 9, 777)]


def test_capture_seq_measured_at_c_press_not_digit(chord_env):
    """التطبيق ينسخ عند Ctrl+C قبل وصول الرقم: القياس وقت C كشف النسخ فعلاً."""
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    env.seq = 100
    down(chord, VK_C)          # الرقم يُقاس هنا (قبل النسخ)
    env.seq = 205              # التطبيق نسخ -> رقم الحافظة تقدّم
    down(chord, VK_1)
    assert events == [("capture", 1, 100)]


def test_repeated_c_keeps_first_measurement(chord_env):
    """ضغطة C ثانية أثناء التسلح لا تُعيد القياس إلى ما بعد النسخ."""
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    env.seq = 100
    down(chord, VK_C)
    env.seq = 200
    down(chord, VK_C)          # لو أُعيد القياس لصار 200
    down(chord, VK_1)
    assert events == [("capture", 1, 100)]


def test_zero_is_not_a_slot_chord(chord_env):
    """الصفر ليس فتحة: يُمرَّر طبيعياً بعد C بلا أي حفظ."""
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)
    assert down(chord, VK_0) == "pass"
    assert events == []


# ── لصق فتحة: Ctrl+رقم يُحجز، ثم V يلصق نص الفتحة ──────────
def test_ctrl_digit_then_v_pastes_slot(chord_env):
    chord, env, events, sent, timers = chord_env
    env.ctrl = True
    assert down(chord, VK_2) == "swallow"          # Ctrl+2 محجوز للفتحة
    assert up(chord, VK_2) == "pass"
    assert events == []                            # لم يحدث لصق بعد
    assert chord.armed is not None
    assert down(chord, VK_V) == "swallow"          # V بدل اللصق الطبيعي
    assert up(chord, VK_V) == "swallow"            # ورفعها محجوز أيضاً
    assert events == [("paste", 2)]
    assert chord.armed is None
    assert timers[0].cancelled is True             # مؤقّت الانتظار أُلغي
    assert sent == []                              # لا إعادة حقن إطلاقاً


def test_ctrl_v_alone_is_purely_natural(chord_env):
    chord, env, events, sent, timers = chord_env
    env.ctrl = True
    assert down(chord, VK_V) == "pass"             # اللصق الطبيعي يجري فوراً
    assert up(chord, VK_V) == "pass"
    assert chord.armed is None
    assert events == [] and sent == []
    assert timers == []                            # لا مؤقّتات إطلاقاً


def test_ctrl_digit_without_v_expires_silently(chord_env):
    """Ctrl+رقم ثم لا شيء: الرقم حُجز لحظياً ثم عاد كل شيء طبيعياً."""
    chord, env, events, sent, timers = chord_env
    env.ctrl = True
    assert down(chord, VK_1) == "swallow"
    timers[0].fn()                                 # انتهت مهلة الانتظار
    assert chord.armed is None
    assert events == [] and sent == []
    env.ctrl = False
    assert down(chord, VK_1) == "pass"             # بعدها رقم عادي


def test_slot_arm_ends_silently_on_ctrl_release(chord_env):
    chord, env, events, sent, timers = chord_env
    env.ctrl = True
    down(chord, VK_2)
    up(chord, VK_CONTROL)                          # حُرر Ctrl قبل V
    assert chord.armed is None
    assert timers[0].cancelled is True
    assert events == [] and sent == []
    env.ctrl = False
    assert down(chord, VK_V) == "pass"             # v عادي


def test_slot_arm_ends_silently_on_other_key(chord_env):
    chord, env, events, sent, timers = chord_env
    env.ctrl = True
    down(chord, VK_2)
    assert down(chord, VK_X) == "pass"             # مفتاح آخر بدل V
    assert chord.armed is None
    assert timers[0].cancelled is True
    assert events == [] and sent == []


def test_slot_arm_window_expiry_passes_v(chord_env):
    chord, env, events, sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_2)
    env.t = SlotChord.SLOT_ARM_S + 0.05
    assert down(chord, VK_V) == "pass"             # فاتت المهلة: لصق طبيعي
    assert events == [] and sent == []


def test_v_repeat_after_slot_paste_is_suppressed(chord_env):
    """الاحتفاظ بـ V بعد لصق الفتحة لا يلصق النص مرات عديدة."""
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_3)
    assert down(chord, VK_V) == "swallow"          # اللصقة الأولى
    assert down(chord, VK_V) == "swallow"          # تكرار تلقائي
    assert down(chord, VK_V) == "swallow"
    assert up(chord, VK_V) == "swallow"            # الرفع يُبتلع
    assert events == [("paste", 3)]                # لصقة واحدة فقط

    env.ctrl = False
    assert down(chord, VK_V) == "pass"             # بعد الرفع: v عادي
    up(chord, VK_V)


def test_capture_digit_replaces_slot_arm(chord_env):
    """Ctrl+C ثم رقم = حفظ، وليس تسلح لصق (لا V اختياري بعده)."""
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)
    assert down(chord, VK_2) == "swallow"
    assert events == [("capture", 2, 100)]
    assert chord.armed is None
    assert down(chord, VK_V) == "pass"             # V هنا لصق طبيعي


def test_numpad_digits_work(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)
    assert down(chord, VK_NUMPAD5) == "swallow"
    assert events == [("capture", 5, 100)]

    assert down(chord, VK_NUMPAD5) == "swallow"    # Ctrl+Numpad5: تسلح فتحة
    assert down(chord, VK_V) == "swallow"
    assert events == [("capture", 5, 100), ("paste", 5)]


def test_zero_after_ctrl_is_plain(chord_env):
    """الصفر ليس فتحة: Ctrl+0 يمر طبيعياً، ولا يلصق شيئاً."""
    chord, env, events, sent, _timers = chord_env
    env.ctrl = True
    assert down(chord, VK_0) == "pass"
    assert chord.armed is None
    assert down(chord, VK_V) == "pass"
    assert events == [] and sent == []


# ── حالات لا يجوز أن تتأثر ──────────────────────────────────
def test_plain_typing_untouched(chord_env):
    chord, env, events, sent, _timers = chord_env
    assert down(chord, VK_V) == "pass"             # حرف v عادي بلا Ctrl
    assert up(chord, VK_V) == "pass"
    assert down(chord, VK_1) == "pass"             # رقم عادي
    assert down(chord, VK_C) == "pass"
    assert events == [] and sent == []


def test_alt_or_win_combos_pass(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    env.mod = True                                 # Alt (أو Win) مضغوط
    assert down(chord, VK_V) == "pass"
    assert down(chord, VK_C) == "pass"
    assert chord.armed is None
    assert events == []


def test_injected_events_pass_and_do_not_touch_state(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = True
    down(chord, VK_C)                              # مسلّح للنسخ
    assert chord.handle(VK_1, True, injected=True) == "pass"
    assert events == []                            # لم يُعَدّ اختطافها
    assert chord.armed is not None                 # الحالة لم تُمس


def test_disabled_feature_passes_everything(chord_env):
    chord, env, events, sent, _timers = chord_env
    env.enabled = False
    env.ctrl = True
    assert down(chord, VK_V) == "pass"
    assert up(chord, VK_V) == "pass"
    assert down(chord, VK_C) == "pass"
    assert down(chord, VK_1) == "pass"
    assert events == [] and sent == []


def test_ctrl_up_without_arm_is_untouched(chord_env):
    chord, env, events, sent, _timers = chord_env
    env.ctrl = True
    assert up(chord, VK_CONTROL) == "pass"
    assert sent == []


def test_slot_chord_requires_ctrl(chord_env):
    chord, env, events, _sent, _timers = chord_env
    env.ctrl = False
    assert down(chord, VK_V) == "pass"
    assert down(chord, VK_2) == "pass"             # رقم عادي بلا Ctrl
    assert chord.armed is None
    assert events == []


# ── مخزن الفتحات المؤقت وملف العرض ──────────────────────────
def test_slots_store_roundtrip_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "KEYCONV_SLOTS", str(tmp_path / "slots.json")
    )
    slots_hook.reset_runtime()
    assert slots_hook.get_slot(1) == ""

    slots_hook.save_slot(1, "نص أول")
    slots_hook.save_slot(2, "second")
    assert slots_hook.get_slot(1) == "نص أول"
    data = slots_hook.read_slots_file()
    assert data == {1: "نص أول", 2: "second"}

    slots_hook.reset_runtime()
    assert slots_hook.get_slot(1) == ""
    assert slots_hook.read_slots_file() == {}


def test_slots_file_read_tolerates_missing_and_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYCONV_SLOTS", str(tmp_path / "nope.json"))
    assert slots_hook.read_slots_file() == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{تالف", encoding="utf-8")
    monkeypatch.setenv("KEYCONV_SLOTS", str(broken))
    assert slots_hook.read_slots_file() == {}


def test_slots_snapshot_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYCONV_SLOTS", str(tmp_path / "slots.json"))
    slots_hook.reset_runtime()
    slots_hook.save_slot(1, "أ")
    snap = slots_hook.slots_snapshot()
    snap[1] = "معَدَّل خارجياً"
    assert slots_hook.get_slot(1) == "أ"


# ── الربط مع الخدمة (main) ──────────────────────────────────
def test_main_has_slot_handlers_and_feature_key():
    import main
    assert "slots_enabled" in main.FEATURE_NAMES
    assert callable(main._run_slot_capture)
    assert callable(main._run_slot_paste)
    assert settings_defaults_has_slots()


def settings_defaults_has_slots():
    import settings_store
    return settings_store.DEFAULTS.get("slots_enabled") is False


def test_update_slots_hook_installs_and_uninstalls(monkeypatch):
    import main
    import settings_store
    calls = []

    def fake_start(on_cap, on_paste):
        calls.append("start")
        return (None, lambda: calls.append("stop"))

    monkeypatch.setattr(slots_hook, "start", fake_start)
    main._slots_stop = None
    try:
        main.set_features({**settings_store.DEFAULTS, "slots_enabled": True})
        main._update_slots_hook()
        assert calls == ["start"]

        main.set_features({**settings_store.DEFAULTS, "slots_enabled": False})
        main._update_slots_hook()
        assert calls == ["start", "stop"]

        main._update_slots_hook()      # متوقفة أصلاً: لا تنصيب ثاني
        assert calls == ["start", "stop"]
    finally:
        main._slots_stop = None
        main.set_features(settings_store.DEFAULTS)


def test_capture_saves_latest_text_even_without_seq_change(tmp_path, monkeypatch):
    """لم يتغيّر الرقم التسلسلي: نحفظ نص الكليبورد الحالي (القدرة على اللصق)."""
    import main
    from test_pipeline import FakeIO

    io = FakeIO()
    io.clipboard_text = "نص محفوظ"
    io.clipboard = "نص محفوظ"
    io.seq_value = 55
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(slots_hook, "slots_file_path", lambda: str(tmp_path / "s.json"))

    slots_hook.reset_runtime()
    main._run_slot_capture(3, 55)   # نفس الرقم -> لا تغيير
    main.flush_actions(2.0)
    assert slots_hook.get_slot(3) == "نص محفوظ"

    io.seq_value = 99               # تغيّر الرقم -> النسخ نجح
    io.clipboard = "نسخ جديد"
    main._run_slot_capture(3, 55)
    main.flush_actions(2.0)
    assert slots_hook.get_slot(3) == "نسخ جديد"


def test_capture_never_overwrites_slot_with_empty_text(tmp_path, monkeypatch):
    import main
    from test_pipeline import FakeIO

    io = FakeIO()
    io.clipboard = ""
    io.seq_value = 55
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(slots_hook, "slots_file_path", lambda: str(tmp_path / "s.json"))

    slots_hook.reset_runtime()
    slots_hook.save_slot(3, "قيمة قديمة")
    main._run_slot_capture(3, 55)
    main.flush_actions(2.0)
    assert slots_hook.get_slot(3) == "قيمة قديمة"


def test_paste_task_skips_restore_when_clipboard_changed(tmp_path, monkeypatch):
    """نسخ جديد من المستخدم أثناء اللصق لا يُطمس بالاسترجاع."""
    import main
    from test_pipeline import FakeIO

    class CopyingIO(FakeIO):
        def send_paste(self):
            self.paste_calls += 1
            self.clipboard = "نسخ جديد من المستخدم"

    io = CopyingIO()
    io.clipboard = "الحالي"
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(slots_hook, "slots_file_path", lambda: str(tmp_path / "s.json"))
    slots_hook.reset_runtime()
    slots_hook.save_slot(1, "نص الفتحة")

    main._run_slot_paste(1)
    main.flush_actions(2.0)
    assert io.paste_calls == 1
    assert io.clipboard == "نسخ جديد من المستخدم"   # لم يُسترجَع النص القديم


def test_paste_task_restores_clipboard(tmp_path, monkeypatch):
    import main
    from test_pipeline import FakeIO

    io = FakeIO()
    io.clipboard = "الحالي"
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(slots_hook, "slots_file_path", lambda: str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)
    slots_hook.reset_runtime()
    slots_hook.save_slot(1, "نص الفتحة")

    main._run_slot_paste(1)
    main.flush_actions(2.0)
    assert io.paste_calls == 1                     # لصقة واحدة فقط
    assert io.clipboard_sets == ["نص الفتحة"]
    assert io.clipboard == "الحالي"                # الحافظة أُعيدت كما كانت

    main._run_slot_paste(9)                        # فتحة فارغة: لا شيء
    main.flush_actions(2.0)
    assert io.paste_calls == 1
