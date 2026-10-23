"""
Config manager - extracted from main v4.py
Handles JSON config persistence, normalization, and utilities.
"""
import os
import sys
import json
import shutil
import logging
import threading
from datetime import datetime, date, timedelta
from theme import DEFAULT_PORT, HIKVISION_DEFAULT_PORT, dias_sem, meses, meses_abrev


logger = logging.getLogger(__name__)

# ─── LAZY IMPORTS ──────────────────────────────────────────────
ZK = None
def cargar_zk():
    global ZK
    if ZK is None:
        from zk import ZK as _ZK
        ZK = _ZK
    return ZK

def crear_zk(*args, **kwargs):
    return cargar_zk()(*args, **kwargs)

# ─── CACHE ──────────────────────────────────────────────────────
_config_cache = None
_config_cache_dirty = True
_dias_no_laborados_cache = None
_dias_no_laborados_cache_key = None
_config_lock = threading.RLock()


def invalidar_cache_config():
    """Invalidate all cached config. Call after any save."""
    global _config_cache, _config_cache_dirty, _dias_no_laborados_cache, _dias_no_laborados_cache_key
    _config_cache = None
    _config_cache_dirty = True
    _dias_no_laborados_cache = None
    _dias_no_laborados_cache_key = None


# ─── PATHS ──────────────────────────────────────────────────────
VERSION = "v 4.2"

def obtener_directorio_ejecucion():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR_LOCAL = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AsistenciaAgropecuaria")
APP_DIR = os.environ.get("ASISTENCIA_CONFIG_DIR") or obtener_directorio_ejecucion()
PATH_CONFIG_ANTERIOR = os.path.join(obtener_directorio_ejecucion(), "config.json")
PATH_CONFIG_LOCAL = os.path.join(APP_DIR_LOCAL, "config.json")
PATH_CONFIG = os.path.join(APP_DIR, "config_global.json")

PASS_EXCEL = os.environ.get("ASISTENCIA_EXCEL_PASSWORD", "demo")

# ─── ZK CONNECTION ─────────────────────────────────────────────
def conectar_zk_robusto(ip, port=DEFAULT_PORT, password=None, progreso=None, modo_preferido=None):
    intentos_udp = [
        ("UDP rapido", True, 4),
        ("TCP rapido", False, 4),
        ("UDP respaldo", True, 7),
        ("TCP respaldo", False, 7),
    ]
    intentos_tcp = [
        ("TCP rapido", False, 4),
        ("UDP rapido", True, 4),
        ("TCP respaldo", False, 7),
        ("UDP respaldo", True, 7),
    ]
    intentos = intentos_tcp if str(modo_preferido).lower() == "tcp" else intentos_udp
    errores = []
    puerto = puerto_seguro(port)
    for idx, (nombre_intento, usar_udp, timeout) in enumerate(intentos, start=1):
        conn = None
        try:
            if progreso:
                progreso(f"Conectando ({nombre_intento})...", 8 + idx * 6)
            kwargs = {
                "port": puerto,
                "timeout": timeout,
                "force_udp": usar_udp,
                "ommit_ping": True,
            }
            if password is not None:
                kwargs["password"] = password
            zk = crear_zk(ip, **kwargs)
            conn = zk.connect()
            return conn, nombre_intento
        except Exception as e:
            errores.append(f"{nombre_intento}: {e}")
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass
    detalle = "\n".join(errores[-4:])
    raise Exception(f"No se pudo conectar al checador {ip}:{puerto} despues de varios intentos.\n{detalle}")

def guardar_preferencia_conexion(nombre_checador, modo_conexion):
    if not nombre_checador:
        return
    modo = "tcp" if "TCP" in str(modo_conexion).upper() else "udp"
    try:
        config = cargar_configuracion()
        actualizado = False
        for checador in config.get("checadores", []):
            if checador.get("nombre") == nombre_checador:
                if checador.get("modo_conexion") != modo:
                    checador["modo_conexion"] = modo
                    actualizado = True
                break
        if actualizado:
            guardar_configuracion(config)
    except Exception:
        pass

