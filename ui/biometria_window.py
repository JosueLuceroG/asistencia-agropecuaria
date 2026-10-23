"""Ventana responsiva para registrar empleados y sus datos biométricos."""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from device_connectors.hikvision import (
    hikvision_configurar_modo_rostro,
    hikvision_crear_y_verificar_persona,
    hikvision_enviar_rostro,
    hikvision_resumen_acceso,
)
from services.biometria_service import (
    crear_y_verificar_zkteco,
    normalizar_persona,
    preparar_jpeg,
    upsert_estado_biometrico,
)
from theme import get_theme


T = get_theme()
BG = T.BG_APP
CARD = T.BG_CARD
SOFT = T.BG_CARD_ALT
HOVER = T.BG_HOVER
INPUT = T.BG_INPUT
FG = T.FG_PRIMARY
DIM = T.FG_SECONDARY
MUTED = T.FG_DIM
BORDER = T.BORDER_CARD
INPUT_BORDER = T.BORDER_INPUT
ACCENT = T.ACCENT
ACCENT_HOVER = T.ACCENT_HOVER
ACCENT_SOFT = T.INFO_BG
SUCCESS = T.SUCCESS
SUCCESS_SOFT = T.SUCCESS_BG
WARNING = T.WARNING
WARNING_SOFT = T.WARNING_BG
DANGER = T.DANGER


def evaluar_encuadre_facial(frame_width, frame_height, faces, brightness):
    """Evalúa encuadre, cantidad de rostros, tamaño, centrado e iluminación."""
    faces = list(faces) if faces is not None else []
    if not faces:
        return {
            "valid": False, "level": "danger", "status": "ROSTRO NO DETECTADO",
            "message": "Coloca tu rostro dentro del óvalo", "face": None,
        }
    if len(faces) != 1:
        return {
            "valid": False, "level": "danger", "status": "VARIOS ROSTROS",
            "message": "Solo debe aparecer una persona", "face": None,
        }
    x, y, width, height = [int(value) for value in faces[0]]
    if brightness < 55:
        return {
            "valid": False, "level": "warning", "status": "POCA ILUMINACIÓN",
            "message": "Mejora la iluminación del rostro", "face": (x, y, width, height),
        }
    width_ratio = width / max(1, frame_width)
    height_ratio = height / max(1, frame_height)
    if width_ratio < 0.20 or height_ratio < 0.25:
        return {
            "valid": False, "level": "warning", "status": "ROSTRO MUY LEJOS",
            "message": "Acércate un poco", "face": (x, y, width, height),
        }
    if width_ratio > 0.60 or height_ratio > 0.72:
        return {
            "valid": False, "level": "warning", "status": "ROSTRO MUY CERCA",
            "message": "Aléjate un poco", "face": (x, y, width, height),
        }
    center_x = x + width / 2
    center_y = y + height / 2
    horizontal_offset = abs(center_x - frame_width / 2) / max(1, frame_width)
    vertical_target = frame_height * 0.43
    vertical_offset = abs(center_y - vertical_target) / max(1, frame_height)
    if horizontal_offset > 0.13 or vertical_offset > 0.16:
        return {
            "valid": False, "level": "warning", "status": "CENTRA EL ROSTRO",
            "message": "Centra el rostro en la guía", "face": (x, y, width, height),
        }
    return {
        "valid": True, "level": "success", "status": "ENCUADRE ADECUADO",
        "message": "✓ Rostro bien posicionado", "face": (x, y, width, height),
    }


def _es_hikvision(device):
    return str((device or {}).get("marca", "")).strip().casefold() == "hikvision"


def _dispositivo_seleccionado(devices, name):
    """Return the single configured device selected by its visible name."""
    selected = str(name or "").strip()
    if not selected:
        return None
    return next(
        (
            device for device in devices or []
            if str((device or {}).get("nombre", "")).strip() == selected
        ),
        None,
    )


def _etiqueta_resultado(
    status, face_state="no_aplica", fingerprint_requested=True,
    facial_mode="no_aplica", access_state="no_aplica",
):
    """Return the concise per-device state shown to the operator."""
    if status not in {"created", "exists"}:
        return "Error"
    prefix = "Registrado" if status == "created" else "Ya vinculado"
    parts = [prefix]
    if face_state == "enviado_y_confirmado":
        parts.append("plantilla facial verificada; prueba física requerida")
    elif face_state == "enviado_sin_verificar":
        parts.append("rostro enviado; pendiente de verificación técnica y prueba física")
    elif face_state == "prueba_fisica_reportada":
        parts.append("prueba física facial reportada")
    elif face_state == "error":
        parts.append("error de rostro")
    if face_state not in {"no_aplica", "no_solicitado", "pendiente", "error"}:
        if access_state == "permiso_confirmado":
            parts.append("acceso asignado")
        elif access_state == "sin_permiso":
            parts.append("sin permiso de acceso")
        elif access_state == "sin_confirmar":
            parts.append("permiso de acceso sin confirmar")
    if fingerprint_requested:
        parts.append("huella pendiente")
    return " · ".join(parts)

def _detalle_verificacion_manual_rostro(device_name):
    """Return the concise operational instruction for unsupported face read-back."""
    device = str(device_name or "el checador").strip() or "el checador"
    return (
        f"{device} aceptó la foto, pero no permite comprobar la plantilla automáticamente. "
        "Haz una checada facial en el equipo; el botón sólo registrará el reporte de la prueba física."
    )

def _validar_metodos_biometricos(devices, face_requested, fingerprint_requested, face_ready):
    """Validate the methods requested for the single selected device."""
    if not fingerprint_requested and any(not _es_hikvision(device) for device in devices):
        return "Este checador requiere completar la huella directamente en el equipo."
    if not face_requested and not fingerprint_requested:
        return "Selecciona al menos un método biométrico: rostro o huella."
    if face_requested and not any(_es_hikvision(device) for device in devices):
        return "El registro facial solo está disponible en checadores Hikvision."
    if face_requested and not face_ready:
        return "Para registrar el rostro, toma una foto con la cámara o elige una imagen JPG."
    return ""


