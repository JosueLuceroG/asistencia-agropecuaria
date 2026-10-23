"""Ventana de consulta local del estado de sincronización de checadores."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import config_manager
from services import attendance_service
from services.device_health_service import (
    STATUS_ERROR,
    STATUS_HEALTHY,
    STATUS_NEVER_SYNCED,
    STATUS_WARNING,
    build_device_health_rows,
)
from theme import get_theme


def _format_timestamp(value):
    value = str(value or "").strip()
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d/%m/%Y %I:%M:%S %p")
    except ValueError:
        return value


class VentanaEstadoChecadores(tk.Toplevel):
    """Presenta información local; no realiza descargas ni conexiones de red."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.theme = get_theme()
        self.rows_by_device = {}
        self.title("Estado de checadores")
        self.configure(bg=self.theme.BG_APP)
        self.geometry("1080x610")
        self.minsize(900, 520)
        self.transient(parent)
        self._configure_tree_style()
        self._build_ui()
        self.refresh()
        self._center_over_parent()

    def _configure_tree_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "DeviceHealth.Treeview",
            background=self.theme.BG_CARD,
            fieldbackground=self.theme.BG_CARD,
            foreground=self.theme.FG_PRIMARY,
            borderwidth=0,
            rowheight=30,
            font=("Segoe UI", 9),
        )
        style.configure(
            "DeviceHealth.Treeview.Heading",
            background=self.theme.BG_CARD_ALT,
            foreground=self.theme.FG_PRIMARY,
            font=("Segoe UI", 9, "bold"),
            borderwidth=0,
            padding=5,
        )
        style.map(
            "DeviceHealth.Treeview",
            background=[("selected", self.theme.ACCENT)],
            foreground=[("selected", self.theme.FG_PRIMARY)],
        )

    def _build_ui(self):
        header = tk.Frame(self, bg=self.theme.BG_APP, padx=20, pady=16)
        header.pack(fill="x")
        tk.Label(
            header, text="Estado de checadores", bg=self.theme.BG_APP,
            fg=self.theme.FG_PRIMARY, font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text=("Consulta local de la última descarga registrada. Esta pantalla no se conecta "
                  "ni modifica los checadores."),
            bg=self.theme.BG_APP, fg=self.theme.FG_SECONDARY, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(3, 0))

        table_card = tk.Frame(
            self, bg=self.theme.BG_CARD, padx=1, pady=1,
            highlightthickness=1, highlightbackground=self.theme.BORDER_CARD,
        )
        table_card.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        columns = ("status", "device", "connection", "last_sync", "last_record", "received", "added", "total", "error")
        self.tree = ttk.Treeview(table_card, style="DeviceHealth.Treeview", columns=columns, show="headings")
        headings = {
            "status": ("Estado", 145), "device": ("Checador", 155),
            "connection": ("Conexión", 155), "last_sync": ("Última sincronización", 175),
            "last_record": ("Última checada", 165), "received": ("Recibidas", 78),
            "added": ("Nuevas", 68), "total": ("Total local", 84), "error": ("Último error", 270),
        }
        for column in columns:
            heading, width = headings[column]
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=55, anchor="center" if column in {"received", "added", "total"} else "w")
        self.tree.column("error", stretch=True)
        scrollbar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.tag_configure(STATUS_HEALTHY, foreground=self.theme.SUCCESS)
        self.tree.tag_configure(STATUS_WARNING, foreground=self.theme.WARNING)
        self.tree.tag_configure(STATUS_ERROR, foreground=self.theme.DANGER)
        self.tree.tag_configure(STATUS_NEVER_SYNCED, foreground=self.theme.WARNING)
        self.tree.bind("<Double-1>", lambda _event: self.show_detail())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_selection_state())

        footer = tk.Frame(self, bg=self.theme.BG_APP, padx=20, pady=0)
        footer.pack(fill="x", pady=(0, 16))
        self.summary_label = tk.Label(footer, bg=self.theme.BG_APP, fg=self.theme.FG_SECONDARY, font=("Segoe UI", 9))
        self.summary_label.pack(side="left", fill="x", expand=True)
        self.detail_button = tk.Button(
            footer, text="Ver detalle", command=self.show_detail, state="disabled",
            bg=self.theme.BG_CARD_ALT, fg=self.theme.FG_PRIMARY, activebackground=self.theme.BG_HOVER,
            activeforeground=self.theme.FG_PRIMARY, bd=0, padx=14, pady=8, cursor="hand2",
        )
        self.detail_button.pack(side="right", padx=(8, 0))
        tk.Button(
            footer, text="Actualizar vista", command=self.refresh,
            bg=self.theme.ACCENT, fg=self.theme.FG_PRIMARY, activebackground=self.theme.ACCENT_HOVER,
            activeforeground=self.theme.FG_PRIMARY, bd=0, padx=14, pady=8, cursor="hand2",
        ).pack(side="right")

    def _center_over_parent(self):
        self.update_idletasks()
        x = self.parent.winfo_rootx() + max(0, (self.parent.winfo_width() - self.winfo_width()) // 2)
        y = self.parent.winfo_rooty() + max(0, (self.parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")

    def refresh(self):
        config_manager.invalidar_cache_config()
        attendance_service.invalidate_cache()
        rows = build_device_health_rows(config_manager.cargar_configuracion(), attendance_service.load_attendance())
        self.rows_by_device = {row["device"]: row for row in rows}
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            connection = ":".join(part for part in (row["ip"], row["port"]) if part) or "—"
            self.tree.insert("", "end", iid=row["device"], tags=(row["status"],), values=(
                row["status_label"], row["device"], connection,
                _format_timestamp(row["last_successful_sync"]),
                _format_timestamp(row["last_record_timestamp"]),
                row["last_received"], row["last_added"], row["last_total"],
                row["last_error"] or "—",
            ))
        errors = sum(row["status"] == STATUS_ERROR for row in rows)
        never = sum(row["status"] == STATUS_NEVER_SYNCED for row in rows)
        self.summary_label.config(
            text=f"{len(rows)} checador(es) configurado(s). Errores: {errors}. Nunca sincronizados: {never}."
        )
        self._update_selection_state()

    def _selected_row(self):
        selection = self.tree.selection()
        return self.rows_by_device.get(selection[0]) if selection else None

    def _update_selection_state(self):
        self.detail_button.config(state="normal" if self._selected_row() else "disabled")

    def show_detail(self):
        row = self._selected_row()
        if not row:
            return messagebox.showinfo("Selecciona un checador", "Selecciona un checador para consultar el detalle.", parent=self)
        error = row["last_error"] or "No hay error registrado."
        messagebox.showinfo(
            f"Detalle: {row['device']}",
            f"Estado: {row['status_label']}\n{row['status_description']}\n\n"
            f"Marca: {row['brand']}\n"
            f"Conexión: {':'.join(part for part in (row['ip'], row['port']) if part) or 'No registrada'}\n"
            f"Última sincronización correcta: {_format_timestamp(row['last_successful_sync'])}\n"
            f"Última checada recibida: {_format_timestamp(row['last_record_timestamp'])}\n"
            f"Última descarga: {row['last_received']} recibidas, {row['last_added']} nuevas.\n"
            f"Total local informado: {row['last_total']}\n\n"
            f"Último error: {error}\n"
            f"Fecha del error: {_format_timestamp(row['last_error_at'])}",
            parent=self,
        )