# ─── TIMEZONE ───────────────────────────────────────────────────
try:
    TZ_LOCAL = datetime.now().astimezone().tzinfo
except Exception:
    TZ_LOCAL = datetime.now().astimezone().tzinfo

# ─── UTILITIES ──────────────────────────────────────────────────
def limpiar_nombre(nombre):
    return str(nombre).replace(".", " ").title().strip()

def puerto_seguro(valor, respaldo=DEFAULT_PORT):
    try:
        puerto = int(str(valor).strip())
    except Exception:
        return respaldo
    if 1 <= puerto <= 65535:
        return puerto
    return respaldo

def entero_no_negativo(valor, respaldo=0):
    try:
        numero = int(str(valor).strip())
    except Exception:
        return respaldo
    return numero if numero >= 0 else respaldo

def fecha_iso_segura(valor):
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    texto = str(valor).strip()
    if not texto:
        return ""
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except Exception:
            pass
    return ""

def opciones_horas(intervalo=5):
    return [f"{hora:02d}:{minuto:02d}" for hora in range(24) for minuto in range(0, 60, intervalo)]

def rango_fechas(fecha_inicio, fecha_fin):
    dias = []
    actual = fecha_inicio
    while actual <= fecha_fin:
        dias.append(actual)
        actual += timedelta(days=1)
    return dias

def marca_checador(checador):
    marca = str(checador.get("marca", checador.get("tipo", "zkteco"))).strip().lower()
    if marca in ["hikvision", "hik", "hik-connect"]:
        return "hikvision"
    return "zkteco"

def puerto_default_marca(marca):
    return HIKVISION_DEFAULT_PORT if marca == "hikvision" else DEFAULT_PORT

def minutos_de_hora(hora):
    return hora.hour * 60 + hora.minute

def segundos_de_hora(hora):
    return hora.hour * 3600 + hora.minute * 60 + hora.second

def hora_vacia(hora):
    if hora is None:
        return True
    try:
        import pandas as pd
        return bool(pd.isna(hora))
    except Exception:
        return False

def formato_hora(hora):
    try:
        import pandas as pd
        if pd.isna(hora): return "---"
        return hora.strftime("%I:%M %p").lstrip("0")
    except:
        return str(hora)

def formato_entrada_reporte(entrada):
    return "SIN ENTRADA" if hora_vacia(entrada) else formato_hora(entrada)

def formato_salida_reporte(entrada, salida):
    if hora_vacia(salida):
        return "SIN SALIDA"
    if not hora_vacia(entrada) and entrada == salida:
        return "SIN SALIDA"
    return formato_hora(salida)

def parse_hora_config(valor, respaldo):
    try:
        return datetime.strptime(str(valor).strip(), "%H:%M").time()
    except Exception:
        return datetime.strptime(respaldo, "%H:%M").time()

# ─── DEFAULT CONFIG ─────────────────────────────────────────────
def configuracion_default():
    return {
        "checadores": [],
        "personas_sistema": [],
        "personas_eliminadas": [],
        "personas_biometria": [],
        "dias_no_laborados": [],
        "horarios": {
            "entrada": "08:00",
            "salida": "17:00",
            "tolerancia_min": 10,
            "dias_laborales": [0, 1, 2, 3, 4, 5],
            "especiales": []
        }
    }

def normalizar_dias_horarios(valor, base_horario):
    resultado = {}
    if not isinstance(valor, dict):
        return resultado
    for dia in [5, 6]:
        item = valor.get(str(dia), valor.get(dia, {}))
        if not isinstance(item, dict) or not item.get("activo", False):
            continue
        resultado[str(dia)] = {
            "activo": True,
            "entrada": item.get("entrada", base_horario.get("entrada", "08:00")),
            "salida": item.get("salida", base_horario.get("salida", "17:00")),
            "tolerancia_min": entero_no_negativo(item.get("tolerancia_min", base_horario.get("tolerancia_min", 10)), base_horario.get("tolerancia_min", 10))
        }
    return resultado

