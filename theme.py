"""
Modern theme system for Asistencia Agropecuaria.
Paleta inspirada en Linear, Notion y aplicaciones SaaS modernas.
"""
import tkinter as tk
from tkinter import ttk

# ─── COLOR PALETTE ──────────────────────────────────────────────
class DarkTheme:
    NAME          = "dark"
    # Backgrounds
    BG_APP        = "#0F172A"  # Slate 900
    BG_SIDEBAR    = "#0B1120"  # Slate 950
    BG_CARD       = "#1E293B"  # Slate 800
    BG_CARD_ALT   = "#253347"  # Slate 800 + light
    BG_HOVER      = "#334155"  # Slate 700
    BG_INPUT      = "#1E293B"  # Slate 800
    
    # Borders
    BORDER_CARD   = "#334155"  # Slate 700
    BORDER_INPUT  = "#475569"  # Slate 600
    BORDER_FOCUS  = "#3B82F6"  # Blue 500
    
    # Text
    FG_PRIMARY    = "#F8FAFC"  # Slate 50
    FG_SECONDARY  = "#94A3B8"  # Slate 400
    FG_DIM        = "#64748B"  # Slate 500
    FG_INVERSE    = "#0F172A"  # Slate 900
    
    # Accents
    ACCENT        = "#2563EB"  # Blue 600
    SECONDARY     = "#3B82F6"  # Blue 500
    ACCENT_HOVER  = "#3B82F6"  # Blue 500
    ACCENT_DIM    = "#1D4ED8"  # Blue 700
    
    # Status
    SUCCESS       = "#10B981"  # Emerald 500
    SUCCESS_BG    = "#064E3B"  # Emerald 900
    WARNING       = "#F59E0B"  # Amber 500
    WARNING_BG    = "#78350F"  # Amber 900
    DANGER        = "#EF4444"  # Red 500
    DANGER_BG     = "#7F1D1D"  # Red 900
    INFO          = "#3B82F6"  # Blue 500
    INFO_BG       = "#1E3A5F"  # Blue 900
    
    # Excel Export
    EXCEL_COL             = "#16A34A"
    EXCEL_SABADO_EXTRA_FILL = "CCFBF1"
    EXCEL_SABADO_EXTRA_FONT = "0F766E"
    
    # Shadows (simulated via frames)
    SHADOW_COLOR  = "#00000020"

class LightTheme:
    NAME          = "light"
    BG_APP        = "#F8FAFC"
    BG_SIDEBAR    = "#FFFFFF"
    BG_CARD       = "#FFFFFF"
    BG_CARD_ALT   = "#F8FAFC"
    BG_HOVER      = "#E2E8F0"
    BG_INPUT      = "#FFFFFF"
    BORDER_CARD   = "#E2E8F0"
    BORDER_INPUT  = "#CBD5E1"
    BORDER_FOCUS  = "#3B82F6"
    FG_PRIMARY    = "#0F172A"
    FG_SECONDARY  = "#475569"
    FG_DIM        = "#94A3B8"
    FG_INVERSE    = "#F8FAFC"
    ACCENT        = "#2563EB"
    SECONDARY     = "#3B82F6"
    ACCENT_HOVER  = "#3B82F6"
    ACCENT_DIM    = "#1D4ED8"
    SUCCESS       = "#16A34A"
    SUCCESS_BG    = "#DCFCE7"
    WARNING       = "#D97706"
    WARNING_BG    = "#FEF3C7"
    DANGER        = "#DC2626"
    DANGER_BG     = "#FEE2E2"
    INFO          = "#2563EB"
    INFO_BG       = "#DBEAFE"
    EXCEL_COL     = "#16A34A"
    EXCEL_SABADO_EXTRA_FILL = "CCFBF1"
    EXCEL_SABADO_EXTRA_FONT = "0F766E"
    SHADOW_COLOR  = "#00000010"

# Active theme (can be toggled at runtime)
_current_theme = DarkTheme

def get_theme():
    return _current_theme

def set_theme(theme_class):
    global _current_theme
    if isinstance(theme_class, str):
        theme_class = DarkTheme if theme_class.lower().startswith("dark") else LightTheme
    _current_theme = theme_class

