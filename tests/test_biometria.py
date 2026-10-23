import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_manager
from device_connectors import hikvision
from ui.biometria_window import (
    VentanaBiometria,
    _detalle_verificacion_manual_rostro,
    _dispositivo_seleccionado,
    _es_hikvision,
    _etiqueta_resultado,
    _validar_metodos_biometricos,
    evaluar_encuadre_facial,
)
from services.biometria_service import (
    crear_y_verificar_zkteco,
    listar_personas_zkteco,
    preparar_jpeg,
    upsert_estado_biometrico,
    validar_person_id,
)


class FakeZKConnection:
    def __init__(self, users=None, publish_created=True):
        self.users = list(users or [])
        self.publish_created = publish_created
        self.last_set_user = None

    def get_users(self):
        return list(self.users)

    def set_user(self, **kwargs):
        self.last_set_user = kwargs
        if self.publish_created:
            self.users.append(SimpleNamespace(
                uid=kwargs["uid"], user_id=kwargs["user_id"], name=kwargs["name"]
            ))


class PersonIdAndZKTecoTests(unittest.TestCase):
    def test_person_id_accepts_stable_cross_brand_characters(self):
        self.assertEqual(validar_person_id("EMP-2026_01"), (True, ""))
        self.assertFalse(validar_person_id("EMP 01")[0])
        self.assertFalse(validar_person_id("")[0])

    def test_zkteco_creation_uses_user_id_and_verifies_afterwards(self):
        conn = FakeZKConnection([SimpleNamespace(uid=1, user_id="OLD", name="Anterior")])

        status, detail, record = crear_y_verificar_zkteco(conn, "EMP-2", "ana. lopez")

        self.assertEqual(status, "created")
        self.assertEqual(conn.last_set_user["user_id"], "EMP-2")
        self.assertEqual(conn.last_set_user["uid"], 2)
        self.assertEqual(record.user_id, "EMP-2")
        self.assertIn("verificada", detail.lower())

    def test_zkteco_rejects_id_owned_by_another_name(self):
        conn = FakeZKConnection([SimpleNamespace(uid=1, user_id="EMP-1", name="Persona Original")])

        status, detail, _ = crear_y_verificar_zkteco(conn, "EMP-1", "Otra Persona")

        self.assertEqual(status, "error")
        self.assertIn("ya pertenece", detail)
        self.assertIsNone(conn.last_set_user)

    def test_zkteco_does_not_report_success_without_post_verification(self):
        conn = FakeZKConnection(publish_created=False)

        status, detail, _ = crear_y_verificar_zkteco(conn, "EMP-3", "Persona Prueba")

        self.assertEqual(status, "error")
        self.assertIn("no apareció", detail)

    def test_zkteco_listing_preserves_employee_numbers_for_reuse(self):
        conn = FakeZKConnection([
            SimpleNamespace(uid=1, user_id="2", name="ana.lopez"),
            SimpleNamespace(uid=2, user_id="1", name="Admin"),
        ])

        self.assertEqual(listar_personas_zkteco(conn)[0]["person_id"], "2")
        self.assertEqual(listar_personas_zkteco(conn)[0]["nombre"], "Ana Lopez")

    def test_zkteco_accepts_terminal_name_truncation_after_verification(self):
        expected = "Francisco Rigoberto Casillas Flores"
        conn = FakeZKConnection([SimpleNamespace(uid=1, user_id="EMP-9", name=expected[:24])])

        status, _, _ = crear_y_verificar_zkteco(conn, "EMP-9", expected)

        self.assertEqual(status, "exists")