def normalizar_dia_no_laborado(item):
    if isinstance(item, dict):
        fecha_txt = fecha_iso_segura(item.get("fecha", ""))
        motivo = str(item.get("motivo", "")).strip()
    else:
        fecha_txt = fecha_iso_segura(item)
        motivo = ""
    if not fecha_txt:
        return None
    return {"fecha": fecha_txt, "motivo": motivo}

def normalizar_biometria_configuracion(config):
    """Persist workflow status only; biometric images/templates are never saved."""
    estados_huella = {"pendiente", "no_solicitada", "requiere_captura_en_dispositivo", "captura_reportada_sin_verificar"}
    estados_rostro = {
        "pendiente", "no_solicitado", "foto_lista", "enviado_y_confirmado",
        "enviado_sin_verificar", "prueba_fisica_reportada", "error",
    }
    estados_modo_rostro = {"no_aplica", "sin_confirmar", "facial_confirmado", "no_facial", "error"}
    estados_acceso = {"no_aplica", "sin_confirmar", "permiso_confirmado", "sin_permiso", "error"}
    registros, vistos = [], set()
    for item in config.get("personas_biometria", []):
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("persona_id", "")).strip()
        checador = str(item.get("checador", "")).strip()
        clave = (persona_id.casefold(), checador.casefold())
        if not persona_id or not checador or clave in vistos:
            continue
        vistos.add(clave)
        huella = str(item.get("huella", "requiere_captura_en_dispositivo")).strip()
        rostro = str(item.get("rostro", "pendiente")).strip()
        registros.append({
            "persona_id": persona_id,
            "nombre": limpiar_nombre(item.get("nombre", "")),
            "checador": checador,
            "estado": "confirmada" if str(item.get("estado", "")).strip() == "confirmada" else "pendiente_obligatoria",
            "tipo_confirmado": str(item.get("tipo_confirmado", "")).strip() if str(item.get("tipo_confirmado", "")).strip() in {"huella", "rostro"} else "",
            "huella": huella if huella in estados_huella else "requiere_captura_en_dispositivo",
            "rostro": rostro if rostro in estados_rostro else "pendiente",
            "modo_rostro": (
                str(item.get("modo_rostro", "sin_confirmar")).strip()
                if str(item.get("modo_rostro", "sin_confirmar")).strip() in estados_modo_rostro
                else "sin_confirmar"
            ),
            "acceso": (
                str(item.get("acceso", "sin_confirmar")).strip()
                if str(item.get("acceso", "sin_confirmar")).strip() in estados_acceso
                else "sin_confirmar"
            ),
            "actualizado_en": str(item.get("actualizado_en", "")).strip(),
            "detalle": str(item.get("detalle", "")).strip()[:500],
        })
    config["personas_biometria"] = sorted(registros, key=lambda item: (item["checador"].casefold(), item["persona_id"].casefold()))
    config.pop("grupos_acceso", None)
    config.pop("accesos_personas", None)
    return config

def normalizar_horario(horario, base=None):
    default = configuracion_default()["horarios"]
    base = base or default
    if not isinstance(horario, dict):
        horario = {}
    limpio = {
        "entrada": horario.get("entrada", base.get("entrada", default["entrada"])),
        "salida": horario.get("salida", base.get("salida", default["salida"])),
        "tolerancia_min": horario.get("tolerancia_min", base.get("tolerancia_min", default["tolerancia_min"])),
        "dias_laborales": horario.get("dias_laborales", base.get("dias_laborales", default["dias_laborales"])),
        "dias_horarios": {},
        "especiales": []
    }
    limpio["tolerancia_min"] = entero_no_negativo(limpio["tolerancia_min"], default["tolerancia_min"])
    if not isinstance(limpio["dias_laborales"], list):
        limpio["dias_laborales"] = list(default["dias_laborales"])
    limpio["dias_horarios"] = normalizar_dias_horarios(horario.get("dias_horarios", base.get("dias_horarios", {})), limpio)
    for especial in horario.get("especiales", []):
        if not isinstance(especial, dict):
            continue
        especial_limpio = {
            "nombre": especial.get("nombre", "Horario especial"),
            "entrada": especial.get("entrada", limpio["entrada"]),
            "salida": especial.get("salida", limpio["salida"]),
            "tolerancia_min": entero_no_negativo(especial.get("tolerancia_min", limpio["tolerancia_min"]), limpio["tolerancia_min"]),
            "dias_laborales": especial.get("dias_laborales", list(limpio["dias_laborales"])),
            "dias_horarios": {},
            "empleados": [limpiar_nombre(emp) for emp in especial.get("empleados", []) if str(emp).strip()]
        }
        especial_limpio["dias_horarios"] = normalizar_dias_horarios(especial.get("dias_horarios", limpio.get("dias_horarios", {})), especial_limpio)
        limpio["especiales"].append({**especial_limpio})
    return limpio


