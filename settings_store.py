# settings_store.py — إعدادات الميزات التي تتحكم بها الواجهة، محفوظة في ملف JSON صغير.
#
# الملف هو قناة التواصل بين الواجهة (gui.py) والخدمة (main.py): الواجهة تكتب،
# والخدمة تراقب وقت التعديل وتُطبّق فوراً بلا إعادة تشغيل.
# أي ملف تالف أو قيمة غريبة -> العودة إلى الافتراضي (الميزة مُفعّلة).

import json
import os
import tempfile
import time

import paths

APP_DIR = paths.app_dir()

DEFAULTS = {
    # الميزة الأساسية: Shift × 2 (تحديد، أو آخر كلمة عند عدم وجود تحديد)
    "double_shift": True,
    # تبديل لغة الكيبورد بعد التحويل: مُعطَّل افتراضياً (قرار المستخدم)
    "switch_layout": False,
    # الحافظة متعددة الفتحات (Ctrl+C+رقم / Ctrl+V+رقم): مُعطَّلة افتراضياً
    "slots_enabled": False,
}


def path(target=None):
    """مسار ملف الإعدادات (قابل للتغيير بالبيئة، لسهولة الاختبار)."""
    if target:
        return target
    return os.environ.get(
        "KEYCONV_SETTINGS", os.path.join(paths.data_dir(), "settings.json")
    )


def load(target=None):
    """الإعدادات الحالية مدمجة مع الافتراضي — لا ترفع استثناءً أبداً."""
    file_path = path(target)
    try:
        with open(file_path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(raw, dict):
        return dict(DEFAULTS)

    values = dict(DEFAULTS)
    for key in DEFAULTS:
        candidate = raw.get(key)
        if isinstance(candidate, bool):
            values[key] = candidate
    return values


def next_revision(target=None):
    """رقم إصدار متزايد: كشف التغيير لا يعتمد على دقة وقت الملف (15ms على ويندوز)."""
    previous = 0
    try:
        with open(path(target), encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict) and isinstance(raw.get("revision"), int):
            previous = raw["revision"]
    except (OSError, ValueError):
        previous = 0
    return max(previous + 1, time.time_ns())


def save(values, target=None):
    """كتابة ذرّية: ملف مؤقت ثم استبدال، فلا تقرأ الخدمة ملفاً نصف مكتوب."""
    file_path = path(target)
    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        if isinstance(values.get(key), bool):
            merged[key] = values[key]
    merged["revision"] = next_revision(file_path)

    directory = os.path.dirname(os.path.abspath(file_path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, file_path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    return merged


def fingerprint(target=None):
    """بصمة تغيير: رقم الإصدار + وقت التعديل + الحجم (تُهمَل الحقول غير المعروفة عند التحميل)."""
    file_path = path(target)
    try:
        with open(file_path, encoding="utf-8") as handle:
            raw = json.load(handle)
        revision = raw.get("revision") if isinstance(raw, dict) else None
        return (
            revision if isinstance(revision, int) else 0,
            os.path.getmtime(file_path),
            os.path.getsize(file_path),
        )
    except (OSError, ValueError):
        return (0, 0.0, 0)


def mtime(target=None):
    """وقت تعديل الملف، أو None إن لم يوجد بعد."""
    try:
        return os.path.getmtime(path(target))
    except OSError:
        return None