class BiometricMetadataTests(unittest.TestCase):
    def test_person_starts_pending_and_can_be_confirmed_by_face(self):
        config = {}
        pending = upsert_estado_biometrico(
            config, "10", "Ana Lopez", "Oficina", confirmado=False,
            huella="requiere_captura_en_dispositivo", rostro="pendiente",
            detalle="Alta verificada.",
        )
        self.assertEqual(pending["estado"], "pendiente_obligatoria")

        confirmed = upsert_estado_biometrico(
            config, "10", "Ana Lopez", "Oficina", confirmado="rostro",
            rostro="enviado_y_confirmado", detalle="Rostro confirmado.",
        )
        self.assertEqual(confirmed["estado"], "confirmada")
        self.assertEqual(confirmed["tipo_confirmado"], "rostro")
        self.assertEqual(len(config["personas_biometria"]), 1)
        self.assertNotIn("bytes", json.dumps(config))

    def test_unselected_method_states_are_persisted_explicitly(self):
        config = {}
        record = upsert_estado_biometrico(
            config, "11", "Luis Perez", "Entrada", confirmado="rostro",
            huella="no_solicitada", rostro="enviado_y_confirmado",
        )
        self.assertEqual(record["huella"], "no_solicitada")
        self.assertEqual(record["rostro"], "enviado_y_confirmado")

    def test_unverified_face_upload_state_survives_configuration_normalization(self):
        config = {
            "personas_biometria": [{
                "persona_id": "12", "nombre": "Rosa Diaz", "checador": "Oficina Chica",
                "rostro": "enviado_sin_verificar", "huella": "no_solicitada",
            }],
        }
        config_manager.normalizar_biometria_configuracion(config)
        self.assertEqual(config["personas_biometria"][0]["rostro"], "enviado_sin_verificar")
    def test_manual_face_test_state_survives_configuration_normalization(self):
        config = {
            "personas_biometria": [{
                "persona_id": "13", "nombre": "Mario Diaz", "checador": "Oficina Chica",
                "rostro": "prueba_fisica_reportada", "huella": "no_solicitada",
            }],
        }
        config_manager.normalizar_biometria_configuracion(config)
        self.assertEqual(config["personas_biometria"][0]["rostro"], "prueba_fisica_reportada")

    def test_configuration_drops_legacy_ivms_fields(self):
        normalized = config_manager.normalizar_configuracion({
            "checadores": [], "grupos_acceso": [{"nombre": "X"}],
            "accesos_personas": [{"nombre": "Ana"}],
        })
        self.assertNotIn("grupos_acceso", normalized)
        self.assertNotIn("accesos_personas", normalized)
        self.assertIn("personas_biometria", normalized)