DIAS_RETENCION_PERSONAS_ELIMINADAS = 60


def _fecha_eliminacion_segura(valor):
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=None)
    if isinstance(valor, date):
        return datetime.combine(valor, datetime.min.time())
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return fecha.replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def eliminar_personas_definitivamente_config(config, nombres):
    """Remove people from all local operational references, preserving attendance history."""
    if not isinstance(config, dict):
        return []
    nombres_limpios = sorted({limpiar_nombre(nombre) for nombre in nombres if str(nombre).strip()})
    claves = {nombre.casefold() for nombre in nombres_limpios}
    if not claves:
        return []

    def conservar_nombre(nombre):
        return limpiar_nombre(nombre).casefold() not in claves

    config["personas_sistema"] = [
        persona for persona in config.get("personas_sistema", []) if conservar_nombre(persona)
    ]
    config["personas_eliminadas"] = [
        item for item in config.get("personas_eliminadas", [])
        if conservar_nombre(item.get("nombre", "") if isinstance(item, dict) else item)
    ]
    config["personas_biometria"] = [
        item for item in config.get("personas_biometria", [])
        if not isinstance(item, dict) or conservar_nombre(item.get("nombre", ""))
    ]

    horarios = []
    if isinstance(config.get("horarios"), dict):
        horarios.append(config["horarios"])
    for checador in config.get("checadores", []):
        if not isinstance(checador, dict):
            continue
        for campo in ("empleados", "empleados_descargados"):
            checador[campo] = [
                persona for persona in checador.get(campo, []) if conservar_nombre(persona)
            ]
        if isinstance(checador.get("horarios"), dict):
            horarios.append(checador["horarios"])

    for horario in horarios:
        for especial in horario.get("especiales", []):
            if isinstance(especial, dict):
                especial["empleados"] = [
                    persona for persona in especial.get("empleados", []) if conservar_nombre(persona)
                ]
    return nombres_limpios


def purgar_personas_eliminadas_vencidas(config, ahora=None, dias=DIAS_RETENCION_PERSONAS_ELIMINADAS):
    """Permanently purge locally deleted people once their retention period expires."""
    ahora = (ahora or datetime.now()).replace(tzinfo=None)
    vencidas = []
    for item in config.get("personas_eliminadas", []) if isinstance(config, dict) else []:
        if not isinstance(item, dict):
            continue
        fecha = _fecha_eliminacion_segura(item.get("fecha", ""))
        nombre = limpiar_nombre(item.get("nombre", ""))
        if nombre and fecha is not None and ahora - fecha >= timedelta(days=dias):
            vencidas.append(nombre)
    return eliminar_personas_definitivamente_config(config, vencidas)

