import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_manager
from services import attendance_service, auth_service, backup_service, history_service
from services.device_health_service import (
    STATUS_ERROR,
    STATUS_HEALTHY,
    STATUS_NEVER_SYNCED,
    STATUS_WARNING,
    build_device_health_rows,
    classify_sync_status,
)


class BackupAndAttendanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name
        self.config_path = os.path.join(self.directory, "config_global.json")
        self.history_path = os.path.join(self.directory, "processing_history.json")
        self.attendance_path = os.path.join(self.directory, "attendance_cache.json")
        self.patchers = [
            patch.object(config_manager, "APP_DIR", self.directory),
            patch.object(config_manager, "PATH_CONFIG", self.config_path),
            patch.object(config_manager, "PATH_CONFIG_ANTERIOR", ""),
            patch.object(config_manager, "PATH_CONFIG_LOCAL", ""),
            patch.object(history_service, "HISTORY_PATH", self.history_path),
            patch.object(attendance_service, "ATTENDANCE_PATH", self.attendance_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        config_manager.invalidar_cache_config()
        history_service.invalidar_cache()
        attendance_service.invalidate_cache()

    def tearDown(self):
        config_manager.invalidar_cache_config()
        history_service.invalidar_cache()
        attendance_service.invalidate_cache()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def _seed(self, person="Ana López"):
        config_manager.guardar_configuracion({
            "checadores": [{
                "nombre": "Acceso", "marca": "hikvision", "ip": "127.0.0.1",
                "port": 80, "empleados": [person],
            }],
            "personas_sistema": [person],
            "dias_no_laborados": [{"fecha": "2026-10-09", "motivo": "Prueba"}],
        })
        history_service.save_history([{"status": "Completado", "records": 1, "output_path": "C:/externo.xlsx"}])
        attendance_service.append_attendance([
            {"nombre": person, "fecha_hora": datetime(2026, 10, 9, 8, 0)},
        ], device="Acceso", source="prueba")

    def test_attendance_cache_deduplicates_and_round_trips(self):
        first = attendance_service.append_attendance([
            {"nombre": "Ana López", "fecha_hora": datetime(2026, 10, 9, 8, 0)},
            {"nombre": "Ana López", "fecha_hora": datetime(2026, 10, 9, 8, 0)},
        ], device="Acceso", source="prueba")
        second = attendance_service.append_attendance([
            {"nombre": "Ana López", "fecha_hora": "2026-10-09T08:00:00"},
        ], device="Acceso", source="prueba")

        self.assertEqual(first["added"], 1)
        self.assertEqual(second["added"], 0)
        self.assertEqual(len(attendance_service.records_for_device("Acceso")), 1)
        sync = attendance_service.load_attendance()["device_sync"]["Acceso"]
        self.assertEqual(sync["last_received"], 1)
        self.assertEqual(sync["last_added"], 0)
        self.assertEqual(sync["last_total"], 1)
        self.assertEqual(sync["last_record_timestamp"], "2026-10-09T08:00:00")

    def test_legacy_attendance_cache_migrates_to_sync_metadata(self):
        legacy = {
            "schema_version": 1,
            "updated_at": "2026-10-09T08:00:00",
            "records": [{
                "device": "Acceso", "name": "Ana López",
                "timestamp": "2026-10-09T08:00:00",
            }],
        }
        normalized = attendance_service.normalize_attendance_data(legacy)

        self.assertEqual(normalized["schema_version"], attendance_service.SCHEMA_VERSION)
        self.assertEqual(normalized["device_sync"], {})
        attendance_service.save_attendance(legacy)
        with open(self.attendance_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["schema_version"], attendance_service.SCHEMA_VERSION)
        self.assertEqual(saved["device_sync"], {})

    def test_sync_status_records_empty_success_and_error(self):
        attendance_service.record_sync_success("Acceso", received=0)
        attendance_service.record_sync_error("Acceso", "Equipo sin respuesta")
        sync = attendance_service.load_attendance()["device_sync"]["Acceso"]

        self.assertTrue(sync["last_successful_sync"])
        self.assertEqual(sync["last_received"], 0)
        self.assertEqual(sync["last_error"], "Equipo sin respuesta")
        self.assertTrue(sync["last_error_at"])

    def test_device_health_status_uses_errors_and_sync_history_not_age(self):
        self.assertEqual(classify_sync_status({}), STATUS_NEVER_SYNCED)
        self.assertEqual(
            classify_sync_status({"last_successful_sync": "2020-01-01T00:00:00"}),
            STATUS_HEALTHY,
        )
        self.assertEqual(
            classify_sync_status({
                "last_successful_sync": "2026-10-09T08:00:00",
                "last_error": "Equipo sin respuesta",
            }),
            STATUS_ERROR,
        )
        self.assertEqual(
            classify_sync_status({"last_successful_sync": "fecha inválida"}),
            STATUS_WARNING,
        )

    def test_device_health_rows_include_all_configured_devices_case_insensitively(self):
        rows = build_device_health_rows(
            {"checadores": [
                {"nombre": "Acceso", "marca": "hikvision", "ip": "10.0.0.10", "port": 80},
                {"nombre": "Patio", "marca": "zkteco", "ip": "10.0.0.11", "port": 4370},
            ]},
            {"device_sync": {
                "acceso": {
                    "device": "acceso", "last_successful_sync": "2026-10-09T08:00:00",
                    "last_record_timestamp": "2026-10-09T07:55:00", "last_received": 4,
                    "last_added": 2, "last_total": 10,
                },
                "Eliminado": {"last_error": "No debe mostrarse"},
            }},
        )

        self.assertEqual([row["device"] for row in rows], ["Acceso", "Patio"])
        self.assertEqual(rows[0]["status"], STATUS_HEALTHY)
        self.assertEqual(rows[0]["last_added"], 2)
        self.assertEqual(rows[1]["status"], STATUS_NEVER_SYNCED)

    def test_backup_contains_operational_json_and_users_and_restores_it(self):
        self._seed()
        auth_service.change_password("admin", "ClaveRespaldo7")
        archive = os.path.join(self.directory, "respaldo.zip")
        created = backup_service.create_backup(archive)

        with zipfile.ZipFile(archive) as backup:
            self.assertEqual(
                set(backup.namelist()),
                {"manifest.json", "config_global.json", "processing_history.json", "attendance_cache.json", "usuarios.json"},
            )
            manifest = json.loads(backup.read("manifest.json"))
            self.assertFalse(manifest["external_excel_files_included"])
            self.assertEqual(manifest["stats"]["attendance_records"], 1)
            cached_attendance = json.loads(backup.read("attendance_cache.json"))
            self.assertIn("Acceso", cached_attendance["device_sync"])
            users = json.loads(backup.read("usuarios.json"))
            self.assertNotIn("ClaveRespaldo7", json.dumps(users, ensure_ascii=False))
        self.assertEqual(created["stats"]["people"], 1)

        config_manager.guardar_configuracion({"personas_sistema": ["Otra persona"]})
        history_service.save_history([])
        attendance_service.save_attendance(attendance_service.empty_attendance_data())
        auth_service.change_password("admin", "OtraClave8")
        restored = backup_service.restore_backup(
            archive, preventive_directory=os.path.join(self.directory, "preventivos"),
        )

        self.assertTrue(os.path.exists(restored["preventive_backup"]))
        self.assertIn("Ana López", config_manager.cargar_configuracion()["personas_sistema"])
        self.assertEqual(len(history_service.load_history()), 1)
        self.assertEqual(len(attendance_service.records_for_device("Acceso")), 1)
        self.assertIn("Acceso", attendance_service.load_attendance()["device_sync"])
        self.assertEqual(auth_service.authenticate("admin", "ClaveRespaldo7")["username"], "admin")

    def test_corrupted_backup_is_rejected(self):
        self._seed()
        archive = os.path.join(self.directory, "respaldo.zip")
        backup_service.create_backup(archive)
        corrupted = os.path.join(self.directory, "corrupto.zip")
        with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(corrupted, "w") as target:
            for name in source.namelist():
                content = b"{}" if name == "config_global.json" else source.read(name)
                target.writestr(name, content)

        with self.assertRaises(backup_service.BackupError):
            backup_service.validate_backup(corrupted)

    def test_backup_is_verified_after_creation_and_removed_when_verification_fails(self):
        self._seed()
        archive = os.path.join(self.directory, "respaldo_no_verificable.zip")
        with patch.object(backup_service, "validate_backup", side_effect=backup_service.BackupError("fallo simulado")):
            with self.assertRaisesRegex(backup_service.BackupError, "verificación de integridad"):
                backup_service.create_backup(archive)
        self.assertFalse(os.path.exists(archive))

    def test_restore_does_not_start_if_preventive_backup_cannot_be_verified(self):
        self._seed("Datos originales")
        archive = os.path.join(self.directory, "respaldo.zip")
        backup_service.create_backup(archive)
        config_manager.guardar_configuracion({"personas_sistema": ["Estado actual"]})
        with open(self.config_path, "rb") as handle:
            before = handle.read()

        with patch.object(backup_service, "create_backup", side_effect=backup_service.BackupError("fallo preventivo")):
            with self.assertRaisesRegex(backup_service.BackupError, "restauración no se inició"):
                backup_service.restore_backup(archive, preventive_directory=os.path.join(self.directory, "preventivos"))

        with open(self.config_path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_restore_rolls_back_when_atomic_write_fails(self):
        self._seed("Persona respaldo")
        archive = os.path.join(self.directory, "respaldo.zip")
        backup_service.create_backup(archive)
        config_manager.guardar_configuracion({"personas_sistema": ["Estado actual"]})
        with open(self.config_path, "rb") as handle:
            before = handle.read()
        real_atomic_write = backup_service._atomic_write
        failed = {"value": False}

        def fail_once(path, content):
            if path == self.history_path and not failed["value"]:
                failed["value"] = True
                raise OSError("fallo simulado")
            return real_atomic_write(path, content)

        with patch.object(backup_service, "_atomic_write", side_effect=fail_once):
            with self.assertRaises(backup_service.BackupError):
                backup_service.restore_backup(
                    archive, preventive_directory=os.path.join(self.directory, "preventivos"),
                )

        with open(self.config_path, "rb") as handle:
            self.assertEqual(handle.read(), before)


if __name__ == "__main__":
    unittest.main()