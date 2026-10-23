"""Helpers for person provisioning and non-sensitive biometric workflow state."""
from __future__ import annotations

import io
import re
from datetime import datetime


PERSON_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def _nombre_normalizado(value):
    return " ".join(str(value or "").replace(".", " ").split()).title()


def _nombres_equivalentes_en_zkteco(actual, esperado):
    """Accept exact names and the common terminal-side 24-char truncation."""
    actual_key = _nombre_normalizado(actual).casefold()
    expected_key = _nombre_normalizado(esperado).casefold()
    if actual_key == expected_key:
        return True
    shorter, longer = sorted((actual_key, expected_key), key=len)
    return len(shorter) >= 20 and longer.startswith(shorter)


def validar_person_id(value):
    person_id = str(value or "").strip()
    if not person_id:
        return False, "El ID / número de empleado es obligatorio."
    if not PERSON_ID_RE.fullmatch(person_id):
        return False, "El ID admite 1 a 32 letras, números, punto, guion y guion bajo."
    return True, ""


def normalizar_persona(person_id, nombre):
    person_id = str(person_id or "").strip()
    nombre = _nombre_normalizado(nombre)
    ok, detail = validar_person_id(person_id)
    if not ok:
        raise ValueError(detail)
    if len(nombre) < 2:
        raise ValueError("Escribe el nombre completo de la persona.")
    return {"person_id": person_id, "nombre": nombre}


def listar_personas_zkteco(conn):
    personas = []
    for user in conn.get_users() or []:
        person_id = str(getattr(user, "user_id", "") or getattr(user, "uid", "")).strip()
        nombre = _nombre_normalizado(getattr(user, "name", ""))
        if person_id and nombre and "admin" not in nombre.casefold():
            personas.append({"person_id": person_id, "nombre": nombre, "raw": user})
    return sorted(personas, key=lambda item: (item["nombre"].casefold(), item["person_id"].casefold()))


def crear_y_verificar_zkteco(conn, person_id, nombre):
    """Create a ZKTeco user with a stable user_id and verify it afterwards."""
    persona = normalizar_persona(person_id, nombre)
    users = list(conn.get_users() or [])
    for user in users:
        if str(getattr(user, "user_id", "")).strip() == persona["person_id"]:
            actual = _nombre_normalizado(getattr(user, "name", ""))
            if not _nombres_equivalentes_en_zkteco(actual, persona["nombre"]):
                return "error", f"El ID {persona['person_id']} ya pertenece a {actual or 'otra persona'}.", None
            return "exists", "La persona ya existía y fue verificada por ID.", user

    used_uids = {int(getattr(user, "uid", 0) or 0) for user in users}
    uid = next((candidate for candidate in range(1, 65536) if candidate not in used_uids), None)
    if uid is None:
        return "error", "El equipo no tiene un UID interno disponible.", None
    conn.set_user(
        uid=uid,
        user_id=persona["person_id"],
        name=persona["nombre"],
        privilege=0,
        password="",
    )
    for user in conn.get_users() or []:
        if str(getattr(user, "user_id", "")).strip() == persona["person_id"]:
            actual = _nombre_normalizado(getattr(user, "name", ""))
            if _nombres_equivalentes_en_zkteco(actual, persona["nombre"]):
                return "created", "Persona creada y verificada por ID estable.", user
    return "error", "El equipo respondió al alta, pero la persona no apareció al verificarla.", None


def preparar_jpeg(path=None, image=None, min_width=640, min_height=480, target_min_kb=60, target_max_kb=200):
    """Validate and optimize a face image entirely in memory."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Instala Pillow para cargar y optimizar fotografías.") from exc
    opened_image = None
    if image is None:
        if not path:
            raise ValueError("Selecciona una imagen JPG o JPEG.")
        opened_image = Image.open(path)
        try:
            if str(opened_image.format or "").upper() not in {"JPEG", "JPG"}:
                raise ValueError("El archivo debe estar codificado realmente como JPG o JPEG.")
            opened_image.load()
            image = opened_image.copy()
        finally:
            opened_image.close()
    else:
        image.load()
    if image.width < min_width or image.height < min_height:
        raise ValueError(f"La fotografía debe medir al menos {min_width}×{min_height} píxeles.")
    if image.mode != "RGB":
        image = image.convert("RGB")
    max_dimension = 1600
    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    chosen = None
    for quality in (92, 88, 84, 80, 76, 72, 68, 64, 60, 55, 50, 45, 40):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=False)
        data = buffer.getvalue()
        chosen = (data, quality)
        if len(data) <= target_max_kb * 1024:
            break
    data, quality = chosen
    if len(data) > target_max_kb * 1024:
        raise ValueError(f"No fue posible reducir la fotografía a {target_max_kb} KB sin invalidarla.")
    warning = ""
    if len(data) < target_min_kb * 1024:
        warning = f"La imagen pesa {len(data) / 1024:.0f} KB; es válida, aunque el objetivo recomendado inicia en {target_min_kb} KB."
    return {
        "bytes": data,
        "width": image.width,
        "height": image.height,
        "size_kb": round(len(data) / 1024, 1),
        "quality": quality,
        "warning": warning,
        "image": image.copy(),
    }


def upsert_estado_biometrico(
    config, person_id, nombre, checador, *, confirmado=None, huella=None,
    rostro=None, modo_rostro=None, acceso=None, detalle="",
):
    """Store status metadata only. Never accepts or persists biometric bytes."""
    persona = normalizar_persona(person_id, nombre)
    device_name = str(checador or "").strip()
    if not device_name:
        raise ValueError("El checador es obligatorio para guardar el estado biométrico.")
    records = [item for item in config.get("personas_biometria", []) if isinstance(item, dict)]
    record = next((item for item in records if str(item.get("persona_id", "")).casefold() == persona["person_id"].casefold() and str(item.get("checador", "")).casefold() == device_name.casefold()), None)
    if record is None:
        record = {
            "persona_id": persona["person_id"], "nombre": persona["nombre"], "checador": device_name,
            "estado": "pendiente_obligatoria", "tipo_confirmado": "",
            "huella": "requiere_captura_en_dispositivo", "rostro": "pendiente",
            "modo_rostro": "sin_confirmar", "acceso": "sin_confirmar",
        }
        records.append(record)
    record.update({"persona_id": persona["person_id"], "nombre": persona["nombre"], "checador": device_name})
    if huella is not None:
        record["huella"] = huella
    if rostro is not None:
        record["rostro"] = rostro
    if modo_rostro is not None:
        record["modo_rostro"] = modo_rostro
    if acceso is not None:
        record["acceso"] = acceso
    if confirmado in {"huella", "rostro"}:
        record["estado"] = "confirmada"
        record["tipo_confirmado"] = confirmado
    elif confirmado is False:
        record["estado"] = "pendiente_obligatoria"
        record["tipo_confirmado"] = ""
    record["actualizado_en"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record["detalle"] = str(detalle or "")[:500]
    config["personas_biometria"] = records
    return record