def normalizar_configuracion(config):
    default = configuracion_default()
    if not isinstance(config, dict):
        config = {}
    if not isinstance(config.get("checadores"), list):
        config["checadores"] = default["checadores"]
    horarios = config.get("horarios")
    if not isinstance(horarios, dict):
        horarios = {}
    horarios = normalizar_horario(horarios)
    config["horarios"] = horarios
    checadores = []
    for checador in config["checadores"]:
        if not isinstance(checador, dict):
            continue
        checador.setdefault("nombre", "Sin Nombre")
        checador.setdefault("ip", "")
        checador["marca"] = marca_checador(checador)
        checador["port"] = puerto_seguro(checador.get("port"), puerto_default_marca(checador["marca"]))
        if "modelo" in checador:
            checador["modelo"] = str(checador.get("modelo", "")).strip().upper()
        if checador["marca"] == "hikvision":
            checador["usuario"] = str(checador.get("usuario", "admin")).strip() or "admin"
            checador["password"] = str(checador.get("password", ""))
        checador["empleados"] = sorted({limpiar_nombre(emp) for emp in checador.get("empleados", []) if str(emp).strip()})
        checador["empleados_descargados"] = sorted({limpiar_nombre(emp) for emp in checador.get("empleados_descargados", []) if str(emp).strip()})
        if not checador["empleados_descargados"] and checador["empleados"]:
            checador["empleados_descargados"] = list(checador["empleados"])
        checador["horarios"] = normalizar_horario(checador.get("horarios"), horarios)
        checadores.append(checador)
    if not checadores:
        for checador in default["checadores"]:
            nuevo = dict(checador)
            nuevo["marca"] = marca_checador(nuevo)
            nuevo["port"] = puerto_seguro(nuevo.get("port"), puerto_default_marca(nuevo["marca"]))
            nuevo["empleados"] = []
            nuevo["horarios"] = normalizar_horario(None, horarios)
            checadores.append(nuevo)
    config["checadores"] = checadores
    personas_sistema = {limpiar_nombre(emp) for emp in config.get("personas_sistema", []) if str(emp).strip()}
    for checador in checadores:
        personas_sistema.update(limpiar_nombre(emp) for emp in checador.get("empleados", []) if str(emp).strip())
        for especial in checador.get("horarios", {}).get("especiales", []):
            personas_sistema.update(limpiar_nombre(emp) for emp in especial.get("empleados", []) if str(emp).strip())
    config["personas_sistema"] = sorted(personas_sistema)
    eliminadas = []
    for item in config.get("personas_eliminadas", []):
        if isinstance(item, dict):
            nombre = limpiar_nombre(item.get("nombre", ""))
            if nombre:
                eliminadas.append({"nombre": nombre, "fecha": item.get("fecha", ""), "checador": item.get("checador", "")})
        elif str(item).strip():
            eliminadas.append({"nombre": limpiar_nombre(item), "fecha": "", "checador": ""})
    vistos = set()
    limpias = []
    for item in eliminadas:
        key = item["nombre"].lower()
        if key in vistos:
            continue
        vistos.add(key)
        limpias.append(item)
    config["personas_eliminadas"] = limpias
    eliminadas_nombres = {item["nombre"].lower() for item in limpias}
    config["personas_sistema"] = [emp for emp in config["personas_sistema"] if emp.lower() not in eliminadas_nombres]
    dias_no_laborados = {}
    for item in config.get("dias_no_laborados", []):
        limpio = normalizar_dia_no_laborado(item)
        if limpio:
            dias_no_laborados[limpio["fecha"]] = limpio
    config["dias_no_laborados"] = [dias_no_laborados[fecha] for fecha in sorted(dias_no_laborados)]
    config = normalizar_biometria_configuracion(config)
    purgar_personas_eliminadas_vencidas(config)
    return config

def respaldar_archivo_config_corrupto(path):
    if not os.path.exists(path):
        return None
    marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
    respaldo = f"{path}.corrupto.{marca_tiempo}.bak"
    try:
        shutil.copy2(path, respaldo)
        return respaldo
    except Exception:
        return None

