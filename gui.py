# gui.py — لوحة التحكم: ثلاثة مفاتيح فقط + إيقاف/تشغيل الخدمة.
#
# تُفتح عند الطلب فقط:  pythonw gui.py   (أو python gui.py للتشخيص بكونسول)
# الخدمة نفسها تبقى بلا واجهة ولا إشعارات: اللوحة تكتب settings.json والخدمة تراقبه،
# والإيقاف/التشغيل يتم على عملية pythonw المنفصلة عبر service_ctl.
#
# المظهر: فاتح/داكن بتبديل فوري من أعلى النافذة. الاختيار يُحفظ في ui.json،
# وعند أول تشغيل نتبع وضع ويندوز (AppsUseLightTheme) بدل فرض مظهر معيّن.
#
# اللمبة: خضراء تتنفّس ببطء عند عمل الخدمة، وحمراء بنبضة مزدوجة أسرع عند توقّفها.

import ctypes
import math
import sys
import tkinter as tk
import tkinter.font as tkfont

import autostart
import paths
import service_ctl
import settings_store
import slots_hook
import ui_prefs

FEATURES = (
    ("double_shift", "Shift × 2",
     "تحويل النص المحدَّد إن وُجد، وإلا آخر كلمة (عربي ↔ إنجليزي)"),
)

THEMES = {
    "light": {
        "bg": "#f1f2f4",
        "card": "#ffffff",
        "border": "#e2e4e9",
        "text": "#1c1f24",
        "muted": "#6b7280",
        "green": "#10b981",
        "red": "#ef4444",
        "grey": "#9aa1ab",
        "button_bg": "#e8eaee",
        "button_hover": "#dcdfe4",
        "error": "#b91c1c",
    },
    "dark": {
        "bg": "#0f1115",
        "card": "#171a20",
        "border": "#2a3039",
        "text": "#e7e9ee",
        "muted": "#9aa3b2",
        "green": "#34d399",
        "red": "#f87171",
        "grey": "#5b6472",
        "button_bg": "#232833",
        "button_hover": "#2d3442",
        "error": "#f87171",
    },
}


