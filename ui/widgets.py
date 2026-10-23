"""
Reusable modern UI components for Asistencia Agropecuaria.
Cards, buttons, inputs, toasts, etc. inspired by Linear/Notion.
"""
import tkinter as tk
from tkinter import ttk
from theme import get_theme, font

# ─── MODERN CARD FRAME ──────────────────────────────────────────
class Card(tk.Frame):
    """Elevated card with rounded corners and shadow effect"""
    def __init__(self, parent, **kwargs):
        T = get_theme()
        super().__init__(parent, bg=T.BG_CARD, **kwargs)
        self._shadow = None
        self._apply_style()
    
    def _apply_style(self):
        T = get_theme()
        self.configure(
            highlightthickness=1,
            highlightbackground=T.BORDER_CARD,
        )
    
    def pack(self, **kwargs):
        super().pack(**kwargs)
    
    def grid(self, **kwargs):
        super().grid(**kwargs)

# ─── CARD WITH HEADER ───────────────────────────────────────────
class CardWithHeader(tk.Frame):
    """Card with title, subtitle and content area"""
    def __init__(self, parent, title="", subtitle="", **kwargs):
        T = get_theme()
        super().__init__(parent, bg=T.BG_CARD, highlightthickness=1, 
                         highlightbackground=T.BORDER_CARD, **kwargs)
        
        # Header
        self.header_frame = tk.Frame(self, bg=T.BG_CARD)
        self.header_frame.pack(fill="x", padx=20, pady=(18, 4))
        
        self.lbl_title = tk.Label(
            self.header_frame, text=title, bg=T.BG_CARD, fg=T.FG_PRIMARY,
            font=font("lg", bold=True)
        )
        self.lbl_title.pack(anchor="w")
        
        if subtitle:
            self.lbl_subtitle = tk.Label(
                self.header_frame, text=subtitle, bg=T.BG_CARD, fg=T.FG_SECONDARY,
                font=font("sm")
            )
            self.lbl_subtitle.pack(anchor="w", pady=(2, 0))
        
        # Separator
        self.sep = tk.Frame(self, bg=T.BORDER_CARD, height=1)
        self.sep.pack(fill="x", padx=20, pady=(12, 0))
        
        # Content area
        self.content = tk.Frame(self, bg=T.BG_CARD)
        self.content.pack(fill="both", expand=True, padx=20, pady=16)

