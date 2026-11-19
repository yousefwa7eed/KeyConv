# slots_hook.py — الحافظة متعددة الفتحات (9 فتحات مؤقتة).
#
# الطريقة (مواصفة المستخدم) مع بقاء المفاتيح الطبيعية طبيعية 100%:
#   Ctrl+C ثم رقم 1..9  -> النسخ العادي يجري كما هو، والرقم يحفظ الحافظة في الفتحة.
#   Ctrl+رقم 1..9 ثم V  -> الرقم يُحتجز، وعند V تُجهَّز الحافظة بنص الفتحة
#                           قبل وصول الضغطة للتطبيق فيلصقها هو طبيعياً (بلا حقن).
#   Ctrl+V وحده         -> لصق طبيعي خالص لآخر نسخة.
#
# التنفيذ: خطاف لوحة مفاتيح منخفض المستوى (WH_KEYBOARD_LL) يمرّر كل الأحداث
# كما هي ويحتجز فقط رقم 1..9 أثناء "التسلح" (بعد C، أو بعد Ctrl+رقم بانتظار V)
# كي لا يصل إلى التطبيق كاختصار Ctrl+رقم. لا احتجاز ولا إعادة حقن لأي مفتاح آخر.
# الأحداث المحقونة (منّا أو من أي برنامج) تمر دائماً دون معالجة، فلا حلقات.
# الميزة قابلة للإيقاف من اللوحة؛ والفتحات مؤقتة: تُمسح عند بدء الخدمة.

import ctypes
import json
import logging
import os
import threading
import time
from ctypes import wintypes

import clipboard_win as winclip
import paths

log = logging.getLogger("keyconv")

# ── ثوابت ويندوز ──────────────────────────────────────────────
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
VK_V = 0x56

WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
LLKHF_UP = 0x80
LLKHF_INJECTED = 0x10

# أرقام الصف العلوي ولوحة الأرقام كلاهما مقبولة: الفتحات 1..9 فقط.
# Ctrl+V وحده (بلا رقم) يلصق آخر نسخة طبيعياً؛ ولا نختطف الصفر.
DIGIT_VKS = {}
for _i in range(1, 10):
    DIGIT_VKS[0x30 + _i] = _i        # '1'..'9'
    DIGIT_VKS[0x60 + _i] = _i        # Numpad1..9

CAPTURE_WINDOW_S = 0.8        # أقصى فرق بين Ctrl+C والرقم
SLOT_ARM_S = 1.5              # أقصى انتظار بين Ctrl+رقم وV


# ═════════════════════════════════════════════════════════════
# 1) مخزن الفتحات (مؤقت في الذاكرة + ملف للعرض في اللوحة فقط)
# ═════════════════════════════════════════════════════════════
_slots = {}
_slots_lock = threading.Lock()


def slots_file_path():
    return os.environ.get(
        "KEYCONV_SLOTS", os.path.join(paths.data_dir(), "slots.json")
    )


def _write_slots_file():
    """ملف عرض فقط: اللوحة (عملية أخرى) تقرأ منه محتوى الفتحات."""
    try:
        target = slots_file_path()
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with _slots_lock:
            payload = {str(k): v for k, v in sorted(_slots.items())}
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, target)
    except OSError:
        log.exception("slots file write failed")


def save_slot(slot, text):
    with _slots_lock:
        _slots[slot] = text
    _write_slots_file()


def get_slot(slot):
    with _slots_lock:
        return _slots.get(slot, "")


def slots_snapshot():
    with _slots_lock:
        return dict(_slots)