def current_theme_name():
    return getattr(_current_theme, "NAME", "dark")

def toggle_theme():
    if _current_theme.__name__ == "DarkTheme":
        set_theme(LightTheme)
    else:
        set_theme(DarkTheme)
    return _current_theme.__name__

# ─── FONTS ──────────────────────────────────────────────────────
FONT_FAMILY = "Segoe UI"
FONT_MONO   = "Consolas"
FONT_SIZES = {
    "xs": 8,
    "sm": 9,
    "base": 10,
    "md": 11,
    "lg": 13,
    "xl": 15,
    "2xl": 17,
    "3xl": 20,
}

def font(size="base", bold=False):
    return (FONT_FAMILY, FONT_SIZES.get(size, 10), "bold" if bold else "normal")

# ─── STYLES CONFIGURATION ───────────────────────────────────────
def configurar_styles(root):
    """Apply the active theme to ttk styles"""
    T = get_theme()
    style = ttk.Style()
    style.theme_use("clam")
    
    # Progress bar
    style.configure(
        "TProgressbar",
        thickness=8,
        troughcolor=T.BG_CARD_ALT,
        background=T.ACCENT,
        borderwidth=0,
    )
    style.configure(
        "Modern.Horizontal.TProgressbar",
        thickness=10,
        troughcolor=T.BG_CARD_ALT,
        background=T.ACCENT,
        borderwidth=0,
        lightcolor=T.ACCENT,
        darkcolor=T.ACCENT,
    )
    
    # Combobox
    style.configure(
        "TCombobox",
        fieldbackground=T.BG_INPUT,
        background=T.BG_CARD,
        foreground=T.FG_PRIMARY,
        darkcolor=T.BG_CARD,
        lightcolor=T.BG_CARD,
        bordercolor=T.BORDER_CARD,
        arrowcolor=T.FG_SECONDARY,
        selectbackground=T.ACCENT,
        selectforeground=T.FG_PRIMARY,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", T.BG_INPUT)],
        selectbackground=[("readonly", T.ACCENT)],
        selectforeground=[("readonly", T.FG_PRIMARY)],
        foreground=[("readonly", T.FG_PRIMARY)],
    )
    
    # Treeview
    style.configure(
        "Custom.Treeview",
        background=T.BG_CARD,
        fieldbackground=T.BG_CARD,
        foreground=T.FG_PRIMARY,
        borderwidth=0,
        font=font("base"),
        rowheight=32,
    )
    style.configure(
        "Custom.Treeview.Heading",
        background=T.BG_CARD_ALT,
        foreground=T.FG_PRIMARY,
        font=font("md", bold=True),
        borderwidth=0,
        padding=5,
    )
    style.map(
        "Custom.Treeview",
        background=[("selected", T.ACCENT)],
        foreground=[("selected", T.FG_PRIMARY)],
    )
    style.layout("Custom.Treeview", [
        ("Treeview.treearea", {"sticky": "nswe"})
    ])
    
    # Scrollbar
    style.configure(
        "Custom.Vertical.TScrollbar",
        background=T.BG_CARD,
        troughcolor=T.BG_APP,
        bordercolor=T.BG_CARD,
        lightcolor=T.BG_CARD,
        darkcolor=T.BG_CARD,
    )
    
    # Configure root options
    root.option_add("*TCombobox*Listbox.background", T.BG_CARD)
    root.option_add("*TCombobox*Listbox.foreground", T.FG_PRIMARY)
    root.option_add("*TCombobox*Listbox.selectBackground", T.ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", T.FG_PRIMARY)

# ─── CONSTANTS ──────────────────────────────────────────────────
HORA_CORTE_SALIDA_MIN = 14 * 60  # 2:00 PM cutoff for entrada/salida
DEFAULT_PORT = 4370
HIKVISION_DEFAULT_PORT = 80

# ─── MESES & DIAS ──────────────────────────────────────────────
meses = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}
meses_abrev = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"
}
dias_sem = {
    0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
    4: "Viernes", 5: "Sábado", 6: "Domingo"
}