# ─── CONFIG LOAD/SAVE ──────────────────────────────────────────
def cargar_configuracion():
    """Load configuration with in-memory cache - avoids redundant disk I/O."""
    global _config_cache, _config_cache_dirty
    with _config_lock:
        if not _config_cache_dirty and _config_cache is not None:
            if purgar_personas_eliminadas_vencidas(_config_cache):
                guardar_configuracion(_config_cache)
            return _config_cache
        os.makedirs(APP_DIR, exist_ok=True)
        if not os.path.exists(PATH_CONFIG):
            for origen in (PATH_CONFIG_ANTERIOR, PATH_CONFIG_LOCAL):
                if not origen or not os.path.exists(origen):
                    continue
                try:
                    if os.path.abspath(origen) != os.path.abspath(PATH_CONFIG):
                        shutil.copy2(origen, PATH_CONFIG)
                        break
                except OSError as exc:
                    logger.warning("No se pudo migrar la configuración desde %s: %s", origen, exc)
        if not os.path.exists(PATH_CONFIG):
            default = normalizar_configuracion(configuracion_default())
            guardar_configuracion(default)
            return _config_cache
        try:
            with open(PATH_CONFIG, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            respaldo = respaldar_archivo_config_corrupto(PATH_CONFIG)
            logger.warning("La configuración no pudo leerse (%s). Respaldo: %s", exc, respaldo or "no creado")
            config = configuracion_default()
        contenido_original = json.dumps(config, ensure_ascii=False, sort_keys=True)
        config_normalizada = normalizar_configuracion(config)
        if json.dumps(config_normalizada, ensure_ascii=False, sort_keys=True) != contenido_original:
            guardar_configuracion(config_normalizada)
            return _config_cache
        _config_cache = config_normalizada
        _config_cache_dirty = False
        return _config_cache

def guardar_configuracion(config):
    """Save configuration to disk and update the in-memory cache."""
    global _config_cache, _config_cache_dirty, _dias_no_laborados_cache, _dias_no_laborados_cache_key
    with _config_lock:
        os.makedirs(APP_DIR, exist_ok=True)
        config_normalizada = normalizar_configuracion(config)
        temp_path = f"{PATH_CONFIG}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(config_normalizada, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, PATH_CONFIG)
        except OSError:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise
        # Cache exactly the validated version written to disk.
        _config_cache = config_normalizada
        _config_cache_dirty = False
        _dias_no_laborados_cache = None
        _dias_no_laborados_cache_key = None
        invalidar_horarios_cache()

# ─── CONFIG HELPERS ─────────────────────────────────────────────
# Cache for horarios config by checador name
_horarios_config_cache = {}


def invalidar_horarios_cache():
    """Invalidate horarios cache - call after saving config changes."""
    global _horarios_config_cache
    _horarios_config_cache = {}


def obtener_horarios_config(nombre_checador=None):
    config = cargar_configuracion()
    cache_key = nombre_checador or "__global__"
    if cache_key in _horarios_config_cache:
        return _horarios_config_cache[cache_key]
    if nombre_checador:
        info = next((c for c in config["checadores"] if c["nombre"] == nombre_checador), None)
        if info:
            result = normalizar_horario(info.get("horarios"), config.get("horarios"))
            _horarios_config_cache[cache_key] = result
            return result
    result = normalizar_horario(config.get("horarios"))
    _horarios_config_cache[cache_key] = result
    return result


# Cache for employee horarios: {horarios_dict_id: {empleado: horario_normalizado}}
_empleado_horario_cache = {}


def obtener_horario_empleado(nombre_empleado, horarios):
    nombre_normalizado = limpiar_nombre(nombre_empleado).lower()
    horarios = normalizar_horario(horarios)
    # Use the horarios dict id as cache key (hash of tuple of specials)
    especiales = horarios.get("especiales", [])
    if not especiales:
        return horarios
    # Check each special directly without building a large cache
    for especial in especiales:
        empleados = {limpiar_nombre(emp).lower() for emp in especial.get("empleados", [])}
        if nombre_normalizado in empleados:
            return normalizar_horario(especial, horarios)
    return horarios

def obtener_horario_para_fecha(horarios, fecha):
    horarios = normalizar_horario(horarios)
    if fecha is None:
        return horarios
    especial_dia = horarios.get("dias_horarios", {}).get(str(fecha.weekday()))
    if especial_dia and especial_dia.get("activo"):
        horario_dia = dict(horarios)
        horario_dia["entrada"] = especial_dia.get("entrada", horarios.get("entrada", "08:00"))
        horario_dia["salida"] = especial_dia.get("salida", horarios.get("salida", "17:00"))
        horario_dia["tolerancia_min"] = especial_dia.get("tolerancia_min", horarios.get("tolerancia_min", 10))
        return horario_dia
    return horarios

def obtener_mapa_dias_no_laborados(config=None):
    global _dias_no_laborados_cache, _dias_no_laborados_cache_key
    if config is None:
        if _dias_no_laborados_cache is not None:
            return _dias_no_laborados_cache
        config = cargar_configuracion()
        _dias_no_laborados_cache = {}
    mapa = {}
    for item in config.get("dias_no_laborados", []):
        limpio = normalizar_dia_no_laborado(item)
        if not limpio:
            continue
        try:
            fecha = datetime.strptime(limpio["fecha"], "%Y-%m-%d").date()
        except Exception:
            continue
        mapa[fecha] = limpio.get("motivo", "")
    if _dias_no_laborados_cache is not None:
        _dias_no_laborados_cache = mapa
    return mapa

def es_dia_no_laborado(fecha, dias_no_laborados=None):
    if fecha is None:
        return False
    if dias_no_laborados is None:
        dias_no_laborados = obtener_mapa_dias_no_laborados()
    return fecha in dias_no_laborados

def motivo_dia_no_laborado(fecha, dias_no_laborados=None):
    if fecha is None:
        return ""
    if dias_no_laborados is None:
        dias_no_laborados = obtener_mapa_dias_no_laborados()
    return dias_no_laborados.get(fecha, "")

def es_dia_laboral(fecha, horarios=None, dias_no_laborados=None):
    if dias_no_laborados is not None and es_dia_no_laborado(fecha, dias_no_laborados):
        return False
    horarios = horarios or obtener_horarios_config()
    return fecha.weekday() in horarios.get("dias_laborales", [0, 1, 2, 3, 4, 5])

def obtener_horario_especial_empleado(nombre_empleado, horarios):
    nombre_normalizado = limpiar_nombre(nombre_empleado).lower()
    for especial in normalizar_horario(horarios).get("especiales", []):
        empleados = {limpiar_nombre(emp).lower() for emp in especial.get("empleados", [])}
        if nombre_normalizado in empleados:
            return especial
    return None

# ─── WINDOW HELPERS ─────────────────────────────────────────────
def ruta_recurso(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def configurar_app_windows():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Asistencia.Agropecuaria.v4")
    except Exception:
        pass

def aplicar_icono(ventana):
    icono = ruta_recurso("icono.ico")
    if not os.path.exists(icono):
        return
    try:
        ventana.iconbitmap(icono)
    except Exception:
        pass
    try:
        from PIL import Image, ImageTk
        with Image.open(icono) as imagen:
            foto = ImageTk.PhotoImage(imagen.copy())
        ventana.wm_iconphoto(True, foto)
        ventana._app_icon_photo = foto
    except Exception:
        pass

def ajustar_ventana(ventana, ancho, alto, min_ancho=None, min_alto=None):
    ventana.update_idletasks()
    margen_ancho = 70
    margen_alto = 95
    screen_w = ventana.winfo_screenwidth()
    screen_h = ventana.winfo_screenheight()
    ancho_final = min(ancho, max(360, screen_w - margen_ancho))
    alto_final = min(alto, max(360, screen_h - margen_alto))
    x = max(0, (screen_w - ancho_final) // 2)
    y = max(0, (screen_h - alto_final) // 2 - 15)
    ventana.geometry(f"{ancho_final}x{alto_final}+{x}+{y}")
    ventana.minsize(min(min_ancho or ancho_final, ancho_final), min(min_alto or alto_final, alto_final))

def opciones_meses_reporte():
    hoy = date.today()
    from theme import meses
    return [f"{meses[m].capitalize()} {hoy.year}" for m in range(1, hoy.month + 1)]

def rango_mes_actual(indice_mes):
    hoy = date.today()
    mes = max(1, min(indice_mes, hoy.month))
    inicio = date(hoy.year, mes, 1)
    if mes == 12:
        fin_mes = date(hoy.year, 12, 31)
    else:
        fin_mes = date(hoy.year, mes + 1, 1) - timedelta(days=1)
    fin = min(fin_mes, hoy) if mes == hoy.month else fin_mes
    return inicio, fin