def read_slots_file():
    """للوحة التحكم: قراءة محتوى الفتحات من ملف العرض."""
    try:
        with open(slots_file_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
        return {int(k): v for k, v in raw.items() if str(k).isdigit()}
    except (OSError, ValueError):
        return {}


def reset_runtime():
    """بدء جديد = فتحات فارغة (المستخدم اختار أن تكون مؤقتة)."""
    with _slots_lock:
        _slots.clear()
    _write_slots_file()


# ═════════════════════════════════════════════════════════════
# 2) آلة القرار (منطق نقي قابل للاختبار بلا ويندوز)
# ═════════════════════════════════════════════════════════════
class SlotChord:
    """تقرر لكل حدث مفتاح: تمرير أو احتجاز، وتُبلغ بالأفعال عبر الندوات.

    المبدأ: C وV يمرّان افتراضياً فينفّذ التطبيق النسخ/اللصق الطبيعي.
    الاستثناءان: الرقم بعد C (يُحفظ نص النسخة)، وV بعد Ctrl+رقم
    (يُجهَّز نص الفتحة في الحافظة قبل وصول V، فتلصقه هي طبيعياً).
    """

    CAPTURE_WINDOW_S = CAPTURE_WINDOW_S
    SLOT_ARM_S = SLOT_ARM_S

    def __init__(
        self,
        on_capture,
        on_paste,
        enabled=lambda: True,
        ctrl_down=lambda: False,
        modifier_down=lambda: False,
        seq=lambda: 0,
        now=time.monotonic,
        arm_timer=None,
    ):
        self.on_capture = on_capture     # (slot, before_seq)
        self.on_paste = on_paste         # (slot,) -> هل جُهزت الحافظة؟
        self.enabled = enabled
        self.ctrl_down = ctrl_down
        self.modifier_down = modifier_down
        self.seq = seq
        self.now = now
        self._make_timer = arm_timer or (
            lambda delay, fn: _daemon_timer(delay, fn)
        )
        # armed: ("capture", deadline, before_seq)
        #        | ("slot", deadline, slot, timer) | None
        self.armed = None
        # بعد تمرير V تلصق الفتحة: نبتلع تكرارها التلقائي (down فقط)
        self._v_repeat = False
        # فشل تجهيز الحافظة: نبتلع V كاملة كي لا يُلصق نص قديم
        self._v_swallow_up = False

    # ── نقطة الدخول ──
    def handle(self, vk, is_down, injected=False):
        """يرجع 'pass' أو 'swallow' لحدث واحد (vk رقم مفتاح افتراضي)."""
        if injected:
            return "pass"   # أحداثنا المحقونة تمر ولا تلمس الحالة إطلاقاً
        if not self.enabled():
            self._disarm()
            return "pass"
        self._expire_if_due()
        if is_down:
            return self._on_down(vk)
        return self._on_up(vk)

    # ── أحداث النزول ──
    def _on_down(self, vk):
        if vk == VK_V and (self._v_repeat or self._v_swallow_up):
            # تكرار تلقائي بعد أول V: يُبتلع حتى تُرفع V
            return "swallow"

        if vk == VK_V:
            if self.ctrl_down() and not self.modifier_down():
                armed = self.armed
                if armed and armed[0] == "slot":
                    slot = armed[2]
                    self._disarm()
                    try:
                        ready = bool(self.on_paste(slot))
                    except Exception:
                        log.exception("slot paste preparation failed")
                        ready = False
                    if ready:
                        # الحافظة جُهزت الآن؛ نمرر V ليُلصقه التطبيق طبيعياً
                        self._v_repeat = True
                        return "pass"
                    # فتحة فارغة أو تعذّر التجهيز: نمنع لصق نص قديم
                    self._v_swallow_up = True
                    return "swallow"
            return "pass"        # Ctrl+V وحده: لصق طبيعي، لا احتجاز أبداً

        if vk == VK_C:
            if self.ctrl_down() and not self.modifier_down():
                self._arm_capture()
            return "pass"

        if vk in DIGIT_VKS and self.ctrl_down():
            armed = self.armed
            if armed and armed[0] == "capture":
                self._disarm()
                self.on_capture(DIGIT_VKS[vk], armed[2])
            else:
                # Ctrl+رقم: نحتجز الرقم وننتظر V لاختيار الفتحة
                self._arm_slot(DIGIT_VKS[vk])
            return "swallow"

        if self.armed and self.armed[0] == "slot":
            # مفتاح آخر بدل V: ألغِ الاختصار ومرّره طبيعياً
            self._disarm()
        return "pass"

    # ── أحداث الرفع ──
    def _on_up(self, vk):
        if vk == VK_CONTROL:
            # حُرّر Ctrl: أي V لاحق يلصق طبيعياً (لا اختطاف)
            self._disarm()
        if vk == VK_V:
            if self._v_swallow_up:
                self._v_swallow_up = False
                return "swallow"     # نبتلع رفع V أيضاً: التطبيق لم يرَ ضغطتها
            self._v_repeat = False   # الرفع يمر ليوازن الضغطة الممررة
        return "pass"

    # ── التسلح ──
    def _arm_slot(self, slot):
        self._disarm()
        timer = self._make_timer(self.SLOT_ARM_S, self._slot_deadline)
        self.armed = ("slot", self.now() + self.SLOT_ARM_S, slot, timer)

    def _arm_capture(self):
        # نقيس رقم الحافظة "قبل" أول ضغطة C: التطبيق ينسخ فوراً عند Ctrl+C،
        # أي قبل وصول الرقم، فمقارنة ما بعدها تكشف نجاح النسخ فعلاً.
        # ضغطة C ثانية أثناء التسلح لا تعيد القياس: الرقم الأصلي هو الأساس،
        # وإلا بدا النسخ "بلا تغيير" بعد أن غيّره بالفعل (فات الفتحة).
        armed = self.armed
        before = armed[2] if armed and armed[0] == "capture" else self.seq()
        self._disarm()
        self.armed = ("capture", self.now() + self.CAPTURE_WINDOW_S, before)

    def _slot_deadline(self):
        """انتهت مهلة Ctrl+رقم بلا V: لا لصق فتحة، ويبقى كل شيء طبيعياً."""
        self.armed = None

    def _expire_if_due(self):
        if self.armed is None:
            return
        if self.now() > self.armed[1]:
            self.armed = None

    # ── إبطال التسلح ──
    def _disarm(self):
        armed = self.armed
        self.armed = None
        if armed and armed[0] == "slot":
            try:
                armed[3].cancel()
            except Exception:
                pass


def _daemon_timer(delay, fn):
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    return timer


# ═════════════════════════════════════════════════════════════
# 3) جسر ويندوز: خطاف منخفض المستوى
# ═════════════════════════════════════════════════════════════
_user32 = _kernel32 = None
try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.GetAsyncKeyState.argtypes = [wintypes.INT]
    _user32.GetAsyncKeyState.restype = wintypes.SHORT
    _user32.SetWindowsHookExW.argtypes = [
        wintypes.INT, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD
    ]
    _user32.SetWindowsHookExW.restype = ctypes.c_void_p
    _user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, wintypes.LONG, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.CallNextHookEx.restype = ctypes.c_ssize_t
    _user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
except AttributeError:
    pass    # نظام غير ويندوز: بلا خطاف (الاختبارات تستخدم الآلة النقية فقط)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.LONG, wintypes.WPARAM, wintypes.LPARAM
)