class VentanaBiometria(tk.Toplevel):
    """Alta biométrica con validación automática por número de empleado."""

    def __init__(self, parent, cargar_configuracion, guardar_configuracion, conectar_zk_robusto, aplicar_icono=None):
        super().__init__(parent)
        self._cargar = cargar_configuracion
        self._guardar = guardar_configuracion
        self._conectar_zk = conectar_zk_robusto
        self.config_local = self._cargar()
        self.face_data = None
        self.face_preview = None
        self.current_result = None
        self.busy = False
        self.camera_dialog = None
        self.camera_capture = None
        self.camera_job = None
        self.camera_photo = None
        self.latest_camera_image = None
        self.camera_capture_button = None
        self.camera_status_label = None
        self._camera_failed_reads = 0
        self._initial_focus_job = None
        if aplicar_icono:
            aplicar_icono(self)

        self.title("Registro biométrico")
        self.configure(bg=BG)
        width = min(1180, max(780, self.winfo_screenwidth() - 100))
        height = min(800, max(620, self.winfo_screenheight() - 100))
        self.geometry(f"{width}x{height}")
        self.minsize(760, 620)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._configure_styles()
        self._build_ui()
        self._center_window()
        self._initial_focus_job = self.after(80, self._focus_initial_field)

    def _focus_initial_field(self):
        self._initial_focus_job = None
        try:
            if self.winfo_exists():
                self.id_entry.focus_set()
        except tk.TclError:
            pass

    def _cancel_initial_focus(self):
        if self._initial_focus_job:
            try:
                self.after_cancel(self._initial_focus_job)
            except (tk.TclError, ValueError):
                pass
            self._initial_focus_job = None

    def _on_destroy(self, event):
        if event.widget == self:
            self._cancel_initial_focus()

    def _center_window(self):
        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - self.winfo_width()) // 2)
        y = max(0, (self.winfo_screenheight() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Biometria.Horizontal.TProgressbar", troughcolor=SOFT,
            background=ACCENT, borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT,
        )
        style.configure(
            "Biometria.TCombobox", fieldbackground=INPUT, background=INPUT,
            foreground=FG, arrowcolor=FG, bordercolor=INPUT_BORDER,
            lightcolor=INPUT_BORDER, darkcolor=INPUT_BORDER, padding=7,
            font=("Segoe UI", 10),
        )
        style.map(
            "Biometria.TCombobox",
            fieldbackground=[("readonly", INPUT), ("disabled", SOFT)],
            foreground=[("readonly", FG), ("disabled", MUTED)],
            selectbackground=[("readonly", INPUT)],
            selectforeground=[("readonly", FG)],
        )

    def _label(self, parent, text="", *, size=9, bold=False, color=FG, **kwargs):
        return tk.Label(
            parent, text=text, bg=parent.cget("bg"), fg=color,
            font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs,
        )

    def _button(self, parent, text, command, *, kind="primary", state="normal"):
        palette = {
            "primary": (ACCENT, "#FFFFFF", ACCENT_HOVER),
            "secondary": (SOFT, FG, HOVER),
            "warning": (WARNING_SOFT, WARNING, WARNING_SOFT),
            "link": (parent.cget("bg"), ACCENT_HOVER, parent.cget("bg")),
        }
        bg, fg, active = palette[kind]
        if state != "normal":
            bg, fg, active = SOFT, MUTED, SOFT
        padx, pady = ((0, 4) if kind == "link" else (11, 7) if kind == "secondary" else (15, 9))
        return tk.Button(
            parent, text=text, command=command, state=state, bg=bg, fg=fg,
            activebackground=active, activeforeground=fg, disabledforeground=MUTED,
            relief="solid" if kind == "secondary" else "flat",
            bd=1 if kind == "secondary" else 0, highlightthickness=0,
            padx=padx, pady=pady, cursor="hand2" if state == "normal" else "arrow",
            font=("Segoe UI", 9, "bold"),
        )

    def _entry(self, parent, variable):
        return tk.Entry(
            parent, textvariable=variable, bg=INPUT, fg=FG, insertbackground=FG,
            relief="solid", bd=1, highlightthickness=1,
            highlightbackground=INPUT_BORDER, highlightcolor=ACCENT,
            selectbackground=ACCENT, selectforeground="#FFFFFF", font=("Segoe UI", 11),
        )

    def _step_title(self, parent, number, title, description):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x")
        tk.Label(
            row, text=str(number), bg=ACCENT, fg="#FFFFFF", width=3, pady=4,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", anchor="n")
        text = tk.Frame(row, bg=parent.cget("bg"))
        text.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._label(text, title, size=12, bold=True).pack(anchor="w")
        self._label(text, description, color=DIM, justify="left", wraplength=680).pack(anchor="w", pady=(2, 0))

    @staticmethod
    def _separator(parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=14)

    def _build_ui(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=24, pady=(16, 11))
        title_row = tk.Frame(header, bg=BG)
        title_row.pack(fill="x")
        self._label(title_row, "Registro biométrico", size=20, bold=True).pack(side="left")
        tk.Label(
            title_row, text="EMPLEADO NUEVO", bg=ACCENT_SOFT, fg=ACCENT_HOVER,
            padx=10, pady=4, font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(12, 0))
        self._label(
            header,
            "Registra un empleado nuevo en un checador.",
            size=10, color=DIM,
        ).pack(anchor="w", pady=(5, 0))

        viewport = tk.Frame(self, bg=BG)
        viewport.pack(fill="both", expand=True, padx=(24, 9), pady=(0, 8))
        self.body_canvas = tk.Canvas(viewport, bg=BG, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(viewport, orient="vertical", command=self.body_canvas.yview)
        self.body_canvas.configure(yscrollcommand=scroll.set)
        self.body_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y", padx=(7, 0))
        self.content = tk.Frame(self.body_canvas, bg=BG)
        self.content_window = self.body_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _event: self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all")))
        self.body_canvas.bind("<Configure>", self._resize_content)
        self.bind("<MouseWheel>", self._on_mousewheel, add="+")

        self.left_panel = tk.Frame(self.content, bg=CARD, padx=18, pady=16, highlightthickness=1, highlightbackground=BORDER)
        self.right_panel = tk.Frame(self.content, bg=CARD, padx=18, pady=16, highlightthickness=1, highlightbackground=BORDER)
        self._build_form_panel()
        self._build_status_panel()
        self._reflow_layout(1180)

        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=24, pady=(0, 13))
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100, style="Biometria.Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="Listo para registrar.")
        tk.Label(footer, textvariable=self.status_var, bg=BG, fg=DIM, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(6, 0))

    def _build_form_panel(self):
        panel = self.left_panel
        self.employee_section = tk.Frame(
            panel, bg=SOFT, padx=14, pady=13,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.employee_section.pack(fill="x")
        self._step_title(
            self.employee_section, 1, "Empleado nuevo",
            "Captura el número y el nombre completo.",
        )
        fields = tk.Frame(self.employee_section, bg=SOFT)
        fields.pack(fill="x", pady=(12, 0))
        fields.columnconfigure(0, weight=1)
        fields.columnconfigure(1, weight=2)
        self.person_id_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self._label(fields, "Número de empleado *", color=DIM, bold=True).grid(row=0, column=0, sticky="w")
        self._label(fields, "Nombre completo *", color=DIM, bold=True).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.id_entry = self._entry(fields, self.person_id_var)
        self.id_entry.grid(row=1, column=0, sticky="ew", ipady=7, pady=(5, 0))
        self.name_entry = self._entry(fields, self.name_var)
        self.name_entry.grid(row=1, column=1, sticky="ew", ipady=7, padx=(12, 0), pady=(5, 0))

        self.device_section = tk.Frame(
            panel, bg=SOFT, padx=14, pady=13,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self._step_title(self.device_section, 2, "Checador", "Selecciona el equipo donde se hará el alta.")
        self.device_var = tk.StringVar(value="")
        device_names = [
            str(item.get("nombre", "")).strip()
            for item in self.config_local.get("checadores", [])
            if str(item.get("nombre", "")).strip()
        ]
        self.device_combo = ttk.Combobox(
            self.device_section, textvariable=self.device_var, values=device_names,
            state="readonly" if device_names else "disabled",
            style="Biometria.TCombobox",
        )
        self.device_combo.pack(fill="x", pady=(10, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._device_changed)
        if not device_names:
            self._label(
                self.device_section, "No hay checadores configurados.", color=WARNING,
            ).pack(anchor="w", pady=(6, 0))

        self.methods_section = tk.Frame(
            panel, bg=SOFT, padx=14, pady=13,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self._build_methods_section()

        self.action_row = tk.Frame(panel, bg=CARD)
        self.run_button = self._button(self.action_row, "Registrar en checador", self._start_provision)
        self.run_button.pack(side="left", fill="x", expand=True)
        self._button(self.action_row, "Limpiar", self._clear_person, kind="secondary").pack(side="left", padx=(8, 0))
        self.person_id_var.trace_add("write", self._employee_fields_changed)
        self.name_var.trace_add("write", self._employee_fields_changed)

    def _build_methods_section(self):
        panel = self.methods_section
        self._step_title(
            panel, 3, "Método biométrico",
            "Selecciona el método que vas a completar.",
        )

        self.face_method_var = tk.BooleanVar(value=False)
        self.fingerprint_method_var = tk.BooleanVar(value=True)
        chooser = tk.Frame(panel, bg=CARD, padx=10, pady=9, highlightthickness=1, highlightbackground=BORDER)
        chooser.pack(fill="x", pady=(10, 0))
        available_methods = tk.Frame(chooser, bg=CARD)
        available_methods.pack(fill="x")
        self.face_method_check = tk.Checkbutton(
            available_methods, text="Rostro (Hikvision)", variable=self.face_method_var,
            command=self._biometric_method_changed, bg=CARD, fg=FG, selectcolor=INPUT,
            activebackground=CARD, activeforeground=FG, font=("Segoe UI", 9, "bold"),
        )
        self.face_method_check.pack(side="left", padx=(0, 16))
        self.fingerprint_method_check = tk.Checkbutton(
            available_methods, text="Huella en el checador", variable=self.fingerprint_method_var,
            command=self._biometric_method_changed, bg=CARD, fg=FG, selectcolor=INPUT,
            activebackground=CARD, activeforeground=FG, font=("Segoe UI", 9, "bold"),
        )
        self.fingerprint_method_check.pack(side="left")

        card = tk.Frame(panel, bg=SOFT, padx=12, pady=12, highlightthickness=1, highlightbackground=BORDER)
        card.pack_forget()
        self.photo_card = card
        self.photo_guide = tk.Canvas(card, width=126, height=152, bg=SOFT, bd=0, highlightthickness=0)
        self.photo_guide.pack(side="left", padx=(0, 14))
        controls = tk.Frame(card, bg=SOFT)
        controls.pack(side="left", fill="both", expand=True)
        self.face_method_status = self._label(
            controls, "Elige una imagen o abre la cámara.",
            bold=True, color=DIM, justify="left", wraplength=480,
        )
        self.face_method_status.pack(anchor="w")
        self._label(
            controls,
            "Rostro frontal y completo · buena iluminación · fondo sencillo · sin contraluz ni lentes oscuros.",
            color=DIM, justify="left", wraplength=480,
        ).pack(anchor="w", pady=(5, 9))
        buttons = tk.Frame(controls, bg=SOFT)
        buttons.pack(fill="x")
        self.choose_photo_button = self._button(buttons, "Elegir foto JPG", self._load_face, kind="secondary", state="disabled")
        self.choose_photo_button.pack(side="left")
        self.camera_button = self._button(buttons, "Abrir cámara con guía", self._capture_camera, kind="secondary", state="disabled")
        self.camera_button.pack(side="left", padx=(7, 0))
        self.discard_button = self._button(buttons, "Quitar", self._discard_face, kind="link", state="disabled")
        self.discard_button.pack(side="left", padx=(7, 0))
        self.face_status = self._label(controls, "", color=DIM, justify="left", wraplength=480)
        self.face_status.pack(anchor="w", pady=(7, 0))
        self._render_face_guide()

        self.fingerprint_method_card = tk.Frame(
            panel, bg=WARNING_SOFT, padx=12, pady=9,
        )
        self._label(
            self.fingerprint_method_card,
            "La huella se captura directamente en el checador después de crear al empleado.",
            color=FG, justify="left", wraplength=680,
        ).pack(anchor="w")

        self.external_reader_card = tk.Frame(
            panel, bg=ACCENT_SOFT, padx=12, pady=11,
            highlightthickness=1, highlightbackground=ACCENT,
        )
        self.external_reader_card.pack(fill="x", pady=(10, 0))
        external_header = tk.Frame(self.external_reader_card, bg=ACCENT_SOFT)
        external_header.pack(fill="x")
        self._label(
            external_header, "Lector de huella externo", bold=True, color=ACCENT_HOVER,
        ).pack(side="left")
        tk.Label(
            external_header, text="PRÓXIMAMENTE", bg=ACCENT, fg="#FFFFFF",
            padx=8, pady=2, font=("Segoe UI", 7, "bold"),
        ).pack(side="right")
        self._label(
            self.external_reader_card,
            "Permitirá capturar la huella desde un lector USB y asociarla al empleado.",
            color=FG, justify="left", wraplength=620,
        ).pack(anchor="w", pady=(5, 9))
        self.external_reader_button = self._button(
            self.external_reader_card,
            "Registrar con lector externo",
            lambda: None,
            kind="secondary",
            state="disabled",
        )
        self.external_reader_button.pack(fill="x")

    def _build_status_panel(self):
        panel = self.right_panel
        self._step_title(panel, 4, "Resultado", "Aquí verás el estado del checador seleccionado.")
        self.results_empty = tk.Frame(panel, bg=SOFT, padx=16, pady=21, highlightthickness=1, highlightbackground=BORDER)
        self.results_empty.pack(fill="x", pady=(12, 0))
        self._label(self.results_empty, "Aún no hay resultados", size=10, bold=True, color=DIM).pack()
        self._label(
            self.results_empty,
            "Captura los datos, elige un checador y registra al empleado.",
            color=DIM, justify="center", wraplength=370,
        ).pack(pady=(5, 0))

        self.result_card = tk.Frame(
            panel, bg=SOFT, padx=16, pady=15,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.result_device_var = tk.StringVar(value="")
        self.result_state_var = tk.StringVar(value="")
        self.result_detail_var = tk.StringVar(value="")
        self.result_device_label = self._label(
            self.result_card, textvariable=self.result_device_var,
            size=13, bold=True, anchor="w",
        )
        self.result_device_label.pack(fill="x", anchor="w")
        self.result_state_label = self._label(
            self.result_card, textvariable=self.result_state_var,
            size=10, bold=True, color=DIM, anchor="w", justify="left", wraplength=360,
        )
        self.result_state_label.pack(fill="x", anchor="w", pady=(7, 0))
        self.result_detail_label = self._label(
            self.result_card, textvariable=self.result_detail_var,
            color=DIM, anchor="w", justify="left", wraplength=360,
        )
        self.result_card.bind("<Configure>", self._resize_result_labels, add="+")

        self.face_verification_help = tk.Frame(
            panel, bg=ACCENT_SOFT, padx=12, pady=12,
            highlightthickness=1, highlightbackground=ACCENT,
        )
        self._label(
            self.face_verification_help, "Prueba física facial pendiente", bold=True, color=ACCENT_HOVER,
        ).pack(anchor="w")
        self.face_verification_message = self._label(
            self.face_verification_help,
            "Haz una checada facial en el checador. El reporte manual no consulta ni verifica técnicamente al equipo.",
            color=FG, anchor="w", justify="left", wraplength=440,
        )
        self.face_verification_message.pack(fill="x", anchor="w", pady=(4, 9))
        self.face_verification_button = self._button(
            self.face_verification_help, "Registrar prueba física realizada",
            self._confirm_face_verification, kind="secondary", state="disabled",
        )
        self.face_verification_button.pack(fill="x")

        self.fingerprint_help = tk.Frame(
            panel, bg=WARNING_SOFT, padx=12, pady=12,
            highlightthickness=1, highlightbackground=WARNING,
        )
        self._label(
            self.fingerprint_help, "Huella pendiente", bold=True, color=WARNING,
        ).pack(anchor="w")
        self._label(
            self.fingerprint_help,
            "Captura la huella directamente en el checador y confirma cuando el equipo la acepte.",
            color=FG, justify="left", wraplength=440,
        ).pack(anchor="w", pady=(4, 9))
        self.fingerprint_button = self._button(
            self.fingerprint_help, "Confirmar huella capturada",
            self._confirm_fingerprint, kind="warning", state="disabled",
        )
        self.fingerprint_button.pack(fill="x")

    def _resize_content(self, event):
        self.body_canvas.itemconfigure(self.content_window, width=max(1, event.width))
        self._reflow_layout(event.width)

    def _reflow_layout(self, width):
        self.left_panel.grid_forget()
        self.right_panel.grid_forget()
        for column in range(2):
            self.content.columnconfigure(column, weight=0, minsize=0, uniform="")
        if width >= 1040:
            self.content.columnconfigure(0, weight=3, uniform="panels")
            self.content.columnconfigure(1, weight=2, uniform="panels")
            self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
            self.right_panel.grid(row=0, column=1, sticky="nsew")
        else:
            self.content.columnconfigure(0, weight=1)
            self.left_panel.grid(row=0, column=0, sticky="ew")
            self.right_panel.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.after_idle(self._resize_result_labels)

    def _resize_result_labels(self, event=None):
        """Keep result text aligned to the real card width, not an estimated column width."""
        card_width = event.width if event is not None else self.result_card.winfo_width()
        if card_width <= 1:
            return
        available_width = max(180, card_width - 36)
        self.result_state_label.config(wraplength=available_width)
        self.result_detail_label.config(wraplength=available_width)
        self.face_verification_message.config(wraplength=available_width)

    def _on_mousewheel(self, event):
        try:
            self.body_canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    def _device(self, name):
        return _dispositivo_seleccionado(self.config_local.get("checadores", []), name)

    def _selected_devices(self):
        device = self._device(self.device_var.get())
        return [device] if device else []

    def _employee_fields_changed(self, *_args):
        ready = bool(self.person_id_var.get().strip() and self.name_var.get().strip())
        if ready:
            if not self.device_section.winfo_manager():
                self.device_section.pack(fill="x", pady=(10, 0))
        else:
            self.device_var.set("")
            self.device_section.pack_forget()
            self.methods_section.pack_forget()
            self.action_row.pack_forget()
            self.face_method_var.set(False)
            self._discard_face()
            self._biometric_method_changed()

    def _device_changed(self, _event=None):
        if self.device_var.get():
            if not self.methods_section.winfo_manager():
                self.methods_section.pack(fill="x", pady=(10, 0))
            if not self.action_row.winfo_manager():
                self.action_row.pack(fill="x", pady=(16, 0))
            device = self._device(self.device_var.get())
            if device and not _es_hikvision(device):
                self.face_method_var.set(False)
                self.fingerprint_method_var.set(True)
        else:
            self.methods_section.pack_forget()
            self.action_row.pack_forget()
        self._update_photo_visibility()

    def _has_selected_hikvision(self):
        return any(_es_hikvision(device) for device in self._selected_devices())

    def _update_photo_visibility(self):
        if self._has_selected_hikvision():
            self.face_method_check.config(state="normal")
        else:
            self.face_method_var.set(False)
            self.photo_card.pack_forget()
            self._discard_face()
        self._biometric_method_changed()

    def _biometric_method_changed(self):
        face_enabled = self._has_selected_hikvision() and self.face_method_var.get() and not self.busy
        method_state = "disabled" if self.busy else "normal"
        self.face_method_check.config(state=method_state if self._has_selected_hikvision() else "disabled")
        self.fingerprint_method_check.config(state=method_state)
        self.external_reader_button.config(state="disabled", cursor="arrow")
        if self._has_selected_hikvision() and self.face_method_var.get():
            if not self.photo_card.winfo_manager():
                self.photo_card.pack(fill="x", pady=(10, 0), before=self.external_reader_card)
        else:
            self.photo_card.pack_forget()
        for button in (self.choose_photo_button, self.camera_button):
            button.config(
                state="normal" if face_enabled else "disabled",
                bg=SOFT,
                fg=FG if face_enabled else MUTED,
                cursor="hand2" if face_enabled else "arrow",
            )
        self.face_method_status.config(
            text=(
                "Elige una imagen o abre la cámara."
                if face_enabled
                else "Activa rostro para cargar la fotografía."
            ),
            fg=SUCCESS if face_enabled else DIM,
        )
        if not self.face_method_var.get() and self.face_data:
            self._discard_face()
        fingerprint_selected = bool(self.fingerprint_method_var.get())
        if fingerprint_selected:
            if not self.fingerprint_method_card.winfo_manager():
                self.fingerprint_method_card.pack(
                    fill="x", pady=(8, 0), before=self.external_reader_card,
                )
        else:
            self.fingerprint_method_card.pack_forget()

    def _set_form_controls_state(self, busy):
        entry_state = "disabled" if busy else "normal"
        self.id_entry.config(state=entry_state)
        self.name_entry.config(state=entry_state)
        combo_state = "disabled" if busy or not self.device_combo.cget("values") else "readonly"
        self.device_combo.config(state=combo_state)

    def _render_face_guide(self):
        canvas = self.photo_guide
        canvas.delete("all")
        if self.face_data:
            try:
                from PIL import ImageTk
                image = self.face_data["image"].copy()
                image.thumbnail((118, 144))
                self.face_preview = ImageTk.PhotoImage(image)
                canvas.create_image(63, 76, image=self.face_preview)
                canvas.create_rectangle(3, 3, 123, 149, outline=SUCCESS, width=3)
                return
            except Exception:
                self.face_preview = None
        canvas.create_rectangle(4, 4, 122, 148, outline=BORDER)
        canvas.create_oval(40, 22, 86, 72, outline=ACCENT, width=3)
        canvas.create_arc(24, 64, 102, 140, start=20, extent=140, style="arc", outline=ACCENT, width=3)
        for points in ((13, 32, 13, 13, 32, 13), (94, 13, 113, 13, 113, 32), (13, 120, 13, 139, 32, 139), (94, 139, 113, 139, 113, 120)):
            canvas.create_line(*points, fill=ACCENT, width=2)

    def _load_face(self):
        if not self.face_method_var.get():
            return messagebox.showinfo(
                "Método facial", "Activa primero la opción “Registrar rostro facial”.", parent=self,
            )
        path = filedialog.askopenfilename(
            parent=self, title="Seleccionar foto del empleado",
            filetypes=[("Fotografía JPG", "*.jpg *.jpeg"), ("Todos", "*.*")],
        )
        if not path:
            return
        try:
            self._set_face(preparar_jpeg(path=path))
        except Exception as exc:
            messagebox.showerror("Foto no válida", str(exc), parent=self)

    def _capture_camera(self):
        if not self.face_method_var.get():
            return messagebox.showinfo(
                "Método facial", "Activa primero la opción “Registrar rostro facial”.", parent=self,
            )
        try:
            import cv2
        except ImportError as exc:
            return messagebox.showinfo(
                "OpenCV requerido",
                "Para abrir la cámara y mostrar la guía se necesita OpenCV.\n\n"
                "Instala la dependencia con:\npython -m pip install opencv-python\n\n"
                "Mientras tanto puedes usar “Elegir foto JPG”.\n\n"
                f"Detalle técnico: {exc}",
                parent=self,
            )
        try:
            from PIL import Image, ImageDraw, ImageTk
        except ImportError as exc:
            return messagebox.showinfo(
                "Pillow requerido",
                "OpenCV puede abrir la cámara, pero Pillow es necesario para mostrar y preparar la imagen.\n\n"
                "Instala la dependencia con:\npython -m pip install Pillow\n\n"
                f"Detalle técnico: {exc}",
                parent=self,
            )
        if self.camera_dialog and self.camera_dialog.winfo_exists():
            self.camera_dialog.lift()
            self.camera_dialog.focus_force()
            return
        capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(0)
        if not capture.isOpened():
            capture.release()
            return messagebox.showerror(
                "Cámara no disponible",
                "OpenCV está instalado, pero no pudo abrir la cámara.\n\n"
                "Revisa que Windows tenga permiso para usarla y que otra aplicación no la esté ocupando.",
                parent=self,
            )

        dialog = tk.Toplevel(self)
        self.camera_dialog = dialog
        self.camera_capture = capture
        self._camera_cv2 = cv2
        self._camera_image_class = Image
        self._camera_draw_class = ImageDraw
        self._camera_imagetk_class = ImageTk
        self.latest_camera_image = None
        self._camera_failed_reads = 0
        self._camera_valid_streak = 0
        self._camera_frame_number = 0
        self._camera_last_evaluation = None
        self._camera_face_detector = None
        try:
            cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            detector = cv2.CascadeClassifier(cascade_path)
            if not detector.empty():
                self._camera_face_detector = detector
        except Exception:
            self._camera_face_detector = None
        dialog.title("Tomar foto facial")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.protocol("WM_DELETE_WINDOW", self._close_camera)
        container = tk.Frame(dialog, bg=CARD, padx=18, pady=16, highlightthickness=1, highlightbackground=BORDER)
        container.pack(padx=18, pady=18)

        header = tk.Frame(container, bg=CARD)
        header.pack(fill="x")
        header_text = tk.Frame(header, bg=CARD)
        header_text.pack(side="left", fill="x", expand=True)
        self._label(header_text, "Toma la foto del rostro", size=15, bold=True).pack(anchor="w")
        self._label(
            header_text,
            "Coloca tu cara dentro del óvalo. Solo toma unos segundos.",
            color=DIM,
        ).pack(anchor="w", pady=(3, 0))
        self.camera_status_label = tk.Label(
            header, text="INICIANDO CÁMARA…", bg=ACCENT_SOFT, fg=ACCENT_HOVER,
            font=("Segoe UI", 8, "bold"), padx=10, pady=6,
        )
        self.camera_status_label.pack(side="right", anchor="n", padx=(16, 0))

        content = tk.Frame(container, bg=CARD)
        content.pack(fill="both", pady=(12, 0))
        video_panel = tk.Frame(content, bg=CARD)
        video_panel.pack(side="left", anchor="n")
        self.camera_canvas = tk.Canvas(
            video_panel, width=640, height=480, bg="#050A13", bd=0,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.camera_canvas.pack()
        self.camera_canvas.create_text(
            320, 220, text="Preparando vista previa…", fill="#CBD5E1",
            font=("Segoe UI", 13, "bold"),
        )
        self.camera_canvas.create_text(
            320, 250, text="La captura se habilitará cuando llegue la imagen.", fill="#94A3B8",
            font=("Segoe UI", 10),
        )

        tips = tk.Frame(
            content, bg=SOFT, padx=16, pady=15,
            highlightthickness=1, highlightbackground=BORDER,
        )
        tips.pack(side="left", fill="y", padx=(14, 0))
        self._label(tips, "Antes de capturar", size=11, bold=True).pack(anchor="w", pady=(0, 10))
        for text, color in (
            ("✓  Rostro completo y de frente", SUCCESS),
            ("✓  Buena luz sobre la cara", SUCCESS),
            ("✓  Sin lentes oscuros ni cubrebocas", SUCCESS),
            ("✕  Evita contraluz o sombras fuertes", DANGER),
            ("✕  Evita un fondo demasiado brillante", DANGER),
        ):
            tk.Label(
                tips, text=text, bg=SOFT, fg=color, justify="left", anchor="w",
                wraplength=210, font=("Segoe UI", 9, "bold"),
            ).pack(fill="x", anchor="w", pady=4)
        tk.Frame(tips, bg=BORDER, height=1).pack(fill="x", pady=12)
        self._label(
            tips,
            "La guía evalúa únicamente el encuadre, tamaño e iluminación. No reconoce ni valida la identidad.",
            color=MUTED, justify="left", wraplength=210,
        ).pack(anchor="w")

        actions = tk.Frame(container, bg=CARD)
        actions.pack(fill="x", pady=(11, 0))
        self.camera_capture_button = self._button(
            actions, "Capturar foto", self._accept_camera_frame, state="disabled",
        )
        self.camera_capture_button.pack(side="left", fill="x", expand=True)
        self._button(actions, "Cancelar", self._close_camera, kind="secondary").pack(side="left", padx=(8, 0))
        self._label(
            container,
            "Podrás revisar la foto antes de registrar al empleado.",
            color=MUTED,
        ).pack(anchor="w", pady=(8, 0))
        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        dialog.focus_force()
        self._camera_tick()

    def _set_camera_status(self, text, foreground, background):
        if self.camera_status_label and self.camera_status_label.winfo_exists():
            self.camera_status_label.config(text=text, fg=foreground, bg=background)

    def _set_camera_capture_enabled(self, enabled):
        if self.camera_capture_button and self.camera_capture_button.winfo_exists():
            self.camera_capture_button.config(
                state="normal" if enabled else "disabled",
                bg=ACCENT if enabled else SOFT,
                fg="#FFFFFF" if enabled else MUTED,
                cursor="hand2" if enabled else "arrow",
            )

    def _draw_camera_overlay(self, image):
        preview = image.resize((640, 480)).convert("RGBA")
        overlay = self._camera_image_class.new("RGBA", preview.size, (2, 6, 15, 145))
        mask = self._camera_image_class.new("L", preview.size, 145)
        mask_draw = self._camera_draw_class.Draw(mask)
        mask_draw.ellipse((198, 48, 442, 366), fill=0)
        overlay.putalpha(mask)
        return self._camera_image_class.alpha_composite(preview, overlay).convert("RGB")

    def _evaluate_camera_frame(self, frame):
        if self._camera_face_detector is None:
            return {
                "valid": False, "level": "warning", "status": "GUÍA NO DISPONIBLE",
                "message": "No se pudo iniciar la detección de encuadre", "face": None,
            }
        cv2 = self._camera_cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        detection_gray = gray
        try:
            detection_gray = cv2.equalizeHist(gray)
        except Exception:
            pass
        faces = self._camera_face_detector.detectMultiScale(
            detection_gray, scaleFactor=1.12, minNeighbors=5, minSize=(80, 80),
        )
        height, width = gray.shape[:2]
        return evaluar_encuadre_facial(width, height, faces, brightness)

    @staticmethod
    def _camera_palette(level):
        if level == "success":
            return SUCCESS, SUCCESS_SOFT
        if level == "warning":
            return WARNING, WARNING_SOFT
        return DANGER, WARNING_SOFT

    def _camera_tick(self):
        if not self.camera_dialog or not self.camera_dialog.winfo_exists() or not self.camera_capture:
            return
        if self.camera_job:
            try:
                self.camera_dialog.after_cancel(self.camera_job)
            except tk.TclError:
                pass
            self.camera_job = None
        ok, frame = self.camera_capture.read()
        if ok:
            self._camera_failed_reads = 0
            self._camera_frame_number += 1
            if self._camera_frame_number % 3 == 1 or self._camera_last_evaluation is None:
                try:
                    self._camera_last_evaluation = self._evaluate_camera_frame(frame)
                except Exception:
                    self._camera_last_evaluation = {
                        "valid": False, "level": "warning", "status": "GUÍA NO DISPONIBLE",
                        "message": "No se pudo analizar el encuadre", "face": None,
                    }
            evaluation = self._camera_last_evaluation
            if evaluation["valid"] and self._camera_frame_number % 3 == 1:
                self._camera_valid_streak += 1
            elif not evaluation["valid"]:
                self._camera_valid_streak = 0
            stable = evaluation["valid"] and self._camera_valid_streak >= 5
            rgb_frame = self._camera_cv2.cvtColor(frame, self._camera_cv2.COLOR_BGR2RGB)
            image = self._camera_image_class.fromarray(rgb_frame)
            self.latest_camera_image = image.copy() if stable else None
            preview = self._draw_camera_overlay(image)
            self.camera_photo = self._camera_imagetk_class.PhotoImage(preview)
            canvas = self.camera_canvas
            canvas.delete("all")
            canvas.create_image(0, 0, image=self.camera_photo, anchor="nw")
            guide_color, status_background = self._camera_palette(evaluation["level"])
            canvas.create_oval(198, 48, 442, 366, outline=guide_color, width=4)
            for points in (
                (170, 98, 170, 34, 234, 34),
                (406, 34, 470, 34, 470, 98),
                (170, 382, 170, 446, 234, 446),
                (406, 446, 470, 446, 470, 382),
            ):
                canvas.create_line(*points, fill=guide_color, width=3)
            canvas.create_rectangle(0, 400, 640, 480, fill="#07101F", outline="")
            canvas.create_text(
                320, 420, text=evaluation["message"],
                fill="#FFFFFF", font=("Segoe UI", 11, "bold"),
            )
            canvas.create_text(
                320, 448,
                text=("✓ LISTO PARA CAPTURAR" if stable else "Mantén la posición unos segundos"),
                fill="#CBD5E1", font=("Segoe UI", 10),
            )
            status = "LISTO PARA CAPTURAR" if stable else evaluation["status"]
            self._set_camera_status(status, guide_color, status_background)
            self._set_camera_capture_enabled(stable)
        else:
            self._camera_failed_reads += 1
            if self._camera_failed_reads >= 12:
                self.latest_camera_image = None
                self._set_camera_status("NO SE RECIBE IMAGEN", DANGER, WARNING_SOFT)
                self._set_camera_capture_enabled(False)
        self.camera_job = self.camera_dialog.after(35, self._camera_tick)

    def _accept_camera_frame(self):
        if self.latest_camera_image is None:
            return messagebox.showwarning(
                "Cámara", "Espera a que aparezca la imagen de la cámara.", parent=self.camera_dialog,
            )
        try:
            data = preparar_jpeg(image=self.latest_camera_image)
        except Exception as exc:
            return messagebox.showerror("Foto no válida", str(exc), parent=self.camera_dialog)
        self._close_camera()
        self._set_face(data)

    def _close_camera(self):
        if self.camera_dialog and self.camera_job:
            try:
                self.camera_dialog.after_cancel(self.camera_job)
            except tk.TclError:
                pass
        self.camera_job = None
        if self.camera_capture:
            try:
                self.camera_capture.release()
            except Exception:
                pass
        self.camera_capture = None
        self.latest_camera_image = None
        self.camera_photo = None
        self.camera_capture_button = None
        self.camera_status_label = None
        self._camera_failed_reads = 0
        self._camera_valid_streak = 0
        self._camera_frame_number = 0
        self._camera_last_evaluation = None
        self._camera_face_detector = None
        if self.camera_dialog and self.camera_dialog.winfo_exists():
            try:
                self.camera_dialog.grab_release()
            except tk.TclError:
                pass
            self.camera_dialog.destroy()
        self.camera_dialog = None

    def _set_face(self, data):
        self.face_data = data
        warning = f" {data['warning']}" if data.get("warning") else ""
        self.face_status.config(
            text=f"Foto lista: {data['width']}×{data['height']} px · {data['size_kb']} KB.{warning}",
            fg=SUCCESS,
        )
        self.discard_button.config(state="normal", fg=ACCENT_HOVER, cursor="hand2")
        self._render_face_guide()

    def _discard_face(self):
        self.face_data = None
        self.face_preview = None
        if hasattr(self, "face_status"):
            self.face_status.config(text="", fg=DIM)
            self.discard_button.config(state="disabled", fg=MUTED, cursor="arrow")
            self._render_face_guide()

    def _start_provision(self):
        if self.busy:
            return
        try:
            person = normalizar_persona(self.person_id_var.get(), self.name_var.get())
        except Exception as exc:
            return messagebox.showwarning("Revisa los datos", str(exc), parent=self)
        devices = self._selected_devices()
        if not devices:
            return messagebox.showwarning(
                "Falta elegir el checador", "Selecciona un checador para continuar.", parent=self,
            )
        device = devices[0]
        face_requested = bool(self.face_method_var.get())
        fingerprint_requested = bool(self.fingerprint_method_var.get())
        methods_error = _validar_metodos_biometricos(
            devices, face_requested, fingerprint_requested, bool(self.face_data),
        )
        if methods_error:
            return messagebox.showwarning("Revisa los métodos biométricos", methods_error, parent=self)
        self.busy = True
        self.run_button.config(state="disabled", bg=SOFT, fg=MUTED, cursor="arrow")
        self._set_form_controls_state(True)
        self._biometric_method_changed()
        self.current_result = None
        self.results_empty.pack_forget()
        if not self.result_card.winfo_manager():
            self.result_card.pack(fill="x", pady=(12, 0))
        self.result_card.config(highlightbackground=BORDER)
        self.result_device_var.set(device["nombre"])
        self.result_state_var.set("Registrando empleado...")
        self.result_state_label.config(fg=DIM)
        self.result_detail_var.set("")
        self.result_detail_label.pack_forget()
        self.face_verification_help.pack_forget()
        self.fingerprint_help.pack_forget()
        self.status_var.set(f"Registrando en {device['nombre']}...")
        self.progress["value"] = 0
        face_bytes = bytes(self.face_data["bytes"]) if face_requested and self.face_data else None
        threading.Thread(
            target=self._provision_worker,
            args=(person, device, face_bytes, fingerprint_requested),
            daemon=True,
        ).start()

    def _provision_worker(self, person, device, face_bytes, fingerprint_requested):
        config = self._cargar()
        conn = None
        status = "error"
        detail = ""
        face_state = "no_aplica"
        facial_mode = "no_aplica"
        access_state = "no_aplica"
        simple_state = "Error"
        try:
            if _es_hikvision(device):
                status, detail, record = hikvision_crear_y_verificar_persona(
                    device, person["nombre"], {
                        "person_id": person["person_id"],
                        "user_verify_mode": "face" if face_bytes else "",
                    },
                )
                if status in {"created", "exists"}:
                    access_summary = hikvision_resumen_acceso(record)
                    facial_mode = access_summary["modo"]
                    access_state = access_summary["acceso"]
                    face_state = "pendiente" if face_bytes else "no_solicitado"
                    if face_bytes:
                        face_state, face_detail = hikvision_enviar_rostro(
                            device, person["person_id"], face_bytes, nombre=person["nombre"],
                        )
                        detail = f"{detail} {face_detail}".strip()
                        if face_state != "error":
                            mode_ok, facial_mode, verified_record, mode_detail = hikvision_configurar_modo_rostro(
                                device, person["person_id"],
                            )
                            detail = f"{detail} {mode_detail}".strip()
                            if verified_record:
                                access_summary = hikvision_resumen_acceso(verified_record)
                                facial_mode = access_summary["modo"]
                                access_state = access_summary["acceso"]
                            elif not mode_ok:
                                facial_mode = facial_mode or "error"
                    simple_state = _etiqueta_resultado(
                        status, face_state, fingerprint_requested, facial_mode, access_state,
                    )
            else:
                conn, _mode = self._conectar_zk(
                    device.get("ip"), device.get("port"),
                    modo_preferido=device.get("modo_conexion"),
                )
                status, detail, _record = crear_y_verificar_zkteco(
                    conn, person["person_id"], person["nombre"],
                )
                if status in {"created", "exists"}:
                    simple_state = _etiqueta_resultado(
                        status, fingerprint_requested=fingerprint_requested,
                    )

            ok = status in {"created", "exists"}
            if ok:
                upsert_estado_biometrico(
                    config, person["person_id"], person["nombre"], device["nombre"],
                    confirmado=False,
                    huella=(
                        "requiere_captura_en_dispositivo"
                        if fingerprint_requested else "no_solicitada"
                    ),
                    rostro=face_state,
                    modo_rostro=facial_mode,
                    acceso=access_state,
                    detalle=detail,
                )
                device_config = next(
                    (item for item in config.get("checadores", []) if item.get("nombre") == device.get("nombre")),
                    None,
                )
                if device_config is not None:
                    names = {str(item) for item in device_config.get("empleados_descargados", [])}
                    names.add(person["nombre"])
                    device_config["empleados_descargados"] = sorted(names)
            result = (device["nombre"], simple_state, ok, detail, status)
        except Exception as exc:
            result = (device["nombre"], "Error", False, str(exc), "error")
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass
        self._guardar(config)
        self.after(0, self._show_progress, device["nombre"], 100)
        self.after(0, self._provision_finished, person, result)

    def _show_progress(self, device_name, value):
        self.progress.config(value=value)
        self.status_var.set(f"Revisando y registrando en {device_name}... {value}%")

    def _provision_finished(self, person, result):
        self.busy = False
        self.run_button.config(state="normal", bg=ACCENT, fg="#FFFFFF", cursor="hand2")
        self._set_form_controls_state(False)
        self._biometric_method_changed()
        device, state, ok, detail, status = result
        state_key = state.casefold()
        pending_fingerprint = ok and "huella pendiente" in state_key
        has_warning = ok and (
            "pendiente" in state_key or "sin confirmar" in state_key
            or "error de rostro" in state_key
        )
        color = SUCCESS if ok and not has_warning else WARNING if ok else DANGER
        self.current_result = {
            "device": device,
            "state": state,
            "status": status,
            "person_id": person["person_id"],
            "person_name": person["nombre"],
            "pending_face_verification": ok and (
                "prueba física requerida" in state_key or "prueba física" in state_key
            ) and "prueba física facial reportada" not in state_key,
            "face_not_technically_verified": ok and "pendiente de verificación técnica" in state_key,
            "pending_fingerprint": pending_fingerprint,
        }
        self.result_device_var.set(device)
        self.result_state_var.set(state)
        self.result_state_label.config(fg=color)
        self.result_card.config(highlightbackground=color)
        pending_face_verification = self.current_result["pending_face_verification"]
        show_detail = not ok or pending_face_verification
        if ok:
            self.result_detail_var.set(
                _detalle_verificacion_manual_rostro(device)
                if pending_face_verification else ""
            )
            if show_detail:
                self.result_detail_label.pack(fill="x", anchor="w", pady=(7, 0))
            else:
                self.result_detail_label.pack_forget()
            self.status_var.set(
                f"{person['nombre']}: "
                + ("ya vinculado." if status == "exists" else "registrado correctamente.")
            )
        else:
            self.result_detail_var.set(detail)
            self.result_detail_label.pack(fill="x", anchor="w", pady=(7, 0))
            self.status_var.set(f"No se pudo registrar a {person['nombre']} en {device}.")
        self.progress["value"] = 100
        self._update_face_verification_button()
        self._update_fingerprint_button()
        if not ok:
            messagebox.showerror(
                "No se pudo registrar",
                detail or f"El checador {device} no completó el alta.",
                parent=self,
            )

    def _update_fingerprint_button(self):
        pending = bool(self.current_result and self.current_result.get("pending_fingerprint"))
        if pending:
            if not self.fingerprint_help.winfo_manager():
                self.fingerprint_help.pack(fill="x", pady=(11, 0))
        else:
            self.fingerprint_help.pack_forget()
        self.fingerprint_button.config(
            state="normal" if pending and not self.busy else "disabled",
            bg=WARNING_SOFT if pending else SOFT,
            fg=WARNING if pending else MUTED,
            cursor="hand2" if pending else "arrow",
        )

    def _update_face_verification_button(self):
        pending = bool(self.current_result and self.current_result.get("pending_face_verification"))
        if pending:
            if not self.face_verification_help.winfo_manager():
                self.face_verification_help.pack(fill="x", pady=(11, 0))
        else:
            self.face_verification_help.pack_forget()
        self.face_verification_button.config(
            state="normal" if pending and not self.busy else "disabled",
            bg=SOFT,
            fg=FG if pending else MUTED,
            cursor="hand2" if pending else "arrow",
        )

    def _confirm_face_verification(self):
        if not self.current_result or not self.current_result.get("pending_face_verification"):
            return
        device = str(self.current_result["device"])
        person_name = str(self.current_result["person_name"])
        if not messagebox.askyesno(
            "Registrar prueba física facial",
            (
                f"¿Realizaste una checada facial de prueba para {person_name} en {device}?\n\n"
                "Esto sólo guarda tu reporte manual; no consulta ni verifica técnicamente al checador."
            ),
            parent=self,
        ):
            return
        try:
            config = self._cargar()
            upsert_estado_biometrico(
                config,
                self.current_result["person_id"],
                person_name,
                device,
                confirmado=False,
                rostro="prueba_fisica_reportada",
                detalle=(
                    "El operador reportó una prueba física facial en el checador; "
                    "no hubo verificación técnica automática de la plantilla."
                ),
            )
            self._guardar(config)
            previous = str(self.current_result["state"])
            state = previous.replace(
                "rostro enviado; pendiente de verificación técnica y prueba física",
                "prueba física facial reportada; pendiente de verificación técnica",
            ).replace(
                "plantilla facial verificada; prueba física requerida",
                "plantilla facial verificada; prueba física facial reportada",
            )
            self.current_result["state"] = state
            self.current_result["pending_face_verification"] = False
            self.result_state_var.set(state)
            self.result_detail_var.set("")
            self.result_detail_label.pack_forget()
            self.status_var.set("Prueba física facial reportada; no se marcó como verificación técnica.")
            self._update_face_verification_button()
            self._refresh_result_color()
        except Exception as exc:
            messagebox.showerror("No se pudo guardar", str(exc), parent=self)

    def _refresh_result_color(self):
        has_pending_confirmation = bool(self.current_result and (
            self.current_result.get("pending_face_verification")
            or self.current_result.get("pending_fingerprint")
            or self.current_result.get("face_not_technically_verified")
        ))
        color = WARNING if has_pending_confirmation else SUCCESS
        self.result_state_label.config(fg=color)
        self.result_card.config(highlightbackground=color)
    def _confirm_fingerprint(self):
        if not self.current_result or not self.current_result.get("pending_fingerprint"):
            return
        device = str(self.current_result["device"])
        if not messagebox.askyesno(
            "Confirmar huella",
            f"¿{device} aceptó correctamente la huella del empleado?",
            parent=self,
        ):
            return
        try:
            config = self._cargar()
            upsert_estado_biometrico(
                config,
                self.current_result["person_id"], self.current_result["person_name"], device,
                confirmado="huella", huella="captura_reportada_sin_verificar",
                detalle="Nóminas confirmó que el checador aceptó la captura de huella.",
            )
            self._guardar(config)
            previous = str(self.current_result["state"])
            base = previous.split("· huella pendiente", 1)[0].rstrip()
            state = f"{base} · huella lista"
            self.current_result["state"] = state
            self.current_result["pending_fingerprint"] = False
            self.result_state_var.set(state)
            self.status_var.set("Huella confirmada. El registro biométrico quedó completo.")
            self._update_fingerprint_button()
            self._refresh_result_color()
        except Exception as exc:
            messagebox.showerror("No se pudo guardar", str(exc), parent=self)

    def _clear_person(self):
        if self.busy:
            return
        self.person_id_var.set("")
        self.name_var.set("")
        self.device_var.set("")
        self.face_method_var.set(False)
        self.fingerprint_method_var.set(True)
        self._discard_face()
        self.methods_section.pack_forget()
        self.current_result = None
        self.result_card.pack_forget()
        self.face_verification_help.pack_forget()
        self.fingerprint_help.pack_forget()
        if not self.results_empty.winfo_manager():
            self.results_empty.pack(fill="x", pady=(12, 0))
        self.progress["value"] = 0
        self.status_var.set("Listo para registrar.")
        self._update_fingerprint_button()
        self.body_canvas.yview_moveto(0)
        self.id_entry.focus_set()

    def _cerrar(self):
        if self.busy:
            return messagebox.showinfo(
                "Registro en proceso", "Espera a que termine el registro antes de cerrar.", parent=self,
            )
        self._cancel_initial_focus()
        self._close_camera()
        self.face_data = None
        self.destroy()