# ─── MODERN BUTTON ──────────────────────────────────────────────
class ModernButton(tk.Canvas):
    """Flat, modern button with hover effect"""
    def __init__(self, parent, text="", command=None, variant="primary", 
                 width=None, height=36, icon="", **kwargs):
        T = get_theme()
        
        self._command = command
        self._text = text
        self._icon = icon
        self._variant = variant
        self._height = height
        
        # Colors per variant
        self._colors = {
            "primary": {"bg": T.ACCENT, "fg": T.FG_INVERSE, "hover": T.ACCENT_HOVER, "active": T.ACCENT_DIM},
            "success": {"bg": T.SUCCESS, "fg": "white", "hover": "#059669", "active": "#047857"},
            "danger":  {"bg": T.DANGER, "fg": "white", "hover": "#DC2626", "active": "#B91C1C"},
            "ghost":   {"bg": T.BG_CARD, "fg": T.FG_PRIMARY, "hover": T.BG_HOVER, "active": T.BORDER_CARD},
            "outline": {"bg": T.BG_CARD, "fg": T.FG_PRIMARY, "hover": T.BG_HOVER, "active": T.BORDER_CARD},
        }
        
        c = self._colors.get(variant, self._colors["primary"])
        display_text = f"{icon} {text}" if icon else text
        
        super().__init__(
            parent, bg=T.BG_CARD, highlightthickness=0, 
            height=height, cursor="hand2", **kwargs
        )
        
        if width:
            self.configure(width=width)
        
        # Draw button
        self._rect_id = self.create_rectangle(
            0, 0, width or 100, height,
            fill=c["bg"], outline=c["bg"], width=0
        )
        
        self._text_id = self.create_text(
            (width or 100) // 2, height // 2,
            text=display_text, fill=c["fg"],
            font=font("base", bold=True),
            anchor="center"
        )
        
        # Events
        self.bind("<Enter>", self._on_hover)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)
        
        # Store for dynamic resize
        self._width = width
    
    def _on_hover(self, event):
        c = self._colors.get(self._variant, self._colors["primary"])
        if self._rect_id:
            self.itemconfig(self._rect_id, fill=c["hover"], outline=c["hover"])
    
    def _on_leave(self, event):
        c = self._colors.get(self._variant, self._colors["primary"])
        if self._rect_id:
            self.itemconfig(self._rect_id, fill=c["bg"], outline=c["bg"])
    
    def _on_click(self, event):
        c = self._colors.get(self._variant, self._colors["primary"])
        if self._rect_id:
            self.itemconfig(self._rect_id, fill=c["active"], outline=c["active"])
    
    def _on_release(self, event):
        c = self._colors.get(self._variant, self._colors["primary"])
        if self._rect_id:
            self.itemconfig(self._rect_id, fill=c["hover"], outline=c["hover"])
        if self._command:
            self._command()
    
    def configure_button(self, **kwargs):
        """Update button properties"""
        if "text" in kwargs:
            self._text = kwargs["text"]
            display_text = f"{self._icon} {self._text}" if self._icon else self._text
            self.itemconfig(self._text_id, text=display_text)
        if "state" in kwargs:
            state = kwargs["state"]
            T = get_theme()
            if state == "disabled":
                self.itemconfig(self._rect_id, fill=T.BG_HOVER, outline=T.BG_HOVER)
                self.itemconfig(self._text_id, fill=T.FG_DIM)
                self.configure(cursor="arrow")
            else:
                c = self._colors.get(self._variant, self._colors["primary"])
                self.itemconfig(self._rect_id, fill=c["bg"], outline=c["bg"])
                self.itemconfig(self._text_id, fill=c["fg"])
                self.configure(cursor="hand2")

# ─── MODERN INPUT ───────────────────────────────────────────────
class ModernInput(tk.Frame):
    """Styled input with label and focus border"""
    def __init__(self, parent, label="", textvariable=None, placeholder="",
                 show=None, width=None, **kwargs):
        T = get_theme()
        super().__init__(parent, bg=T.BG_CARD, **kwargs)
        
        if label:
            self.lbl = tk.Label(
                self, text=label, bg=T.BG_CARD, fg=T.FG_SECONDARY,
                font=font("sm", bold=True)
            )
            self.lbl.pack(anchor="w", pady=(0, 4))
        
        self.entry = tk.Entry(
            self, textvariable=textvariable, bg=T.BG_INPUT, fg=T.FG_PRIMARY,
            font=font("base"), bd=0, insertbackground=T.FG_PRIMARY,
            highlightthickness=1, highlightbackground=T.BORDER_INPUT,
            highlightcolor=T.BORDER_FOCUS, relief="flat",
            show=show,
        )
        self.entry.pack(fill="x", ipady=8, pady=(0 if label else 0, 0))
        
        if width:
            self.entry.configure(width=width)
        
        # Focus effects
        self.entry.bind("<FocusIn>", self._on_focus)
        self.entry.bind("<FocusOut>", self._on_blur)
    
    def _on_focus(self, event):
        self.entry.configure(highlightbackground=get_theme().BORDER_FOCUS)
    
    def _on_blur(self, event):
        self.entry.configure(highlightbackground=get_theme().BORDER_INPUT)
    
    def get(self):
        return self.entry.get()
    
    def set(self, value):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, str(value))
    
    def configure_entry(self, **kwargs):
        self.entry.configure(**kwargs)
    
    def config(self, **kwargs):
        super().configure(**kwargs)

