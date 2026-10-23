import os
import sys
import tkinter as tk
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests import main_v4_loader
from ui.device_health_window import VentanaEstadoChecadores
from ui.stable_date_entry import StableDateEntry


def button_texts(widget):
    texts = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Button):
            texts.append(child.cget("text"))
        texts.extend(button_texts(child))
    return texts


class ConfigurationMenuTests(unittest.TestCase):
    def test_preventive_backups_folder_is_not_exposed_in_configuration_menu(self):
        source_path = os.path.join(PROJECT_ROOT, "main v4.py")
        with open(source_path, "r", encoding="utf-8") as source_file:
            source = source_file.read()

        self.assertNotIn('label="Abrir respaldos preventivos"', source)
        self.assertNotIn("open_preventive_backups_folder", source)


class StableDateEntryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        self.root.geometry("420x240+20+20")
        self.entry = StableDateEntry(
            self.root, locale="es_MX", date_pattern="dd/MM/yyyy",
        )
        self.entry.pack(padx=20, pady=20)
        self.root.update()

    def tearDown(self):
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.destroy()

    def test_month_navigation_keeps_popup_open_and_selection_updates_date(self):
        self.entry.set_date(date(2026, 10, 9))
        self.entry.drop_down()
        self.root.update()
        self.assertTrue(self.entry._calendar.winfo_ismapped())

        initial_month = self.entry._calendar.get_displayed_month()
        self.entry._calendar._next_month()
        self.entry._pointer_is_inside_calendar = lambda: True
        self.entry._on_focus_out_cal()
        self.root.update()

        self.assertNotEqual(self.entry._calendar.get_displayed_month(), initial_month)
        self.assertTrue(self.entry._calendar.winfo_ismapped())

        chosen = date(2026, 11, 15)
        self.entry._calendar.selection_set(chosen)
        self.entry._select()
        self.root.update()
        self.assertEqual(self.entry.get_date(), chosen)
        self.assertFalse(self.entry._calendar.winfo_ismapped())


class DeviceHealthWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        self.root.withdraw()
        self.config = {
            "checadores": [
                {"nombre": "Acceso", "marca": "hikvision", "ip": "127.0.0.1", "port": 80},
                {"nombre": "Patio", "marca": "zkteco", "ip": "127.0.0.2", "port": 4370},
            ],
        }
        self.attendance = {
            "device_sync": {
                "Acceso": {
                    "last_successful_sync": "2026-10-12T10:00:00",
                    "last_received": 3,
                    "last_added": 2,
                    "last_total": 9,
                },
                "Patio": {"last_error": "Sin respuesta", "last_error_at": "2026-10-12T10:01:00"},
            },
        }
        self.patchers = [
            patch("ui.device_health_window.config_manager.cargar_configuracion", return_value=self.config),
            patch("ui.device_health_window.config_manager.invalidar_cache_config"),
            patch("ui.device_health_window.attendance_service.load_attendance", return_value=self.attendance),
            patch("ui.device_health_window.attendance_service.invalidate_cache"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.window = VentanaEstadoChecadores(self.root)
        self.window.withdraw()

    def tearDown(self):
        if hasattr(self, "window") and self.window.winfo_exists():
            self.window.destroy()
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.destroy()
        for patcher in reversed(getattr(self, "patchers", [])):
            patcher.stop()

    def test_panel_displays_local_health_without_network_operations(self):
        self.assertEqual(len(self.window.tree.get_children()), 2)
        self.assertEqual(self.window.tree.item("Acceso", "values")[0], "Correcto")
        self.assertEqual(self.window.tree.item("Patio", "values")[0], "Error")
        self.assertIn("Errores: 1", self.window.summary_label.cget("text"))


class DeviceConfigurationWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        self.root.withdraw()
        self.saved = []
        self.config = {
            "checadores": [{
                "nombre": "Equipo anterior", "marca": "zkteco", "ip": "127.0.0.1",
                "port": 4370, "modelo": "F22", "empleados": [],
                "empleados_descargados": [], "horarios": {},
            }],
            "horarios": {},
        }
        self.original_load = main_v4_loader._globals["cargar_configuracion"]
        self.original_save = main_v4_loader._globals["guardar_configuracion"]
        self.original_refresh = main_v4_loader._globals["actualizar_combos_globales"]
        main_v4_loader._globals["cargar_configuracion"] = lambda: self.config
        main_v4_loader._globals["guardar_configuracion"] = lambda config: self.saved.append(config)
        main_v4_loader._globals["actualizar_combos_globales"] = lambda: None
        self.window = main_v4_loader.VentanaConfiguracion(self.root)
        self.window.withdraw()

    def tearDown(self):
        main_v4_loader._globals["cargar_configuracion"] = self.original_load
        main_v4_loader._globals["guardar_configuracion"] = self.original_save
        main_v4_loader._globals["actualizar_combos_globales"] = self.original_refresh
        if hasattr(self, "window") and self.window.winfo_exists():
            self.window.destroy()
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.destroy()

    def test_model_field_is_absent_and_new_devices_do_not_save_it(self):
        self.assertFalse(hasattr(self.window, "var_modelo"))
        self.assertFalse(hasattr(self.window, "ent_modelo"))
        texts = []
        pending = [self.window]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            try:
                texts.append(str(widget.cget("text")))
            except tk.TclError:
                pass
        self.assertFalse(any("Modelo" in text for text in texts))

        self.window.nuevo_registro()
        self.window.var_nombre.set("Nuevo equipo")
        self.window.var_ip.set("192.0.2.50")
        self.window.var_puerto.set("4370")
        with patch.object(main_v4_loader._globals["messagebox"], "showinfo"):
            self.window.toggle_edicion()

        created = self.config["checadores"][-1]
        self.assertEqual(created["nombre"], "Nuevo equipo")
        self.assertNotIn("modelo", created)

    def test_editing_legacy_device_preserves_existing_model_silently(self):
        self.window.listbox.selection_set(0)
        self.window.cargar_datos(None)
        self.window.toggle_edicion()
        self.window.var_ip.set("127.0.0.2")
        with patch.object(main_v4_loader._globals["messagebox"], "showinfo"):
            self.window.toggle_edicion()

        updated = self.config["checadores"][0]
        self.assertEqual(updated["modelo"], "F22")
        self.assertEqual(updated["ip"], "127.0.0.2")


class HistoricalCalendarTests(unittest.TestCase):
    def test_historical_range_uses_stable_entries_and_standard_colors(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        original_load = main_v4_loader._globals["cargar_configuracion"]
        window = None
        try:
            main_v4_loader._globals["cargar_configuracion"] = lambda: {
                "checadores": [{"nombre": "Acceso", "marca": "hikvision"}],
                "horarios": {},
            }
            window = main_v4_loader.VentanaHistorico(root)
            window.withdraw()

            self.assertIsInstance(window.cal_desde, StableDateEntry)
            self.assertIsInstance(window.cal_hasta, StableDateEntry)
            expected_card = main_v4_loader._globals["BG_CARD"]
            expected_success = main_v4_loader._globals["SUCCESS"]
            self.assertEqual(window.cal_desde.cget("normalbackground"), expected_card)
            self.assertEqual(window.cal_hasta.cget("selectbackground"), expected_success)
            self.assertEqual(str(window.progreso_historico.cget("maximum")), "100")
            self.assertEqual(window.btn_cancelar_descarga.cget("state"), "disabled")
            self.assertIs(window.btn_extraer.master, window.btn_cancelar_descarga.master)
            self.assertEqual(window.btn_extraer.master.pack_info()["side"], "right")
            self.assertNotIn("Usar guardado", button_texts(window))
            self.assertFalse(hasattr(window, "cargar_historico_guardado"))

            window.geometry("940x580+20+20")
            root.deiconify()
            window.deiconify()
            root.update()
            self.assertTrue(window.btn_cancelar_descarga.winfo_viewable())
            self.assertGreater(window.btn_cancelar_descarga.winfo_width(), 60)
            self.assertGreaterEqual(
                window.btn_cancelar_descarga.winfo_x(),
                window.btn_extraer.winfo_x() + window.btn_extraer.winfo_width(),
            )

            window.descargando_historico = True
            window.btn_cancelar_descarga.config(state="normal")
            window.cancelar_extraccion_historica()
            self.assertTrue(window.cancelar_descarga_historico.is_set())
            self.assertEqual(window.btn_cancelar_descarga.cget("state"), "disabled")
            self.assertIn("Cancelando descarga", window.lbl_historico_estado.cget("text"))
            root.after(25, root.quit)
            root.mainloop()
        finally:
            main_v4_loader._globals["cargar_configuracion"] = original_load
            if window is not None and window.winfo_exists():
                window.destroy()
            root.destroy()


class ScheduleWindowUxTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        self.root.withdraw()
        self.config = {
            "checadores": [{
                "nombre": "Acceso", "marca": "hikvision", "ip": "127.0.0.1", "port": 80,
                "empleados": ["Ana López", "Bruno Díaz"],
                "horarios": {
                    "entrada": "08:00", "salida": "17:00", "tolerancia_min": 10,
                    "dias_laborales": [0, 1, 2, 3, 4], "dias_horarios": {},
                    "especiales": [{
                        "nombre": "Temprano", "entrada": "07:00", "salida": "16:00",
                        "tolerancia_min": 5, "dias_laborales": [0, 1, 2, 3, 4],
                        "dias_horarios": {}, "empleados": ["Ana López"],
                    }],
                },
            }],
            "horarios": {
                "entrada": "08:00", "salida": "17:00", "tolerancia_min": 10,
                "dias_laborales": [0, 1, 2, 3, 4], "dias_horarios": {}, "especiales": [],
            },
        }
        self.original_load = main_v4_loader._globals["cargar_configuracion"]
        self.original_save = main_v4_loader._globals["guardar_configuracion"]
        main_v4_loader._globals["cargar_configuracion"] = lambda: self.config
        main_v4_loader._globals["guardar_configuracion"] = lambda _config: None
        self.window = main_v4_loader.VentanaHorarios(self.root)
        self.window.withdraw()
        self.window.var_checador.set("Acceso")
        self.window.cargar_checador()

    def tearDown(self):
        main_v4_loader._globals["cargar_configuracion"] = self.original_load
        main_v4_loader._globals["guardar_configuracion"] = self.original_save
        if hasattr(self, "window") and self.window.winfo_exists():
            self.window.destroy()
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.destroy()

    def test_summary_filter_individual_assignment_and_conflict_warning_are_visible(self):
        self.assertIn("lunes a viernes", self.window.lbl_resumen_horario.cget("text"))
        self.assertIn("1 especiales", self.window.lbl_resumen_horario.cget("text"))
        self.assertEqual(self.window.lbl_cambios_horario.cget("text"), "Sin cambios pendientes")
        self.assertNotIn("Agregar filtrados", button_texts(self.window))
        self.assertFalse(hasattr(self.window, "agregar_empleados_filtrados"))

        self.window.var_esp_nombre.set("Nueva excepción")
        self.window.var_filtro_empleado.set("ana")
        self.window.actualizar_empleados_combo()
        self.assertEqual(tuple(self.window.cb_empleado.cget("values")), ("Ana López",))
        self.window.var_empleado.set("Ana López")
        self.window.agregar_empleado_especial()

        self.assertEqual(self.window.empleados_especial, ["Ana López"])
        self.assertEqual(self.window.lbl_conteo_empleados_especial.cget("text"), "1 persona asignada")
        self.assertIn("otro horario especial", self.window.lbl_validacion_especial.cget("text"))
        self.assertEqual(self.window.lbl_cambios_horario.cget("text"), "Cambios pendientes")


class PeopleAssignmentUxTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        self.root.withdraw()
        self.saved = []
        self.config = {
            "checadores": [
                {
                    "nombre": "Destino", "marca": "hikvision", "ip": "127.0.0.1", "port": 80,
                    "empleados": ["Ana López"], "empleados_descargados": ["Ana López", "Carla Ruiz"],
                    "horarios": {
                        "entrada": "08:00", "salida": "17:00", "tolerancia_min": 10,
                        "dias_laborales": [0, 1, 2, 3, 4], "dias_horarios": {},
                        "especiales": [{
                            "nombre": "Destino especial", "entrada": "09:00", "salida": "18:00",
                            "tolerancia_min": 5, "dias_laborales": [0, 1, 2, 3, 4],
                            "dias_horarios": {}, "empleados": ["Ana López"],
                        }],
                    },
                },
                {
                    "nombre": "Origen", "marca": "hikvision", "ip": "127.0.0.2", "port": 80,
                    "empleados": ["Bruno Díaz"], "empleados_descargados": ["Bruno Díaz"],
                    "horarios": {
                        "entrada": "08:00", "salida": "17:00", "tolerancia_min": 10,
                        "dias_laborales": [0, 1, 2, 3, 4], "dias_horarios": {},
                        "especiales": [{
                            "nombre": "Especial", "entrada": "07:00", "salida": "16:00",
                            "tolerancia_min": 5, "dias_laborales": [0, 1, 2, 3, 4],
                            "dias_horarios": {}, "empleados": ["Bruno Díaz"],
                        }],
                    },
                },
            ],
            "personas_sistema": ["Ana López", "Bruno Díaz", "Carla Ruiz"],
            "personas_eliminadas": [],
            "horarios": {},
        }
        self.original_load = main_v4_loader._globals["cargar_configuracion"]
        self.original_save = main_v4_loader._globals["guardar_configuracion"]
        main_v4_loader._globals["cargar_configuracion"] = lambda: self.config
        main_v4_loader._globals["guardar_configuracion"] = lambda config: self.saved.append(config)
        self.window = main_v4_loader.VentanaPersonasChecador(self.root)
        self.window.withdraw()
        self.window.var_checador.set("Destino")
        self.window.cargar_checador()

    def tearDown(self):
        main_v4_loader._globals["cargar_configuracion"] = self.original_load
        main_v4_loader._globals["guardar_configuracion"] = self.original_save
        if hasattr(self, "window") and self.window.winfo_exists():
            self.window.destroy()
        if hasattr(self, "root") and self.root.winfo_exists():
            self.root.destroy()

    def test_bulk_add_requires_approval_only_for_people_from_other_devices(self):
        self.assertIn("Bruno Díaz  —  Origen", self.window.lst_disponibles.get(0, tk.END))
        indices = {
            person: index for index, person in enumerate(self.window.disponibles_actuales)
        }
        self.window.lst_disponibles.selection_set(indices["Bruno Díaz"])
        self.window.lst_disponibles.selection_set(indices["Carla Ruiz"])

        with patch.object(self.window, "revisar_conflictos_movimiento", return_value=set()) as review:
            self.window.pasar_a_registrados()

        review.assert_called_once()
        self.assertNotIn("Bruno Díaz", self.window.empleados_registrados)
        self.assertIn("Carla Ruiz", self.window.empleados_registrados)
        self.assertTrue(self.window.cambios_pendientes)

    def test_approved_move_and_bulk_unassign_are_saved_with_summary(self):
        indices = {
            person: index for index, person in enumerate(self.window.disponibles_actuales)
        }
        self.window.lst_disponibles.selection_set(indices["Bruno Díaz"])
        self.window.lst_disponibles.selection_set(indices["Carla Ruiz"])
        with patch.object(self.window, "revisar_conflictos_movimiento", return_value={"Bruno Díaz"}):
            self.window.pasar_a_registrados()

        ana_index = self.window.registrados_actuales.index("Ana López")
        self.window.lst_registrados.selection_set(ana_index)
        self.window.quitar_de_registrados()

        with patch.object(main_v4_loader._globals["messagebox"], "askyesno", return_value=True), \
             patch.object(main_v4_loader._globals["messagebox"], "showinfo"):
            self.window.guardar_personas()

        destino = next(item for item in self.config["checadores"] if item["nombre"] == "Destino")
        origen = next(item for item in self.config["checadores"] if item["nombre"] == "Origen")
        self.assertEqual(destino["empleados"], ["Bruno Díaz", "Carla Ruiz"])
        self.assertNotIn("Bruno Díaz", origen["empleados"])
        self.assertNotIn("Ana López", destino["horarios"]["especiales"][0]["empleados"])
        self.assertNotIn("Bruno Díaz", origen["horarios"]["especiales"][0]["empleados"])
        self.assertFalse(self.window.cambios_pendientes)

    def test_bulk_delete_removes_assignments_and_special_schedules(self):
        self.window.eliminar_personas_sistema(["Bruno Díaz"])

        origen = next(item for item in self.config["checadores"] if item["nombre"] == "Origen")
        self.assertNotIn("Bruno Díaz", origen["empleados"])
        self.assertNotIn("Bruno Díaz", origen["horarios"]["especiales"][0]["empleados"])
        self.assertNotIn("Bruno Díaz", self.config["personas_sistema"])
        self.assertEqual(self.config["personas_eliminadas"][0]["nombre"], "Bruno Díaz")

    def test_recover_all_deleted_people_returns_them_to_general_bank(self):
        self.config["personas_eliminadas"] = [
            {"nombre": "Bruno Díaz", "fecha": "2026-10-12 10:00", "checador": "Origen"},
            {"nombre": "Carla Ruiz", "fecha": "2026-10-12 10:00", "checador": "Destino"},
        ]
        self.config["personas_sistema"] = ["Ana López"]
        self.window.abrir_eliminados()

        with patch.object(main_v4_loader._globals["messagebox"], "askyesno", return_value=True):
            self.window.recuperar_todas_personas()

        self.assertEqual(self.config["personas_eliminadas"], [])
        self.assertEqual(self.config["personas_sistema"], ["Ana López", "Bruno Díaz", "Carla Ruiz"])

    def test_permanent_delete_selected_cleans_all_local_references(self):
        self.config["personas_eliminadas"] = [
            {"nombre": "Bruno Díaz", "fecha": "2026-10-12 10:00", "checador": "Origen"},
            {"nombre": "Carla Ruiz", "fecha": "2026-10-12 10:00", "checador": "Destino"},
        ]
        self.config["personas_biometria"] = [
            {"persona_id": "1", "nombre": "Bruno Díaz", "checador": "Origen"},
            {"persona_id": "2", "nombre": "Carla Ruiz", "checador": "Destino"},
        ]
        origen = next(item for item in self.config["checadores"] if item["nombre"] == "Origen")
        origen["empleados_descargados"].append("Bruno Díaz")
        self.window.abrir_eliminados()
        self.window.lst_eliminados_popup.selection_set(0)

        with patch.object(main_v4_loader._globals["messagebox"], "askyesno", return_value=True), \
             patch.object(main_v4_loader._globals["messagebox"], "showinfo"):
            self.window.eliminar_definitivamente_seleccionadas()

        self.assertEqual([item["nombre"] for item in self.config["personas_eliminadas"]], ["Carla Ruiz"])
        self.assertNotIn("Bruno Díaz", origen["empleados"])
        self.assertNotIn("Bruno Díaz", origen["empleados_descargados"])
        self.assertNotIn("Bruno Díaz", origen["horarios"]["especiales"][0]["empleados"])
        self.assertEqual([item["nombre"] for item in self.config["personas_biometria"]], ["Carla Ruiz"])


class BiometricAdapterUxTests(unittest.TestCase):
    def test_adapter_applies_icon_adds_progress_and_reviews_photo(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        window = None
        original_load = main_v4_loader._globals["cargar_configuracion"]
        original_save = main_v4_loader._globals["guardar_configuracion"]
        original_icon = main_v4_loader._globals["aplicar_icono"]
        icon_windows = []

        class FakeCapture:
            def __init__(self):
                self.released = False

            def isOpened(self):
                return True

            def read(self):
                return True, object()

            def release(self):
                self.released = True

        capture = FakeCapture()
        fake_cv2 = SimpleNamespace(
            CAP_DSHOW=700, COLOR_BGR2RGB=4,
            VideoCapture=lambda *_args: capture,
            cvtColor=lambda frame, _mode: frame,
        )
        valid = {
            "valid": True, "level": "success", "status": "ENCUADRE ADECUADO",
            "message": "Rostro bien posicionado", "face": (230, 95, 180, 220),
        }
        try:
            from PIL import Image

            main_v4_loader._globals["cargar_configuracion"] = lambda: {
                "checadores": [{"nombre": "Acceso", "marca": "hikvision", "ip": "127.0.0.1", "port": 80}],
            }
            main_v4_loader._globals["guardar_configuracion"] = lambda _config: None
            main_v4_loader._globals["aplicar_icono"] = lambda target: icon_windows.append(target)
            window = main_v4_loader.VentanaAgregarUsuario(root)
            window.withdraw()
            window.device_var.set("Acceso")
            window._device_changed()
            window.face_method_var.set(True)
            window._biometric_method_changed()

            with patch.dict(sys.modules, {"cv2": fake_cv2}), \
                 patch("PIL.Image.fromarray", return_value=Image.new("RGB", (640, 480))), \
                 patch.object(main_v4_loader.VentanaAgregarUsuario, "_evaluate_camera_frame", return_value=valid):
                window._capture_camera()
                for _ in range(13):
                    window._camera_tick()
                window.update_idletasks()

                self.assertIn(window.camera_dialog, icon_windows)
                self.assertIsNotNone(window.camera_progress_canvas)
                self.assertEqual(window.camera_stability_metric.cget("text"), "Estabilidad: lista")
                window._accept_camera_frame()
                window.update_idletasks()
                self.assertIsNotNone(window.camera_review_dialog)
                self.assertTrue(window.camera_review_dialog.winfo_exists())
                self.assertIn(window.camera_review_dialog, icon_windows)

            window._close_camera()
            self.assertTrue(capture.released)
        finally:
            main_v4_loader._globals["cargar_configuracion"] = original_load
            main_v4_loader._globals["guardar_configuracion"] = original_save
            main_v4_loader._globals["aplicar_icono"] = original_icon
            if window is not None and window.winfo_exists():
                window._close_camera()
                window.destroy()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
