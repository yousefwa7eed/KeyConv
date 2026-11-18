# autostart.py — تشغيل KeyConv تلقائياً فور بدء تشغيل ويندوز، بلا نافذة وبلا إشعار.
#
# يستخدم مفتاح Run الخاص بالمستخدم (HKCU) فلا يحتاج صلاحيات مدير ولا نافذة UAC.
# يشغّل pythonw.exe حتى لا تظهر أي نافذة كونسول عند الإقلاع.
#
# الاستخدام:
#   python autostart.py install     تثبيت التشغيل التلقائي (آمن ومتكرر)
#   python autostart.py status      عرض الحالة الحالية
#   python autostart.py uninstall   إزالة التشغيل التلقائي

import os
import sys

import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "KeyboardConverter"

APP_DIR = paths.app_dir()
MAIN_SCRIPT = os.path.join(APP_DIR, "main.py")


def _windowless_python():
    """مسار pythonw.exe (بلا كونسول) المرافق للمفسّر الحالي."""
    exe = sys.executable or "python.exe"
    if getattr(sys, "frozen", False):
        return exe
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else exe


def build_command():
    """الأمر الذي يسجّل في الريجستري، بمسارات مُقتبسة دائماً.

    بعد التجميد نشغّل الخدمة مباشرة (--service) حتى لا تظهر لوحة التحكم عند الإقلاع.
    """
    exe = _windowless_python()
    if getattr(sys, "frozen", False):
        return f'"{exe}" --service'
    return f'"{exe}" "{MAIN_SCRIPT}"'


def _winreg():
    try:
        import winreg
    except ImportError as exc:  # نظام غير ويندوز
        raise RuntimeError("التشغيل التلقائي مدعوم على ويندوز فقط") from exc
    return winreg


def install():
    winreg = _winreg()
    command = build_command()
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    return command


def uninstall():
    """إزالة التسجيل؛ True إن كان موجوداً وحُذف."""
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def status():
    """قيمة التشغيل التلقائي المسجّلة حالياً أو None."""
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except FileNotFoundError:
        return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else "status"
    if action == "install":
        command = install()
        print("تم تثبيت التشغيل التلقائي:", command)
        return 0
    if action == "uninstall":
        removed = uninstall()
        print("تمت إزالة التشغيل التلقائي" if removed else "لم يكن مثبتاً")
        return 0
    if action == "status":
        current = status()
        print("مُثبَّت:", current if current else "لا")
        return 0
    print("الاستخدام: python autostart.py [install|status|uninstall]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
