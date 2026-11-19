# tests/test_pipeline.py — اختبارات خط الأنابيب كاملاً (بدون كيبورد/كليبورد حقيقي)
# الاستراتيجية: كل منطق القرارات موجّه بأسماء نصوص عبر main.handle_key_event(...)،
# وكل I/O حي (كيبورد + كليبورد) مستبدل بـ FakeIO لضمان عمل النقاس في أي بيئة.

import main
from converter import convert_text


class FakeIO:
    """بديل كامل عن RealIO: يجمع كل ما سُجّل بدل إرساله فعلياً."""

    def __init__(self):
        self.clipboard = ""          # محتوى الكليبورد الحقيقي
        self.selected = ""           # النص المحدد في التطبيق (يُنقله Ctrl+C للكليبورد)
        self.copy_calls = 0
        self.paste_calls = 0
        self.backspaces_total = 0
        self.written = []
        self.separators = []
        self.clipboard_sets = []     # سجل كامل بكل ما كُتب في الكليبورد بالترتيب

    def send_copy(self):
        self.copy_calls += 1
        self.clipboard = self.selected   # كأن الضغطة الحقيقية نسخت المحدد

    def send_paste(self):
        self.paste_calls += 1

    def send_undo(self):
        self.undo_calls = getattr(self, "undo_calls", 0) + 1

    def clipboard_get(self):
        return self.clipboard

    def clipboard_set(self, text):
        self.clipboard_sets.append(text)
        self.clipboard = text

    def backspaces(self, n):
        self.backspaces_total += n

    def write(self, text):
        self.written.append(text)

    def press_separator(self, name):
        self.separators.append(name)

    def clipboard_seq(self):
        return getattr(self, "seq_value", 0)

    def snapshot(self):
        return {"text": self.clipboard}

    def restore(self, snap):
        self.clipboard = snap.get("text", "")
        return True


def double_tap(name):
    """يحاكي ضغطتين سريعتين (DOWN -> UP -> DOWN -> UP) على مفتاح مزدوج.

    التنفيذ يُطلَق عند رفع الضغطة الثانية (UP الرابعة)، لذلك نكمل التسلسل كاملاً.
    """
    main.handle_key_event(name, True)
    main.handle_key_event(name, False)
    main.handle_key_event(name, True)
    main.handle_key_event(name, False)
    main.flush_actions(2.0)   # الحقن صار في خيط عمل: ننتظره ليصبح الاختبار حتمياً


class SilentCopyIO(FakeIO):
    """يحاكي النسخ الصامت (Ctrl+Shift+C فاشل): يضغط Ctrl+C لكن لا يُحدّث الكليبورد."""

    def send_copy(self):
        self.copy_calls += 1   # النسخ "صامت": الكليبورد يبقى على الإشارة


# ── 1) تحويل نص محدد ذهاباً وإياباً (وحالات التعادل / الخارج عن الجدول) ──
def test_selection_round_trip():
    arabic = "مرحبا"
    english = "lvpfh"
    assert convert_text(arabic) == english          # عربي -> إنجليزي
    assert convert_text(english) == arabic          # إنجليزي -> عربي (round trip)
    # تعادل 50/50 -> لا تحويل إطلاقاً
    assert convert_text("aا") == "aا"
    # حروف خارج الجدول (أرقام...) تمر كما هي دون تغيير
    assert convert_text("مرحبا123") == "lvpfh123"


def test_selection_uses_clipboard_and_restores_original(monkeypatch):
    io = FakeIO()
    io.clipboard = "قيمة أصلية"   # ما في الكليبورد قبل العملية
    io.selected = "مرحبا"         # ما حدّده المستخدم في التطبيق
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)  # لا نوم في الاختبار

    # محاكاة: المستخدم حدد نصاً عربياً وضغط Shift مرتين
    double_tap("shift")
    main.guard.release_now()

    assert io.copy_calls == 1
    assert io.paste_calls == 1
    assert io.written == []                 # الميزة 1 لا تكتب كيبورد أبداً
    assert io.clipboard == "قيمة أصلية"     # الكليبورد عاد لنصه الأصلي


