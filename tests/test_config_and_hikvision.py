import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

# Allow running this test file directly from VS Code as well as through
# ``python -m unittest discover -s tests``.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_manager
from device_connectors import hikvision
from tests import main_v4_loader


class ConfigurationPersistenceTests(unittest.TestCase):
    def test_purge_uses_exact_sixty_day_boundary_and_keeps_invalid_dates(self):
        ahora = datetime(2026, 10, 12, 12, 0)
        config = {
            "personas_sistema": ["Cumplida", "Pendiente", "Fecha Inválida"],
            "personas_eliminadas": [
                {"nombre": "Cumplida", "fecha": "2026-08-13 12:00", "checador": "Prueba"},
                {"nombre": "Pendiente", "fecha": "2026-08-13 12:01", "checador": "Prueba"},
                {"nombre": "Fecha Inválida", "fecha": "sin fecha", "checador": "Prueba"},
            ],
            "personas_biometria": [],
            "checadores": [],
        }

        eliminadas = config_manager.purgar_personas_eliminadas_vencidas(config, ahora=ahora)

        self.assertEqual(eliminadas, ["Cumplida"])
        self.assertEqual(
            [item["nombre"] for item in config["personas_eliminadas"]],
            ["Pendiente", "Fecha Inválida"],
        )

    def test_save_and_reload_normalized_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config_global.json")
            with patch.object(config_manager, "APP_DIR", directory), \
                 patch.object(config_manager, "PATH_CONFIG", config_path), \
                 patch.object(config_manager, "PATH_CONFIG_ANTERIOR", ""), \
                 patch.object(config_manager, "PATH_CONFIG_LOCAL", ""):
                config_manager.invalidar_cache_config()
                config_manager.guardar_configuracion(
                    {"checadores": [{"nombre": "Prueba", "ip": "127.0.0.1", "port": "80", "marca": "hikvision"}]}
                )
                config_manager.invalidar_cache_config()
                cargada = config_manager.cargar_configuracion()

                self.assertTrue(os.path.exists(config_path))
                self.assertEqual(cargada["checadores"][0]["port"], 80)
                self.assertEqual(cargada["checadores"][0]["marca"], "hikvision")
                self.assertNotIn("grupos_acceso", cargada)
                self.assertNotIn("accesos_personas", cargada)
                self.assertEqual(cargada["personas_biometria"], [])

    def test_load_permanently_purges_people_deleted_for_sixty_days(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config_global.json")
            ahora = datetime.now().replace(microsecond=0)
            antigua = "Persona Antigua"
            reciente = "Persona Reciente"
            raw = {
                "checadores": [{
                    "nombre": "Prueba", "ip": "127.0.0.1", "port": 80, "marca": "hikvision",
                    "empleados": [antigua], "empleados_descargados": [antigua],
                    "horarios": {"especiales": [{"nombre": "Especial", "empleados": [antigua]}]},
                }],
                "personas_sistema": [antigua],
                "personas_eliminadas": [
                    {"nombre": antigua, "fecha": (ahora - timedelta(days=61)).strftime("%Y-%m-%d %H:%M"), "checador": "Prueba"},
                    {"nombre": reciente, "fecha": (ahora - timedelta(days=59)).strftime("%Y-%m-%d %H:%M"), "checador": "Prueba"},
                ],
                "personas_biometria": [
                    {"persona_id": "1", "nombre": antigua, "checador": "Prueba"},
                    {"persona_id": "2", "nombre": reciente, "checador": "Prueba"},
                ],
            }
            with open(config_path, "w", encoding="utf-8") as archivo:
                json.dump(raw, archivo)

            with patch.object(config_manager, "APP_DIR", directory), \
                 patch.object(config_manager, "PATH_CONFIG", config_path), \
                 patch.object(config_manager, "PATH_CONFIG_ANTERIOR", ""), \
                 patch.object(config_manager, "PATH_CONFIG_LOCAL", ""):
                config_manager.invalidar_cache_config()
                cargada = config_manager.cargar_configuracion()

                self.assertNotIn(antigua, cargada["personas_sistema"])
                self.assertNotIn(antigua, cargada["checadores"][0]["empleados"])
                self.assertNotIn(antigua, cargada["checadores"][0]["empleados_descargados"])
                self.assertNotIn(antigua, cargada["checadores"][0]["horarios"]["especiales"][0]["empleados"])
                self.assertEqual([item["nombre"] for item in cargada["personas_eliminadas"]], [reciente])
                self.assertEqual([item["nombre"] for item in cargada["personas_biometria"]], [reciente])

                with open(config_path, "r", encoding="utf-8") as archivo:
                    persistida = json.load(archivo)
                self.assertEqual([item["nombre"] for item in persistida["personas_eliminadas"]], [reciente])


class HikvisionHelpersTests(unittest.TestCase):
    def tearDown(self):
        hikvision.limpiar_cache_conexiones()

    def test_month_ranges_cover_requested_period_only(self):
        rangos = list(hikvision._rangos_mensuales(date(2026, 1, 29), date(2026, 3, 2)))

        self.assertEqual(
            rangos,
            [
                (date(2026, 1, 29), date(2026, 1, 31)),
                (date(2026, 2, 1), date(2026, 2, 28)),
                (date(2026, 3, 1), date(2026, 3, 2)),
            ],
        )

    def test_hikvision_brand_aliases_use_the_same_runtime_flow(self):
        marcar = main_v4_loader._globals["marca_checador"]

        for marca in ("hikvision", "hik", "hik-connect"):
            with self.subTest(marca=marca):
                self.assertEqual(marcar({"marca": marca}), "hikvision")

    def test_auth_opener_cache_is_bounded(self):
        hikvision.limpiar_cache_conexiones()
        for index in range(hikvision._OPENER_CACHE_MAXSIZE + 3):
            hikvision._get_opener(f"http://127.0.0.{index + 1}:80", "test-user", f"test-{index}")

        self.assertLessEqual(len(hikvision._opener_cache), hikvision._OPENER_CACHE_MAXSIZE)

    def test_event_download_paginates_terminal_pages_and_merges_nominal_subtypes(self):
        """A DS-K1T may cap pages at 30 despite maxResults=100."""
        inicio = date(2026, 10, 12)
        fin = date(2026, 10, 12)
        llamadas = []

        eventos_75 = [
            {
                "name": f"Empleado {indice:02d}",
                "time": f"2026-10-12T{8 + indice // 60:02d}:{indice % 60:02d}:00",
            }
            for indice in range(62)
        ]
        respuesta_75 = (
            eventos_75[:35]
            + [{"time": "2026-10-12T09:30:00"}]
            + eventos_75[35:]
        )

        def respuesta_simulada(_info, _ruta, method, payload, timeout):
            self.assertEqual(method, "POST")
            self.assertEqual(timeout, 18)
            condicion = payload["AcsEventCond"]
            llamadas.append((condicion["minor"], condicion["searchResultPosition"]))
            self.assertEqual(condicion["maxResults"], 100)
            minor = condicion["minor"]
            posicion = condicion["searchResultPosition"]
            if minor == 38:
                eventos = [{"name": "Ana", "time": "2026-10-12T07:55:00"}]
                return {"AcsEvent": {"InfoList": eventos, "totalMatches": 1}}
            if minor == 75:
                pagina = respuesta_75[posicion:posicion + 30]
                return {"AcsEvent": {"InfoList": pagina, "totalMatches": len(respuesta_75)}}
            self.assertEqual(minor, 0)
            return {"AcsEvent": {"InfoList": [], "totalMatches": 0}}

        with patch.object(hikvision, "hikvision_request_json", side_effect=respuesta_simulada):
            records = hikvision.hikvision_obtener_eventos({"ip": "127.0.0.1"}, inicio, fin)

        self.assertEqual(len(records), 63)
        self.assertEqual(records[0], {"nombre": "Ana", "fecha_hora": datetime(2026, 10, 12, 7, 55)})
        self.assertEqual(records[-1]["nombre"], "Empleado 61")
        self.assertEqual(llamadas, [(38, 0), (75, 0), (75, 30), (75, 60)])

    def test_embedded_runtime_delegates_to_central_hikvision_connector(self):
        esperado = [{"nombre": "Ana", "fecha_hora": datetime(2026, 10, 12, 7, 55)}]
        obtener_eventos = main_v4_loader._globals["hikvision_obtener_eventos"]

        with patch.object(main_v4_loader._globals["hikvision_connector"], "hikvision_obtener_eventos", return_value=esperado) as descargar:
            records = obtener_eventos(
                {"ip": "127.0.0.1"}, date(2026, 10, 12), date(2026, 10, 12),
                por_dia=False, cancelado=lambda: False,
            )

        self.assertEqual(records, esperado)
        descargar.assert_called_once_with(
            {"ip": "127.0.0.1"},
            date(2026, 10, 12),
            date(2026, 10, 12),
            progreso=None,
            cancelado=unittest.mock.ANY,
        )

    def test_progress_uses_clear_page_messages_without_isapi_codes(self):
        mensajes = []

        def respuesta_simulada(_info, _ruta, method, payload, timeout):
            minor = payload["AcsEventCond"]["minor"]
            eventos = [{"name": "Ana", "time": "2026-10-12T07:55:00"}] if minor == 38 else []
            return {"AcsEvent": {"InfoList": eventos, "totalMatches": len(eventos)}}

        with patch.object(hikvision, "hikvision_request_json", side_effect=respuesta_simulada):
            hikvision.hikvision_obtener_eventos(
                {"ip": "127.0.0.1"}, date(2026, 10, 12), date(2026, 10, 12),
                progreso=lambda texto, _valor: mensajes.append(texto),
            )

        self.assertTrue(any("página" in mensaje.lower() for mensaje in mensajes))
        self.assertTrue(all("major" not in mensaje.lower() and "minor" not in mensaje.lower() for mensaje in mensajes))

    def test_download_stops_before_requesting_the_next_page_when_cancelled(self):
        llamadas = []

        def respuesta_simulada(_info, _ruta, method, payload, timeout):
            llamadas.append(payload["AcsEventCond"]["searchResultPosition"])
            return {
                "AcsEvent": {
                    "InfoList": [{"name": "Ana", "time": "2026-10-12T07:55:00"}] * 30,
                    "totalMatches": 60,
                }
            }

        with patch.object(hikvision, "hikvision_request_json", side_effect=respuesta_simulada):
            with self.assertRaises(hikvision.HikvisionDescargaCancelada):
                hikvision.hikvision_obtener_eventos(
                    {"ip": "127.0.0.1"}, date(2026, 10, 12), date(2026, 10, 12),
                    cancelado=lambda: len(llamadas) >= 1,
                )

        self.assertEqual(llamadas, [0])