class BiometricWindowPresentationTests(unittest.TestCase):
    @staticmethod
    def _widget_texts(widget):
        texts = []
        try:
            text = widget.cget("text")
        except tk.TclError:
            text = ""
        if text:
            texts.append(str(text))
        for child in widget.winfo_children():
            texts.extend(BiometricWindowPresentationTests._widget_texts(child))
        return texts

    def test_face_framing_evaluation_covers_count_position_size_and_light(self):
        self.assertIn("Coloca", evaluar_encuadre_facial(640, 480, [], 120)["message"])
        self.assertIn("Solo", evaluar_encuadre_facial(640, 480, [(1, 1, 100, 100), (2, 2, 100, 100)], 120)["message"])
        self.assertIn("iluminación", evaluar_encuadre_facial(640, 480, [(230, 90, 180, 220)], 20)["message"])
        self.assertIn("Acércate", evaluar_encuadre_facial(640, 480, [(285, 180, 70, 80)], 120)["message"])
        self.assertIn("Centra", evaluar_encuadre_facial(640, 480, [(40, 90, 180, 220)], 120)["message"])
        self.assertTrue(evaluar_encuadre_facial(640, 480, [(230, 95, 180, 220)], 120)["valid"])

    def test_external_fingerprint_reader_is_presented_as_disabled_action(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        window = None
        try:
            config = {
                "checadores": [
                    {"nombre": "Caseta Norte", "marca": "zkteco", "ip": "127.0.0.1", "port": 4370},
                ],
            }
            window = VentanaBiometria(
                root,
                cargar_configuracion=lambda: config,
                guardar_configuracion=lambda _config: None,
                conectar_zk_robusto=lambda *_args, **_kwargs: (None, "prueba"),
            )
            window.withdraw()
            button = window.external_reader_button

            self.assertEqual(button.cget("text"), "Registrar con lector externo")
            self.assertEqual(str(button.cget("state")), "disabled")
            self.assertEqual(button.winfo_manager(), "pack")
            self.assertEqual(window.external_reader_card.winfo_manager(), "pack")
            self.assertFalse(hasattr(window, "external_fingerprint_method_check"))
            self.assertFalse(hasattr(window, "privacy_card"))

            window._biometric_method_changed()
            self.assertEqual(str(button.cget("state")), "disabled")
        finally:
            if window is not None and window.winfo_exists():
                window.destroy()
            root.destroy()

    def test_progressive_steps_use_separate_ordered_cards(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        window = None
        try:
            config = {
                "checadores": [
                    {"nombre": "Caseta Norte", "marca": "zkteco", "ip": "127.0.0.1", "port": 4370},
                ],
            }
            window = VentanaBiometria(
                root,
                cargar_configuracion=lambda: config,
                guardar_configuracion=lambda _config: None,
                conectar_zk_robusto=lambda *_args, **_kwargs: (None, "prueba"),
            )
            window.withdraw()
            window.person_id_var.set("EMP-1")
            window.name_var.set("Empleado Prueba")
            window.device_var.set("Caseta Norte")
            window._device_changed()
            window.update_idletasks()

            packed = window.left_panel.pack_slaves()
            self.assertLess(packed.index(window.employee_section), packed.index(window.device_section))
            self.assertLess(packed.index(window.device_section), packed.index(window.methods_section))
            self.assertLess(packed.index(window.methods_section), packed.index(window.action_row))
            self.assertEqual(int(window.device_section.cget("highlightthickness")), 1)
            self.assertEqual(int(window.methods_section.cget("highlightthickness")), 1)

            method_blocks = window.methods_section.pack_slaves()
            self.assertLess(
                method_blocks.index(window.fingerprint_method_card),
                method_blocks.index(window.external_reader_card),
            )
            ordered_sections = [
                window.employee_section,
                window.device_section,
                window.methods_section,
                window.action_row,
            ]
            for previous, current in zip(ordered_sections, ordered_sections[1:]):
                previous_bottom = previous.winfo_y() + previous.winfo_height()
                self.assertLessEqual(previous_bottom, current.winfo_y())
        finally:
            if window is not None and window.winfo_exists():
                window.destroy()
            root.destroy()

    def test_single_device_selection_returns_only_the_combobox_choice(self):
        devices = [
            {"nombre": "Caseta Norte", "marca": "zkteco"},
            {"nombre": "Almacen", "marca": "hikvision"},
        ]

        self.assertIs(_dispositivo_seleccionado(devices, "Almacen"), devices[1])
        self.assertIsNone(_dispositivo_seleccionado(devices, ""))
        self.assertIsNone(_dispositivo_seleccionado(devices, "No configurado"))

    def test_face_controls_apply_only_to_hikvision(self):
        self.assertTrue(_es_hikvision({"marca": "Hikvision", "modelo": "irrelevante"}))
        self.assertFalse(_es_hikvision({"marca": "ZKTeco"}))
        self.assertFalse(_es_hikvision({}))

    def test_result_label_distinguishes_created_existing_and_errors(self):
        self.assertEqual(_etiqueta_resultado("created"), "Registrado · huella pendiente")
        self.assertEqual(_etiqueta_resultado("exists"), "Ya vinculado · huella pendiente")
        self.assertEqual(
            _etiqueta_resultado("exists", "enviado_y_confirmado"),
            "Ya vinculado · plantilla facial verificada; prueba física requerida · huella pendiente",
        )
        self.assertEqual(
            _etiqueta_resultado("created", "error"),
            "Registrado · error de rostro · huella pendiente",
        )
        self.assertEqual(_etiqueta_resultado("error"), "Error")

    def test_result_label_can_finish_without_fingerprint(self):
        self.assertEqual(
            _etiqueta_resultado("created", "enviado_y_confirmado", False),
            "Registrado · plantilla facial verificada; prueba física requerida",
        )
        self.assertEqual(
            _etiqueta_resultado("exists", "no_solicitado", False),
            "Ya vinculado",
        )

    def test_result_label_distinguishes_unverified_face_and_access_permission(self):
        label = _etiqueta_resultado(
            "created", "enviado_sin_verificar", False,
            "facial_confirmado", "sin_permiso",
        )
        self.assertEqual(
            label,
            "Registrado · rostro enviado; pendiente de verificación técnica y prueba física · sin permiso de acceso",
        )
        self.assertIn("sin permiso de acceso", label)

    def test_unverified_face_instruction_is_operational_and_does_not_expose_api_detail(self):
        detail = _detalle_verificacion_manual_rostro("Oficina Chica")

        self.assertIn("Oficina Chica aceptó la foto", detail)
        self.assertIn("checada facial", detail)
        self.assertNotIn("ISAPI", detail)

    def test_biometric_method_selection_rejects_incompatible_choices(self):
        hikvision_device = [{"marca": "Hikvision"}]
        zkteco_device = [{"marca": "ZKTeco"}]
        self.assertIn(
            "al menos un método",
            _validar_metodos_biometricos(hikvision_device, False, False, False),
        )
        self.assertIn(
            "solo está disponible en checadores Hikvision",
            _validar_metodos_biometricos(zkteco_device, True, True, True),
        )
        self.assertIn(
            "toma una foto",
            _validar_metodos_biometricos(hikvision_device, True, False, False),
        )
        self.assertIn(
            "requiere completar la huella",
            _validar_metodos_biometricos(zkteco_device, False, False, False),
        )

    def test_biometric_method_selection_accepts_face_fingerprint_or_both(self):
        hikvision_device = [{"marca": "Hikvision"}]
        zkteco_device = [{"marca": "ZKTeco"}]
        self.assertEqual(
            _validar_metodos_biometricos(hikvision_device, True, False, True), "",
        )
        self.assertEqual(
            _validar_metodos_biometricos(hikvision_device, True, True, True), "",
        )
        self.assertEqual(
            _validar_metodos_biometricos(zkteco_device, False, True, False), "",
        )

    def test_result_card_uses_real_width_and_manual_face_confirmation_keeps_original_person(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        window = None
        saved = []
        config = {
            "checadores": [{
                "nombre": "Oficina Chica", "marca": "hikvision", "ip": "127.0.0.1", "port": 80,
            }],
        }
        try:
            window = VentanaBiometria(
                root,
                cargar_configuracion=lambda: config,
                guardar_configuracion=lambda value: saved.append(value),
                conectar_zk_robusto=lambda *_args, **_kwargs: (None, "prueba"),
            )
            window.withdraw()
            window._provision_finished(
                {"person_id": "00215", "nombre": "Ana Prueba"},
                (
                    "Oficina Chica",
                    "Registrado · rostro enviado; pendiente de verificación técnica y prueba física · acceso asignado",
                    True,
                    "Respuesta técnica extensa que no debe aparecer como instrucción principal.",
                    "created",
                ),
            )
            window._resize_result_labels(SimpleNamespace(width=310))

            self.assertEqual(window.result_state_label.pack_info()["fill"], "x")
            self.assertEqual(window.result_detail_label.pack_info()["fill"], "x")
            self.assertEqual(int(window.result_state_label.cget("wraplength")), 274)
            self.assertEqual(int(window.result_detail_label.cget("wraplength")), 274)
            self.assertIn("checada facial", window.result_detail_var.get())
            self.assertNotIn("Respuesta técnica", window.result_detail_var.get())
            self.assertEqual(str(window.face_verification_button.cget("state")), "normal")

            window.person_id_var.set("OTRA-PERSONA")
            window.name_var.set("Persona Distinta")
            with patch("ui.biometria_window.messagebox.askyesno", return_value=True):
                window._confirm_face_verification()

            record = config["personas_biometria"][0]
            self.assertEqual(record["persona_id"], "00215")
            self.assertEqual(record["nombre"], "Ana Prueba")
            self.assertEqual(record["rostro"], "prueba_fisica_reportada")
            self.assertEqual(record["estado"], "pendiente_obligatoria")
            self.assertEqual(record["tipo_confirmado"], "")
            self.assertIn("prueba física facial reportada", window.result_state_var.get())
            self.assertFalse(window.current_result["pending_face_verification"])
            self.assertGreaterEqual(len(saved), 1)
        finally:
            if window is not None and window.winfo_exists():
                window.destroy()
            root.destroy()

    def test_camera_dialog_guides_user_enables_capture_and_releases_device(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        window = None

        from PIL import Image

        class FakeCapture:
            def __init__(self):
                self.read_count = 0
                self.released = False

            def isOpened(self):
                return True

            def read(self):
                self.read_count += 1
                if self.read_count == 1:
                    return False, None
                return True, object()

            def release(self):
                self.released = True

        capture = FakeCapture()
        fake_cv2 = SimpleNamespace(
            CAP_DSHOW=700,
            COLOR_BGR2RGB=4,
            VideoCapture=lambda *_args: capture,
            cvtColor=lambda frame, _mode: frame,
        )
        try:
            config = {
                "checadores": [
                    {"nombre": "Acceso", "marca": "hikvision", "ip": "127.0.0.1", "port": 80},
                ],
            }
            window = VentanaBiometria(
                root,
                cargar_configuracion=lambda: config,
                guardar_configuracion=lambda _config: None,
                conectar_zk_robusto=lambda *_args, **_kwargs: (None, "prueba"),
            )
            window.withdraw()
            window.device_var.set("Acceso")
            window._device_changed()
            window.face_method_var.set(True)
            window._biometric_method_changed()

            valid = {
                "valid": True, "level": "success", "status": "ENCUADRE ADECUADO",
                "message": "✓ Rostro bien posicionado", "face": (230, 95, 180, 220),
            }
            with patch.dict(sys.modules, {"cv2": fake_cv2}), \
                 patch("PIL.Image.fromarray", return_value=Image.new("RGB", (640, 480))), \
                 patch.object(VentanaBiometria, "_evaluate_camera_frame", return_value=valid):
                window._capture_camera()
                window.update_idletasks()
                self.assertEqual(str(window.camera_capture_button.cget("state")), "disabled")
                self.assertEqual(window.camera_status_label.cget("text"), "INICIANDO CÁMARA…")

                texts = self._widget_texts(window.camera_dialog)
                self.assertIn("Toma la foto del rostro", texts)
                self.assertIn("Antes de capturar", texts)
                self.assertIn("Podrás revisar la foto antes de registrar al empleado.", texts)

                for _ in range(13):
                    window._camera_tick()
                window.update_idletasks()
                self.assertEqual(str(window.camera_capture_button.cget("state")), "normal")
                self.assertEqual(window.camera_status_label.cget("text"), "LISTO PARA CAPTURAR")
                self.assertIsNotNone(window.latest_camera_image)

            window._close_camera()
            self.assertTrue(capture.released)
            self.assertIsNone(window.camera_dialog)
            self.assertIsNone(window.camera_capture)
        finally:
            if window is not None and window.winfo_exists():
                window._close_camera()
                window.destroy()
            root.destroy()

    def test_camera_disables_capture_after_repeated_frame_loss(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tkinter no está disponible: {exc}")
        root.withdraw()
        window = None

        from PIL import Image

        class IntermittentCapture:
            def __init__(self):
                self.first = True

            def isOpened(self):
                return True

            def read(self):
                if self.first:
                    self.first = False
                    return True, object()
                return False, None

            def release(self):
                pass

        capture = IntermittentCapture()
        fake_cv2 = SimpleNamespace(
            CAP_DSHOW=700,
            COLOR_BGR2RGB=4,
            VideoCapture=lambda *_args: capture,
            cvtColor=lambda frame, _mode: frame,
        )
        try:
            config = {"checadores": [{"nombre": "Acceso", "marca": "hikvision", "ip": "127.0.0.1", "port": 80}]}
            window = VentanaBiometria(
                root,
                cargar_configuracion=lambda: config,
                guardar_configuracion=lambda _config: None,
                conectar_zk_robusto=lambda *_args, **_kwargs: (None, "prueba"),
            )
            window.withdraw()
            window.device_var.set("Acceso")
            window._device_changed()
            window.face_method_var.set(True)
            window._biometric_method_changed()

            with patch.dict(sys.modules, {"cv2": fake_cv2}), \
                 patch("PIL.Image.fromarray", return_value=Image.new("RGB", (640, 480))):
                window._capture_camera()
                self.assertEqual(str(window.camera_capture_button.cget("state")), "disabled")
                for _ in range(12):
                    window._camera_tick()
                self.assertEqual(str(window.camera_capture_button.cget("state")), "disabled")
                self.assertEqual(window.camera_status_label.cget("text"), "NO SE RECIBE IMAGEN")
                self.assertIsNone(window.latest_camera_image)
        finally:
            if window is not None and window.winfo_exists():
                window._close_camera()
                window.destroy()
            root.destroy()


class FaceImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PIL import Image
        except ImportError:
            raise unittest.SkipTest("Pillow no está instalado")
        cls.Image = Image

    def test_jpeg_is_optimized_in_memory(self):
        image = self.Image.new("RGB", (1280, 960), (130, 170, 190))
        result = preparar_jpeg(image=image)

        self.assertLessEqual(result["size_kb"], 200)
        self.assertGreaterEqual(result["width"], 640)
        self.assertTrue(result["bytes"].startswith(b"\xff\xd8"))

    def test_image_smaller_than_minimum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "640×480"):
            preparar_jpeg(image=self.Image.new("RGB", (320, 240)))

    def test_png_file_is_rejected_even_if_extension_is_irrelevant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "foto.jpg")
            self.Image.new("RGB", (640, 480)).save(path, format="PNG")
            with self.assertRaisesRegex(ValueError, "JPG"):
                preparar_jpeg(path=path)


class HikvisionBiometricTests(unittest.TestCase):
    def test_people_listing_keeps_employee_number(self):
        response = {"UserInfoSearch": {"totalMatches": 1, "UserInfo": [
            {"employeeNo": "EMP-7", "name": "Ana Lopez"}
        ]}}
        with patch.object(hikvision, "hikvision_request_json", return_value=response):
            people = hikvision.hikvision_listar_personas({"ip": "127.0.0.1"})
        self.assertEqual(people[0]["person_id"], "EMP-7")

    def test_existing_hikvision_id_with_different_name_is_rejected(self):
        with patch.object(hikvision, "hikvision_buscar_persona", return_value={"employeeNo": "EMP-7", "name": "Otra Persona"}):
            status, detail, _ = hikvision.hikvision_crear_y_verificar_persona(
                {"ip": "127.0.0.1"}, "Ana Lopez", {"person_id": "EMP-7"}
            )
        self.assertEqual(status, "error")
        self.assertIn("ya pertenece", detail)

    def test_hikvision_create_requires_post_verification(self):
        with patch.object(hikvision, "hikvision_buscar_persona", side_effect=[None, None]), \
             patch.object(hikvision, "hikvision_crear_persona", return_value=(True, "OK")):
            status, detail, _ = hikvision.hikvision_crear_y_verificar_persona(
                {"ip": "127.0.0.1"}, "Ana Lopez", {"person_id": "EMP-8"}
            )
        self.assertEqual(status, "error")
        self.assertIn("no devolvió", detail)

    def test_face_library_fdid_is_discovered_from_inventory(self):
        inventory = {"FDLibInfoList": [{"FDID": "42", "faceLibType": "blackFD", "libArmingType": "armingLib"}]}
        with patch.object(hikvision, "hikvision_request_json", return_value=inventory):
            fdid, record = hikvision.hikvision_descubrir_biblioteca_rostro({"ip": "127.0.0.1"})
        self.assertEqual(fdid, "42")
        self.assertEqual(record["FDID"], "42")

    def test_capabilities_are_not_enough_without_real_fdid(self):
        responses = [
            {"FaceDataRecordCap": {"enabled": True}},
            {"FDLibInfoList": []},
        ]
        with patch.object(hikvision, "hikvision_request_json", side_effect=responses):
            capabilities = hikvision.hikvision_capacidades_rostro({"ip": "127.0.0.1"})
        self.assertFalse(capabilities["supported"])

    def test_multipart_contains_json_metadata_and_image(self):
        boundary, body = hikvision._multipart_body(
            {"FaceDataRecord": '{"FDID":"42"}'}, "FaceImage", "face.jpg", b"JPEGDATA"
        )
        self.assertIn(boundary.encode(), body)
        self.assertIn(b'name="FaceDataRecord"', body)
        self.assertIn(b"Content-Type: application/json", body)
        self.assertIn(b'name="FaceImage"; filename="face.jpg"', body)
        self.assertIn(b"JPEGDATA", body)

    def test_face_upload_uses_library_type_and_requires_readback_confirmation(self):
        class Response:
            status = 200

            def read(self):
                return b'{"statusCode": 1, "statusString": "OK"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Opener:
            def __init__(self):
                self.request = None

            def open(self, request, timeout):
                self.request = request
                return Response()

        opener = Opener()
        capabilities = {
            "supported": True,
            "fdid": "42",
            "library": {"FDID": "42", "faceLibType": "whiteFD"},
        }
        with patch.object(hikvision, "hikvision_capacidades_rostro", return_value=capabilities), \
             patch.object(hikvision, "hikvision_base_urls", return_value=["http://127.0.0.1:80"]), \
             patch.object(hikvision, "_get_opener", return_value=opener), \
             patch.object(hikvision, "hikvision_verificar_rostro", return_value=(True, "Rostro verificado en la biblioteca 42.")):
            state, detail = hikvision.hikvision_enviar_rostro(
                {"ip": "127.0.0.1"}, "EMP-1", b"JPEGDATA", nombre="Ana Lopez",
            )
        self.assertEqual(state, "enviado_y_confirmado")
        self.assertIn("verificado", detail.lower())
        self.assertIn(b'"faceLibType": "whiteFD"', opener.request.data)

    def test_accepted_face_upload_without_readback_is_not_marked_confirmed(self):
        class Response:
            status = 200

            def read(self):
                return b'{"statusCode": 1, "statusString": "OK"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Opener:
            def open(self, _request, timeout):
                return Response()

        capabilities = {"supported": True, "fdid": "42", "library": {"FDID": "42"}}
        with patch.object(hikvision, "hikvision_capacidades_rostro", return_value=capabilities), \
             patch.object(hikvision, "hikvision_base_urls", return_value=["http://127.0.0.1:80"]), \
             patch.object(hikvision, "_get_opener", return_value=Opener()), \
             patch.object(hikvision, "hikvision_verificar_rostro", return_value=(False, "Pendiente de verificación.")):
            state, detail = hikvision.hikvision_enviar_rostro(
                {"ip": "127.0.0.1"}, "EMP-2", b"JPEGDATA",
            )
        self.assertEqual(state, "enviado_sin_verificar")
        self.assertIn("pendiente", detail.lower())

    def test_http_200_with_isapi_failure_is_not_success(self):
        self.assertFalse(hikvision._isapi_operation_succeeded('{"statusCode": 4, "statusString": "Invalid Operation"}'))
        self.assertTrue(hikvision._isapi_operation_succeeded('{"statusCode": 1, "statusString": "OK"}'))

    def test_access_summary_requires_both_facial_mode_and_permission(self):
        ready = hikvision.hikvision_resumen_acceso({
            "userVerifyMode": "face",
            "doorRight": "1",
            "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
        })
        self.assertEqual(ready["modo"], "facial_confirmado")
        self.assertEqual(ready["acceso"], "permiso_confirmado")
        self.assertTrue(ready["listo_desbloqueo_facial"])

        unconfirmed = hikvision.hikvision_resumen_acceso({"employeeNo": "EMP-1"})
        self.assertEqual(unconfirmed["modo"], "sin_confirmar")
        self.assertEqual(unconfirmed["acceso"], "sin_confirmar")
        self.assertFalse(unconfirmed["listo_desbloqueo_facial"])

    def test_access_profile_is_discovered_from_an_authorized_terminal_user(self):
        response = {"UserInfoSearch": {"totalMatches": 2, "UserInfo": [
            {"employeeNo": "NO-ACCESS", "doorRight": "", "RightPlan": []},
            {"employeeNo": "EMP-1", "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]},
        ]}}
        with patch.object(hikvision, "hikvision_request_json", return_value=response):
            profile, detail = hikvision.hikvision_descubrir_perfil_acceso({"ip": "127.0.0.1"})
        self.assertEqual(profile["doorRight"], "1")
        self.assertEqual(profile["RightPlan"][0]["planTemplateNo"], "1")
        self.assertIn("sin inventar", detail)
    def test_access_profile_is_not_inferred_when_authorized_users_have_multiple_profiles(self):
        response = {"UserInfoSearch": {"totalMatches": 2, "UserInfo": [
            {"employeeNo": "EMP-1", "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]},
            {"employeeNo": "EMP-2", "doorRight": "2", "RightPlan": [{"doorNo": 2, "planTemplateNo": "2"}]},
        ]}}
        with patch.object(hikvision, "hikvision_request_json", return_value=response):
            profile, detail = hikvision.hikvision_descubrir_perfil_acceso({"ip": "127.0.0.1"})
        self.assertEqual(profile, {})
        self.assertIn("varios perfiles", detail)
    def test_explicit_empty_door_right_is_reported_as_no_permission(self):
        summary = hikvision.hikvision_resumen_acceso({
            "userVerifyMode": "face", "doorRight": "", "RightPlan": [],
        })
        self.assertEqual(summary["acceso"], "sin_permiso")
        self.assertFalse(summary["listo_desbloqueo_facial"])

    def test_face_mode_setup_is_verified_after_update(self):
        verified = {
            "employeeNo": "EMP-1", "userVerifyMode": "face",
            "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
        }
        with patch.object(hikvision, "hikvision_request_json", return_value={"statusCode": 1, "statusString": "OK"}) as request, \
             patch.object(hikvision, "hikvision_buscar_persona", return_value=verified):
            ok, mode, record, detail = hikvision.hikvision_configurar_modo_rostro(
                {"ip": "127.0.0.1"}, "EMP-1",
            )
        self.assertTrue(ok)
        self.assertEqual(mode, "facial_confirmado")
        self.assertEqual(record, verified)
        self.assertIn("confirmado", detail.lower())
        self.assertEqual(request.call_args.kwargs["method"], "PUT")
        self.assertEqual(request.call_args.kwargs["payload"]["UserInfo"]["userVerifyMode"], "face")

    def test_face_mode_setup_discovers_and_applies_an_effective_access_profile(self):
        verified = {
            "employeeNo": "EMP-1", "userVerifyMode": "face",
            "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
        }
        with patch.object(hikvision, "hikvision_buscar_persona", side_effect=[
            {"employeeNo": "EMP-1", "doorRight": "", "RightPlan": []}, verified,
        ]), patch.object(hikvision, "hikvision_descubrir_perfil_acceso", return_value=(
            {"doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]}, "Perfil encontrado.",
        )), patch.object(hikvision, "hikvision_request_json", return_value={"statusCode": 1, "statusString": "OK"}) as request:
            ok, _mode, _record, detail = hikvision.hikvision_configurar_modo_rostro(
                {"ip": "127.0.0.1"}, "EMP-1",
            )
        self.assertTrue(ok)
        payload = request.call_args.kwargs["payload"]["UserInfo"]
        self.assertEqual(payload["doorRight"], "1")
        self.assertEqual(payload["RightPlan"][0]["planTemplateNo"], "1")
        self.assertIn("permiso de acceso confirmados", detail)

if __name__ == "__main__":
    unittest.main()