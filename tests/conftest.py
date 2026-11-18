# tests/conftest.py — إعداد مشترك: سجل الاختبارات منفصل تماماً عن سجل التطبيق الحقيقي.
# يُنفَّذ قبل استيراد وحدات الاختبار، أي قبل أن ينشئ main.py معالج السجل.

import os
import tempfile

import pytest

os.environ.setdefault(
    "KEYCONV_LOG_PATH", os.path.join(tempfile.gettempdir(), "keyconv-tests.log")
)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """النوم في الخدمة لا يغيّر القرارات: نستبدله بلا نوم لاختبارات سريعة وحتمية."""
    import main

    monkeypatch.setattr(main, "sleep_fn", lambda _seconds: None)
