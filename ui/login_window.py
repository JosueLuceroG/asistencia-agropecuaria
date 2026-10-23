"""Diálogos Tk para iniciar sesión y administrar usuarios."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from services import auth_service


BG_APP = "#0F172A"
BG_CARD = "#1E293B"
BG_ALT = "#253347"
BORDER = "#334155"
FG_TEXT = "#F8FAFC"
FG_DIM = "#94A3B8"
ACCENT = "#3B82F6"
SUCCESS = "#10B981"
DANGER = "#EF4444"


def _center(window, width, height):
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2 - 20)
    window.geometry(f"{width}x{height}+{x}+{y}")


class LoginWindow(tk.Toplevel):
    """Ventana modal de acceso y cambio obligatorio de contraseña."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.user = None
        self.title("Iniciar sesión | Asistencia Agropecuaria")
        self.configure(bg=BG_APP)
        self.resizable(False, False)
        # No usar transient mientras el root está oculto: en Windows/RDP una
        # ventana transitoria de un padre oculto puede permanecer invisible.
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.username = tk.StringVar(value="")
        self.password = tk.StringVar(value="")
        self._build()
        _center(self, 420, 370)
        self.update_idletasks()
        self.deiconify()
        self.update()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.grab_set()
        # El root principal permanece oculto hasta autenticar. Forzar el
        # primer plano evita que el login quede detrás de la sesión RDP.
        self.after_idle(self._bring_to_front)
        self.entry_username.focus_set()
        self.bind("<Return>", lambda _event: self._login())

    def _bring_to_front(self):
        if not self.winfo_exists():
            return
        self.deiconify()
        self.update_idletasks()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.after(250, self._clear_topmost)

    def _clear_topmost(self):
        if self.winfo_exists():
            self.attributes("-topmost", False)

    def _build(self):
        card = tk.Frame(self, bg=BG_CARD, padx=30, pady=26, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(card, text="SISTEMA ASISTENCIA", bg=BG_CARD, fg=FG_TEXT, font=("Segoe UI", 16, "bold")).pack()
        tk.Label(card, text="Inicia sesión para continuar", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 10)).pack(pady=(4, 22))
        tk.Label(card, text="Usuario", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.entry_username = tk.Entry(card, textvariable=self.username, bg=BG_ALT, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat", font=("Segoe UI", 11))
        self.entry_username.pack(fill="x", ipady=7, pady=(4, 15))
        tk.Label(card, text="Contraseña", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        entry_password = tk.Entry(card, textvariable=self.password, show="•", bg=BG_ALT, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat", font=("Segoe UI", 11))
        entry_password.pack(fill="x", ipady=7, pady=(4, 18))
        tk.Button(card, text="Iniciar sesión", command=self._login, bg=ACCENT, fg=FG_TEXT, bd=0, font=("Segoe UI", 10, "bold"), pady=9, cursor="hand2").pack(fill="x")
        tk.Button(card, text="Configurar administrador inicial", command=self._configure_admin, bg=BG_CARD, fg=ACCENT, bd=0, font=("Segoe UI", 9, "underline"), pady=8, cursor="hand2").pack()
        tk.Label(card, text="Acceso restringido al personal autorizado", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 8)).pack(pady=(4, 0))

    def _cancel(self):
        self.user = None
        self.destroy()

    def _login(self):
        try:
            user = auth_service.authenticate(self.username.get(), self.password.get())
        except auth_service.AuthError as exc:
            messagebox.showerror("No se pudo iniciar sesión", str(exc), parent=self)
            self.password.set("")
            return
        if user.get("must_change_password"):
            if not PasswordChangeWindow.change(self, user["username"], required=True):
                return
            user = auth_service.get_public_user(user["username"])
        self.user = user
        self.destroy()

    def _configure_admin(self):
        try:
            admin = auth_service.get_user("admin")
            if admin and admin.get("password"):
                messagebox.showinfo("Administrador configurado", "La contraseña del administrador ya fue establecida.", parent=self)
                return
            if PasswordChangeWindow.configure_initial_admin(self):
                messagebox.showinfo("Administrador configurado", "La contraseña del administrador se configuró correctamente.", parent=self)
        except auth_service.AuthError as exc:
            messagebox.showerror("No se pudo configurar", str(exc), parent=self)

    @classmethod
    def ask(cls, parent):
        dialog = cls(parent)
        parent.update_idletasks()
        parent.wait_window(dialog)
        return dialog.user


class PasswordChangeWindow(tk.Toplevel):
    def __init__(self, parent, username, *, initial_admin=False, required=False):
        super().__init__(parent)
        self.parent = parent
        self.username = username
        self.initial_admin = initial_admin
        self.required = required
        self.changed = False
        self.title("Configurar contraseña" if initial_admin else "Cambiar contraseña")
        self.configure(bg=BG_APP)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.password = tk.StringVar()
        self.confirmation = tk.StringVar()
        self._build()
        # El contenido del cambio obligatorio ocupa más de 300 px, sobre todo
        # con el escalado de Windows/RDP. La altura anterior recortaba Guardar.
        _center(self, 430, 390)
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.grab_set()
        self.after_idle(self._bring_to_front)
        self.entry_password.focus_set()
        self.bind("<Return>", lambda _event: self._save())

    def _bring_to_front(self):
        if not self.winfo_exists():
            return
        self.deiconify()
        self.update_idletasks()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.after(250, self._clear_topmost)

    def _clear_topmost(self):
        if self.winfo_exists():
            self.attributes("-topmost", False)

    def _build(self):
        card = tk.Frame(self, bg=BG_CARD, padx=28, pady=24, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True, padx=18, pady=18)
        heading = "Configura la contraseña del administrador" if self.initial_admin else f"Nueva contraseña para {self.username}"
        tk.Label(card, text=heading, bg=BG_CARD, fg=FG_TEXT, font=("Segoe UI", 13, "bold"), wraplength=330, justify="center").pack()
        if self.required:
            tk.Label(card, text="Debes cambiar la contraseña inicial para continuar.", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9), wraplength=330, justify="center").pack(pady=(6, 16))
        else:
            tk.Label(card, text="Usa al menos 5 caracteres.", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(pady=(6, 16))
        for label, variable in (("Nueva contraseña", self.password), ("Confirmar contraseña", self.confirmation)):
            tk.Label(card, text=label, bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            entry = tk.Entry(card, textvariable=variable, show="•", bg=BG_ALT, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat", font=("Segoe UI", 11))
            entry.pack(fill="x", ipady=6, pady=(4, 10))
            if label == "Nueva contraseña":
                self.entry_password = entry
        buttons = tk.Frame(card, bg=BG_CARD)
        buttons.pack(fill="x", pady=(10, 0))
        tk.Button(buttons, text="Cambiar contraseña", command=self._save, bg=SUCCESS, fg=FG_TEXT, bd=0, font=("Segoe UI", 10, "bold"), padx=14, pady=8, cursor="hand2").pack(side="right")
        if not self.required:
            tk.Button(buttons, text="Cancelar", command=self._cancel, bg=BG_ALT, fg=FG_TEXT, bd=0, font=("Segoe UI", 10), padx=14, pady=8, cursor="hand2").pack(side="right", padx=(0, 8))

    def _cancel(self):
        self.changed = False
        self.destroy()

    def _save(self):
        if self.password.get() != self.confirmation.get():
            messagebox.showerror("Contraseñas distintas", "Las contraseñas no coinciden.", parent=self)
            return
        try:
            if self.initial_admin:
                auth_service.configure_initial_admin(self.password.get())
            else:
                auth_service.change_password(self.username, self.password.get(), require_change=False)
        except auth_service.AuthError as exc:
            messagebox.showerror("No se pudo guardar", str(exc), parent=self)
            return
        self.changed = True
        self.destroy()

    @classmethod
    def change(cls, parent, username, *, required=False):
        dialog = cls(parent, username, required=required)
        parent.wait_window(dialog)
        return dialog.changed

    @classmethod
    def configure_initial_admin(cls, parent):
        dialog = cls(parent, "admin", initial_admin=True)
        parent.wait_window(dialog)
        return dialog.changed


class UserManagementWindow(tk.Toplevel):
    """Administración de cuentas y permisos, exclusiva de administradores."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.selected_username = None
        self.title("Usuarios y permisos")
        self.configure(bg=BG_APP)
        self.geometry("900x610")
        self.minsize(780, 560)
        self.transient(parent)
        self._build()
        self.refresh()

    def _build(self):
        container = tk.Frame(self, bg=BG_APP, padx=18, pady=16)
        container.pack(fill="both", expand=True)
        tk.Label(container, text="Usuarios y permisos", bg=BG_APP, fg=FG_TEXT, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(container, text="Los cambios se aplican en el archivo de usuarios del servidor.", bg=BG_APP, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 12))
        body = tk.PanedWindow(container, orient="horizontal", sashwidth=6, bg=BG_APP, bd=0)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=BG_CARD, padx=12, pady=12)
        right = tk.Frame(body, bg=BG_CARD, padx=16, pady=14)
        body.add(left, minsize=330)
        body.add(right, minsize=410)
        self.tree = ttk.Treeview(left, columns=("name", "status"), show="headings", height=17, style="Custom.Treeview")
        self.tree.heading("name", text="Usuario")
        self.tree.heading("status", text="Estado")
        self.tree.column("name", width=210, anchor="w")
        self.tree.column("status", width=90, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        tk.Button(left, text="Nuevo usuario", command=self._new_user, bg=ACCENT, fg=FG_TEXT, bd=0, font=("Segoe UI", 9, "bold"), pady=8, cursor="hand2").pack(fill="x", pady=(10, 0))
        self.name_var = tk.StringVar()
        self.active_var = tk.BooleanVar(value=True)
        tk.Label(right, text="Usuario seleccionado", bg=BG_CARD, fg=FG_TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.user_label = tk.Label(right, text="Selecciona un usuario", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 10))
        self.user_label.pack(anchor="w", pady=(4, 16))
        tk.Label(right, text="Nombre visible", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.name_entry = tk.Entry(right, textvariable=self.name_var, bg=BG_ALT, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat", font=("Segoe UI", 10))
        self.name_entry.pack(fill="x", ipady=6, pady=(4, 10))
        tk.Checkbutton(right, text="Usuario activo", variable=self.active_var, bg=BG_CARD, fg=FG_TEXT, activebackground=BG_CARD, activeforeground=FG_TEXT, selectcolor=BG_ALT, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))
        tk.Label(right, text="Permisos", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        permissions_frame = tk.Frame(right, bg=BG_CARD)
        permissions_frame.pack(fill="both", expand=True, pady=(6, 8))
        self.permission_vars = {}
        for index, (permission, label) in enumerate(auth_service.PERMISSIONS.items()):
            variable = tk.BooleanVar(value=False)
            self.permission_vars[permission] = variable
            tk.Checkbutton(permissions_frame, text=label, variable=variable, bg=BG_CARD, fg=FG_TEXT, activebackground=BG_CARD, activeforeground=FG_TEXT, selectcolor=BG_ALT, font=("Segoe UI", 9)).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 14), pady=2)
        buttons = tk.Frame(right, bg=BG_CARD)
        buttons.pack(fill="x")
        tk.Button(buttons, text="Guardar cambios", command=self._save, bg=SUCCESS, fg=FG_TEXT, bd=0, font=("Segoe UI", 9, "bold"), padx=12, pady=8, cursor="hand2").pack(side="right")
        tk.Button(buttons, text="Restablecer contraseña", command=self._reset_password, bg=BG_ALT, fg=FG_TEXT, bd=0, font=("Segoe UI", 9), padx=12, pady=8, cursor="hand2").pack(side="right", padx=(0, 8))

    def refresh(self):
        selected = self.selected_username
        for item in self.tree.get_children():
            self.tree.delete(item)
        for user in auth_service.list_users():
            status = "Activo" if user["active"] else "Inactivo"
            item = self.tree.insert("", "end", iid=user["username"], values=(user["display_name"] + f" ({user['username']})", status))
            if user["username"] == selected:
                self.tree.selection_set(item)

    def _select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        self.selected_username = selection[0]
        user = auth_service.get_user(self.selected_username)
        self.user_label.config(text=f"{user['username']}")
        self.name_var.set(user["display_name"])
        self.active_var.set(user["active"])
        granted = set(user["permissions"])
        for permission, variable in self.permission_vars.items():
            variable.set(permission in granted)
        is_admin = user["username"] == "admin"
        self.name_entry.config(state="normal")
        for permission, variable in self.permission_vars.items():
            state = "disabled" if is_admin else "normal"
            # Tk variables remain consistent; disable widgets by traversing below.
            variable.set(permission in granted)
        for child in self.winfo_children():
            _ = child

    def _new_user(self):
        dialog = NewUserWindow(self)
        self.wait_window(dialog)
        if dialog.created:
            self.selected_username = dialog.created["username"]
            self.refresh()

    def _save(self):
        if not self.selected_username:
            messagebox.showwarning("Selecciona un usuario", "Selecciona un usuario para guardar cambios.", parent=self)
            return
        permissions = [permission for permission, value in self.permission_vars.items() if value.get()]
        try:
            auth_service.update_user(
                self.selected_username, display_name=self.name_var.get(), permissions=permissions, active=self.active_var.get(),
            )
        except auth_service.AuthError as exc:
            messagebox.showerror("No se pudo guardar", str(exc), parent=self)
            return
        self.refresh()
        messagebox.showinfo("Usuario actualizado", "Los cambios se guardaron correctamente.", parent=self)

    def _reset_password(self):
        if not self.selected_username:
            messagebox.showwarning("Selecciona un usuario", "Selecciona un usuario primero.", parent=self)
            return
        if PasswordChangeWindow.change(self, self.selected_username):
            messagebox.showinfo("Contraseña actualizada", "La contraseña se actualizó correctamente.", parent=self)


class NewUserWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.created = None
        self.title("Nuevo usuario")
        self.configure(bg=BG_APP)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.username_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.active_var = tk.BooleanVar(value=True)
        self.permission_vars = {key: tk.BooleanVar(value=False) for key in auth_service.PERMISSIONS}
        self._build()
        _center(self, 570, 555)
        self.grab_set()

    def _build(self):
        card = tk.Frame(self, bg=BG_CARD, padx=22, pady=18)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(card, text="Crear usuario", bg=BG_CARD, fg=FG_TEXT, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 12))
        for label, variable, secret in (("Usuario", self.username_var, False), ("Nombre visible", self.name_var, False), ("Contraseña inicial", self.password_var, True)):
            tk.Label(card, text=label, bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Entry(card, textvariable=variable, show="•" if secret else "", bg=BG_ALT, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat", font=("Segoe UI", 10)).pack(fill="x", ipady=6, pady=(3, 9))
        tk.Checkbutton(card, text="Usuario activo", variable=self.active_var, bg=BG_CARD, fg=FG_TEXT, activebackground=BG_CARD, activeforeground=FG_TEXT, selectcolor=BG_ALT, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(card, text="Permisos", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 3))
        frame = tk.Frame(card, bg=BG_CARD)
        frame.pack(fill="both", expand=True)
        for index, (permission, label) in enumerate(auth_service.PERMISSIONS.items()):
            tk.Checkbutton(frame, text=label, variable=self.permission_vars[permission], bg=BG_CARD, fg=FG_TEXT, activebackground=BG_CARD, activeforeground=FG_TEXT, selectcolor=BG_ALT, font=("Segoe UI", 9)).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 12), pady=1)
        tk.Button(card, text="Crear usuario", command=self._create, bg=ACCENT, fg=FG_TEXT, bd=0, font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2").pack(fill="x", pady=(12, 0))

    def _create(self):
        try:
            self.created = auth_service.create_user(
                self.username_var.get(), self.name_var.get(), self.password_var.get(),
                [permission for permission, variable in self.permission_vars.items() if variable.get()],
                active=self.active_var.get(), require_password_change=True,
            )
        except auth_service.AuthError as exc:
            messagebox.showerror("No se pudo crear", str(exc), parent=self)
            return
        self.destroy()