"""Carga las definiciones de la aplicación sin iniciar la interfaz.

La aplicación histórica concentra ventanas y lógica en ``main v4.py`` y crea
la ventana raíz al final del archivo. Este cargador ejecuta únicamente la parte
de definiciones para que las pruebas puedan importar las ventanas sin abrir la
GUI.
"""
from __future__ import annotations

import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(PROJECT_ROOT, "main v4.py")
TEST_CONFIG_DIR = tempfile.mkdtemp(prefix="asistencia-tests-")

os.environ["ASISTENCIA_CONFIG_DIR"] = TEST_CONFIG_DIR

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_manager

config_manager.APP_DIR = TEST_CONFIG_DIR
config_manager.PATH_CONFIG = os.path.join(TEST_CONFIG_DIR, "config_global.json")
config_manager.PATH_CONFIG_ANTERIOR = ""
config_manager.PATH_CONFIG_LOCAL = ""
config_manager.invalidar_cache_config()

with open(MAIN_PATH, "r", encoding="utf-8") as source_file:
    _source = source_file.read()

for marker in (
    "\n# --- INTERFAZ GRAFICA MAIN ---",
    "\n# --- INTERFAZ GR",
    "\nconfigurar_app_windows()\nroot = tk.Tk()",
):
    index = _source.find(marker)
    if index != -1:
        _source = _source[:index]
        break

_globals = {
    "__file__": MAIN_PATH,
    "__name__": "main_v4_original",
    "__builtins__": __builtins__,
}
exec(_source, _globals)

_globals.setdefault("cb_checador", None)
_globals.setdefault("checador_var", None)
_globals.setdefault("period_frame_widgets", [])
_globals.setdefault("modo_reporte", None)


def _actualizar_combos_globales_seguro():
    combo = _globals.get("cb_checador")
    variable = _globals.get("checador_var")
    if combo is None or variable is None:
        return
    config = _globals["cargar_configuracion"]()
    nombres = [item["nombre"] for item in config["checadores"]]
    combo["values"] = nombres
    if nombres and variable.get() not in nombres:
        combo.current(0)


_globals["actualizar_combos_globales"] = _actualizar_combos_globales_seguro

VentanaConfiguracion = _globals.get("VentanaConfiguracion")
VentanaHorarios = _globals.get("VentanaHorarios")
VentanaPersonasChecador = _globals.get("VentanaPersonasChecador")
VentanaHistorico = _globals.get("VentanaHistorico")
VentanaAgregarUsuario = _globals.get("VentanaAgregarUsuario")
VentanaDiasNoLaborados = _globals.get("VentanaDiasNoLaborados")
