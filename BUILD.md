# بناء KeyConv.exe

## الأمر الواحد

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

الناتج: **`dist\KeyConv.exe`** — ملف واحد مستقل، لا يحتاج تثبيت Python
ولا وجود `main.py` / `gui.py` بجانبه.

## المتطلبات (مرة واحدة)

```powershell
python -m pip install -r requirements-build.txt
```

## طريقة العمل بعد التجميد

| الأمر | النتيجة |
|---|---|
| `KeyConv.exe` | لوحة التحكم |
| `KeyConv.exe --service` | الخدمة الخلفية بلا نافذة (تستدعيها اللوحة تلقائياً) |

التشغيل التلقائي من اللوحة يسجّل الأمر `"...\KeyConv.exe" --service`،
ويُصلح أي أمر قديم تلقائياً عند فتح اللوحة.

## أين تُحفظ البيانات؟

في `%LOCALAPPDATA%\KeyConv`:

- `settings.json` — تفعيل/تعطيل الميزات
- `ui.json` — المظهر المختار (فاتح/داكن)
- `keyconv.log` — سجل الأخطاء
- `keyconv.pid` — رقم عملية الخدمة

وذلك لأن ملف EXE أحادي الملف يُفكّ إلى مجلد مؤقت يُمحى عند الإغلاق،
فلا يصلح لحفظ الإعدادات بجانبه.

## الأيقونة

الأيقونة في `assets/keyconv.ico` بكل المقاسات (16 حتى 256).
لإعادة توليدها بعد تعديل `tools/make_icon.py`:

```powershell
python tools\make_icon.py
```

## ملاحظات

- ويندوز قد يعرض تحذير SmartScreen عند أول تشغيل لملف غير موقّع رقمياً.
  هذا طبيعي؛ يمكن التوقيع بشهادة Code Signing إن لزم.
- عند نقل `KeyConv.exe` إلى مكان آخر، افتح اللوحة مرة واحدة ليُحدَّث مسار
  التشغيل التلقائي.
