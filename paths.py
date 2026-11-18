# paths.py — مسارات موحّدة تعمل سواء شُغّل البرنامج من المصدر أو كملف EXE.
#
# بعد التجميد (PyInstaller onefile) يكون __file__ داخل مجلد مؤقت يُمحى عند الإغلاق،
# لذلك لا يصلح لحفظ settings.json أو keyconv.log أو keyconv.pid. نحفظها في
# %LOCALAPPDATA%\KeyConv بدلاً من ذلك، ونقرأ الأيقونة من حزمة الـ EXE نفسها.

import os
import sys

APP_NAME = "KeyConv"


def is_frozen():
    """True عندما يعمل البرنامج كملف EXE مجمّد."""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """مجلد البرنامج: جوار الملفات المصدرية، أو جوار الـ EXE بعد التجميد."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """مجلد البيانات الدائم (إعدادات/سجل/PID) — لا يضيع مع المجلد المؤقت."""
    override = os.environ.get("KEYCONV_DATA_DIR")
    if override:
        return override
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    return os.path.dirname(os.path.abspath(__file__))


def runtime_dir():
    """مجلد معلومات التشغيل المشترك بين نسخة المصدر ونسخة EXE.

    ملف PID يجب أن يراه الطرفان معاً حتى تستطيع لوحة EXE إيقاف خدمة المصدر
    والعكس؛ لذلك لا نستخدم data_dir (الذي يختلف بين الوضعين) لهذا الغرض.
    """
    override = os.environ.get("KEYCONV_DATA_DIR")
    if override:
        return override
    if is_frozen():
        return data_dir()
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def resource_path(name):
    """مسار ملف مرفق داخل الحزمة (مثل الأيقونة) في الوضعين."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