def _key_state(vk):
    if _user32 is None:
        return False
    try:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def _ctrl_down():
    return _key_state(VK_CONTROL)


def _modifier_down():
    """Alt أو زر ويندوز مضغوط: لا نتدخل في تركيباتهم أبداً."""
    return _key_state(VK_MENU) or _key_state(VK_LWIN) or _key_state(VK_RWIN)


def start(on_capture, on_paste):
    """يثبّت الخطاف في خيط خلفي ويرجع (chord, stop_fn)."""
    chord = SlotChord(
        on_capture=on_capture,
        on_paste=on_paste,
        ctrl_down=_ctrl_down,
        modifier_down=_modifier_down,
        seq=winclip.sequence_number,
    )
    installed = []
    ready = threading.Event()

    def hook_proc(n_code, w_param, l_param):
        try:
            if n_code >= 0:
                info = ctypes.cast(
                    l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
                ).contents
                if not (info.flags & LLKHF_INJECTED):
                    vk = info.vkCode & 0xFFFF
                    down = not (info.flags & LLKHF_UP)
                    if chord.handle(vk, down) == "swallow":
                        return 1
        except Exception:
            log.exception("slots hook callback failed")
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    proc_ref = _HOOKPROC(hook_proc)   # مرجع حي يمنع جامع الأنقاض

    def pump():
        try:
            handle = _user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, proc_ref, _kernel32.GetModuleHandleW(None), 0
            )
        except Exception:
            log.exception("slots hook install failed")
            handle = None
        if not handle:
            log.error("slots hook could not be installed")
            ready.set()
            return
        installed.append(handle)
        ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass    # لا رسائل نافذة مطلوبة؛ الضخ للبقاء حيّاً فقط

    thread = threading.Thread(target=pump, name="keyconv-slots-hook", daemon=True)
    thread.start()
    ready.wait(2.0)

    def stop():
        handle = installed[0] if installed else None
        if handle:
            _user32.UnhookWindowsHookEx(handle)
            installed.clear()
        if thread.ident:
            _user32.PostThreadMessageW(thread.ident, WM_QUIT, 0, 0)

    return chord, stop