def _enable_dpi_awareness():
    """نص حادّ على الشاشات عالية الدقة بدل نافذة ضبابية."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _mix(color_a, color_b, amount):
    """مزج لونين بنسبة amount (0 = الأول، 1 = الثاني) لمحاكاة الشفافية."""
    amount = max(0.0, min(1.0, amount))
    a = tuple(int(color_a[index:index + 2], 16) for index in (1, 3, 5))
    b = tuple(int(color_b[index:index + 2], 16) for index in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(
        *(round(x + (y - x) * amount) for x, y in zip(a, b))
    )


def _rounded_points(x1, y1, x2, y2, radius, steps=8):
    """نقاط مستطيل بزوايا دائرية حقيقية (أقواس ربع دائرة، لا منحنيات Tk الناعمة).

    smooth=True في Tk يسطّح الزوايا كثيراً فيظهر نصف القطر أصغر بكثير من المطلوب،
    لذلك نبني القوس صراحةً فيبقى الانحناء واضحاً وخفيفاً في الوقت نفسه.
    """
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = []
    corners = (
        (x2 - radius, y1 + radius, -90, 0),   # أعلى اليمين
        (x2 - radius, y2 - radius, 0, 90),    # أسفل اليمين
        (x1 + radius, y2 - radius, 90, 180),  # أسفل اليسار
        (x1 + radius, y1 + radius, 180, 270), # أعلى اليسار
    )
    for cx, cy, start, end in corners:
        for index in range(steps + 1):
            angle = math.radians(start + (end - start) * index / steps)
            points.extend((
                cx + radius * math.cos(angle),
                cy + radius * math.sin(angle),
            ))
    return points


def _draw_rounded(canvas, x1, y1, x2, y2, radius, **kwargs):
    return canvas.create_polygon(
        _rounded_points(x1, y1, x2, y2, radius), **kwargs
    )


class RoundedCard(tk.Canvas):
    """بطاقة بخلفية ذات زوايا دائرية خفيفة، والمحتوى يُضاف إلى card.body."""

    def __init__(self, parent, bg, fill, border, radius=12, padding=13):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0, height=10)
        self.fill = fill
        self.border = border
        self.radius = radius
        self.padding = padding
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(
            padding, padding, anchor="nw", window=self.body
        )
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        self.delete("shape")
        if event.width > 4 and event.height > 4:
            _draw_rounded(
                self, 1, 1, event.width - 1, event.height - 1, self.radius,
                fill=self.fill, outline=self.border, width=1, tags="shape",
            )
            self.tag_lower("shape")
        self.itemconfigure(
            self._window, width=max(1, event.width - 2 * self.padding)
        )

    def fit_height(self):
        """يضبط ارتفاع اللوحة على ارتفاع المحتوى الفعلي + الحشو."""
        self.update_idletasks()
        height = self.body.winfo_reqheight() + 2 * self.padding
        self.configure(height=height)


class RoundedButton(tk.Canvas):
    """زر مسطّح بزوايا دائرية مع تأثير مرور وحالة تركيز بسيطة."""

    def __init__(self, parent, text, command, *, bg, fill, hover, fg, font,
                 padx=16, pady=7, radius=10):
        self._font = tkfont.Font(font=font)
        self._text = text
        self._command = command
        self._fill = fill
        self._hover = hover
        self._fg = fg
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._pressed = False
        self._focused = False
        self._current = fill
        self._width = self._font.measure(text) + 2 * padx
        self._height = self._font.metrics("linespace") + 2 * pady

        super().__init__(
            parent, width=self._width, height=self._height, bg=bg, bd=0,
            highlightthickness=0, cursor="hand2", takefocus=True,
        )
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self.bind("<Return>", lambda _event: self._invoke())
        self.bind("<space>", lambda _event: self._invoke())

    @property
    def text(self):
        return self._text

    def set_text(self, text):
        if text == self._text:
            return
        self._text = text
        self._width = self._font.measure(text) + 2 * self._padx
        self.configure(width=self._width)
        self._draw()

    def _draw(self):
        self.delete("all")
        outline = self._fg if self._focused else ""
        _draw_rounded(
            self, 1, 1, self._width - 1, self._height - 1, self._radius,
            fill=self._current, outline=outline, width=1,
        )
        self.create_text(
            self._width / 2, self._height / 2, text=self._text,
            fill=self._fg, font=self._font,
        )

    def _invoke(self):
        if self._command:
            self._command()

    def _on_enter(self, _event):
        self._current = self._hover
        self._draw()

    def _on_leave(self, _event):
        self._current = self._fill
        self._pressed = False
        self._draw()

    def _on_press(self, _event):
        self._pressed = True

    def _on_release(self, event):
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        self._pressed = False
        if inside:
            self._invoke()

    def _on_focus(self, _event):
        self._focused = True
        self._draw()

    def _on_blur(self, _event):
        self._focused = False
        self._draw()


class Panel:
    def __init__(self, root):
        self.root = root
        self.dark = ui_prefs.load_dark()
        # المتغيّرات تُنشأ مرة واحدة فتبقى حالتها عند إعادة بناء الواجهة لتبديل المظهر
        self.vars = {key: tk.BooleanVar(value=True) for key, _t, _d in FEATURES}
        self.layout_var = tk.BooleanVar(value=False)
        self.slots_var = tk.BooleanVar(value=False)
        self._sync_autostart()
        self.autostart_var = tk.BooleanVar(value=self._autostart_state())
        self._note_job = None
        self._light_phase = 0.0
        self._service_running = False

        root.title("KeyConv — لوحة التحكم")
        root.resizable(False, False)
        root.geometry("540x597")

        self._build_all()
        self._load_settings()
        self._start_refresh_loop()
        self._animate_light()

    # ── البناء ─────────────────────────────────────────────────
    def _build_all(self):
        self.C = THEMES["dark"] if self.dark else THEMES["light"]
        self.root.configure(bg=self.C["bg"])
        for child in self.root.winfo_children():
            child.destroy()

        self.outer = tk.Frame(self.root, bg=self.C["bg"], padx=20, pady=16)
        self.outer.pack(fill="both", expand=True)

        self._build_header()
        self._build_features(self.outer)
        self._build_service(self.outer)
        self._build_footer(self.outer)
        self._apply_titlebar()

    def _build_header(self):
        C = self.C
        header = tk.Frame(self.outer, bg=C["bg"])
        header.pack(fill="x")

        label = "الوضع الفاتح" if self.dark else "الوضع الداكن"
        self.theme_button = RoundedButton(
            header, label, self._toggle_theme,
            bg=C["bg"], fill=C["button_bg"], hover=C["button_hover"],
            fg=C["muted"], font=("Segoe UI", 9), padx=12, pady=5, radius=9,
        )
        self.theme_button.pack(side="left", anchor="n")

        titles = tk.Frame(header, bg=C["bg"])
        titles.pack(side="right", fill="x", expand=True, padx=(12, 0))
        tk.Label(
            titles, text="KeyConv", font=("Segoe UI", 18, "bold"),
            bg=C["bg"], fg=C["text"], anchor="e",
        ).pack(fill="x")
        tk.Label(
            titles, text="تحويل الكتابة بين التخطيط العربي والإنجليزي",
            font=("Segoe UI", 9), bg=C["bg"], fg=C["muted"], anchor="e",
        ).pack(fill="x", pady=(0, 12))

    def _card(self, parent):
        card = RoundedCard(
            parent, bg=self.C["bg"], fill=self.C["card"],
            border=self.C["border"], radius=12, padding=13,
        )
        card.pack(fill="x", pady=(0, 10))
        return card

    def _switch_row(self, card, var, title, desc, command):
        C = self.C
        row = tk.Frame(card, bg=C["card"])
        row.pack(fill="x", pady=5)

        check = tk.Checkbutton(
            row, variable=var, command=command,
            bg=C["card"], fg=C["text"], activebackground=C["card"],
            activeforeground=C["text"], selectcolor=C["card"],
            bd=0, highlightthickness=0, cursor="hand2", takefocus=True,
        )
        check.pack(side="right")

        text = tk.Frame(row, bg=C["card"])
        text.pack(side="right", fill="x", expand=True, padx=(0, 8))
        tk.Label(
            text, text=title, font=("Segoe UI", 10, "bold"),
            bg=C["card"], fg=C["text"], anchor="e",
        ).pack(fill="x")
        tk.Label(
            text, text=desc, font=("Segoe UI", 9),
            bg=C["card"], fg=C["muted"], anchor="e",
        ).pack(fill="x")

    def _build_features(self, outer):
        card = self._card(outer)
        for key, title, desc in FEATURES:
            self._switch_row(
                card.body, self.vars[key], title, desc,
                lambda k=key: self._on_feature(k),
            )

        self._switch_row(
            card.body, self.layout_var, "تغيير لغة الكيبورد",
            "بعد التحويل، بدّل لغة الإدخال إلى لغة النتيجة",
            self._on_layout,
        )

        divider0 = tk.Frame(card.body, bg=self.C["border"], height=1)
        divider0.pack(fill="x", pady=6)

        self._switch_row(
            card.body, self.slots_var, "الحافظة متعددة الفتحات",
            "Ctrl+C+رقم للنسخ إلى فتحة، Ctrl+V لآخر نسخة، Ctrl+رقم+V للصق فتحة",
            self._on_slots,
        )
        self._slots_viewer_row(card.body)

        divider = tk.Frame(card.body, bg=self.C["border"], height=1)
        divider.pack(fill="x", pady=6)

        self._switch_row(
            card.body, self.autostart_var, "التشغيل التلقائي",
            "يبدأ مع ويندوز بلا نافذة ولا إشعار",
            self._on_autostart,
        )
        card.fit_height()

    def _slots_viewer_row(self, parent):
        """سطر مساعد: زر صغير يعرض محتوى الفتحات التسعة الحالية."""
        C = self.C
        row = tk.Frame(parent, bg=C["card"])
        row.pack(fill="x", pady=(0, 3))
        self.slots_button = RoundedButton(
            row, "عرض الفتحات", self._open_slots_viewer,
            bg=C["card"], fill=C["button_bg"], hover=C["button_hover"],
            fg=C["text"], font=("Segoe UI", 8),
            padx=10, pady=3, radius=8,
        )
        self.slots_button.pack(side="right", padx=(0, 26))

    def _open_slots_viewer(self):
        """نافذة صغيرة تعرض نصوص الفتحات التسعة وتحدّث نفسها تلقائياً."""
        C = self.C
        try:
            win = tk.Toplevel(self.root)
        except tk.TclError:
            return
        win.title("KeyConv — الفتحات")
        win.configure(bg=C["card"])
        win.resizable(False, False)
        win.attributes("-topmost", True)

        tk.Label(
            win,
            text="نصوص الفتحات الحالية (مؤقتة — تُمسح عند إعادة تشغيل الخدمة)",
            font=("Segoe UI", 8), bg=C["card"], fg=C["muted"],
        ).pack(fill="x", padx=14, pady=(10, 6))

        labels = []
        for n in range(1, 10):
            line = tk.Frame(win, bg=C["card"])
            line.pack(fill="x", padx=14, pady=1)
            tk.Label(
                line, text=f"{n}", font=("Segoe UI", 9, "bold"),
                bg=C["card"], fg=C["text"], width=2,
            ).pack(side="right")
            label = tk.Label(
                line, text="—", font=("Segoe UI", 9),
                bg=C["card"], fg=C["muted"], anchor="e",
                wraplength=380, justify="right",
            )
            label.pack(side="right", fill="x", expand=True)
            labels.append(label)

        def refresh():
            if not win.winfo_exists():
                return
            data = slots_hook.read_slots_file()
            for n, label in enumerate(labels, start=1):
                text = data.get(n, "")
                shown = text.replace("\n", " ⏎ ")
                if len(shown) > 70:
                    shown = shown[:70] + "…"
                label.config(text=shown if shown else "—")
            win.after(1000, refresh)

        refresh()
        win.geometry("+%d+%d" % (
            self.root.winfo_rootx() + 40, self.root.winfo_rooty() + 40
        ))

    def _on_slots(self):
        wanted = bool(self.slots_var.get())
        if not self._save_feature("slots_enabled", wanted):
            self.slots_var.set(not wanted)
            return
        if wanted:
            self._flash("الحافظة متعددة الفتحات تعمل الآن (تُطبَّق خلال ثانية).")
        else:
            self._flash("الحافظة متعددة الفتحات متوقفة.")

    def _build_service(self, outer):
        C = self.C
        card = self._card(outer)

        top = tk.Frame(card.body, bg=C["card"])
        top.pack(fill="x")
        tk.Label(
            top, text="حالة الخدمة", font=("Segoe UI", 9),
            bg=C["card"], fg=C["muted"], anchor="w",
        ).pack(side="left")

        self.light = tk.Canvas(
            top, width=22, height=22, bg=C["card"],
            bd=0, highlightthickness=0,
        )
        self.light.pack(side="right")
        self.status = tk.Label(
            top, text="…", font=("Segoe UI", 10, "bold"),
            bg=C["card"], fg=C["text"], anchor="e",
        )
        self.status.pack(side="right", padx=6)

        self.button = RoundedButton(
            card.body, "تشغيل الخدمة", self._toggle_service,
            bg=C["card"], fill=C["button_bg"], hover=C["button_hover"],
            fg=C["text"], font=("Segoe UI", 9, "bold"),
            padx=16, pady=6, radius=9,
        )
        self.button.pack(side="right", pady=(10, 0))
        card.fit_height()

    def _build_footer(self, outer):
        C = self.C
        self.note = tk.Label(
            outer, text="", font=("Segoe UI", 9), bg=C["bg"], fg=C["muted"],
            anchor="e", justify="right", wraplength=485,
        )
        self.note.pack(fill="x", pady=(2, 0))
        tk.Label(
            outer, text="التغييرات تُطبَّق فوراً على الخدمة، دون إعادة تشغيل.",
            font=("Segoe UI", 8), bg=C["bg"], fg=C["muted"], anchor="e",
        ).pack(fill="x", pady=(6, 0))

    def _apply_titlebar(self):
        """يجعل شريط عنوان النافذة داكناً أيضاً (ويندوز 11 / 10 الحديث)."""
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            value = ctypes.c_int(1 if self.dark else 0)
            for attribute in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
        except Exception:
            pass

    # ── الحالة ─────────────────────────────────────────────────
    def _sync_autostart(self):
        """يصلح أمر تشغيل تلقائي قديماً (نسخة مصدر أو مسار سابق) ليطابق نسختنا الحالية."""
        try:
            current = autostart.status()
            if current and current != autostart.build_command():
                autostart.install()
        except Exception:
            pass

    def _autostart_state(self):
        try:
            return bool(autostart.status())
        except Exception:
            return False

    def _load_settings(self):
        values = settings_store.load()
        for key, _title, _desc in FEATURES:
            self.vars[key].set(values[key])
        self.layout_var.set(values["switch_layout"])
        self.slots_var.set(values.get("slots_enabled", False))

    def _start_refresh_loop(self):
        self._refresh()
        self.root.after(1000, self._start_refresh_loop)

    def _refresh(self):
        running = service_ctl.is_running()
        pid = service_ctl.running_pid() if running else None
        self._service_running = running
        if running:
            self.status.config(text="تعمل" + (f"  (PID {pid})" if pid else ""))
            self.button.set_text("إيقاف الخدمة")
        else:
            self.status.config(text="متوقفة")
            self.button.set_text("تشغيل الخدمة")

        installed = self._autostart_state()
        if installed != bool(self.autostart_var.get()):
            self.autostart_var.set(installed)

    def _animate_light(self):
        self._light_phase += 0.05
        light = getattr(self, "light", None)
        try:
            alive = light is not None and light.winfo_exists()
        except tk.TclError:
            alive = False

        if alive:
            C = self.C
            light.delete("all")
            if self._service_running:
                # تنفّس أخضر ناعم: هالة تتسع وتضيق مع سطوع خفيف
                wave = (math.sin(self._light_phase * math.pi) + 1) / 2
                halo = 5.5 + 4.0 * wave
                halo_color = _mix(C["card"], C["green"], 0.08 + 0.28 * wave)
                core_color = _mix(C["card"], C["green"], 0.60 + 0.40 * wave)
            else:
                # أحمر بنبضة مزدوجة سريعة ثم سكون قصير — إحساس مختلف عن التنفّس
                t = self._light_phase % 2.0
                if t < 0.16:
                    pulse = t / 0.16
                elif t < 0.34:
                    pulse = 1 - (t - 0.16) / 0.18
                elif t < 0.60:
                    pulse = (t - 0.34) / 0.26
                elif t < 0.78:
                    pulse = 1 - (t - 0.60) / 0.18
                else:
                    pulse = 0.0
                halo = 5.0 + 4.5 * pulse
                halo_color = _mix(C["card"], C["red"], 0.05 + 0.30 * pulse)
                core_color = _mix(C["card"], C["red"], 0.35 + 0.65 * pulse)

            center = 11
            light.create_oval(
                center - halo, center - halo, center + halo, center + halo,
                fill=halo_color, outline="",
            )
            light.create_oval(
                center - 3.4, center - 3.4, center + 3.4, center + 3.4,
                fill=core_color, outline="",
            )

        self.root.after(50, self._animate_light)

    def _flash(self, message, error=False):
        self.note.config(text=message, fg=self.C["error"] if error else self.C["muted"])
        if self._note_job is not None:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(6000, lambda: self.note.config(text=""))

    # ── الأفعال ────────────────────────────────────────────────
    def _toggle_theme(self):
        self.dark = not self.dark
        try:
            ui_prefs.save_dark(self.dark)
        except OSError:
            pass  # فشل الحفظ لا يمنع تبديل المظهر في هذه الجلسة

        note = self.note.cget("text") if hasattr(self, "note") else ""
        self._build_all()
        self._refresh()
        if note:
            self.note.config(text=note)

    def _save_feature(self, key, wanted):
        """يحفظ مفتاحاً واحداً في settings.json؛ يرجع False ويُظهر خطأً عند الفشل."""
        values = settings_store.load()
        values[key] = bool(wanted)
        try:
            settings_store.save(values)
        except OSError as exc:
            self._flash(f"تعذّر حفظ الإعداد: {exc}", error=True)
            return False
        return True

    def _on_feature(self, key):
        wanted = bool(self.vars[key].get())
        if not self._save_feature(key, wanted):
            self.vars[key].set(not wanted)
            return

        state = "مُفعّلة" if wanted else "موقوفة"
        if service_ctl.is_running():
            self._flash(f"{state} — طُبِّق على الخدمة الآن.")
        else:
            self._flash(f"{state} — سيُطبَّق عند تشغيل الخدمة.")

    def _on_layout(self):
        wanted = bool(self.layout_var.get())
        if not self._save_feature("switch_layout", wanted):
            self.layout_var.set(not wanted)
            return
        self._flash(
            "ستتبدّل لغة الكيبورد إلى لغة النتيجة بعد كل تحويل."
            if wanted else "لن تتغيّر لغة الكيبورد بعد التحويل."
        )

    def _on_autostart(self):
        wanted = bool(self.autostart_var.get())
        try:
            if wanted:
                autostart.install()
            else:
                autostart.uninstall()
        except Exception as exc:
            self.autostart_var.set(not wanted)
            self._flash(f"تعذّر تغيير التشغيل التلقائي: {exc}", error=True)
            return
        self._flash(
            "سيبدأ تلقائياً مع ويندوز بلا أي نافذة."
            if wanted else "لن يبدأ تلقائياً بعد الآن."
        )

    def _toggle_service(self):
        if service_ctl.is_running():
            ok, message = service_ctl.stop()
        else:
            ok, message = service_ctl.start()
        self._flash(message, error=not ok)
        self._refresh()


def _set_window_icon(root):
    """أيقونة النافذة نفسها (شريط العنوان/Alt+Tab)، لا أيقونة الملف فقط."""
    icon = paths.resource_path("assets/keyconv.ico")
    try:
        root.iconbitmap(icon)           # النافذة الحالية
        root.iconbitmap(default=icon)   # وأي نافذة تُفتح لاحقاً
    except Exception:
        pass


def main():
    _enable_dpi_awareness()
    root = tk.Tk()
    _set_window_icon(root)
    Panel(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
