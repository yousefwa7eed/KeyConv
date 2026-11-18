# layout_switch.py — تبديل لغة إدخال ويندوز إلى لغة النص بعد التحويل.
#
# الهدف: إذا حوّل المستخدم نصاً من العربية إلى الإنجليزية (أو العكس)، فمن المرجح
# أنه يريد متابعة الكتابة باللغة الجديدة. بدل أن يضغط Alt+Shift بنفسه، نرسل
# WM_INPUTLANGCHANGEREQUEST إلى النافذة النشطة فيه تغيّر لغة إدخالها فوراً.
#
# كل الدوال النقية (اختيار التخطيط من القائمة) منفصلة عن استدعاءات ويندوز
# لتكون قابلة للاختبار دون تغيير لغة الجهاز فعلاً.

import ctypes
import logging
import time
from ctypes import wintypes

import converter

WM_INPUTLANGCHANGEREQUEST = 0x0050
LANG_ARABIC = 0x01
LANG_ENGLISH = 0x09
SWITCH_VERIFY_S = 0.15     # أقصى انتظار لظهور اللغة الجديدة في نافذة المقدمة
SWITCH_POLL_S = 0.02

log = logging.getLogger("keyconv")

_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
_user32.GetKeyboardLayout.restype = ctypes.c_void_p
_user32.GetKeyboardLayoutList.argtypes = [
    ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
]
_user32.GetKeyboardLayoutList.restype = ctypes.c_int
_user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
_user32.PostMessageW.restype = wintypes.BOOL


def language_of_layout(hkl):
    """لغة تخطيط لوحة المفاتيح من مقبض HKL: 'ar' أو 'en' أو None.

    HKL يحتوي LANGID في الكلمتين السفليتين: 0x0401 عربي، 0x0409 إنجليزي،
    وكلاهما قد يأتي بأي لهجة (0x0C01، 0x0809...) لذلك نقارن اللغة الأساسية.
    """
    try:
        language_id = int(hkl) & 0xFFFF
    except (TypeError, ValueError):
        return None
    primary = language_id & 0x3FF
    if primary == LANG_ARABIC:
        return "ar"
    if primary == LANG_ENGLISH:
        return "en"
    return None


def choose_layout(layouts, language, current=None):
    """يختار HKL للغة المطلوبة من التخطيطات المثبَّتة.

    يفضّل التخطيط الحالي إن كان بنفس اللغة (فلا نبدّل بين عربي 101 وعربي 102
    بلا داعٍ)، وإلا أول تخطيط مطابق. يرجع None إذا لم تكن اللغة مثبَّتة.
    """
    if language not in ("ar", "en"):
        return None
    matches = [int(hkl) for hkl in layouts if language_of_layout(hkl) == language]
    if not matches:
        return None
    if current is not None:
        current = int(current)
        if current in matches:
            return current
    return matches[0]


def installed_layouts():
    """قائمة HKL لكل تخطيطات لوحة المفاتيح المحمّلة حالياً."""
    try:
        count = _user32.GetKeyboardLayoutList(0, None)
        if count <= 0:
            return []
        buffer = (ctypes.c_void_p * count)()
        written = _user32.GetKeyboardLayoutList(count, buffer)
        return [
            int(buffer[index]) for index in range(max(0, written))
            if buffer[index]
        ]
    except Exception:
        log.exception("could not list keyboard layouts")
        return []


def foreground_layout():
    """(نافذة المقدمة، خيطها، HKL الحالي) أو (None, 0, None)."""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None, 0, None
        thread_id = _user32.GetWindowThreadProcessId(hwnd, None)
        current = _user32.GetKeyboardLayout(thread_id) if thread_id else None
        return hwnd, int(thread_id or 0), int(current) if current else None
    except Exception:
        log.exception("could not read foreground keyboard layout")
        return None, 0, None


def switch_to_language(language):
    """يبدّل لغة إدخال النافذة النشطة ويتحقق من نجاح الطلب فعلاً."""
    if language not in ("ar", "en"):
        return False
    try:
        hwnd, _thread_id, current = foreground_layout()
        if not hwnd:
            return False
        if language_of_layout(current) == language:
            return True   # اللغة المطلوبة مفعّلة أصلاً
        target = choose_layout(installed_layouts(), language, current)
        if target is None:
            log.warning("keyboard layout for '%s' is not installed", language)
            return False
        posted = _user32.PostMessageW(
            hwnd, WM_INPUTLANGCHANGEREQUEST, 0, target
        )
        if not posted:
            return False
        # الطلب غير متزامن: نتأكد أن نافذة المقدمة التقطته قبل أن نعلن النجاح
        deadline = time.monotonic() + SWITCH_VERIFY_S
        while time.monotonic() < deadline:
            _hwnd, _tid, now = foreground_layout()
            if language_of_layout(now) == language:
                log.info("keyboard layout switched to %s", language)
                return True
            time.sleep(SWITCH_POLL_S)
        log.warning("keyboard layout switch to %s was not observed", language)
        return False
    except Exception:
        log.exception("could not switch keyboard layout")
        return False


def switch_to_text_language(text):
    """يبدّل لغة الإدخال إلى لغة النص المعطى (نتيجة التحويل)."""
    language = converter.detect_language(text)
    if language is None:
        return False
    return switch_to_language(language)
