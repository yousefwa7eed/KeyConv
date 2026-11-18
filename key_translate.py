# key_translate.py — ترجمة المفتاح إلى نصه الفعلي لحظة الحدث حسب لغة النافذة النشطة.
#
# لماذا لا نكتفي بـ key.char من pynput؟
#   pynput يبني جدول الترجمة مرة واحدة عند بدء الخدمة (GetKeyboardLayout لخيطه)،
#   ورسالة WM_INPUTLANGCHANGE لا تصله لأن حلقة رسائله بلا نافذة. فإذا كتب المستخدم
#   بالتخطيط العربي بعد بدء الخدمة، يبقى pynput يترجم الأزرار بالإنجليزية -> يُسجَّل
#   في المخزن نص مختلف عن المعروض على الشاشة، فيحوّل الاتجاه الخاطئ (المشكلة 3).
#
# الحل: ToUnicodeEx وقت كل حدث مع HKL نافذة المقدمة نفسها. البايتات الأخرى في
# الحالة (Shift/Caps) من GetAsyncKeyState، فيخرج النص كما يراه التطبيق المستهدف
# (بما فيه "لا" الناتجة عن زر b، والهمزات، والتشكيل).
# علم 0x4 يمنع تعديل حالة لوحة المفاتيح العامة، فمجرد القراءة لا تُفسد الكتابة.

import ctypes
from ctypes import wintypes

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_CAPITAL = 0x14

MAPVK_VK_TO_VSC = 0
TOUNICODE_DONT_CHANGE_STATE = 0x4

_user32 = None
try:
    _user32 = ctypes.windll.user32
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    _user32.GetKeyboardLayout.restype = ctypes.c_void_p
    _user32.GetAsyncKeyState.argtypes = [wintypes.INT]
    _user32.GetAsyncKeyState.restype = wintypes.SHORT
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _user32.ToUnicodeEx.argtypes = [
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        wintypes.UINT,
        ctypes.c_void_p,
    ]
    _user32.ToUnicodeEx.restype = ctypes.c_int
except AttributeError:
    _user32 = None      # نظام غير ويندوز


def _down(vk):
    try:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def foreground_hkl():
    """مقبض تخطيط نافذة المقدمة، أو None إن تعذّرت قراءته."""
    if _user32 is None:
        return None
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        thread_id = _user32.GetWindowThreadProcessId(hwnd, None)
        if not thread_id:
            return None
        hkl = _user32.GetKeyboardLayout(thread_id)
        return int(hkl) if hkl else None
    except Exception:
        return None


def _modifier_state():
    """حالة لوحة المفاتيح التي يحتاجها ToUnicodeEx: Shift/Ctrl/Alt/Caps."""
    state = (ctypes.c_ubyte * 256)()
    if _down(VK_SHIFT):
        state[VK_SHIFT] = 0x80
    if _down(VK_CONTROL):
        state[VK_CONTROL] = 0x80
    if _down(VK_MENU):
        state[VK_MENU] = 0x80
    try:
        if _user32.GetAsyncKeyState(VK_CAPITAL) & 1:
            state[VK_CAPITAL] = 0x01
    except Exception:
        pass
    return state


def text_for_key(vk, scan=None):
    """نص المفتاح الفعلي حسب لغة النافذة النشطة.

    يرجع:
      * نصاً (قد يكون حرفين مثل "لا")
      * ""  لمفتاح ميت (dead key)
      * None إن تعذّرت الترجمة (نرجع عندها إلى key.char في pynput)
    """
    if not vk or _user32 is None:
        return None
    hkl = foreground_hkl()
    if hkl is None:
        return None
    try:
        if not scan:
            scan = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        buffer = ctypes.create_unicode_buffer(8)
        state = _modifier_state()
        count = _user32.ToUnicodeEx(
            vk, scan, ctypes.byref(state), ctypes.byref(buffer), len(buffer),
            TOUNICODE_DONT_CHANGE_STATE, hkl,
        )
    except Exception:
        return None
    if count < 0:
        return ""          # مفتاح ميت: لا يُكتب وحده
    if count == 0:
        return None
    return "".join(buffer[index] for index in range(min(count, len(buffer))))
