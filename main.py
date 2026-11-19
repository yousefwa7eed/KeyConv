# main.py — التطبيق: كل منطق الـ Hook والـ Buffer والـ Guard والحقن في مكان واحد
#
# الميزة الأساسية:
#   Shift × 2 سريع -> إن كان هناك نص محدد يُحوَّل كاملاً، وإلا تُحوَّل آخر كلمة
#                     مكتوبة (عربي <-> إنجليزي حسب الأغلبية) مع toggle للرجوع.
#
# تصميم مُوجّه للاختبار: كل منطق القرارات يعمل بأسماء نصوص عبر handle_key_event("...")
# والـ listeners الحقيقيون (pynput) مجرد جسر يحوّل أحداثهم إلى هذه الأسماء.
# هكذا تختبر tests/test_pipeline.py كل السلوك بدون كيبورد حقيقي إطلاقاً.

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse
from pynput.keyboard import Key, Controller

import clipboard_win as winclip
import converter
import key_translate
import layout_switch
import paths
import service_ctl
import settings_store
import slots_hook
from converter import convert_text

try:    # هياكل SendInput الجاهزة في pynput (حجم INPUT صحيح على x64)
    from pynput._util.win32 import INPUT, INPUT_union, KEYBDINPUT
except Exception:   # pragma: no cover - نظام غير ويندوز
    INPUT = INPUT_union = KEYBDINPUT = None

