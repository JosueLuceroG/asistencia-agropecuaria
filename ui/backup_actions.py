"""Acciones Tk reutilizables para crear y restaurar respaldos."""
from __future__ import annotations

import os
from tkinter import filedialog, messagebox

from services.backup_service import (
    BackupError,
    create_backup,
    restore_backup,
    preventive_backup_directory,
    suggested_backup_name,
    validate_backup,
)


def _summary(stats):
    return (
        f"{stats.get('devices', 0)} checadores, "
        f"{stats.get('people', 0)} personas, "
        f"{stats.get('attendance_records', 0)} checadas y "
        f"{stats.get('processing_history_items', 0)} registros de historial"
    )


def _format_created_at(value):
    value = str(value or "").strip()
    if not value:
        return "No disponible"
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d/%m/%Y %I:%M:%S %p")
    except ValueError:
        return value


def _backup_preview(path, validated):
    manifest = validated.get("manifest", {})
    return (
        f"Archivo seleccionado:\n{os.path.abspath(path)}\n\n"
        f"Creado: {_format_created_at(manifest.get('created_at'))}\n"
        f"Versión de aplicación: {manifest.get('application_version', 'No disponible')}\n"
        f"Tipo: {manifest.get('kind', 'manual')}\n"
        f"Integridad: verificada correctamente.\n\n"
        f"Contenido: {_summary(validated.get('stats', {}))}.\n"
        f"También incluye {validated.get('stats', {}).get('non_working_days', 0)} días no laborados y "
        f"{validated.get('stats', {}).get('biometric_states', 0)} estados biométricos.\n\n"
        "Incluye usuarios y hashes de contraseña; no incluye contraseñas visibles, archivos Excel externos, "
        "fotografías ni plantillas biométricas."
    )


def create_backup_dialog(parent):
    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Crear respaldo completo",
        initialfile=suggested_backup_name(),
        defaultextension=".zip",
        filetypes=[("Respaldo de Asistencia", "*.zip")],
    )
    if not path:
        return None
    try:
        result = create_backup(path)
    except (BackupError, OSError) as exc:
        messagebox.showerror("No se pudo crear el respaldo", str(exc), parent=parent)
        return None
    messagebox.showinfo(
        "Respaldo creado",
        "El respaldo se creó y se verificó correctamente.\n\n"
        f"Contenido: {_summary(result['stats'])}.\n\n"
        "Incluye usuarios y hashes de contraseña, pero no contraseñas visibles, archivos Excel externos "
        "ni fotos/plantillas biométricas.\n\n"
        f"Archivo:\n{result['path']}",
        parent=parent,
    )
    return result


def restore_backup_dialog(parent, on_restored=None):
    path = filedialog.askopenfilename(
        parent=parent,
        title="Restaurar respaldo",
        filetypes=[("Respaldo de Asistencia", "*.zip"), ("Todos los archivos", "*.*")],
    )
    if not path:
        return None
    try:
        validated = validate_backup(path)
    except BackupError as exc:
        messagebox.showerror("Respaldo no válido", str(exc), parent=parent)
        return None
    if not messagebox.askyesno(
        "Confirmar restauración",
        "Se reemplazarán la configuración, el historial, la caché local de checadas y los usuarios.\n\n"
        f"{_backup_preview(path, validated)}\n\n"
        "Antes de restaurar se creará automáticamente un respaldo preventivo del estado actual. "
        "Los dispositivos biométricos físicos y los archivos Excel externos no serán modificados.\n\n"
        "¿Deseas continuar?",
        parent=parent,
    ):
        return None
    try:
        result = restore_backup(path)
    except (BackupError, OSError) as exc:
        messagebox.showerror("No se pudo restaurar", str(exc), parent=parent)
        return None
    if on_restored:
        on_restored()
    messagebox.showinfo(
        "Restauración completada",
        "Los datos se restauraron correctamente.\n\n"
        f"Contenido restaurado: {_summary(result['stats'])}.\n\n"
        f"Respaldo preventivo:\n{os.path.abspath(result['preventive_backup'])}\n\n"
        "La interfaz se actualizó. Reinicia la aplicación antes de continuar trabajando para "
        "garantizar que todas las ventanas usen el estado restaurado.",
        parent=parent,
    )
    return result


def open_preventive_backups_folder(parent):
    """Abre la ubicación local de copias previas a una restauración."""
    directory = os.path.abspath(preventive_backup_directory())
    try:
        os.makedirs(directory, exist_ok=True)
        os.startfile(directory)
    except OSError as exc:
        messagebox.showerror("No se pudo abrir la carpeta", f"Carpeta: {directory}\n\n{exc}", parent=parent)
        return None
    return directory