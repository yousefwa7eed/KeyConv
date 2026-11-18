# keyconv.py — نقطة الدخول الموحّدة لملف KeyConv.exe.
#
#   KeyConv.exe            -> لوحة التحكم (بلا كونسول)
#   KeyConv.exe --service  -> الخدمة الخلفية (بلا نافذة ولا إشعار)
#
# الفصل بعلم بسيط يسمح بملف EXE واحد يقوم بالدورين، فلا يحتاج المستخدم
# إلى تثبيت بايثون ولا إلى إبقاء main.py/gui.py بجانبه.

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--service" in argv:
        import main as service
        service.run()
        return 0

    import gui
    return gui.main()


if __name__ == "__main__":
    raise SystemExit(main())