_SendInput = None
if INPUT is not None:
    try:
        _SendInput = ctypes.windll.user32.SendInput
        _SendInput.argtypes = (ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
        _SendInput.restype = ctypes.c_uint
    except Exception:   # pragma: no cover
        _SendInput = None


# ── ثوابت الإعداد ──────────────────────────────────────────────
DOUBLE_TAP_MS = 0.400          # أقصى فراغ بين ضغطتين حتى تُعتبر double-tap
COPY_READ_DELAY_S = 0.25       # مهلة انتظار وصول النص المحدد إلى الكليبورد
COPY_RETRY_DELAY_S = 0.30      # مهلة أطول لمحاولة ثانية عند نسخ بطيء
COPY_POLL_INTERVAL_S = 0.02    # فاصل الاستقصاء داخل المهلة
PASTE_DELAY_S = 0.15           # انتظار إتمام اللصق قبل استرجاع الكليبورد الأصلي
GUARD_SAFETY_MS = 0.150        # مهلة أمان بعد الحقن لامتصاص كل الأصداء (echoes)
FOCUS_POLL_INTERVAL_S = 0.20   # فاصل فحص إطار النافذة النشطة
ACTION_QUEUE_TIMEOUT_S = 5.0   # أقصى انتظار لانتهاء عمليات الحقن في الطابور
WORD_COPY_WAIT_S = 0.12        # فحص سريع للتحديد قبل الرجوع لآخر كلمة (Shift × 2)

# نتائج رقصة التحديد
SELECTION_CONVERTED = "converted"
SELECTION_NO_CHANGE = "no_change"
SELECTION_NONE = "no_selection"    # النسخ لم يغيّر الكليبورد: لا يوجد تحديد
SELECTION_ERROR = "error"          # تعذّرت قراءة الكليبورد: لا نرجع لكلمة أبداً

DEBUG_TRACE = os.environ.get("KEYCONV_DEBUG") == "1"

# ── السجل: ضروري لأن pythonw (التشغيل التلقائي) بلا كونسول فيختفي أي خطأ بصمت ──
LOG_PATH = os.environ.get(
    "KEYCONV_LOG_PATH",
    os.path.join(paths.data_dir(), "keyconv.log"),
)
log = logging.getLogger("keyconv")


def _setup_logging():
    if log.handlers:
        return log
    log.setLevel(os.environ.get("KEYCONV_LOG_LEVEL", "INFO").upper())
    try:
        directory = os.path.dirname(LOG_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        log.addHandler(handler)
    except Exception:
        log.addHandler(logging.NullHandler())
    log.propagate = False
    return log


_setup_logging()


def _make_streams_safe():
    """يجعل stdout/stderr يقبلان العربية.

    pythonw عند تشغيله من الواجهة يصل إخراجه إلى NUL بترميز cp1252، فتفشل
    طباعة أي نص عربي بخطأ UnicodeEncodeError يقتل الخدمة بصمت. نُجبر UTF-8
    مع errors=replace، ثم نحيط الطباعة بحماية إضافية.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _out(s=""):
    """طباعة آمنة: python.exe تعرض في الكونسول، وpythonw (بدون نافذة) تتجاهل بصمت."""
    if sys.stdout is None:
        return
    try:
        print(s, flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write(str(s).encode("utf-8", "replace") + b"\n")
            sys.stdout.flush()
        except Exception:
            pass


def trace(*parts):
    """سجل تتبّع مؤقت: يعمل فقط عند تشغيل KEYCONV_DEBUG=1 python main.py."""
    if DEBUG_TRACE:
        _out(" ".join(["[trace]", *[str(part) for part in parts]]))


# ═══════════════════════════════════════════════════════════════
# 1) حارس الحقن (InjectionGuard) — الحاجز الوحيد ضد التغذية الراجعة
#    يُفعَّل فور بدء أي حقن اصطناعي، ويُطفأ بعد آخر ضغطة + مهلة أمان.
#    أي event جديد يدخل الـ hook أثناء تفعيله يُرفض فوراً بلا معالجة،
#    فلا يُفسَّر أي صدى من ضغطاتنا بأنه ضغطة مستخدم حقيقية تُعيد الميزة.
# ═══════════════════════════════════════════════════════════════
class InjectionGuard:
    def __init__(self):
        self._active = False
        self._lock = threading.Lock()
        self._off_timer = None

    @property
    def active(self):
        with self._lock:
            return self._active

    def begin(self):
        """فعِّل الحارس: يُستدعى قبل أول ضغطة اصطناعية وبلا استثناء."""
        with self._lock:
            self._cancel_timer_locked()
            self._active = True

    def finish(self):
        """جدوِل إطفاء الحارس بعد GUARD_SAFETY_MS لامتصاص كل الأصداء."""
        with self._lock:
            self._cancel_timer_locked()
            self._off_timer = threading.Timer(GUARD_SAFETY_MS, self._turn_off)
            self._off_timer.daemon = True
            self._off_timer.start()

    def release_now(self):
        """إطفاء فوري (يُستخدم في الاختبارات غالباً)."""
        with self._lock:
            self._cancel_timer_locked()
            self._active = False

    def _turn_off(self):
        with self._lock:
            self._active = False
            self._off_timer = None

    def _cancel_timer_locked(self):
        if self._off_timer is not None:
            self._off_timer.cancel()
            self._off_timer = None


guard = InjectionGuard()


# ═══════════════════════════════════════════════════════════════
# 2) كاشف الضغط المزدوج (TapDetector) — نموذج double-click الحقيقي
#    DOWN أول -> UP -> DOWN ثانٍ خلال 400ms من أول UP.
#    أي مفتاح آخر في أثنائها يلغي الفصيل كلياً (شرط "بدون ضغط أي زرار تاني").
#    التنفيذ مؤجَّل عمداً حتى رفع الضغطة الثانية: هكذا نبدأ حقن Ctrl+C / Ctrl+V
#    والأصابع خالية تماماً من أي مفتاح تعديلي (يمنع Ctrl+Shift+C الخطأ).
# ═══════════════════════════════════════════════════════════════
class TapDetector:
    def __init__(self, max_interval=DOUBLE_TAP_MS):
        self.max_interval = max_interval
        self._pending = False       # ضغطة أولى مضغوطة بانتظار رفعها
        self._last_up = None        # زمن آخر رفع "نظيف" للمفتاح الهدف
        self._fire_pending = False  # ضغطة ثانية اكتمل ضغطها -> بانتظار رفعها للتنفيذ
        self._fire_down_time = None
        self._valid = False         # هل ضغطة المفتاح الحالية لم يقطعها مفتاح آخر؟

    def key_down(self, now=None):
        """تُستدعى عند ضغط المفتاح الهدف. تُنشئ فصيلاً بانتظار رفع الضغطة."""
        now = time.monotonic() if now is None else now
        if self._pending:
            # المفتاح ما زال مضغوطاً (رويتر) -> ليس ضغطة جديدة أبداً
            return False
        if self._last_up is not None and (now - self._last_up) <= self.max_interval:
            # double-tap اكتمل ضغطه: لا ننفّذ الآن، ننتظر رفع الضغطة الثانية
            self._fire_pending = True
            self._fire_down_time = now
        self._last_up = None
        self._pending = True
        self._valid = True
        return False   # التنفيذ الفعلي يأتي من key_up

    def key_up(self, now=None):
        """تُستدعى عند رفع المفتاح الهدف. ترجع True عند اكتمال double-tap حقيقي."""
        now = time.monotonic() if now is None else now
        fired = False
        valid = self._pending and self._valid
        if self._fire_pending and valid:
            if (now - self._fire_down_time) <= self.max_interval:
                # أُرفعت بسرعة (ليست إمساكاً) -> double-tap صادق
                fired = True
        self._fire_pending = False
        self._fire_down_time = None
        self._pending = False
        self._valid = False
        # رفع ضغطة قطعها مفتاح آخر (مثل Shift ثم حرف كبير) لا يبدأ سلسلة
        # double-tap جديدة، وإلا ضغطة واحدة تالية تُعتبر ثانية وتُفعّل التحويل.
        self._last_up = now if valid else None
        return fired

    def other_key_down(self):
        """أي مفتاح آخر خلال نافذة الانتظار يلغي الفصيل كلياً (بما فيه المؤجَّل)."""
        self._pending = False
        self._last_up = None
        self._fire_pending = False
        self._fire_down_time = None
        self._valid = False


shift_detector = TapDetector()


# ═══════════════════════════════════════════════════════════════
# 3) مخزن الكلمة الأخيرة (WordBuffer) — آلة حالة صافية
#    حالة (أ): current_word        = كلمة جارٍ كتابتها والمؤشر خلفها مباشرة
#    حالة (ب): last_word + فاصل    = كلمة مؤكَّدة اندفع بعدها مسافة/Enter/Tab
#    أي إدخال يفسد "المجاورة النظيفة" للكلمة الأخيرة -> إلغاء صامت (dirty)
# ═══════════════════════════════════════════════════════════════
SEPARATORS = {"space", "enter", "tab"}
EDIT_INVALIDATORS = {"left", "right", "up", "down", "home",
                     "end", "page up", "page down", "delete",
                     "esc", "insert", "print screen", "menu",
                     "num lock", "scroll lock", "pause"}
F_INVALIDATORS = {f"f{i}" for i in range(1, 13)}
_MODIFIER_NAMES = {"ctrl", "alt", "shift", "cmd"}
STRONG_MODIFIERS = {"ctrl", "alt", "cmd"}


class WordBuffer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.current_word = ""      # الحالة (أ)
        self.last_word = None       # الحالة (ب)
        self.last_separator = None  # "space"/"enter"/"tab"
        self.pending_toggle = None  # (أصل, محوَّل, فاصل) للرجوع بـ Shift×2

    def on_char(self, ch):
        """حرف قابل للطباعة: استمرار الكلمة الجارية أو بداية كلمة جديدة."""
        if not ch or ch == "":
            return
        if self.last_word is not None:
            # كانت هناك كلمة مؤكَّدة من قبل، وحرفٌ جديد يعني كلمةً جديدة:
            # الكلمة السابقة "خانت" مكانها فلا نتجاوزها أبداً
            self.last_word = None
            self.last_separator = None
        self.pending_toggle = None
        self.current_word += ch

    def on_separator(self, name):
        """مسافة/Enter/Tab: كلمة جارية -> مؤكَّدة؛ فاصل مكرر -> حالة غير نظيفة."""
        if self.current_word:
            self.last_word = self.current_word
            self.last_separator = name
            self.current_word = ""
        else:
            # لا كلمة جارية: فاصل زائد بعد كلمة مؤكَّدة يُفسد المجاورة
            self.last_word = None
            self.last_separator = None
            self.pending_toggle = None

    def on_backspace(self):
        """Backspace: يزيل من الكلمة الجارية؛ إن دخل الكلمة المؤكَّدة -> إلغاء."""
        if self.current_word:
            self.current_word = self.current_word[:-1]
            self.pending_toggle = None   # كسر تطابق الأطوال -> ألغِ التوجّل
        elif self.last_word is not None:
            self.last_word = None
            self.last_separator = None
            self.pending_toggle = None

    def invalidate(self):
        """تغيير فوكس/نقر ماوس/مفاتيح تحرير: تعذّر التحويل الآمن، صفّر كل شيء."""
        self.reset()

    def last_word_for_conversion(self):
        """ترجع آخر كلمة قابلة للتحويل أو None (الجارية أولاً ثم المؤكَّدة)."""
        if self.current_word:
            return {"word": self.current_word,
                    "separator": None,
                    "confirmed": False}
        if self.last_word is not None:
            return {"word": self.last_word,
                    "separator": self.last_separator,
                    "confirmed": True}
        return None

    def toggle_target(self, word):
        """لو الكلمة الحالية طرفٌ سبق تحويله -> أرجع الطرف الآخر، وإلا None."""
        if self.pending_toggle is None:
            return None
        if word == self.pending_toggle["converted"]:
            return self.pending_toggle["original"]
        if word == self.pending_toggle["original"]:
            return self.pending_toggle["converted"]
        return None


buffer = WordBuffer()
_modifiers = set()
state_lock = threading.RLock()


# ═══════════════════════════════════════════════════════════════
# 3.5) مفاتيح التشغيل من لوحة التحكم (settings.json)
#      الخدمة تراقب الملف وتُطبّق أي تغيير فوراً بلا إعادة تشغيل.
#      الافتراضي: كل الميزات مُفعّلة، فلا يتغير أي سلوك لمن لم يفتح اللوحة.
# ═══════════════════════════════════════════════════════════════
FEATURE_NAMES = (
    "double_shift",
    "switch_layout",
    "slots_enabled",
)
features = dict(settings_store.DEFAULTS)


def set_features(values):
    """يعتمد الميزات المفعّلة ويرجع الحالة الناتجة (أي قيمة غير منطقية تُهمَل)."""
    with state_lock:
        for name in FEATURE_NAMES:
            value = values.get(name) if isinstance(values, dict) else None
            if isinstance(value, bool):
                features[name] = value
        return dict(features)


def feature_enabled(name):
    return features.get(name, True)


def layout_switch_active(trigger=None):
    """هل نبدّل لغة الكيبورد بعد التحويل؟ (الاختصار الموحّد Shift × 2)"""
    return feature_enabled("switch_layout")


def apply_layout_switch(text, trigger=None):
    """يبدّل لغة الإدخال إلى لغة النص الناتج إن كان الخيار مفعّلاً لهذا الاختصار."""
    if not layout_switch_active(trigger):
        return False
    try:
        return layout_switch.switch_to_text_language(text)
    except Exception:
        log.exception("keyboard layout switch failed")
        return False


def start_settings_watcher(interval=0.5):
    """خيط خفيف يراقب settings.json: تعديل الواجهة يُطبّق دون إعادة تشغيل."""
    stop = threading.Event()
    state = {"seen": settings_store.fingerprint()}

    def poll():
        while not stop.is_set():
            stamp = settings_store.fingerprint()
            if stamp != state["seen"]:
                state["seen"] = stamp
                try:
                    log.info("settings applied: %s", set_features(settings_store.load()))
                    _update_slots_hook()
                except BaseException:
                    log.exception("settings reload failed")
            stop.wait(interval)

    threading.Thread(target=poll, name="keyconv-settings", daemon=True).start()
    return stop


# ═══════════════════════════════════════════════════════════════
# 4) قنوات I/O الحية (تُستبدل بـ Fake في الاختبارات)
# ═══════════════════════════════════════════════════════════════
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


def _send_unicode_text(text):
    """كتابة النص كوحدات UTF-16 مستقلة عن تخطيط الكيبورد (KEYEVENTF_UNICODE).

    Controller.type يعتمد على VkKeyScan/التخطيط الحالي، فإن كان التخطيط مختلفاً
    عن لغة النص خرج نص خاطئ. الحقن بترميز Unicode يصل كما هو مهما كانت اللغة.
    """
    if not text:
        return
    if INPUT is None or _SendInput is None:   # pragma: no cover - نظام غير ويندوز
        Controller().type(text)
        return
    encoded = text.encode("utf-16-le")
    units = [
        encoded[index] | (encoded[index + 1] << 8)
        for index in range(0, len(encoded), 2)
    ]
    events = (INPUT * (len(units) * 2))()
    for index, unit in enumerate(units):
        events[2 * index].type = INPUT.KEYBOARD
        events[2 * index].value.ki = KEYBDINPUT(
            wVk=0, wScan=unit,
            dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=None,
        )
        events[2 * index + 1].type = INPUT.KEYBOARD
        events[2 * index + 1].value.ki = KEYBDINPUT(
            wVk=0, wScan=unit,
            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
            time=0, dwExtraInfo=None,
        )
    sent = _SendInput(len(events), events, ctypes.sizeof(INPUT))
    if sent != len(events):
        log.warning("unicode SendInput incomplete: %s/%s", sent, len(events))


class RealIO:
    """الواجهة الفعلية لإرسال الضغطات وقراءة/كتابة الكليبورد."""

    def __init__(self):
        self.controller = Controller()

    def send_copy(self):
        c = self.controller
        c.press(Key.ctrl)
        c.press("c")
        c.release("c")
        c.release(Key.ctrl)

    def send_paste(self):
        c = self.controller
        c.press(Key.ctrl)
        c.press("v")
        c.release("v")
        c.release(Key.ctrl)

    def send_undo(self):
        c = self.controller
        c.press(Key.ctrl)
        c.press("z")
        c.release("z")
        c.release(Key.ctrl)

    def clipboard_get(self):
        return winclip.get_text()

    def clipboard_set(self, text):
        winclip.set_text(text)

    def clipboard_seq(self):
        """رقم الكليبورد التسلسلي: يكشف نجاح النسخ دون كتابة أي شيء مسبقاً."""
        return winclip.sequence_number()

    def snapshot(self):
        """لقطة كل صيغ الكليبورد (نص/صور/ملفات) لاسترجاعها كما كانت."""
        return winclip.snapshot()

    def restore(self, snap):
        return winclip.restore(snap)

    def backspaces(self, n):
        c = self.controller
        for _ in range(n):
            c.press(Key.backspace)
            c.release(Key.backspace)

    def write(self, text):
        _send_unicode_text(text)

    def press_separator(self, name):
        # نعيد كتابة نفس الفاصل الذي كان بعد الكلمة بالضبط
        key = {"space": Key.space, "enter": Key.enter, "tab": Key.tab}[name]
        c = self.controller
        c.press(key)
        c.release(key)


IO = RealIO()
sleep_fn = time.sleep


class ActionRunner:
    """خيط عمل واحد ينفّذ الحقن خارج خيط الـ Hook.

    السبب: أي نوم داخل callback الخاص بـ pynput يجمّد حلقة رسائل الـ Hook نفسها،
    فيتوقف إحساس الكيبورد في النظام كله وقد يزيل ويندوز الـ Hook. هنا يعود الـ
    callback فوراً والعمل الفعلي يجري في هذا الخيط.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()
        self._thread = None

    def submit(self, fn):
        with self._lock:
            self._ensure_thread()
            self._idle.clear()
            self._queue.put(fn)

    def flush(self, timeout=ACTION_QUEUE_TIMEOUT_S):
        """ينتظر انتهاء كل المهام (تستخدمه الاختبارات لضمان الحتمية)."""
        return self._idle.wait(timeout)

    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name="keyconv-inject", daemon=True
            )
            self._thread.start()

    def _loop(self):
        while True:
            fn = self._queue.get()
            try:
                fn()
            except BaseException:
                log.exception("injection action failed")
            finally:
                self._queue.task_done()
                if self._queue.unfinished_tasks == 0:
                    self._idle.set()


runner = ActionRunner()


def flush_actions(timeout=ACTION_QUEUE_TIMEOUT_S):
    """ينتظر انتهاء عمليات الحقن المعلّقة (للاختبارات وللإغلاق الآمن)."""
    return runner.flush(timeout)


def _submit_action(fn):
    """يضع المهمة في الطابور؛ إن فشل الطابور لا يبقى الحارس مفعّلاً للأبد."""
    try:
        runner.submit(fn)
        return True
    except BaseException:
        log.exception("could not queue action")
        return False


# ═══════════════════════════════════════════════════════════════
# 5) تخطيط الأفعال (منطق قرارات خالص قابل للاختبار)
# ═══════════════════════════════════════════════════════════════
def plan_selection_action(selected):
    """ميزة 1: قرار تحويل النص المحدد. يرجع None إن لم يتغير شيء."""
    converted = convert_text(selected)
    if converted == selected:
        return None
    return converted


def plan_word_action(info):
    """ميزة 2: قرار تحويل آخر كلمة (جارية أو مؤكَّدة) أو توجّلها.

    يرجع خطة حقن أو None (لا كلمة / لا تغيير / خارج جدول التحويل).
    """
    if info is None:
        return None
    word = info["word"]
    # لا نعتبر إلا "كلمات" حقيقية: لابد من وجود حرف (عربي أو لاتيني) فيها،
    # فرمز/رقم بمفرده بعد الفاصل = حالة غير نظيفة -> لا تحوّل شيئاً
    if not any(ch.isalpha() for ch in word):
        return None
    converted = convert_text(word)
    if converted == word:
        return None  # تعادل أو كلها خارج الجدول -> لا تلمس شيئاً

    target = buffer.toggle_target(word)
    toggle = target is not None
    return {
        "word": word,
        "converted": converted,
        "text": target if toggle else converted,
        "toggle": toggle,
        "delete_count": len(word) + (1 if info["confirmed"] else 0),
        "separator": info["separator"],
        "confirmed": info["confirmed"],
    }


# ═══════════════════════════════════════════════════════════════
# 6) أدوات الحقن الفعلية
# ═══════════════════════════════════════════════════════════════
def _safe_snapshot(io):
    """لقطة كاملة للكليبورد (كل الصيغ) إن كانت الواجهة تدعمها."""
    snap_fn = getattr(io, "snapshot", None)
    if not callable(snap_fn):
        return None
    try:
        return snap_fn()
    except Exception:
        log.exception("clipboard snapshot failed")
        return None


def _restore_clipboard(io, snap, original_text):
    """يعيد الكليبورد كما كان: كل الصيغ أولاً، ثم النص كخطة بديلة."""
    if snap:
        try:
            if io.restore(snap):
                trace("clip_restored=snapshot")
                return
        except Exception:
            log.exception("clipboard restore failed")
    if original_text is None:
        return
    try:
        io.clipboard_set(original_text)
        trace("clip_restored=text")
    except Exception:
        log.exception("clipboard text restore failed")


def _restore_if_unchanged(io, snap, original_text, sent_text):
    """استرجاع الكليبورد إلا إذا غيّره برنامج آخر أثناء انتظار اللصق."""
    try:
        current = io.clipboard_get()
    except Exception:
        current = None
    if current is not None and current != sent_text:
        log.info("clipboard changed externally; restore skipped")
        trace("clip_restore_skipped=changed")
        return
    _restore_clipboard(io, snap, original_text)


def _foreground_label():
    """وسف النافذة الأمامية للتسجيل: الفئة + بداية العنوان (لأغراض التشخيص)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "none"
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        title = ctypes.create_unicode_buffer(96)
        user32.GetWindowTextW(hwnd, title, 96)
        return "%s|%s" % (cls.value, title.value[:40])
    except Exception:
        return "?"


def _read_selection(io, budget_s):
    """Ctrl+C ثم انتظار تغيّر الكليبورد فعلياً.

    يرجع (status, text):
      * ("found", text) عند تغيّر الكليبورد فعلاً
      * (SELECTION_NONE, None) عند انتهاء المهلة بلا تغيير: لا يوجد تحديد
      * (SELECTION_ERROR, None) إذا تعذّرت قراءة الكليبورد

    التمييز بين "لا تحديد" و"خطأ وصول" ضروري كي لا نرجع لتحويل كلمة عند فشل حقيقي.
    """
    seq_fn = getattr(io, "clipboard_seq", None)
    failed = False
    before_seq = before_text = None
    try:
        before_text = io.clipboard_get()
    except Exception as exc:
        failed = True
        log.info("copy probe: read-before failed: %r", exc)
    if callable(seq_fn):
        try:
            before_seq = seq_fn() or None   # 0 = فشل القياس في ويندوز: نعتبره غير متاح
        except Exception as exc:
            before_seq = None
            log.info("copy probe: seq-before failed: %r", exc)
    log.info(
        "copy probe: before seq=%s text_len=%s budget=%.2fs",
        before_seq, None if before_text is None else len(before_text), budget_s,
    )

    io.send_copy()
    tries = max(1, int(round(budget_s / COPY_POLL_INTERVAL_S)))
    for i in range(tries):
        sleep_fn(COPY_POLL_INTERVAL_S)
        if callable(seq_fn) and before_seq is not None:
            try:
                seq = seq_fn()
            except Exception as exc:
                seq = None
                log.info("copy probe: seq-after failed: %r", exc)
            if seq:   # 0 = تعذّر القياس الآن: نسقط إلى مقارنة النص
                if seq != before_seq:
                    try:
                        text = io.clipboard_get()
                    except Exception as exc:
                        log.info("copy probe: read-after failed: %r", exc)
                        return SELECTION_ERROR, None
                    log.info("copy probe: seq changed %s->%s (try %d)", before_seq, seq, i)
                    return "found", text
                continue
        try:
            text = io.clipboard_get()
        except Exception as exc:
            log.info("copy probe: text-after failed: %r", exc)
            return SELECTION_ERROR, None
        if before_text is None or text != before_text:
            log.info("copy probe: text changed (try %d, len=%d)", i, len(text))
            return "found", text
    log.info("copy probe: timed out (%d tries, seq=%r)", tries, None)
    return (SELECTION_ERROR if failed else SELECTION_NONE), None


def perform_selection_dance(io, budget_s=COPY_READ_DELAY_S, retry=True):
    """تحويل النص المحدد: Ctrl+C -> تحويل -> Ctrl+V -> استرجاع مضمون.

    * لا نكتب شيئاً في الكليبورد قبل النسخ، فلا يتلف محتوى المستخدم لو فشل النسخ.
    * كشف النسخ الصامت بمقارنة رقم الكليبورد التسلسلي (أو النص) لا بإشارة نصية.
    * الاسترجاع في finally: يحدث دائماً حتى لو وقع استثناء في أي خطوة.
    * الحارس يبقى بيد المستدعي لأنه قد يُتبع بتحويل آخر كلمة في نفس العملية.

    ترجع حالة SELECTION_* ليعرف المستدعي هل يرجع لتحويل آخر كلمة أم لا.
    """
    original_text = None
    snap = None
    try:
        log.info("selection dance started: fg=%s", _foreground_label())
        try:
            original_text = io.clipboard_get()
        except Exception:
            original_text = None
        snap = _safe_snapshot(io)
        trace("clip_original=", repr(original_text))

        status, selected = _read_selection(io, budget_s)
        if status == SELECTION_NONE and retry:
            trace("copy_silent_fail -> retry")
            status, selected = _read_selection(io, COPY_RETRY_DELAY_S)
        if status == SELECTION_ERROR:
            log.info("selection aborted: clipboard access failed fg=%s",
                     _foreground_label())
            return SELECTION_ERROR
        if status == SELECTION_NONE:
            log.info("selection skipped: nothing copied (no selection) fg=%s",
                     _foreground_label())
            return SELECTION_NONE
        if not selected:
            # بعض التطبيقات (WhatsApp مثلاً) عند عدم التحديد تفرّغ الكليبورد
            # وتغيّر رقمه التسلسلي، فيبدو الأمر كنسخ ناجح لنص فارغ. هذا يعني
            # عملياً "لا يوجد تحديد"، فنُعيد NONE كي يُستكمل تحويل آخر كلمة.
            log.info("selection skipped: copy produced empty text fg=%s",
                     _foreground_label())
            return SELECTION_NONE
        trace("clip_selected=", repr(selected))

        converted = plan_selection_action(selected)
        if converted is None:
            log.info("selection aborted: text needs no conversion")
            trace("no change")
            return SELECTION_NO_CHANGE
        io.clipboard_set(converted)
        trace("clip_sent_converted=", repr(converted))
        io.send_paste()
        sleep_fn(PASTE_DELAY_S)
        apply_layout_switch(converted, "selection")
        log.info(
            "selection converted: chars=%d lang=%s",
            len(selected), converter.detect_language(converted),
        )
        return SELECTION_CONVERTED
    finally:
        _restore_clipboard(io, snap, original_text)


def inject_word_action(action, io):
    """ميزة 2: تنفيذ خطة تحويل/توجّل آخر كلمة على النظام الحي."""
    trace("word_action=", action)
    io.backspaces(action["delete_count"])
    io.write(action["text"])
    if action["confirmed"]:
        io.press_separator(action["separator"])   # إعادة الفاصل كما كان


def commit_word_action(action):
    """تحديث المخزن ليطابق ما دُفع فعلاً (لا نقرؤه من أصداء الأحداث)."""
    if not action["toggle"]:
        # أول تحويل لهذه الكلمة: سجّل (أصل, محوَّل) ليمكن التوجّل للخلف لاحقاً
        buffer.pending_toggle = {
            "original": action["word"],
            "converted": action["converted"],
            "separator": action["separator"],
        }
    if action["confirmed"]:
        buffer.current_word = ""
        buffer.last_word = action["text"]
        buffer.last_separator = action["separator"]
    else:
        buffer.current_word = action["text"]
        buffer.last_word = None


def _act_convert():
    """Shift × 2: التحديد أولاً، وآخر كلمة كخطة بديلة عند عدم وجود تحديد."""
    if not feature_enabled("double_shift"):
        return  # موقوفة من لوحة التحكم
    if guard.active:
        return  # صدا/ضوضاء أثناء الحقن -> لا تبدأ عملية جديدة في منتصف أخرى
    action = plan_word_action(buffer.last_word_for_conversion())
    # نغلق الباب فوراً على خيط الحدث، ثم يجري العمل الفعلي في خيط العمل
    guard.begin()
    if not _submit_action(lambda: _run_convert(action)):
        guard.finish()


def _run_convert(word_action):
    try:
        # مع وجود كلمة جاهزة نفحص التحديد سريعاً: أغلب النسخ يحدث فوراً.
        status = perform_selection_dance(
            IO,
            budget_s=WORD_COPY_WAIT_S if word_action is not None else COPY_READ_DELAY_S,
            retry=word_action is None,
        )
        if status == SELECTION_NONE and word_action is not None:
            trace("word_fallback: no selection")
            _run_word_action(word_action)
    except BaseException:
        log.exception("conversion action failed")
    finally:
        guard.finish()


def _run_word_action(action):
    try:
        inject_word_action(action, IO)
        log.info(
            "word converted: %r -> %r (confirmed=%s)",
            action["word"], action["text"], action["confirmed"],
        )
        with state_lock:
            commit_word_action(action)
        apply_layout_switch(action["text"], "word")
    except BaseException:
        log.exception("word injection failed")


# ── الحافظة متعددة الفتحات: أفعال النسخ إلى فتحة والصق منها ──
SLOT_CAPTURE_WAIT_S = 0.60     # أقصى انتظار لنسخ التطبيق (يقيسه الاستقصاء)
SLOT_CAPTURE_POLL_S = 0.05     # فاصل الاستقصاء: ننهي فور تغيّر الرقم التسلسلي
SLOT_CAPTURE_TRIES = max(1, int(round(SLOT_CAPTURE_WAIT_S / SLOT_CAPTURE_POLL_S)))
SLOT_PASTE_DELAY_S = 0.60      # مهلة أطول من الرقصة: تطبيقات إلكترون تقرأ
                               # الحافظة متأخراً (IPC)، خصوصاً مع نصوص كبيرة،
                               # فلا نسترجع الأصل قبل أن يلتصق النص فعلاً.

_slots_stop = None
_slots_lock = threading.Lock()


def _update_slots_hook():
    """يثبّت خطاف الفتحات عند تفعيل الخيار وينزعه فوراً عند إيقافه.

    التنصيب الديناميكي يعني: والخيار مطفأ لا يوجد أي خطاف إضافي على
    لوحة المفاتيح إطلاقاً، فسلوك النظام يبقى كما كان تماماً.
    """
    global _slots_stop
    with _slots_lock:
        wanted = feature_enabled("slots_enabled")
        if wanted and _slots_stop is None:
            try:
                _slots_stop = slots_hook.start(
                    _run_slot_capture,
                    _prepare_slot_paste,
                )[1]
                log.info("slots hook started")
            except BaseException:
                log.exception("slots hook start failed")
                _slots_stop = None
        elif not wanted and _slots_stop is not None:
            stop_fn, _slots_stop = _slots_stop, None
            try:
                stop_fn()
                log.info("slots hook stopped (feature off)")
            except BaseException:
                log.exception("slots hook stop failed")


def _run_slot_capture(slot, before_seq):
    def task():
        try:
            # نستقصي الرقم التسلسلي حتى ينسخ التطبيق فعلاً. إن لم يتغير خلال
            # المهلة (تطبيق بطيء أو لا يغيّره) نحفظ نص الكليبورد الحالي:
            # الهدف هو القدرة على اللصق، لا إجهاض العملية بصمت.
            for _ in range(SLOT_CAPTURE_TRIES):
                sleep_fn(SLOT_CAPTURE_POLL_S)
                if not before_seq:
                    break            # لا قياس متاح: مهلة واحدة ثم نقرأ الحالي
                try:
                    seq = IO.clipboard_seq()
                except Exception:
                    seq = 0
                if seq and seq != before_seq:
                    break
            try:
                text = IO.clipboard_get()
            except Exception:
                log.exception("slot %d capture read failed", slot)
                return
            if not text:
                log.info("slot %d capture skipped: clipboard text is empty", slot)
                return
            slots_hook.save_slot(slot, text)
            log.info("slot %d captured: %d chars", slot, len(text))
        except BaseException:
            log.exception("slot capture failed")
    if not _submit_action(task):
        log.error("slot capture could not be queued")


def _prepare_slot_paste(slot):
    """يُستدعى في خيط الخطاف عند V: يضع نص الفتحة في الحافظة فوراً.

    الترتيب هو كل شيء: الكتابة تسبق إعادة V للتطبيق، فيقرأ هو النص الجديد
    ويلصقه لصقاً طبيعياً — لا حقن ولا تراجع.
    """
    text = slots_hook.get_slot(slot)
    if not text:
        log.info("slot %d empty: paste skipped", slot)
        return False
    try:
        original_text = IO.clipboard_get()
    except Exception:
        original_text = None
    snap = _safe_snapshot(IO)
    try:
        IO.clipboard_set(text)
    except Exception:
        log.exception("slot %d clipboard write failed", slot)
        return False
    try:
        written = IO.clipboard_get()
    except Exception:
        written = None
    if written is not None and written != text:
        log.warning(
            "slot %d clipboard mismatch: wrote %d, read %d",
            slot, len(text), len(written),
        )
        return False
    if not _submit_action(
        lambda: _finish_slot_paste(slot, text, snap, original_text)
    ):
        log.error("slot paste cleanup could not be queued")
    log.info(
        "slot %d prepared for natural paste fg=%s",
        slot, _foreground_label(),
    )
    return True


def _finish_slot_paste(slot, text, snap, original_text):
    try:
        sleep_fn(SLOT_PASTE_DELAY_S)
        apply_layout_switch(text, "slot")
    except BaseException:
        log.exception("slot paste finish failed")
    finally:
        # لا نطمس نسخاً جديداً نسخه المستخدم في هذه الأثناء
        _restore_if_unchanged(IO, snap, original_text, text)


# ═══════════════════════════════════════════════════════════════
# 7) الـ Dispatcher — كل الأحداث تمر من هنا (من الـ hooks أو الاختبارات)
#    القاعدة الأولى: أثناء تفعيل حارس الحقن لا يُقبل أي ضغطة جديدة.
# ═══════════════════════════════════════════════════════════════
def _is_text(name):
    """هل هذا الحدث نص يُكتب (حرف أو أكثر) لا اسماً لمفتاح خاص؟

    نتحقق isprintable كي لا تلوّث رموز التحكم (Ctrl+C -> "\\x03"، Ctrl+V -> "\\x16")
    مخزن آخر كلمة فيحذف Backspace أحرفاً أكثر من اللازم (المشكلة 3).
    "لا" الناتجة عن زر b حرفان لكنها تُكتب بمفتاح واحد، فتُقبل كوحدة واحدة.
    """
    if not isinstance(name, str) or not name:
        return False
    if name in _KEY_NAMES.values():
        return False
    return all(ch.isprintable() for ch in name)


def handle_key_event(name, is_down, now=None):
    """البوابة المركزية لكل أحداث الكيبورد."""
    if guard.active and is_down:
        # صدى ضغطة اصطناعية أثناء الحقن -> تُرفض كلياً (لا re-trigger ممكن)
        return
    now = time.monotonic() if now is None else now

    with state_lock:
        if is_down:
            # ── كاشف Shift: ضغطته تُبنى، وأي مفتاح آخر يُلغي ──
            #    لا يُنفَّذ أي فعل هنا أبداً: التنفيذ مؤجَّل لرفع الضغطة
            if name == "shift":
                shift_detector.key_down(now)
            else:
                shift_detector.other_key_down()

            # ── تتبّع المفاتيح التعديلية ──
            if name in _MODIFIER_NAMES:
                _modifiers.add(name)

            # ── تحديث مخزن آخر كلمة ──
            if (name in STRONG_MODIFIERS or name in EDIT_INVALIDATORS
                    or name in F_INVALIDATORS):
                # اختصارات أو تحريك مؤشر/حذف: لم يعد هناك تحويل آمن
                buffer.invalidate()
            elif name in SEPARATORS:
                buffer.on_separator(name)
            elif name == "backspace":
                buffer.on_backspace()
            elif _is_text(name):
                buffer.on_char(name)
            # shift / caps lock / ctrl / alt لا تلمس المخزن إطلاقاً
        else:
            # ── أحداث الرفع: هنا يتم التنبيه بفعل double-tap المكتمل ──
            fired = False
            if name == "shift":
                fired = shift_detector.key_up(now)
            if name in _MODIFIER_NAMES:
                _modifiers.discard(name)

            # التنفيذ بعد الرّفع الكامل: لا مفتاح تعديلي في اليد أثناء الحقن
            if fired:
                trace("fired=shift-x2")
                _act_convert()


def handle_focus_change():
    """تغيير النافذة النشطة: يصفّر الكلمة المسجلة (متطلب رقم 6)."""
    if guard.active:
        return
    with state_lock:
        buffer.invalidate()


# ═══════════════════════════════════════════════════════════════
# 8) الجسور الحية: pynput -> أسماء نصوص -> handle_key_event
#    لا يُنشأ أي listener هنا إلا داخل run() حصراً.
# ═══════════════════════════════════════════════════════════════
_KEY_NAMES = {
    Key.shift_l: "shift", Key.shift_r: "shift",
    Key.ctrl_l: "ctrl", Key.ctrl_r: "ctrl",
    Key.alt_l: "alt", Key.alt_r: "alt", Key.alt_gr: "alt",
    Key.caps_lock: "caps lock",
    Key.space: "space", Key.enter: "enter", Key.tab: "tab",
    Key.backspace: "backspace", Key.delete: "delete",
    Key.left: "left", Key.right: "right", Key.up: "up", Key.down: "down",
    Key.home: "home", Key.end: "end",
    Key.page_up: "page up", Key.page_down: "page down",
}


def _add_key(mapping, attr, name):
    """يضيف مفتاحاً إن كان موجوداً في نسخة pynput الحالية (تختلف بين الإصدارات)."""
    key = getattr(Key, attr, None)
    if key is not None:
        mapping[key] = name


# مفاتيح كانت تُتجاهل كلياً فلا تُبطل الكلمة المسجلة -> تحويل في سياق خاطئ
_add_key(_KEY_NAMES, "esc", "esc")
_add_key(_KEY_NAMES, "insert", "insert")
_add_key(_KEY_NAMES, "print_screen", "print screen")
_add_key(_KEY_NAMES, "menu", "menu")
_add_key(_KEY_NAMES, "num_lock", "num lock")
_add_key(_KEY_NAMES, "scroll_lock", "scroll lock")
_add_key(_KEY_NAMES, "pause", "pause")
_add_key(_KEY_NAMES, "cmd", "cmd")
_add_key(_KEY_NAMES, "cmd_l", "cmd")
_add_key(_KEY_NAMES, "cmd_r", "cmd")
for _i in range(1, 13):
    _add_key(_KEY_NAMES, f"f{_i}", f"f{_i}")


def _key_name(key):
    """يحوّل كائن مفتاح pynput إلى اسم نصي موحّد (أو None إن كان غير ذي صلة).

    الحرف يُترجم وقت الحدث بـ key_translate حسب لغة نافذة المقدمة، لا بجدول
    pynput المجمَّد عند بدء الخدمة (سبب تحويل الاتجاه الخاطئ).
    """
    if isinstance(key, Key):
        return _KEY_NAMES.get(key)
    vk = getattr(key, "vk", None)
    if vk:
        if _modifiers & STRONG_MODIFIERS:
            # Ctrl/Alt/Win + حرف اختصار لا يُكتب نصاً ولا يمس المخزن
            return None
        text = key_translate.text_for_key(vk, getattr(key, "_scan", None))
        if text is not None:
            return text or None      # مفتاح ميت -> لا نص
    char = getattr(key, "char", None)
    return char or None


def on_press(key):
    """جسر pynput: أي استثناء هنا كان يُسقط المستمع كلياً، لذلك نحتويه."""
    try:
        name = _key_name(key)
        if name is not None:
            handle_key_event(name, True)
    except BaseException:
        log.exception("on_press failed")


def on_release(key):
    try:
        name = _key_name(key)
        if name is not None:
            handle_key_event(name, False)
    except BaseException:
        log.exception("on_release failed")


def _on_click(x, y, button, pressed):
    # نقر الفأرة يحوّل موضع المؤشر -> لم يعد الحذف "بالظبط" مضموناً -> إلغاء
    if pressed:
        try:
            handle_focus_change()
        except BaseException:
            log.exception("on_click failed")


def _start_focus_poller():
    """Thread خلفي يراقب GetForegroundWindow: أي تبديل إطار -> صفّر الكلمة."""
    stop = threading.Event()

    try:
        import ctypes
        user32 = ctypes.windll.user32
    except Exception:
        return stop  # نظام غير Windows: بلا مراقب فوكس

    current_hwnd = [user32.GetForegroundWindow()]

    def poll():
        while not stop.is_set():
            hwnd = user32.GetForegroundWindow()
            if hwnd != current_hwnd[0]:
                current_hwnd[0] = hwnd
                handle_focus_change()
            stop.wait(FOCUS_POLL_INTERVAL_S)

    threading.Thread(target=poll, daemon=True).start()
    return stop


# ═══════════════════════════════════════════════════════════════
# 9) نقطة التشغيل
# ═══════════════════════════════════════════════════════════════
_INSTANCE_MUTEX = None
_STOP_EVENT = None


def _create_stop_event(name=service_ctl.STOP_EVENT_NAME):
    """Event مسمّى تستطيع اللوحة إشارته لإيقاف الخدمة بلا الاعتماد على PID."""
    global _STOP_EVENT
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p
        ]
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        _STOP_EVENT = kernel32.CreateEventW(None, True, False, name)
    except Exception:
        _STOP_EVENT = None
    return _STOP_EVENT


def _wait_for_stop():
    """خيط ينتظر إشارة الإيقاف ثم يخرج فوراً بعد تنظيف ملف PID."""
    if not _STOP_EVENT:
        return
    try:
        ctypes.windll.kernel32.WaitForSingleObject(_STOP_EVENT, 0xFFFFFFFF)
    except Exception:
        return
    log.info("stop requested via control event")
    service_ctl.remove_pid_file()
    os._exit(0)


def acquire_single_instance(name="Local\\KeyConv.SingleInstance"):
    """يمنع نسختين معاً: مع التشغيل التلقائي قد يبدأ البرنامج مرتين فيضاعف التحويل."""
    global _INSTANCE_MUTEX
    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return True
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, name)
    if not _INSTANCE_MUTEX:
        return True
    return kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def run():
    _make_streams_safe()
    _setup_logging()
    if not acquire_single_instance():
        log.info("another instance is already running; exiting")
        _out("نسخة أخرى تعمل بالفعل — لن أبدأ نسخة ثانية.")
        return

    log.info("starting pid=%s python=%s", os.getpid(), sys.executable)
    set_features(settings_store.load())
    service_ctl.write_pid_file()    # كي تستطيع لوحة التحكم إيقاف هذه العملية
    _create_stop_event()
    threading.Thread(
        target=_wait_for_stop, name="keyconv-stop", daemon=True
    ).start()
    stop_watch = start_settings_watcher()
    kb_listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener = pynput_mouse.Listener(on_click=_on_click)
    stop_focus = _start_focus_poller()

    try:
        slots_hook.reset_runtime()   # فتحات مؤقتة: تبدأ فارغة كل تشغيل
        _update_slots_hook()         # يثبّت الخطاف فقط إن كان الخيار مفعّلاً
    except BaseException:
        log.exception("slots hook start failed")

    kb_listener.start()
    mouse_listener.start()

    _out("شغّال الآن:")
    _out("  * Shift × 2 سريع    -> تحويل التحديد إن وُجد، وإلا آخر كلمة (عربي <-> إنجليزي)")
    _out("  * Ctrl+C+رقم -> نسخ إلى فتحة | Ctrl+V -> لصق آخر نسخة | Ctrl+رقم+V -> لصق فتحة")
    _out("لإنهاء البرنامج: Ctrl+C في هذه النافذة")
    log.info("listeners started")

    try:
        kb_listener.join()
        mouse_listener.join()
    except KeyboardInterrupt:
        stop_focus.set()
        stop_watch.set()
        kb_listener.stop()
        mouse_listener.stop()
        log.info("stopped by user (Ctrl+C)")
    finally:
        try:
            with _slots_lock:
                stop_fn, _slots_stop = _slots_stop, None
            if stop_fn:
                stop_fn()
        except BaseException:
            log.exception("slots hook stop failed")
        service_ctl.remove_pid_file()


if __name__ == "__main__":
    run()