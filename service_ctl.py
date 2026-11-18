# service_ctl.py — التحكم في الخدمة من خارجها: هل تعمل؟ شغّلها. أوقفها.
#
# "تعمل" تُقرأ من شيئين معاً:
#   * وجود الـ Mutex الذي تحمله الخدمة (Local\KeyConv.SingleInstance) — دقيق وفوري.
#   * ملف keyconv.pid الذي تكتبه الخدمة عند الإقلاع — يلزم لمعرفة رقم العملية.
# الملف قد يكون قديماً، لذلك لا نثق به وحده: نتحقق أن العملية حيّة وأنها بايثون.

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import paths

MUTEX_NAME = "Local\\KeyConv.SingleInstance"
STOP_EVENT_NAME = "Local\\KeyConv.Stop"
APP_DIR = paths.app_dir()
MAIN_SCRIPT = os.path.join(APP_DIR, "main.py")

SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

PYTHON_IMAGES = ("pythonw.exe", "python.exe", "keyconv.exe")

_kernel32 = ctypes.windll.kernel32
_kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def pid_file():
    return os.environ.get(
        "KEYCONV_PID_PATH", os.path.join(paths.runtime_dir(), "keyconv.pid")
    )


# ── تُستدعى من الخدمة نفسها ─────────────────────────────────────
def write_pid_file():
    try:
        directory = os.path.dirname(pid_file())
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(pid_file(), "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return True
    except OSError:
        return False


def remove_pid_file():
    try:
        os.remove(pid_file())
        return True
    except OSError:
        return False


# ── استعلام الحالة ──────────────────────────────────────────────
def _mutex_running():
    handle = _kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if not handle:
        return False
    _kernel32.CloseHandle(handle)
    return True


def _alive(pid):
    if not pid or pid <= 0:
        return False
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def _image_name(pid):
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if not _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return ""
        return os.path.basename(buffer.value).lower()
    finally:
        _kernel32.CloseHandle(handle)


def _is_service_image(name):
    """اسم صورة العملية المسموح بها: بايثون في المصدر، أو اسم الـ EXE بعد التجميد."""
    if name in PYTHON_IMAGES:
        return True
    if getattr(sys, "frozen", False):
        return name == os.path.basename(sys.executable).lower()
    return False


def _signal_stop_event():
    """يوقظ الخدمة عبر Event مسمّى: لا يحتاج PID ولا صلاحيات إنهاء عملية."""
    handle = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, STOP_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(_kernel32.SetEvent(handle))
    finally:
        _kernel32.CloseHandle(handle)


def running_pid():
    """رقم عملية الخدمة الحيّة، أو None. لا يثق بملف PID وحده."""
    try:
        with open(pid_file(), encoding="utf-8") as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    if _alive(pid) and _is_service_image(_image_name(pid)):
        return pid
    return None


def is_running():
    return _mutex_running() or running_pid() is not None


# ── التشغيل والإيقاف ──────────────────────────────────────────
def _windowless_python():
    exe = sys.executable or "python.exe"
    if getattr(sys, "frozen", False):
        return exe
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else exe


def _service_command():
    """أمر تشغيل الخدمة: نفس الـ EXE بعلم --service، أو pythonw + main.py في المصدر."""
    exe = _windowless_python()
    if getattr(sys, "frozen", False):
        return [exe, "--service"]
    return [exe, MAIN_SCRIPT]


def start(timeout=10.0):
    """يشغّل الخدمة بلا نافذة. يرجع (نجاح, رسالة عربية)."""
    if is_running():
        return True, "الخدمة تعمل بالفعل."
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    try:
        subprocess.Popen(
            _service_command(),
            cwd=APP_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as exc:
        return False, f"تعذّر تشغيل الخدمة: {exc}"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running():
            return True, "تم تشغيل الخدمة."
        time.sleep(0.15)
    return False, "بدأت العملية لكن الخدمة لم تستجب — راجع keyconv.log."


def stop(timeout=6.0):
    """يوقف الخدمة. يرجع (نجاح, رسالة عربية).

    الطريق الأساسي: إشارة Event مسمّى تلتقطها الخدمة وتخرج بنفسها — تعمل
    حتى لو ضاع ملف PID أو اختلف مساره. ثم نستخدم إنهاء العملية كخطة بديلة.
    """
    if not is_running():
        remove_pid_file()
        return True, "الخدمة متوقفة أصلاً."

    # إن لم يوجد Event مسمّى (خدمة قديمة أو مُغلقة) لا داعي لانتظار طويل:
    # ننتقل مباشرة إلى إنهاء العملية عبر PID.
    signalled = _signal_stop_event()
    deadline = time.monotonic() + (timeout if signalled else 0)
    while time.monotonic() < deadline:
        if not is_running():
            remove_pid_file()
            return True, "تم إيقاف الخدمة."
        time.sleep(0.1)

    pid = running_pid()
    if pid is None:
        if not is_running():
            remove_pid_file()
            return True, "تم إيقاف الخدمة."
        return False, "الخدمة تعمل لكنها لم تستجب لطلب الإيقاف — أعد تشغيل الجهاز أو أنهِ العملية يدوياً."

    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False, "لا أملك صلاحية إيقاف العملية — قد تكون تعمل بصلاحيات مدير."
    try:
        if not _kernel32.TerminateProcess(handle, 1):
            return False, "فشل إيقاف العملية."
    finally:
        _kernel32.CloseHandle(handle)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running():
            remove_pid_file()
            return True, "تم إيقاف الخدمة."
        time.sleep(0.1)
    return False, "أُرسل طلب الإيقاف لكن العملية لم تنتهِ بعد."