# ── 1b) النسخ الصامت (فشل Ctrl+C) -> إجهاض بدون تحويل نص قديم ──
def test_selection_aborts_on_silent_copy(monkeypatch):
    io = SilentCopyIO()
    io.clipboard = "تكست قديم في الكليبورد"
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)

    double_tap("shift")
    main.guard.release_now()

    assert io.copy_calls >= 1
    assert io.paste_calls == 0              # لا لصق بلا تحديد حقيقي
    assert io.clipboard == "تكست قديم في الكليبورد"   # أُعيد كما كان، لا تلوث


# ── 1c) الخلل المبلَّغ: تحويل جديد لا يعيد تحويل النص القديم أبداً ──
def test_selection_never_reconverts_stale_clipboard(monkeypatch):
    io = FakeIO()
    io.clipboard = "قيمة أصلية"            # ما في الكليبورد قبل كل عملية
    io.selected = "The first sentence"     # أول تحديد للمستخدم
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)

    double_tap("shift")                    # العملية 1: تحويل ناجح
    main.guard.release_now()
    old_converted = convert_text("The first sentence")
    # الأصلي استُرجِع في نهاية العملية 1 (عبر اللقطة الكاملة الآن)
    assert io.clipboard == "قيمة أصلية"
    assert io.clipboard_sets[-1] == convert_text("The first sentence")

    # المستخدم يحدد الآن نصاً مختلفاً كلياً؛ الكليبورد القديم ما زال حاملاً
    # النتيجة المسترجَعة من الأول (لكن يجب تجاهلها بالكلية)
    io.selected = "totally new selection"
    io.clipboard = old_converted

    double_tap("shift")                    # العملية 2
    main.guard.release_now()

    new_set = io.clipboard_sets[-1]        # العملية 2 كتبت الكليبورد مرة واحدة: النص للصق
    pasted = new_set
    assert pasted == convert_text("totally new selection")
    # الأصل (ما قبل العملية 2) استُرجِع عبر اللقطة الكاملة
    assert io.clipboard == old_converted
    assert pasted != convert_text("The first sentence")   # لم يُعَد القديم إطلاقاً


