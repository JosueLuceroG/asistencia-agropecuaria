"""DateEntry estable al navegar por meses dentro del calendario desplegable."""
from __future__ import annotations

import tkinter as tk

from tkcalendar import DateEntry


def standard_calendar_style(bg_card, fg_text, bg_app, fg_dim, border, select, other_month="#334155"):
    """Devuelve el estilo visual estándar usado por los calendarios clásicos."""
    return {
        "background": bg_card,
        "foreground": fg_text,
        "headersbackground": bg_app,
        "headersforeground": fg_dim,
        "normalbackground": bg_card,
        "normalforeground": fg_text,
        "weekendbackground": bg_card,
        "weekendforeground": fg_dim,
        "othermonthbackground": bg_app,
        "othermonthwebackground": bg_app,
        "othermonthforeground": other_month,
        "othermonthweforeground": other_month,
        "selectbackground": select,
        "selectforeground": fg_text,
        "bottombackground": bg_app,
        "bordercolor": border,
        "borderwidth": 0,
        "font": ("Segoe UI", 10),
    }


class StableDateEntry(DateEntry):
    """Evita que el popup se cierre al mover el foco entre sus controles internos."""

    def __init__(self, master=None, **kwargs):
        self._calendar_focus_job = None
        super().__init__(master, **kwargs)

    @staticmethod
    def _is_descendant(widget, ancestor):
        while widget is not None:
            if widget == ancestor:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _pointer_is_inside_calendar(self):
        try:
            x, y = self._top_cal.winfo_pointerxy()
            left = self._top_cal.winfo_rootx()
            top = self._top_cal.winfo_rooty()
            return (
                left <= x <= left + self._top_cal.winfo_width()
                and top <= y <= top + self._top_cal.winfo_height()
            )
        except tk.TclError:
            return False

    def _on_focus_out_cal(self, _event=None):
        """Comprueba el foco después de que Tk termine de procesar el clic actual."""
        if self._calendar_focus_job is not None:
            try:
                self.after_cancel(self._calendar_focus_job)
            except (tk.TclError, ValueError):
                pass
        self._calendar_focus_job = self.after_idle(self._resolve_calendar_focus_out)

    def _resolve_calendar_focus_out(self):
        self._calendar_focus_job = None
        try:
            if not self._top_cal.winfo_exists() or not self._calendar.winfo_ismapped():
                return
            focused = self.focus_get()
            if self._is_descendant(focused, self._top_cal) or self._pointer_is_inside_calendar():
                self._calendar.focus_set()
                return
            self._top_cal.withdraw()
            self.state(["!pressed"])
        except tk.TclError:
            return

    def destroy(self):
        """Cancela callbacks diferidos propios antes de eliminar el widget."""
        try:
            registered = set(getattr(self, "_tclCommands", None) or [])
            for job in self.tk.call("after", "info"):
                info = self.tk.call("after", "info", job)
                script = info[0] if isinstance(info, tuple) else info
                command = str(script).split()[0]
                if command in registered and command.endswith("_on_theme_change"):
                    self.after_cancel(job)
            if self._calendar_focus_job is not None:
                self.after_cancel(self._calendar_focus_job)
                self._calendar_focus_job = None
        except (tk.TclError, ValueError):
            pass
        super().destroy()