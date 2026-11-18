# clipboard_win.py — واجهة ويندوز للكليبورد: نص، رقم تسلسلي، ولقطة/استرجاع لكل الصيغ.
#
# لماذا لا نستخدم pyperclip وحده؟
#   1) pyperclip يرمي استثناءً عند احتجاز الكليبورد من برنامج آخر (Excel/متصفح/مدير
#      كليبورد)، وهذا الاستثناء كان يتسلل إلى مستمع الكيبورد فيوقفه نهائياً وبصمت.
#   2) pyperclip يتعامل مع النص فقط، فاسترجاع الكليبورد يتلف الصور والملفات المنسوخة.
#
# هذه الوحدة: تفتح الكليبورد بمحاولات متكررة (لا انهيار عند الاحتجاز)، وتقرأ/تكتب
# النص، وتعطي رقماً تسلسلياً لكشف نجاح النسخ دون كتابة أي شيء مسبقاً، وتأخذ لقطة من
# كل الصيغ القابلة للنسخ (HGLOBAL) لتعيدها كما كانت.

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field

CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_TIFF = 6
CF_OEMTEXT = 7
CF_DIB = 8
CF_PALETTE = 9
CF_UNICODETEXT = 13
CF_ENHMETAFILE = 14
CF_HDROP = 15
CF_LOCALE = 16
CF_DIBV5 = 17
CF_OWNERDISPLAY = 0x0080

GMEM_MOVEABLE = 0x0002

OPEN_TRIES = 10
OPEN_DELAY_S = 0.02
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024

# صيغ لا يمكن نسخها كذاكرة (مقابض GDI) أو صيغ مشتقة/خاصة: نتجاهلها عند اللقطة
_SKIP_FORMATS = {
    CF_TEXT, CF_OEMTEXT,          # مشتقة تلقائياً من CF_UNICODETEXT
    CF_BITMAP, CF_METAFILEPICT,   # مقابض GDI لا تُنسخ
    CF_PALETTE, CF_ENHMETAFILE,
    CF_OWNERDISPLAY,
}


class ClipboardError(RuntimeError):
    """فشل الوصول إلى الكليبورد (محجوز، أو غير متاح)."""


@dataclass
class ClipboardSnapshot:
    """لقطة صيغ الكليبورد كما كانت قبل التعديل."""

    formats: list = field(default_factory=list)   # [(format, bytes)]
    skipped: list = field(default_factory=list)   # صيغ تعذّر حفظها

    def __bool__(self):
        return bool(self.formats)


_IS_WINDOWS = hasattr(ctypes, "windll")
_user32 = None
_kernel32 = None


def available():
    """هل طبقة الكليبورد متاحة على هذا النظام؟"""
    return _IS_WINDOWS


def _load():
    global _user32, _kernel32
    if _user32 is not None:
        return _user32, _kernel32
    if not _IS_WINDOWS:
        raise ClipboardError("clipboard layer is Windows-only")
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32

    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.OpenClipboard.restype = wintypes.BOOL
    u.CloseClipboard.argtypes = []
    u.CloseClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.argtypes = []
    u.EmptyClipboard.restype = wintypes.BOOL
    u.GetClipboardData.argtypes = [wintypes.UINT]
    u.GetClipboardData.restype = wintypes.HANDLE
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u.SetClipboardData.restype = wintypes.HANDLE
    u.EnumClipboardFormats.argtypes = [wintypes.UINT]
    u.EnumClipboardFormats.restype = wintypes.UINT
    u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    u.IsClipboardFormatAvailable.restype = wintypes.BOOL
    u.GetClipboardSequenceNumber.argtypes = []
    u.GetClipboardSequenceNumber.restype = wintypes.DWORD

    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wintypes.HANDLE
    k.GlobalFree.argtypes = [wintypes.HANDLE]
    k.GlobalFree.restype = wintypes.HANDLE
    k.GlobalLock.argtypes = [wintypes.HANDLE]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HANDLE]
    k.GlobalUnlock.restype = wintypes.BOOL
    k.GlobalSize.argtypes = [wintypes.HANDLE]
    k.GlobalSize.restype = ctypes.c_size_t

    _user32, _kernel32 = u, k
    return _user32, _kernel32