# ── 2) تحويل كلمة لسه ما اتكتبتش بعدها مسافة (الحالة: جارية) ──
def test_unconfirmed_word_conversion(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.buffer.reset()

    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    assert main.buffer.current_word == "مرحبا"

    double_tap("shift")
    main.guard.release_now()

    assert io.backspaces_total == 5          # حذف الكلمة بحروفها بالظبط
    assert io.written == ["lvpfh"]
    assert io.separators == []               # لا فاصل لأن الكلمة جارية
    assert main.buffer.current_word == "lvpfh"  # الكلمة الجديدة مسجلة للtoggle
    assert main.buffer.last_word is None


# ── 3) تحويل كلمة اتكتب بعدها مسافة/Enter/Tab والفاصل يرجع كما كان ──
def test_confirmed_word_conversion(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.buffer.reset()

    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    main.handle_key_event("space", True)
    main.handle_key_event("space", False)      # الضغطات تُرفع وقت ما شاءت
    assert main.buffer.last_word == "مرحبا"
    assert main.buffer.current_word == ""

    double_tap("shift")
    main.guard.release_now()

    assert io.backspaces_total == 6            # الكلمة + المسافة معاً
    assert io.written == ["lvpfh"]
    assert io.separators == ["space"]          # الفاصل أُعيد كما كان (مسافة)
    assert main.buffer.last_word == "lvpfh"


def test_confirmed_word_enter_and_tab(monkeypatch):
    for sep in ("enter", "tab"):
        io = FakeIO()
        monkeypatch.setattr(main, "IO", io)
        main.buffer.reset()

        for ch in "كلمة":
            main.handle_key_event(ch, True)
        main.handle_key_event(sep, True)
        main.handle_key_event(sep, False)

        double_tap("shift")
        main.guard.release_now()

        assert io.backspaces_total == 4 + 1    # الكلمة + الفاصل
        assert io.written == [";glm"]          # كلمة -> ; g l m
        assert io.separators == [sep]          # الفاصل نفسه عاد (enter/tab)
        assert main.buffer.last_separator == sep


# ── 4) "حالة غير نظيفة" (حاجة اتكتبت بعد الفاصل) بترفض التحويل ──
def test_dirty_state_rejected(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.buffer.reset()

    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    main.handle_key_event("space", True)      # كلمة مؤكَّدة بمسافة
    main.handle_key_event("space", True)      # فاصل مكرر = حالة غير نظيفة

    double_tap("shift")
    main.guard.release_now()

    assert io.backspaces_total == 0
    assert io.written == []

    # حالة أخرى: حرف/رمز اتكتب بعد الكلمة -> يُحوَّل الجديد فقط لا القديم
    io2 = FakeIO()
    monkeypatch.setattr(main, "IO", io2)
    main.buffer.reset()
    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    main.handle_key_event("space", True)
    main.handle_key_event(".", True)          # رمز بعد الفاصل
    double_tap("shift")
    main.guard.release_now()
    assert io2.backspaces_total == 0          # "." بلا تحويل -> لا تلمس شيئاً


# ── 5) الحارس يمنع أي إعادة تفعيل ذاتية من الأحداث الاصطناعية ──
def test_guard_no_self_retrigger(monkeypatch):
    io = FakeIO()
    io.clipboard = ""              # لا تحديد في النص: النسخ لا يغيّر الكليبورد
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)
    main.buffer.reset()

    main.guard.begin()                        # يبدأ الحقن الاصطناعي
    try:
        # أصداء تبدو كضغطات مزدوجة حقيقية + أحرف مكتوبة أثناء الحقن
        double_tap("shift")
        double_tap("shift")
        for ch in "echo":
            main.handle_key_event(ch, True)
    finally:
        main.guard.release_now()

    # لا شيء من "الأصداء" يجب أن يُفسَّر كتَفعيلة ميزة
    assert io.copy_calls == 0
    assert io.paste_calls == 0
    assert io.backspaces_total == 0
    assert io.written == []
    assert main.buffer.current_word == ""
    assert main.buffer.last_word is None

    # وبعد إطفاء الحارس: ضغطة مزدوجة حقيقية تعمل عادة بشكل طبيعي
    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    double_tap("shift")
    main.guard.release_now()
    assert io.written == ["lvpfh"]


# ── 6) تغيير الـ focus يصفّر الكلمة المسجلة ──
def test_focus_change_clears(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.buffer.reset()

    for ch in "كلمة":
        main.handle_key_event(ch, True)
    main.handle_key_event("space", True)
    assert main.buffer.last_word is not None

    main.handle_focus_change()                # تبديل النافذة النشطة
    assert main.buffer.last_word is None
    assert main.buffer.current_word == ""

    double_tap("shift")
    main.guard.release_now()
    assert io.written == []                   # لا كلمة -> لا عملية


# ── 7) (إضافي) Caps×2 تاني على نفس الكلمة يرجعها لنصها الأصلي ──
def test_toggle_restores_original(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.buffer.reset()

    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    main.handle_key_event("space", True)
    main.handle_key_event("space", False)

    double_tap("shift")                   # 1) تحويل
    main.guard.release_now()
    assert io.written == ["lvpfh"]
    assert main.buffer.last_word == "lvpfh"

    double_tap("shift")                   # 2) رجوع للأصل بالظبط
    main.guard.release_now()
    assert io.written == ["lvpfh", "مرحبا"]
    assert main.buffer.last_word == "مرحبا"

    double_tap("shift")                   # 3) توجّل للخلف مرة أخرى
    main.guard.release_now()
    assert io.written[-1] == "lvpfh"
    assert main.buffer.last_word == "lvpfh"


# ── 8) استثناء الكليبورد لا يُسقط المستمع ولا يترك الكليبورد ملوَّثاً ──
def test_clipboard_error_does_not_kill_listener(monkeypatch):
    class BrokenIO(FakeIO):
        """يحاكي برنامجاً يحتجز الكليبورد: كل قراءة ترمي استثناءً."""

        def clipboard_get(self):
            raise RuntimeError("OpenClipboard failed")

    bad = BrokenIO()
    bad.selected = "مرحبا"
    monkeypatch.setattr(main, "IO", bad)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)

    double_tap("shift")
    main.guard.release_now()
    assert main.guard.active is False          # الحارس أُغلق رغم الخطأ
    assert bad.paste_calls == 0                # لا لصق بلا تحديد معروف

    good = FakeIO()
    good.selected = "مرحبا"
    monkeypatch.setattr(main, "IO", good)
    double_tap("shift")                        # الميزة تعمل عادةً بعد زوال الخطأ
    main.guard.release_now()
    assert good.paste_calls == 1


# ── 9) النسخ البطيء: المحاولة الثانية تنجح ولا تُقرأ قيمة قديمة ──
def test_slow_copy_retries_and_succeeds(monkeypatch):
    class SlowIO(FakeIO):
        """النسخ الأول صامت، والثاني ينجح."""

        def send_copy(self):
            self.copy_calls += 1
            if self.copy_calls >= 2:
                self.clipboard = self.selected

    main.buffer.reset()               # لا كلمة جاهزة: المسار مسار تحديد فقط
    io = SlowIO()
    io.clipboard = "قديم"
    io.selected = "مرحبا"
    monkeypatch.setattr(main, "IO", io)
    monkeypatch.setattr(main, "sleep_fn", lambda _s: None)

    double_tap("shift")
    main.guard.release_now()

    assert io.copy_calls == 2
    assert io.paste_calls == 1
    assert io.clipboard == "قديم"              # أُعيد الأصل بعد العملية


# ── 10) فشل الحقن داخل خيط العمل لا يوقف الخيط ولا يُبقي الحارس مفعّلاً ──
def test_worker_survives_injection_failure(monkeypatch):
    class FailingWrite(FakeIO):
        def write(self, text):
            raise RuntimeError("cannot type unicode")

    bad = FailingWrite()
    monkeypatch.setattr(main, "IO", bad)
    main.buffer.reset()
    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    double_tap("shift")
    main.guard.release_now()
    assert main.guard.active is False

    good = FakeIO()
    monkeypatch.setattr(main, "IO", good)
    main.buffer.reset()
    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    double_tap("shift")
    main.guard.release_now()
    assert good.written == ["lvpfh"]


# ── 11) Escape/Insert/F-keys/Win تُبطل الكلمة المسجلة (لا تحويل في سياق خاطئ) ──
def test_edit_keys_invalidate_word_state(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    for invalidation in ("esc", "insert", "f2", "menu", "print screen", "cmd"):
        main.buffer.reset()
        for ch in "مرحبا":
            main.handle_key_event(ch, True)
        main.handle_key_event("space", True)
        assert main.buffer.last_word == "مرحبا"
        main.handle_key_event(invalidation, True)
        double_tap("shift")
        main.guard.release_now()
        assert io.written == [], invalidation


# ── 12) Shift×2: التحديد له الأولوية على آخر كلمة ──
def test_selection_takes_precedence_over_pending_word(monkeypatch):
    main.buffer.reset()
    io = FakeIO()
    io.selected = "مرحبا"
    monkeypatch.setattr(main, "IO", io)

    for ch in "hello":
        main.handle_key_event(ch, True)
    double_tap("shift")
    main.guard.release_now()

    assert io.paste_calls == 1
    assert io.clipboard_sets[-1] == "lvpfh"   # تحويل التحديد
    assert io.written == []                   # الكلمة لم تُمَس
    assert io.backspaces_total == 0
    assert main.buffer.current_word == "hello"


def test_clipboard_error_never_falls_back_to_word(monkeypatch):
    """خطأ وصول للكليبورد ليس "لا يوجد تحديد": لا نحذف كلمة بالخطأ."""
    class BrokenIO(FakeIO):
        def clipboard_get(self):
            raise RuntimeError("OpenClipboard failed")

    main.buffer.reset()
    io = BrokenIO()
    monkeypatch.setattr(main, "IO", io)
    for ch in "lvpfh":
        main.handle_key_event(ch, True)

    double_tap("shift")
    main.guard.release_now()

    assert io.written == []
    assert io.backspaces_total == 0


def test_word_fallback_when_no_selection(monkeypatch):
    main.buffer.reset()
    io = FakeIO()                 # selected يبقى "" -> النسخ لا يغيّر الكليبورد
    monkeypatch.setattr(main, "IO", io)
    for ch in "lvpfh":
        main.handle_key_event(ch, True)

    double_tap("shift")
    main.guard.release_now()

    assert io.copy_calls >= 1     # فحص سريع للتحديد أولاً
    assert io.written == ["مرحبا"]
    assert io.backspaces_total == 5


class EmptyCopyIO(FakeIO):
    """مثل WhatsApp: Ctrl+C بلا تحديد يفرّغ الكليبورد ويغيّر رقمه التسلسلي."""

    def __init__(self):
        super().__init__()
        self.clipboard = "نص قديم"
        self.seq_value = 10

    def send_copy(self):
        self.copy_calls += 1
        self.seq_value += 4
        self.clipboard = ""


def test_word_fallback_when_copy_is_empty_but_seq_changes(monkeypatch):
    main.buffer.reset()
    io = EmptyCopyIO()
    monkeypatch.setattr(main, "IO", io)
    for ch in "lvpfh":
        main.handle_key_event(ch, True)

    double_tap("shift")
    main.guard.release_now()

    assert io.written == ["مرحبا"]        # نسخ فارغ = لا تحديد -> تحويل الكلمة
    assert io.backspaces_total == 5
    assert io.clipboard == "نص قديم"      # الكليبورد رجع كما كان


# ── 13) رموز التحكم لا تلوّث آخر كلمة (Ctrl+C/V وغيرها) ──
def test_control_chars_never_join_word(monkeypatch):
    io = FakeIO()
    monkeypatch.setattr(main, "IO", io)
    main.buffer.reset()
    for ch in "مرحبا":
        main.handle_key_event(ch, True)
    main.handle_key_event("\x16", True)   # صدى Ctrl+V
    main.handle_key_event("\x03", True)   # صدى Ctrl+C
    assert main.buffer.current_word == "مرحبا"
    assert main._is_text("\x16") is False
    assert main._is_text("\x03") is False
    assert main._is_text("لا") is True


# ── 14) "لا" الناتجة عن ضغطة واحدة تُحسب وحدة واحدة في المخزن ──
def test_multi_char_key_is_one_buffer_unit():
    main.buffer.reset()
    main.handle_key_event("لا", True)
    assert main.buffer.current_word == "لا"      # ضغطة واحدة -> حرفان معروضان
    main.handle_key_event("x", True)
    assert main.buffer.current_word == "لاx"
    action = main.plan_word_action(main.buffer.last_word_for_conversion())
    assert action is not None
    assert action["delete_count"] == 3            # الحذف بطول النص المعروض نفسه


# ── 15) Shift ثم حرف كبير لا يجعل ضغطة Shift منفردة تالية double-tap ──
def test_shift_tap_after_capital_letter_does_not_fire():
    detector = main.TapDetector()
    detector.key_down(now=0.0)
    detector.key_up(now=0.05)               # ضغطة Shift أولى
    detector.key_down(now=0.10)             # بداية ثانية
    detector.other_key_down()               # قُطعت بحرف كبير
    assert detector.key_up(now=0.15) is False
    detector.key_down(now=0.20)
    assert detector.key_up(now=0.25) is False   # ضغطة منفردة: لا تفعيل
    detector.key_down(now=0.30)
    assert detector.key_up(now=0.35) is True    # double-tap حقيقي