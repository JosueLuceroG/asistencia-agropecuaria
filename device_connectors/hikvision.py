"""
Hikvision ISAPI connector - extracted from main v4.py
Handles communication with Hikvision devices via ISAPI protocol.
Optimized with opener caching to avoid recreating HTTP auth handlers per request.
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import ssl
import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, date, timedelta
from threading import RLock

# Ensure project root is in sys.path for direct imports
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config_manager import (
    limpiar_nombre, puerto_seguro, TZ_LOCAL,
    HIKVISION_DEFAULT_PORT
)

# ─── AUTH OPENER CACHE ──────────────────────────────────────────
# Keep a small LRU cache: the desktop application normally communicates with
# one or two devices, and credentials may change while it remains open.
_OPENER_CACHE_MAXSIZE = 8
_opener_cache = OrderedDict()
_opener_cache_lock = RLock()
logger = logging.getLogger(__name__)


class HikvisionDescargaCancelada(Exception):
    """Indica que el usuario canceló una descarga antes de pedir más datos."""


def _descarga_cancelada(cancelado):
    if cancelado is None:
        return False
    try:
        return bool(cancelado() if callable(cancelado) else cancelado.is_set())
    except AttributeError:
        return bool(cancelado)


def _comprobar_cancelacion(cancelado):
    if _descarga_cancelada(cancelado):
        raise HikvisionDescargaCancelada("La descarga de Hikvision fue cancelada por el usuario.")

def _get_opener(base_url, usuario, password):
    """Get or create a cached HTTP opener with digest/basic auth for a device."""
    # Include the password so a credential update cannot reuse stale auth data.
    cache_key = (base_url, usuario, password)
    with _opener_cache_lock:
        opener = _opener_cache.get(cache_key)
        if opener is not None:
            _opener_cache.move_to_end(cache_key)
            return opener
    passman = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passman.add_password(None, base_url, usuario, password)
    handlers = [
        urllib.request.HTTPDigestAuthHandler(passman),
        urllib.request.HTTPBasicAuthHandler(passman)
    ]
    if base_url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    opener = urllib.request.build_opener(*handlers)
    with _opener_cache_lock:
        _opener_cache[cache_key] = opener
        _opener_cache.move_to_end(cache_key)
        while len(_opener_cache) > _OPENER_CACHE_MAXSIZE:
            _opener_cache.popitem(last=False)
    return opener


def limpiar_cache_conexiones():
    """Remove cached HTTP authentication handlers after a credential update."""
    with _opener_cache_lock:
        _opener_cache.clear()

# ─── HIKVISION HELPERS ─────────────────────────────────────────
def hikvision_base_urls(info):
    ip = info.get("ip")
    puerto = puerto_seguro(info.get("port"), HIKVISION_DEFAULT_PORT)
    candidatos = []
    if puerto == 443:
        candidatos.append(f"https://{ip}:443")
    elif puerto in [80, 8000]:
        candidatos.append(f"http://{ip}:80")
    else:
        esquema = "https" if str(info.get("https", "")).lower() in ["1", "true", "si", "yes"] else "http"
        candidatos.append(f"{esquema}://{ip}:{puerto}")
    for url in [f"http://{ip}:80", f"https://{ip}:443"]:
        if url not in candidatos:
            candidatos.append(url)
    return candidatos

def hikvision_base_url(info):
    return hikvision_base_urls(info)[0]

def _dict_to_hikvision_xml(root_tag, d):
    def _xml_escape(s):
        s2 = str(s)
        amp = chr(38)
        lt = chr(60)
        gt = chr(62)
        quot = chr(34)
        apos = chr(39)
        s2 = s2.replace(amp, amp + "amp;")
        s2 = s2.replace(lt, amp + "lt;")
        s2 = s2.replace(gt, amp + "gt;")
        s2 = s2.replace(quot, amp + "quot;")
        s2 = s2.replace(apos, amp + "apos;")
        return s2
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xmlns = ' xmlns="http://www.isapi.org/ver20/XMLSchema"'
    def _build(elem_name, value, indent=0):
        prefix = "  " * indent
        if isinstance(value, dict):
            parts.append(prefix + "<" + elem_name + (xmlns if indent == 0 else "") + ">")
            for k, v in value.items():
                _build(k, v, indent + 1)
            parts.append(prefix + "</" + elem_name + ">")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    parts.append(prefix + "<" + elem_name + ">")
                    for k2, v2 in item.items():
                        _build(k2, v2, indent + 1)
                    parts.append(prefix + "</" + elem_name + ">")
                else:
                    parts.append(prefix + "<" + elem_name + ">" + _xml_escape(str(item)) + "</" + elem_name + ">")
        else:
            parts.append(prefix + "<" + elem_name + ">" + _xml_escape(str(value)) + "</" + elem_name + ">")
    _build(root_tag, d)
    return "\n".join(parts)

def hikvision_request_json(info, path, method="GET", payload=None, timeout=12):
    usuario = str(info.get("usuario", "admin")).strip() or "admin"
    password = str(info.get("password", ""))
    intentos_payload = [(None, None, {"Accept": "application/json, application/xml, */*"})]
    if payload is not None:
        json_data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        intentos_payload = [("JSON", json_data, {"Content-Type": "application/json", "Accept": "application/json, application/xml, */*"})]
        root_tag = None
        if "AcsEventCond" in payload:
            root_tag = "AcsEventCond"
        elif "UserInfoSearchCond" in payload:
            root_tag = "UserInfoSearchCond"
        elif "UserInfo" in payload:
            root_tag = "UserInfo"
        if root_tag:
            xml_str = _dict_to_hikvision_xml(root_tag, payload[root_tag])
            intentos_payload.append(("XML", xml_str.encode("utf-8"), {"Content-Type": "application/xml", "Accept": "application/xml, application/json, */*"}))
    errores = []
    for base_url in hikvision_base_urls(info):
        opener = _get_opener(base_url, usuario, password)
        for tipo_payload, data, headers in intentos_payload:
            url = f"{base_url}{path}"
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with opener.open(request, timeout=timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace").strip()
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except Exception:
                    try:
                        return _hikvision_xml_a_dict(raw)
                    except Exception:
                        return {"raw": raw}
            except urllib.error.HTTPError as e:
                detalle = e.read().decode("utf-8", errors="replace")
                if e.code == 401:
                    raise Exception("Hikvision requiere usuario y contrasena correctos para ISAPI.")
                errores.append(f"{base_url} {tipo_payload or 'GET'}: HTTP {e.code} {detalle[:180]}")
            except Exception as e:
                errores.append(f"{base_url} {tipo_payload or 'GET'}: {e}")
                break
    raise Exception("No se pudo consultar ISAPI Hikvision.\n" + "\n".join(errores[-4:]))

def _hikvision_xml_a_dict(xml_str):
    root = ET.fromstring(xml_str)
    def _tag(elem):
        tag = elem.tag
        if '}' in tag:
            return tag.split('}', 1)[1]
        return tag
    root_tag = _tag(root)
    resultado = {}
    info_list_items = []
    for child in root:
        tag = _tag(child)
        text = (child.text or "").strip()
        sub_children = list(child)
        if tag == "InfoList" or tag == "infoList":
            for evento in sub_children:
                ev_dict = {}
                for campo in evento:
                    ct = _tag(campo)
                    cv = (campo.text or "").strip()
                    ev_dict[ct] = cv
                info_list_items.append(ev_dict)
        elif sub_children:
            sub_dict = {}
            for sub in sub_children:
                st = _tag(sub)
                sv = (sub.text or "").strip()
                if st in sub_dict:
                    if not isinstance(sub_dict[st], list):
                        sub_dict[st] = [sub_dict[st]]
                    sub_dict[st].append(sv)
                else:
                    sub_dict[st] = sv
            if tag in resultado:
                if not isinstance(resultado[tag], list):
                    resultado[tag] = [resultado[tag]]
                resultado[tag].append(sub_dict)
            else:
                resultado[tag] = sub_dict
        else:
            if tag in resultado:
                if not isinstance(resultado[tag], list):
                    resultado[tag] = [resultado[tag]]
                resultado[tag].append(text)
            else:
                resultado[tag] = text
    if info_list_items:
        resultado["InfoList"] = info_list_items
    if root_tag and root_tag not in resultado:
        return {root_tag: resultado}
    return resultado

def hikvision_search_id():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")

def hikvision_obtener_hora_dispositivo(info):
    data = hikvision_request_json(info, "/ISAPI/System/time?format=json", timeout=8)
    root = data.get("Time", data)
    valor = root.get("localTime") or root.get("time") or root.get("dateTime")
    if not valor:
        return None
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))

def hikvision_ajuste_tiempo(info):
    try:
        hora_dispositivo = hikvision_obtener_hora_dispositivo(info)
        if hora_dispositivo is None:
            return timedelta(0)
        if hora_dispositivo.tzinfo is not None:
            hora_dispositivo = hora_dispositivo.astimezone(TZ_LOCAL).replace(tzinfo=None)
        hora_dispositivo = hora_dispositivo.replace(microsecond=0)
        ahora_local = datetime.now(TZ_LOCAL).replace(tzinfo=None, microsecond=0)
        ajuste = ahora_local - hora_dispositivo
        if abs(ajuste.total_seconds()) > 300:
            return ajuste
    except Exception as exc:
        logger.debug("No se pudo calcular el ajuste horario de Hikvision: %s", exc)
    return timedelta(0)

def hikvision_fecha_iso(fecha, final=False, ajuste_tiempo=None):
    hora = datetime.max.time().replace(microsecond=0) if final else datetime.min.time()
    dt = datetime.combine(fecha, hora)
    if ajuste_tiempo:
        dt = dt - ajuste_tiempo
    dt = dt.replace(tzinfo=TZ_LOCAL)
    return dt.isoformat(timespec="seconds")

def parse_fecha_hikvision(valor, ajuste_tiempo=None):
    texto = str(valor or "").strip()
    if not texto:
        return None
    texto = texto.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except Exception:
        for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(texto[:19], formato)
                break
            except Exception:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ_LOCAL).replace(tzinfo=None)
    if ajuste_tiempo:
        dt = dt + ajuste_tiempo
    return dt

def hikvision_probar_conexion(info):
    hikvision_request_json(info, "/ISAPI/System/deviceInfo?format=json", timeout=8)
    return "ISAPI"

def hikvision_obtener_personas(info):
    personas = set()
    posicion = 0
    max_resultados = 30
    search_id = hikvision_search_id()
    for _ in range(80):
        payload = {
            "UserInfoSearchCond": {
                "searchID": search_id,
                "searchResultPosition": posicion,
                "maxResults": max_resultados
            }
        }
        data = hikvision_request_json(info, "/ISAPI/AccessControl/UserInfo/Search?format=json", method="POST", payload=payload)
        root = data.get("UserInfoSearch", data.get("UserInfoSearchCond", data))
        lista = root.get("UserInfo", root.get("UserInfoList", []))
        if isinstance(lista, dict):
            lista = [lista]
        for item in lista or []:
            nombre = item.get("name") or item.get("employeeName") or item.get("employeeNo") or item.get("employeeNoString")
            if nombre and "admin" not in limpiar_nombre(nombre).lower():
                personas.add(limpiar_nombre(nombre))
        total = int(root.get("totalMatches", root.get("numOfMatches", 0)) or 0)
        cantidad = len(lista or [])
        posicion += cantidad
        if cantidad < max_resultados or (total and posicion >= total):
            break
    return sorted(personas)

def hikvision_obtener_eventos(info, fecha_inicio, fecha_fin, progreso=None, cancelado=None):
    """Descarga checadas identificadas de Hikvision con progreso cancelable.

    Los terminales conocidos distribuyen asistencias nominales entre los
    subtipos 38 y 75. El subtipo amplio 0 puede devolver muchos eventos
    técnicos, por lo que sólo se consulta si ambos subtipos no devolvieron
    checadas válidas para el periodo solicitado.
    """
    if fecha_inicio is None or fecha_fin is None:
        fecha_fin = date.today()
        fecha_inicio = fecha_fin - timedelta(days=90)
    if fecha_inicio > fecha_fin:
        raise ValueError("La fecha inicial no puede ser posterior a la fecha final.")
    _comprobar_cancelacion(cancelado)
    records = []
    vistos = set()
    ajuste_tiempo = hikvision_ajuste_tiempo(info)
    bloques = list(_rangos_mensuales(fecha_inicio, fecha_fin))
    total_bloques = len(bloques)

    def agregar_resultados(resultado):
        for item in resultado or []:
            clave = (item["nombre"], item["fecha_hora"])
            if clave not in vistos:
                vistos.add(clave)
                records.append(item)

    if progreso:
        progreso(
            f"Buscando registros de asistencia identificados del {fecha_inicio:%d/%m/%Y} al {fecha_fin:%d/%m/%Y}…",
            5,
        )

    # Consultar ambos subtipos nominales para todos los bloques. No se debe
    # detener tras hallar el primero: modelos como DS-K1T registran en ambos.
    for indice_tipo, minor_val in enumerate((38, 75), start=1):
        for idx_bloque, (bloque_inicio, bloque_fin) in enumerate(bloques, start=1):
            _comprobar_cancelacion(cancelado)
            if progreso:
                progreso(
                    f"Buscando checadas identificadas: {bloque_inicio:%m/%Y} ({idx_bloque} de {total_bloques})…",
                    5 + int(60 * ((indice_tipo - 1 + (idx_bloque - 1) / total_bloques) / 2)),
                )
            resultado = _hikvision_buscar_eventos(
                info,
                bloque_inicio,
                bloque_fin,
                5,
                minor_val,
                progreso,
                ajuste_tiempo,
                cancelado=cancelado,
                progreso_inicio=5 + int(60 * ((indice_tipo - 1 + (idx_bloque - 1) / total_bloques) / 2)),
                progreso_fin=5 + int(60 * ((indice_tipo - 1 + idx_bloque / total_bloques) / 2)),
            )
            agregar_resultados(resultado)

    # Algunos modelos sólo exponen las checadas mediante la consulta amplia.
    # Se deja como último recurso para no penalizar los modelos conocidos con
    # miles de eventos técnicos que después se descartarían.
    if not records:
        if progreso:
            progreso("No se encontraron checadas nominales; buscando en el respaldo del dispositivo…", 68)
        for idx_bloque, (bloque_inicio, bloque_fin) in enumerate(bloques, start=1):
            _comprobar_cancelacion(cancelado)
            resultado = _hikvision_buscar_eventos(
                info,
                bloque_inicio,
                bloque_fin,
                5,
                0,
                progreso,
                ajuste_tiempo,
                cancelado=cancelado,
                progreso_inicio=68 + int(27 * ((idx_bloque - 1) / total_bloques)),
                progreso_fin=68 + int(27 * (idx_bloque / total_bloques)),
            )
            agregar_resultados(resultado)

    _comprobar_cancelacion(cancelado)
    if progreso:
        progreso(f"Se encontraron {len(records)} checadas válidas; preparando resumen…", 98)
    return sorted(records, key=lambda item: item["fecha_hora"])

def _rangos_mensuales(fecha_inicio, fecha_fin):
    actual = date(fecha_inicio.year, fecha_inicio.month, 1)
    while actual <= fecha_fin:
        if actual.month == 12:
            siguiente = date(actual.year + 1, 1, 1)
        else:
            siguiente = date(actual.year, actual.month + 1, 1)
        bloque_inicio = max(fecha_inicio, actual)
        bloque_fin = min(fecha_fin, siguiente - timedelta(days=1))
        yield bloque_inicio, bloque_fin
        actual = siguiente

def _hikvision_buscar_eventos(
    info,
    fecha_inicio,
    fecha_fin,
    major_val,
    minor_val,
    progreso=None,
    ajuste_tiempo=None,
    cancelado=None,
    progreso_inicio=5,
    progreso_fin=95,
):
    records = []
    posicion = 0
    max_resultados = 100
    search_id = hikvision_search_id()
    inicio_reporte = datetime.combine(fecha_inicio, datetime.min.time())
    fin_reporte = datetime.combine(fecha_fin, datetime.max.time().replace(microsecond=0))
    for pagina in range(300):
        _comprobar_cancelacion(cancelado)
        if progreso and pagina == 0:
            progreso("Conectando para consultar las checadas identificadas…", progreso_inicio)
        payload = {
            "AcsEventCond": {
                "searchID": search_id,
                "searchResultPosition": posicion,
                "maxResults": max_resultados,
                "major": major_val,
                "minor": minor_val,
                "startTime": hikvision_fecha_iso(fecha_inicio, final=False, ajuste_tiempo=ajuste_tiempo),
                "endTime": hikvision_fecha_iso(fecha_fin, final=True, ajuste_tiempo=ajuste_tiempo),
                "timeReverseOrder": True
            }
        }
        try:
            data = hikvision_request_json(info, "/ISAPI/AccessControl/AcsEvent?format=json", method="POST", payload=payload, timeout=18)
        except Exception as e:
            if progreso:
                progreso("No se pudo consultar el checador Hikvision.", progreso_inicio)
            logger.debug("Error al consultar Hikvision (major=%s minor=%s): %s", major_val, minor_val, e)
            return None
        if "raw" in data and len(data) == 1:
            if progreso:
                progreso("Respuesta no es JSON del dispositivo", 0)
            try:
                with open("hikvision_debug.txt", "w", encoding="utf-8") as f:
                    f.write(f"=== RESPUESTA CRUDA (major={major_val} minor={minor_val}) ===\n")
                    f.write(data.get("raw", "SIN CONTENIDO")[:2000])
                    f.write("\n=== FIN ===\n")
            except Exception:
                pass
            return None
        root = data.get("AcsEvent", data)
        if isinstance(root, str):
            root = {}
        lista = root.get("InfoList", root.get("infoList", []))
        if isinstance(lista, dict):
            lista = [lista]
        if not lista:
            if pagina == 0:
                try:
                    with open("hikvision_debug.txt", "w", encoding="utf-8") as f:
                        f.write(f"=== RESPUESTA COMPLETA (major={major_val} minor={minor_val}) - SIN InfoList===\n")
                        f.write(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
                        f.write("\n=== FIN ===\n")
                except Exception:
                    pass
            break
        for item in lista:
            if not isinstance(item, dict):
                continue
            fecha_hora = parse_fecha_hikvision(item.get("time") or item.get("dateTime") or item.get("eventTime"), ajuste_tiempo)
            if not fecha_hora:
                continue
            if fecha_hora < inicio_reporte or fecha_hora > fin_reporte:
                continue
            nombre = item.get("name") or item.get("employeeName") or item.get("employeeNoString") or item.get("employeeNo") or item.get("cardNo")
            if not nombre:
                continue
            records.append({"nombre": limpiar_nombre(nombre), "fecha_hora": fecha_hora})
        total = int(root.get("totalMatches", root.get("numOfMatches", 0)) or 0)
        cantidad = len(lista)
        posicion += cantidad
        if progreso:
            if total:
                tamano_pagina = max(cantidad, 1)
                paginas_estimadas = max(1, (total + tamano_pagina - 1) // tamano_pagina)
                avance = progreso_inicio + int((progreso_fin - progreso_inicio) * min(posicion, total) / total)
                progreso(
                    f"Descargando página {pagina + 1} de {paginas_estimadas}: {min(posicion, total)} de {total} registros revisados…",
                    avance,
                )
            else:
                avance = min(progreso_fin - 1, progreso_inicio + pagina + 1)
                progreso(f"Descargando página {pagina + 1}: {posicion} registros revisados…", avance)
        # Algunos terminales limitan cada respuesta a 30 eventos aunque se
        # soliciten 100. Si informan totalMatches, éste es el único indicador
        # confiable de que se alcanzó el final de la búsqueda.
        if (total and posicion >= total) or (not total and cantidad < max_resultados):
            break
    if records:
        return records
    return None

def hikvision_crear_persona(info, nombre, extra_fields=None):
    usuario = str(info.get("usuario", "admin")).strip() or "admin"
    password = str(info.get("password", ""))
    base_url = hikvision_base_url(info)
    opener = _get_opener(base_url, usuario, password)

    if extra_fields is None:
        extra_fields = {}
    employee_no = extra_fields.get("person_id", "") or nombre.lower().replace(" ", "")
    organization = extra_fields.get("organization", "")
    user_verify_mode = str(extra_fields.get("user_verify_mode", "")).strip()

    def _try_create(user_info):
        payload = {"UserInfo": user_info}
        data = json.dumps(payload).encode("utf-8")
        url = f"{base_url}/ISAPI/AccessControl/UserInfo/Record?format=json"
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(req, timeout=15) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace").strip()
        if status in (200, 201) and _isapi_operation_succeeded(raw):
            return True, raw[:200]
        return False, f"HTTP {status}: {raw[:200]}"

    validity = {"enable": True, "beginTime": "2020-01-01T00:00:00", "endTime": "2037-12-31T23:59:59"}
    variants = [
        {"employeeNo": employee_no, "name": nombre, "userType": "normal",
         "organization": organization or "", "gender": 1, "Valid": validity},
        {"employeeNo": employee_no, "name": nombre, "userType": "normal", "gender": 1,
         "Valid": validity},
        {"employeeNo": employee_no, "name": nombre, "userType": "normal", "Valid": validity},
    ]
    # Some firmware accepts the personal verification mode during creation and
    # others only through UserInfo/SetUp. Try the explicit mode first, then keep
    # the compatible variants so employee creation is never lost for this field.
    if user_verify_mode:
        variants = [{**item, "userVerifyMode": user_verify_mode} for item in variants] + variants

    for user_info in variants:
        try:
            ok, msg = _try_create(user_info)
            if ok:
                return True, ""
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass

    # Final attempt to get error
    try:
        ok, msg = _try_create(user_info_min := {"employeeNo": employee_no, "name": nombre, "userType": "normal",
            "Valid": {"enable": True, "beginTime": "2020-01-01T00:00:00", "endTime": "2037-12-31T23:59:59"}})
        return ok, msg
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return False, str(e)


# ─── PEOPLE AND FACE ENROLLMENT ─────────────────────────────────
# These helpers deliberately keep biometric image bytes out of persistent
# configuration.  The UI holds an image only while the operator is reviewing
# and sending it to a device.

def hikvision_buscar_persona(info, employee_no):
    """Return the first ISAPI user matching ``employee_no``, or ``None``."""
    employee_no = str(employee_no or "").strip()
    if not employee_no:
        return None
    payload = {
        "UserInfoSearchCond": {
            "searchID": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "searchResultPosition": 0,
            "maxResults": 10,
            "EmployeeNoList": [{"employeeNo": employee_no}],
        }
    }
    data = hikvision_request_json(
        info, "/ISAPI/AccessControl/UserInfo/Search?format=json",
        method="POST", payload=payload, timeout=12,
    )
    root = data.get("UserInfoSearch", data.get("UserInfoSearchCond", data))
    users = root.get("UserInfo", root.get("UserInfoList", [])) if isinstance(root, dict) else []
    if isinstance(users, dict):
        users = [users]
    for user in users or []:
        if str(user.get("employeeNo", user.get("employeeNoString", ""))).strip() == employee_no:
            return user
    return None


def hikvision_listar_personas(info, maximo=1000):
    """Return remote Hikvision people with their stable employee IDs."""
    personas, posicion, max_results = [], 0, 50
    search_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    while posicion < maximo:
        payload = {"UserInfoSearchCond": {
            "searchID": search_id,
            "searchResultPosition": posicion,
            "maxResults": min(max_results, maximo - posicion),
        }}
        data = hikvision_request_json(
            info, "/ISAPI/AccessControl/UserInfo/Search?format=json",
            method="POST", payload=payload, timeout=15,
        )
        root = data.get("UserInfoSearch", data.get("UserInfoSearchCond", data))
        lista = root.get("UserInfo", root.get("UserInfoList", [])) if isinstance(root, dict) else []
        if isinstance(lista, dict):
            lista = [lista]
        for item in lista or []:
            employee_no = str(item.get("employeeNo", item.get("employeeNoString", ""))).strip()
            name = str(item.get("name", item.get("employeeName", ""))).strip()
            if employee_no and name and "admin" not in name.casefold():
                personas.append({"person_id": employee_no, "nombre": name, "raw": item})
        cantidad = len(lista or [])
        posicion += cantidad
        total = int(root.get("totalMatches", root.get("numOfMatches", 0)) or 0) if isinstance(root, dict) else 0
        if not cantidad or cantidad < max_results or (total and posicion >= total):
            break
    return personas


def hikvision_crear_y_verificar_persona(info, nombre, extra_fields=None):
    """Create a user when absent and verify it through ISAPI afterwards.

    Returns ``(status, detail, record)`` where status is ``created``,
    ``exists`` or ``error``.  A successful HTTP create alone is never treated
    as a verified enrolment.
    """
    extra_fields = dict(extra_fields or {})
    employee_no = str(extra_fields.get("person_id") or "").strip()
    if not employee_no:
        return "error", "Se requiere un ID estable de empleado para verificar el alta.", None
    try:
        existing = hikvision_buscar_persona(info, employee_no)
        if existing:
            existing_name = limpiar_nombre(existing.get("name", existing.get("employeeName", "")))
            expected_name = limpiar_nombre(nombre)
            if existing_name and existing_name.casefold() != expected_name.casefold():
                return "error", f"El ID {employee_no} ya pertenece a {existing_name}.", existing
            return "exists", "La persona ya existía y fue verificada por ID.", existing
    except Exception as exc:
        return "error", f"No se pudo verificar si la persona ya existe: {exc}", None
    ok, detail = hikvision_crear_persona(info, nombre, extra_fields)
    if not ok:
        return "error", detail or "El checador rechazó el alta.", None
    try:
        record = hikvision_buscar_persona(info, employee_no)
    except Exception as exc:
        return "error", f"El checador respondió al alta, pero no se pudo verificar: {exc}", None
    if not record:
        return "error", "El checador respondió al alta, pero no devolvió la persona al verificarla.", None
    verified_name = limpiar_nombre(record.get("name", record.get("employeeName", "")))
    if verified_name and verified_name.casefold() != limpiar_nombre(nombre).casefold():
        return "error", f"El ID se creó, pero la verificación devolvió el nombre {verified_name}.", record
    return "created", "Persona creada y verificada por ID.", record


def _hikvision_valor_contenido(value):
    """Return whether an ISAPI right/plan field contains an effective value."""
    if isinstance(value, dict):
        if "planTemplateNo" in value:
            return _hikvision_valor_contenido(value.get("planTemplateNo"))
        return any(_hikvision_valor_contenido(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_hikvision_valor_contenido(item) for item in value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value or "").strip().casefold()
    return text not in {"", "0", "none", "null", "false", "[]", "{}"}


def _hikvision_campos_perfil_acceso(record):
    """Return only effective, safe-to-copy access fields from an ISAPI user."""
    record = record if isinstance(record, dict) else {}
    profile = {}
    if _hikvision_valor_contenido(record.get("doorRight")):
        profile["doorRight"] = record["doorRight"]
    if _hikvision_valor_contenido(record.get("RightPlan")):
        profile["RightPlan"] = record["RightPlan"]
    return profile


def hikvision_descubrir_perfil_acceso(info):
    """Discover the sole effective access profile configured on this terminal.

    Standalone Hikvision firmware often does not expose PlanTemplate endpoints,
    but it does return each user's effective ``doorRight``/``RightPlan`` through
    UserInfo/Search. A profile is reusable only when every authorized user
    publishes the same profile; otherwise assigning the first match could grant
    the wrong door or schedule.
    """
    posicion = 0
    max_resultados = 30
    search_id = hikvision_search_id()
    profiles = {}
    for _ in range(80):
        payload = {"UserInfoSearchCond": {
            "searchID": search_id,
            "searchResultPosition": posicion,
            "maxResults": max_resultados,
        }}
        data = hikvision_request_json(
            info, "/ISAPI/AccessControl/UserInfo/Search?format=json",
            method="POST", payload=payload, timeout=15,
        )
        root = data.get("UserInfoSearch", data.get("UserInfoSearchCond", data))
        records = root.get("UserInfo", root.get("UserInfoList", [])) if isinstance(root, dict) else []
        if isinstance(records, dict):
            records = [records]
        for record in records or []:
            profile = _hikvision_campos_perfil_acceso(record)
            if not profile:
                continue
            key = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            employee_no = str(record.get("employeeNo", record.get("employeeNoString", ""))).strip()
            profiles.setdefault(key, {"profile": profile, "employees": []})["employees"].append(employee_no)
        total = int(root.get("totalMatches", root.get("numOfMatches", 0)) or 0) if isinstance(root, dict) else 0
        cantidad = len(records or [])
        posicion += cantidad
        if cantidad < max_resultados or (total and posicion >= total):
            break
    if len(profiles) == 1:
        discovered = next(iter(profiles.values()))
        employee_no = next((value for value in discovered["employees"] if value), "existente")
        return discovered["profile"], (
            f"Se reutilizó el único perfil de acceso efectivo del empleado {employee_no} "
            "sin inventar puerta ni plan."
        )
    if len(profiles) > 1:
        return {}, (
            "El terminal publicó varios perfiles de acceso efectivos; no se asignó ninguno "
            "automáticamente para evitar otorgar una puerta o plan incorrectos."
        )
    return {}, "El terminal no publicó ningún usuario con un permiso de acceso efectivo para reutilizar."

def hikvision_resumen_acceso(record):
    """Summarize face mode and explicit door/plan rights returned by ISAPI.

    Missing fields are reported as unconfirmed rather than denied because some
    standalone terminals omit access-control fields from UserInfo/Search.
    """
    record = record if isinstance(record, dict) else {}
    verify_mode = str(record.get("userVerifyMode", "")).strip()
    mode_present = "userVerifyMode" in record
    facial_mode = bool(verify_mode and "face" in verify_mode.casefold())

    rights_present = "doorRight" in record or "RightPlan" in record
    door_right = record.get("doorRight")
    right_plan = record.get("RightPlan")
    permission = _hikvision_valor_contenido(door_right) or _hikvision_valor_contenido(right_plan)

    mode_state = "facial_confirmado" if facial_mode else ("no_facial" if mode_present else "sin_confirmar")
    access_state = "permiso_confirmado" if permission else ("sin_permiso" if rights_present else "sin_confirmar")
    return {
        "modo": mode_state,
        "acceso": access_state,
        "user_verify_mode": verify_mode,
        "listo_desbloqueo_facial": facial_mode and permission,
    }


def hikvision_configurar_modo_rostro(info, employee_no, perfil_acceso=None):
    """Configure face verification and an existing effective access profile.

    A face template alone is not sufficient for a usable checada on access
    terminals: the employee also needs a valid door/plan assignment. When the
    caller does not provide one, discover it from an already-authorized user on
    the same terminal rather than guessing ISAPI plan identifiers.
    """
    employee_no = str(employee_no or "").strip()
    if not employee_no:
        return False, "error", None, "No se recibió el número de empleado."
    profile_detail = ""
    try:
        current = hikvision_buscar_persona(info, employee_no) or {}
    except Exception:
        current = {}
    profile = _hikvision_campos_perfil_acceso(perfil_acceso)
    if not profile:
        profile = _hikvision_campos_perfil_acceso(current)
    if not profile:
        try:
            profile, profile_detail = hikvision_descubrir_perfil_acceso(info)
        except Exception as exc:
            profile = {}
            profile_detail = f"No se pudo descubrir un perfil de acceso existente: {exc}"
    payload_user = {
        "employeeNo": employee_no,
        "userType": "normal",
        "userVerifyMode": "face",
    }
    payload_user.update(profile)
    payload = {"UserInfo": payload_user}
    try:
        response = hikvision_request_json(
            info, "/ISAPI/AccessControl/UserInfo/SetUp?format=json",
            method="PUT", payload=payload, timeout=15,
        )
        if isinstance(response, dict):
            status = response.get("statusCode")
            status_text = str(response.get("statusString", response.get("subStatusCode", ""))).casefold()
            if (status is not None and str(status) not in {"0", "1"}) or any(
                token in status_text for token in ("fail", "error", "invalid")
            ):
                return False, "error", None, "El checador rechazó activar el modo de verificación facial."
        verified = hikvision_buscar_persona(info, employee_no)
    except Exception as exc:
        return False, "error", None, f"No se pudo configurar el modo facial: {exc}"
    summary = hikvision_resumen_acceso(verified)
    if summary["modo"] != "facial_confirmado":
        return False, summary["modo"], verified, "El checador no devolvió el modo facial al verificar al empleado."
    if summary["acceso"] == "permiso_confirmado":
        detail = "Modo facial y permiso de acceso confirmados por el checador."
        if profile_detail:
            detail = f"{detail} {profile_detail}"
        return True, summary["modo"], verified, detail
    detail = (
        "El checador confirmó el modo facial, pero no devolvió un permiso de acceso efectivo; "
        "la persona no debe considerarse lista para checar por rostro."
    )
    if profile_detail:
        detail = f"{detail} {profile_detail}"
    return True, summary["modo"], verified, detail

def hikvision_capacidades_rostro(info):
    """Discover face-library support without assuming it from the model name.

    Firmware families expose different capability paths.  The first successful
    structured response is returned for diagnostics; an unavailable endpoint is
    *not* interpreted as face-enrolment support.
    """
    attempts = (
        "/ISAPI/Intelligent/FDLib/capabilities?format=json",
        "/ISAPI/Intelligent/FDLib/FaceDataRecord/capabilities?format=json",
    )
    errors = []
    capability_data = {}
    capability_path = ""
    for path in attempts:
        try:
            data = hikvision_request_json(info, path, timeout=10)
            if isinstance(data, dict):
                capability_data = data
                capability_path = path
                break
        except Exception as exc:
            errors.append(str(exc).split("\n", 1)[0])
    if capability_data:
        try:
            fdid, library = hikvision_descubrir_biblioteca_rostro(info)
            if fdid:
                return {
                    "supported": True, "path": capability_path, "data": capability_data,
                    "fdid": fdid, "library": library,
                    "detail": f"El equipo confirmó enrolamiento facial remoto en la biblioteca {fdid}.",
                }
            errors.append("El equipo respondió capacidades, pero no publicó una biblioteca facial utilizable.")
        except Exception as exc:
            errors.append(str(exc).split("\n", 1)[0])
    return {
        "supported": False,
        "path": "",
        "data": {},
        "detail": "El equipo no confirmó enrolamiento facial remoto por ISAPI. Registra el rostro directamente en el terminal.",
        "errors": errors[-2:],
    }


def hikvision_descubrir_biblioteca_rostro(info):
    """Discover a real black-list face library instead of assuming FDID=1."""
    errors = []
    data = None
    for path in ("/ISAPI/Intelligent/FDLib?format=json", "/ISAPI/Intelligent/FDLib"):
        try:
            data = hikvision_request_json(info, path, timeout=10)
            if isinstance(data, dict):
                break
        except Exception as exc:
            errors.append(str(exc).split("\n", 1)[0])
    if not isinstance(data, dict):
        raise Exception("El equipo no publicó el inventario de bibliotecas faciales. " + " | ".join(errors[-2:]))

    records = []
    def collect(value):
        if isinstance(value, dict):
            if "FDID" in value or "fdid" in value:
                records.append(value)
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
    collect(data)
    candidates = []
    for item in records or []:
        if not isinstance(item, dict):
            continue
        fdid = str(item.get("FDID", item.get("fdid", ""))).strip()
        lib_type = str(item.get("faceLibType", item.get("FDLibType", "blackFD"))).strip()
        if fdid and lib_type.casefold() in {"blackfd", "ordinary", "allowable", ""}:
            candidates.append(item)
    if not candidates:
        return "", {}
    armed = [item for item in candidates if str(item.get("libArmingType", "")).casefold() == "arminglib"]
    selected = (armed or candidates)[0]
    return str(selected.get("FDID", selected.get("fdid", ""))).strip(), selected


def _multipart_body(fields, file_field, filename, image_bytes):
    boundary = "----AsistenciaFace" + uuid.uuid4().hex
    chunks = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n'.encode(),
            b"Content-Type: application/json; charset=utf-8\r\n\r\n",
            str(value).encode("utf-8"), b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: image/jpeg\r\n\r\n", image_bytes, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, b"".join(chunks)


def _isapi_operation_succeeded(raw):
    if not raw:
        return True
    try:
        data = json.loads(raw)
    except Exception:
        try:
            data = _hikvision_xml_a_dict(raw)
        except Exception:
            return True
    if not isinstance(data, dict):
        return True
    status = data.get("statusCode")
    status_string = str(data.get("statusString", data.get("subStatusCode", ""))).casefold()
    error_msg = str(data.get("errorMsg", "")).casefold()
    if status is not None and str(status) not in {"0", "1"}:
        return False
    return not any(token in status_string or token in error_msg for token in ("fail", "error", "invalid"))


def _face_record_matches(value, fdid, fpid):
    """Return whether a decoded ISAPI response contains the requested face record."""
    if isinstance(value, dict):
        found_fpid = str(value.get("FPID", value.get("fpid", ""))).strip()
        found_fdid = str(value.get("FDID", value.get("fdid", ""))).strip()
        if found_fpid == str(fpid) and (not found_fdid or found_fdid == str(fdid)):
            return True
        return any(_face_record_matches(nested, fdid, fpid) for nested in value.values())
    if isinstance(value, list):
        return any(_face_record_matches(item, fdid, fpid) for item in value)
    return False


def hikvision_verificar_rostro(info, fdid, fpid):
    """Read back a face record after enrolment when the terminal supports it.

    Some Hikvision firmware only supports the multipart creation endpoint.  A
    failed or unsupported read-back is intentionally reported as unverified,
    rather than as a failed upload or a confirmed facial template.
    """
    fdid = str(fdid or "").strip()
    fpid = str(fpid or "").strip()
    if not fdid or not fpid:
        return False, "No fue posible verificar el rostro porque faltan FDID o FPID."
    query = urllib.parse.urlencode({"format": "json", "FDID": fdid, "FPID": fpid})
    attempts = (
        f"/ISAPI/Intelligent/FDLib/FaceDataRecord?{query}",
        f"/ISAPI/Intelligent/FDLib/FaceDataRecord?FDID={urllib.parse.quote(fdid)}&FPID={urllib.parse.quote(fpid)}",
    )
    responses, errors = [], []
    for path in attempts:
        try:
            data = hikvision_request_json(info, path, timeout=12)
            if _face_record_matches(data, fdid, fpid):
                return True, f"Rostro verificado en la biblioteca {fdid}."
            responses.append(path)
        except Exception as exc:
            errors.append(str(exc).split("\n", 1)[0])
    if responses:
        return False, (
            f"El checador aceptó el envío del rostro, pero la consulta de la biblioteca {fdid} "
            f"no devolvió el FPID {fpid}; quedó pendiente de verificación."
        )
    return False, (
        f"El checador aceptó el envío del rostro, pero este firmware no permitió consultar "
        f"la biblioteca {fdid}; quedó pendiente de verificación."
    )


def hikvision_enviar_rostro(info, employee_no, image_bytes, filename="rostro.jpg", fdid=None, nombre=""):
    """Send a validated JPEG through the Hikvision FDLib multipart endpoint.

    This is attempted only after ``hikvision_capacidades_rostro`` confirmed a
    The returned state distinguishes a verified template from an accepted
    upload that the firmware does not allow us to read back.
    """
    if not image_bytes:
        return "error", "No hay imagen facial para enviar."
    capabilities = hikvision_capacidades_rostro(info)
    if not capabilities.get("supported"):
        return "error", capabilities.get("detail", "El equipo no confirmó soporte facial remoto.")
    discovered_fdid = str(capabilities.get("fdid", "")).strip()
    fdid = str(fdid or discovered_fdid).strip()
    if not fdid:
        return "error", "El equipo no publicó un FDID facial utilizable; no se asumió una biblioteca por defecto."
    library = capabilities.get("library") if isinstance(capabilities.get("library"), dict) else {}
    face_lib_type = str(library.get("faceLibType", library.get("FDLibType", "blackFD"))).strip() or "blackFD"
    face_record = json.dumps({
        "faceLibType": face_lib_type,
        "FDID": fdid,
        "FPID": str(employee_no),
        "name": str(nombre or employee_no),
    }, ensure_ascii=False)
    usuario = str(info.get("usuario", "admin")).strip() or "admin"
    password = str(info.get("password", ""))
    errors = []
    for file_field in ("FaceImage", "faceImage"):
        boundary, body = _multipart_body({"FaceDataRecord": face_record}, file_field, filename, image_bytes)
        for base_url in hikvision_base_urls(info):
            try:
                opener = _get_opener(base_url, usuario, password)
                request = urllib.request.Request(
                    f"{base_url}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json, application/xml, */*"},
                    method="POST",
                )
                with opener.open(request, timeout=20) as response:
                    status = response.status
                    raw = response.read().decode("utf-8", errors="replace").strip()
                if status in (200, 201) and _isapi_operation_succeeded(raw):
                    verified, verification_detail = hikvision_verificar_rostro(info, fdid, employee_no)
                    if verified:
                        return "enviado_y_confirmado", verification_detail
                    return "enviado_sin_verificar", verification_detail
                errors.append(f"{file_field} HTTP {status}: {raw[:160]}")
            except urllib.error.HTTPError as exc:
                errors.append(f"{file_field} HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:160]}")
            except Exception as exc:
                errors.append(f"{file_field}: {exc}")
    return "error", "No se pudo enviar el rostro: " + " | ".join(errors[-2:])