class _OpenClipboard:
    """فتح الكليبورد بمحاولات متكررة بدل الانهيار عند احتجازه من برنامج آخر."""

    def __enter__(self):
        u, k = _load()
        for _ in range(OPEN_TRIES):
            if u.OpenClipboard(None):
                return u, k
            time.sleep(OPEN_DELAY_S)
        raise ClipboardError("OpenClipboard failed (clipboard is held by another app)")

    def __exit__(self, exc_type, exc_value, tb):
        try:
            _user32.CloseClipboard()
        except Exception:
            pass
        return False


def _read_global(k, handle, size=None):
    if size is None:
        size = k.GlobalSize(handle)
    if not size:
        return b""
    pointer = k.GlobalLock(handle)
    if not pointer:
        raise ClipboardError("GlobalLock failed")
    try:
        return ctypes.string_at(pointer, size)
    finally:
        k.GlobalUnlock(handle)


def _write_global(u, k, fmt, data):
    handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise ClipboardError("GlobalAlloc failed")
    pointer = k.GlobalLock(handle)
    if not pointer:
        k.GlobalFree(handle)
        raise ClipboardError("GlobalLock failed")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        k.GlobalUnlock(handle)
    if not u.SetClipboardData(fmt, handle):
        k.GlobalFree(handle)
        raise ClipboardError("SetClipboardData failed")
    # بعد النجاح يملك النظام المقبض؛ لا نحرره.


def sequence_number():
    """رقم يتغير مع كل كتابة في الكليبورد من أي برنامج (0 إن تعذّر)."""
    try:
        return int(_load()[0].GetClipboardSequenceNumber())
    except Exception:
        return 0


def has_text():
    try:
        return bool(_load()[0].IsClipboardFormatAvailable(CF_UNICODETEXT))
    except Exception:
        return False


def get_text():
    """نص الكليبورد، أو "" إن كان فارغاً. يرمي ClipboardError إن كان محجوزاً."""
    with _OpenClipboard() as (u, _k):
        handle = u.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        raw = _read_global(_k, handle)
        text = raw.decode("utf-16-le", "replace")
        return text.split("\x00", 1)[0]


def set_text(text):
    """كتابة نص في الكليبورد (يستبدل كل الصيغ كأي عملية نسخ عادية)."""
    data = ("" if text is None else str(text)).encode("utf-16-le") + b"\x00\x00"
    with _OpenClipboard() as (u, k):
        if not u.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        _write_global(u, k, CF_UNICODETEXT, data)


def _skip_format(fmt):
    if fmt in _SKIP_FORMATS:
        return True
    if 0x0200 <= fmt <= 0x03FF:   # صيغ خاصة بالتطبيق / كائنات GDI
        return True
    if 0x0082 <= fmt <= 0x008E:   # صيغ العرض الخاصة
        return True
    return False


def snapshot():
    """لقطة من كل صيغ الكليبورد القابلة للنسخ، لاسترجاعها لاحقاً كما كانت."""
    snap = ClipboardSnapshot()
    with _OpenClipboard() as (u, k):
        fmt = u.EnumClipboardFormats(0)
        while fmt:
            if _skip_format(fmt):
                snap.skipped.append(fmt)
            else:
                handle = u.GetClipboardData(fmt)
                if handle:
                    size = k.GlobalSize(handle)
                    if 0 < size <= MAX_SNAPSHOT_BYTES:
                        try:
                            data = _read_global(k, handle, size)
                        except Exception:
                            data = b""
                        if data:
                            snap.formats.append((fmt, data))
            fmt = u.EnumClipboardFormats(fmt)
    return snap


def restore(snap):
    """إعادة لقطة كاملة إلى الكليبورد. True إن أعيدت صيغة واحدة على الأقل."""
    if not snap or not snap.formats:
        return False
    restored = False
    with _OpenClipboard() as (u, k):
        if not u.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        for fmt, data in snap.formats:
            try:
                _write_global(u, k, fmt, data)
                restored = True
            except Exception:
                continue
    return restored