# ─── TOAST NOTIFICATION ─────────────────────────────────────────
class Toast:
    """Non-blocking toast notification inspired by Linear"""
    _instance = None
    
    @classmethod
    def show(cls, parent, message, type="info", duration=3000):
        T = get_theme()
        colors = {
            "info": {"bg": T.INFO_BG, "fg": T.INFO, "icon": "ℹ"},
            "success": {"bg": T.SUCCESS_BG, "fg": T.SUCCESS, "icon": "✓"},
            "warning": {"bg": T.WARNING_BG, "fg": T.WARNING, "icon": "⚠"},
            "error": {"bg": T.DANGER_BG, "fg": T.DANGER, "icon": "✗"},
        }
        c = colors.get(type, colors["info"])
        
        toast = tk.Toplevel(parent)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=c["bg"])
        
        frame = tk.Frame(toast, bg=c["bg"], padx=16, pady=10)
        frame.pack()
        
        tk.Label(frame, text=f" {c['icon']} {message}", bg=c["bg"], fg=c["fg"],
                font=font("base", bold=True)).pack()
        
        # Position: top-right of parent
        toast.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width() - toast.winfo_width() - 20
        py = parent.winfo_y() + 20
        toast.geometry(f"+{px}+{py}")
        
        # Fade animation
        toast.after(100, lambda: toast.lift())
        toast.after(duration, lambda: toast.destroy())

# ─── SECTION LABEL ──────────────────────────────────────────────
class SectionLabel(tk.Frame):
    """Step/section label with number indicator"""
    def __init__(self, parent, number="1", title="", **kwargs):
        T = get_theme()
        super().__init__(parent, bg=T.BG_APP, **kwargs)
        
        # Number circle
        self.badge = tk.Label(
            self, text=str(number), bg=T.ACCENT, fg=T.FG_PRIMARY,
            font=font("sm", bold=True), width=2, height=1
        )
        self.badge.pack(side="left")
        
        # Title
        self.lbl = tk.Label(
            self, text=title, bg=T.BG_APP, fg=T.FG_SECONDARY,
            font=font("sm", bold=True)
        )
        self.lbl.pack(side="left", padx=(8, 0))

# ─── BADGE STATUS ──────────────────────────────────────────────
class StatusBadge(tk.Frame):
    """Indicator dot with status text"""
    def __init__(self, parent, text="Desconectado", status="offline", **kwargs):
        T = get_theme()
        super().__init__(parent, bg=T.BG_CARD, **kwargs)
        
        self.dot = tk.Frame(self, bg=T.DANGER, width=10, height=10, relief="flat")
        self.dot.pack(side="left", padx=(0, 6))
        
        self.lbl = tk.Label(
            self, text=text, bg=T.BG_CARD, fg=T.DANGER,
            font=font("sm", bold=True)
        )
        self.lbl.pack(side="left")
        
        self.set_status(status)
    
    def set_status(self, status, text=None):
        T = get_theme()
        colors = {
            "online": {"bg": T.SUCCESS, "fg": T.SUCCESS},
            "offline": {"bg": T.DANGER, "fg": T.DANGER},
            "connecting": {"bg": T.WARNING, "fg": T.WARNING},
            "warning": {"bg": T.WARNING, "fg": T.WARNING},
        }
        c = colors.get(status, colors["offline"])
        self.dot.configure(bg=c["bg"])
        self.lbl.configure(fg=c["fg"])
        if text:
            self.lbl.configure(text=text)


class Tooltip:
    """Small hover tooltip for icon-only actions."""
    def __init__(self, widget, text, delay=550):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        T = get_theme()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self._tip = tk.Toplevel(self.widget)
        self._tip.overrideredirect(True)
        self._tip.attributes("-topmost", True)
        frame = tk.Frame(self._tip, bg=T.FG_PRIMARY, padx=10, pady=6)
        frame.pack()
        tk.Label(
            frame, text=self.text, bg=T.FG_PRIMARY, fg=T.BG_APP,
            font=font("sm")
        ).pack()
        self._tip.geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip:
            self._tip.destroy()
            self._tip = None
