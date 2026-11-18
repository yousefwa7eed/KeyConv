# slots_hook.py — الحافظة متعددة الفتحات (9 فتحات مؤقتة).
#
# الطريقة (مواصفة المستخدم) مع بقاء المفاتيح الطبيعية طبيعية 100%:
#   Ctrl+C ثم رقم 1..9  -> النسخ العادي يجري كما هو، والرقم يحفظ الحافظة في الفتحة.
#   Ctrl+V ثم رقم 1..9  -> Ctrl+V يُلصق طبيعياً فوراً (المحتوى الحالي)، فإن وصل
#                           الرقم خلال مهلة قصيرة تراجعنا عن ذلك اللصق (Ctrl+Z)
#                           ولصقنا نص الفتحة بدله. بلا رقم = لصق طبيعي خالص.
#
# التنفيذ: خطاف لوحة مفاتيح منخفض المستوى (WH_KEYBOARD_LL) يمرّر كل الأحداث
# كما هي ويحتجز فقط رقم 1..9 أثناء "التسلح" (بعد C أو V حديثاً مع Ctrl) كي لا
# يصل إلى التطبيق كاختصار Ctrl+رقم. لا احتجاز ولا إعادة حقن لأي مفتاح آخر.
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

# أرقام الصف العلوي ولوحة الأرقام كلاهما مقبولة. الصفر يعني "آخر نسخة":
# Ctrl+V+0 يبقي اللصق الطبيعي كما هو، وCtrl+C+0 لا يحفظ شيئاً.
DIGIT_VKS = {0x30: 0, 0x60: 0}
for _i in range(1, 10):
    DIGIT_VKS[0x30 + _i] = _i        # '1'..'9'
    DIGIT_VKS[0x60 + _i] = _i        # Numpad1..9

CAPTURE_WINDOW_S = 0.8        # أقصى فرق بين Ctrl+C والرقم
PASTE_ARM_S = 0.40            # أقصى فرق بين Ctrl+V والرقم


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

    المبدأ الثابت: C وV يمرّان دائماً — التطبيق ينفّذ النسخ/اللصق الطبيعي
    بنفسه، ونحن فقط نراقب هل يتبعها رقم أم لا.
    """

    CAPTURE_WINDOW_S = CAPTURE_WINDOW_S
    PASTE_ARM_S = PASTE_ARM_S

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
        self.on_paste = on_paste         # (slot,)
        self.enabled = enabled
        self.ctrl_down = ctrl_down
        self.modifier_down = modifier_down
        self.seq = seq
        self.now = now
        self._make_timer = arm_timer or (
            lambda delay, fn: _daemon_timer(delay, fn)
        )
        # armed: ("capture", deadline, before_seq)
        #        | ("paste", deadline, timer) | None
        self.armed = None

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
        armed = self.armed
        if armed and armed[0] == "paste" and vk not in DIGIT_VKS and vk != VK_V:
            # مفتاح آخر بدل الرقم: اللصق الطبيعي جرى أصلاً وبقي مكانه
            self._disarm()
            return "pass"

        if vk == VK_V:
            if self.ctrl_down() and not self.modifier_down():
                armed = self.armed
                if armed and armed[0] == "paste":
                    # تكرار V أثناء الاحتفاظ بها (auto-repeat): كل تكرار
                    # يلصق مرة أخرى، فعدّه ليتراجع الرئيس عن كل واحدة.
                    self._arm_paste(armed[3] + 1)
                else:
                    self._arm_paste(1)
            return "pass"        # اللصق الطبيعي يجري دائماً، لا احتجاز أبداً

        if vk == VK_C:
            if self.ctrl_down() and not self.modifier_down():
                self._arm_capture()
            return "pass"

        if vk in DIGIT_VKS and armed and self.ctrl_down():
            slot = DIGIT_VKS[vk]
            kind = armed[0]
            before_seq = armed[2] if kind == "capture" else None
            presses = armed[3] if kind == "paste" else 1
            self._disarm()
            if kind == "capture":
                if slot:
                    self.on_capture(slot, before_seq)
                # الصفر مع C لا يحفظ: "آخر نسخة" تبقى في الكليبورد طبيعياً
            else:
                self.on_paste(slot, presses)   # slot 0 = اللصق الطبيعي نفسه
            return "swallow"
        return "pass"

    # ── أحداث الرفع ──
    def _on_up(self, vk):
        armed = self.armed
        if armed and vk == VK_CONTROL:
            # حُرّر Ctrl: أي رقم لاحق يُكتب طبيعياً (لا اختطاف)
            self._disarm()
        return "pass"

    # ── التسلح ──
    def _arm_paste(self, presses=1):
        self._disarm()
        timer = self._make_timer(self.PASTE_ARM_S, self._paste_deadline)
        # الـ presses: عدد ضغطات V (الأصلية + التكرار التلقائي) حتى الآن،
        # فكل واحدة تلصق مرة وتحتاج تراجعاً واحداً.
        self.armed = ("paste", self.now() + self.PASTE_ARM_S, timer, presses)

    def _arm_capture(self):
        # نقيس رقم الحافظة "قبل" أول ضغطة C: التطبيق ينسخ فوراً عند Ctrl+C،
        # أي قبل وصول الرقم، فمقارنة ما بعدها تكشف نجاح النسخ فعلاً.
        # ضغطة C ثانية أثناء التسلح لا تعيد القياس: الرقم الأصلي هو الأساس،
        # وإلا بدا النسخ "بلا تغيير" بعد أن غيّره بالفعل (فتات الفتحة).
        armed = self.armed
        before = armed[2] if armed and armed[0] == "capture" else self.seq()
        self.armed = ("capture", self.now() + self.CAPTURE_WINDOW_S, before)

    def _paste_deadline(self):
        """انتهت مهلة الرقم بلا رقم: بقي اللصق الطبيعي كما هو، لا شيء آخر."""
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
        if armed and armed[0] == "paste":
            try:
                armed[2].cancel()
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
