# ui_prefs.py — تفضيلات شكل الواجهة (المظهر)، منفصلة عن settings.json.
#
# settings.json تراقبه الخدمة وتُعيد كتابته عند كل تبديل ميزة، فلا يصلح لحفظ
# تفضيل شكلي. نحفظ هنا في ui.json، وإن لم يوجد نتبع وضع ويندوز (فاتح/داكن).

import json
import os
import tempfile

import paths


def path():
    """مسار ملف تفضيلات الواجهة (قابل للتغيير بالبيئة، لسهولة الاختبار)."""
    return os.environ.get(
        "KEYCONV_UI_PREFS", os.path.join(paths.data_dir(), "ui.json")
    )


def system_prefers_dark():
    """وضع التطبيقات في ويندوز: True إذا كان داكن."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False


def load_dark():
    """المظهر المحفوظ، أو وضع ويندوز عند أول تشغيل."""
    try:
        with open(path(), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return system_prefers_dark()
    if isinstance(raw, dict) and isinstance(raw.get("dark"), bool):
        return raw["dark"]
    return system_prefers_dark()


def save_dark(dark):
    """كتابة ذرّية: ملف مؤقت ثم استبدال، فلا يُقرأ ملف نصف مكتوب."""
    file_path = path()
    directory = os.path.dirname(os.path.abspath(file_path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".ui-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"dark": bool(dark)}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, file_path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
