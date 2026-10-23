"""Creación, validación y restauración transaccional de respaldos."""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime

import config_manager
from services import attendance_service, auth_service, history_service


BACKUP_FORMAT = "asistencia-agropecuaria-backup"
BACKUP_VERSION = 1
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
DATA_FILES = (
    "config_global.json",
    "processing_history.json",
    "attendance_cache.json",
)
USER_FILE = "usuarios.json"


class BackupError(ValueError):
    """Error controlado al crear, validar o restaurar un respaldo."""


def _paths():
    return {
        "config_global.json": config_manager.PATH_CONFIG,
        "processing_history.json": history_service.HISTORY_PATH,
        "attendance_cache.json": attendance_service.ATTENDANCE_PATH,
        USER_FILE: auth_service.user_path(),
    }


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _read_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"No se pudo leer '{os.path.basename(path)}': {exc}") from exc


def _validated_payloads_from_disk():
    paths = _paths()
    config = config_manager.normalizar_configuracion(
        _read_json_file(paths["config_global.json"], config_manager.configuracion_default())
    )
    history = _read_json_file(paths["processing_history.json"], [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise BackupError("El historial de procesos local no tiene una estructura válida.")
    attendance = attendance_service.normalize_attendance_data(
        _read_json_file(paths["attendance_cache.json"], attendance_service.empty_attendance_data())
    )
    users = auth_service.load_store(paths[USER_FILE])
    return {
        "config_global.json": _json_bytes(config),
        "processing_history.json": _json_bytes(history),
        "attendance_cache.json": _json_bytes(attendance),
        USER_FILE: _json_bytes(users),
    }, config, history, attendance


def _stats(config, history, attendance):
    devices = config.get("checadores", [])
    people = set(config.get("personas_sistema", []))
    for device in devices:
        people.update(device.get("empleados", []))
    return {
        "devices": len(devices),
        "people": len(people),
        "non_working_days": len(config.get("dias_no_laborados", [])),
        "biometric_states": len(config.get("personas_biometria", [])),
        "processing_history_items": len(history),
        "attendance_records": len(attendance.get("records", [])),
    }


def create_backup(destination, *, kind="manual"):
    if not destination:
        raise BackupError("No se indicó dónde guardar el respaldo.")
    payloads, config, history, attendance = _validated_payloads_from_disk()
    created_at = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "format": BACKUP_FORMAT,
        "backup_version": BACKUP_VERSION,
        "application_version": config_manager.VERSION,
        "created_at": created_at,
        "kind": kind,
        "external_excel_files_included": False,
        "biometric_images_or_templates_included": False,
        "contains_sensitive_configuration": True,
        "files": {
            name: {"sha256": _sha256(content), "size": len(content)}
            for name, content in payloads.items()
        },
        "stats": _stats(config, history, attendance),
    }
    destination = os.path.abspath(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temp_path = f"{destination}.tmp"
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json_bytes(manifest))
            for name, content in payloads.items():
                archive.writestr(name, content)
        os.replace(temp_path, destination)
    except (OSError, zipfile.BadZipFile) as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise BackupError(f"No se pudo crear el respaldo: {exc}") from exc
    try:
        verified = validate_backup(destination)
    except BackupError as exc:
        try:
            if os.path.exists(destination):
                os.remove(destination)
        except OSError:
            pass
        raise BackupError(
            "El archivo de respaldo se creó, pero no superó la verificación de integridad y fue descartado: "
            f"{exc}"
        ) from exc
    return {"path": destination, "manifest": verified["manifest"], "stats": verified["stats"]}


def validate_backup(archive_path):
    if not archive_path or not os.path.isfile(archive_path):
        raise BackupError("El archivo de respaldo no existe.")
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BackupError("El respaldo contiene archivos duplicados.")
            legacy_allowed = {"manifest.json", *DATA_FILES}
            current_allowed = {"manifest.json", *DATA_FILES, USER_FILE}
            if frozenset(names) not in {frozenset(legacy_allowed), frozenset(current_allowed)}:
                raise BackupError("El respaldo está incompleto o contiene archivos no reconocidos.")
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise BackupError("El respaldo supera el tamaño máximo permitido.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != BACKUP_FORMAT:
                raise BackupError("El archivo no es un respaldo de Asistencia Agropecuaria.")
            if manifest.get("backup_version") != BACKUP_VERSION:
                raise BackupError("La versión del respaldo no es compatible con esta aplicación.")
            declared = manifest.get("files")
            if not isinstance(declared, dict) or frozenset(declared) not in {frozenset(DATA_FILES), frozenset((*DATA_FILES, USER_FILE))}:
                raise BackupError("El manifiesto del respaldo está incompleto.")
            raw = {}
            for name in declared:
                content = archive.read(name)
                metadata = declared.get(name, {})
                if metadata.get("sha256") != _sha256(content) or metadata.get("size") != len(content):
                    raise BackupError(f"Falló la verificación de integridad de '{name}'.")
                raw[name] = content
    except BackupError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"El respaldo está dañado o no se puede leer: {exc}") from exc

    try:
        config = config_manager.normalizar_configuracion(json.loads(raw["config_global.json"].decode("utf-8")))
        history = json.loads(raw["processing_history.json"].decode("utf-8"))
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise BackupError("El historial incluido en el respaldo no es válido.")
        attendance = attendance_service.normalize_attendance_data(
            json.loads(raw["attendance_cache.json"].decode("utf-8"))
        )
        users = auth_service.normalize_store(json.loads(raw[USER_FILE].decode("utf-8"))) if USER_FILE in raw else None
    except BackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise BackupError(f"Los datos internos del respaldo no son válidos: {exc}") from exc
    payloads = {
        "config_global.json": _json_bytes(config),
        "processing_history.json": _json_bytes(history),
        "attendance_cache.json": _json_bytes(attendance),
    }
    if users is not None:
        payloads[USER_FILE] = _json_bytes(users)
    return {"manifest": manifest, "payloads": payloads, "stats": _stats(config, history, attendance)}


def _atomic_write(path, content):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temp_path = f"{path}.restore.tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def restore_backup(archive_path, preventive_directory=None):
    validated = validate_backup(archive_path)
    paths = _paths()
    preventive_directory = preventive_directory or os.path.join(config_manager.APP_DIR, "respaldos_previos")
    os.makedirs(preventive_directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    preventive_path = os.path.join(preventive_directory, f"Respaldo_PreRestauracion_{stamp}.zip")
    try:
        create_backup(preventive_path, kind="pre_restore")
    except (BackupError, OSError) as exc:
        raise BackupError(
            "No se pudo crear y verificar el respaldo preventivo. La restauración no se inició: "
            f"{exc}"
        ) from exc

    originals = {}
    for name, path in paths.items():
        if os.path.exists(path):
            with open(path, "rb") as handle:
                originals[name] = handle.read()
        else:
            originals[name] = None
    try:
        for name, content in validated["payloads"].items():
            _atomic_write(paths[name], content)
    except Exception as exc:
        rollback_errors = []
        for name, original in originals.items():
            try:
                if original is None:
                    if os.path.exists(paths[name]):
                        os.remove(paths[name])
                else:
                    _atomic_write(paths[name], original)
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        detail = f" No se pudo completar el rollback: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise BackupError(f"La restauración falló y se recuperó el estado anterior.{detail}") from exc

    config_manager.invalidar_cache_config()
    config_manager.invalidar_horarios_cache()
    history_service.invalidar_cache()
    attendance_service.invalidate_cache()
    return {
        "preventive_backup": preventive_path,
        "manifest": validated["manifest"],
        "stats": validated["stats"],
    }


def suggested_backup_name(now=None):
    now = now or datetime.now()
    return f"Respaldo_Asistencia_{now.strftime('%Y-%m-%d_%H%M%S')}.zip"


def preventive_backup_directory():
    """Carpeta local donde se guardan las copias previas a una restauración."""
    return os.path.join(config_manager.APP_DIR, "respaldos_previos")