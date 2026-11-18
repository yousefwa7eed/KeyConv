# tests/test_converter.py — اختبارات جدول التحويل نفسه (بلا أي Hook أو كليبورد)
# المرجع: تخطيط "العربية (101)" كما استُخرج من ويندوز عبر ToUnicodeEx.

from converter import AR_TO_EN, EN_TO_AR, convert_text


def test_lowercase_converts_both_ways():
    assert convert_text("مرحبا") == "lvpfh"
    assert convert_text("lvpfh") == "مرحبا"


def test_hamza_letters_are_covered():
    assert convert_text("أحمد") == "Hpl]"
    assert convert_text("إسلام") == "Ysghl"
    assert convert_text("آمن") == "Nlk"


def test_hamza_round_trip():
    assert convert_text("أ") == "H"
    assert convert_text("H") == "أ"
    assert convert_text("إ") == "Y"
    assert convert_text("آ") == "N"


def test_tashkeel_round_trip():
    text = "مَرْحَبًا"
    english = convert_text(text)
    assert english == "lQvXpQfWh"
    assert convert_text(english) == text


def test_uppercase_latin_converts_and_leaves_no_latin():
    assert convert_text("Hello") == "أثممخ"
    converted = convert_text("HELLO")
    assert "L" not in converted              # لا حرف لاتيني متبقٍ
    assert not any(ch.isascii() and ch.isalpha() for ch in converted)


def test_arabic_punctuation_and_digits_to_ascii():
    assert convert_text("مرحبا؟") == "lvpfh?"
    assert convert_text("مرحبا، عالم") == "lvpfh, uhgl"
    assert convert_text("١٢٣") == "123"
    assert convert_text("؟") == "?"


def test_lam_alef_uses_real_letters_not_presentation_form():
    assert convert_text("book") == "لاخخن"
    assert "\ufefb" not in convert_text("book")     # صيغة عرض لا تُكتب داخل الكلمة
    assert convert_text("\ufefb") == "b"            # لكن نص ملصق منها يتحوّل


def test_english_symbols_pass_through():
    assert convert_text("lvpfh!") == "مرحبا!"       # "!" ليس مفتاحاً عربياً
    assert convert_text("test@mail.com")            # لا يتعطل على رموز


def test_ties_and_unknown_text_are_untouched():
    assert convert_text("aا") == "aا"
    assert convert_text("") == ""
    assert convert_text("123 !?") == "123 !?"
    assert convert_text("مرحبا123") == "lvpfh123"


def test_table_is_reversible_for_single_char_values():
    import converter

    for ar in AR_TO_EN:
        assert not ar.isascii(), ar                # مفاتيح الاتجاه العكسي عربية فقط
    for source in (converter._EN_UNSHIFTED, converter._EN_SHIFTED):
        for en, ar in source.items():
            if len(ar) != 1:
                continue                            # لام-ألف حرفان: يتعامل معهما التحويل حرفاً حرفاً
            back = convert_text(ar)                # الحرف العربي وحده -> مفتاح ASCII واحد
            assert back.isascii() and len(back) == 1, (en, ar, back)
