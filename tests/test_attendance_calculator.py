import unittest
from datetime import date, time
import os
import sys
import tempfile

# Allow running this test file directly from VS Code as well as through
# ``python -m unittest discover -s tests``.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from attendance_calculator import agrupar_entrada_salida
from excel_exporter import exportar_reporte


class AttendanceGroupingTests(unittest.TestCase):
    def setUp(self):
        self.fecha = date(2026, 5, 4)
        self.registros = pd.DataFrame(
            [
                {"nombre": "Ana", "Fecha_D": self.fecha, "Hora_D": time(8, 5)},
                {"nombre": "Ana", "Fecha_D": self.fecha, "Hora_D": time(12, 0)},
                {"nombre": "Ana", "Fecha_D": self.fecha, "Hora_D": time(17, 10)},
                {"nombre": "Beto", "Fecha_D": self.fecha, "Hora_D": time(16, 0)},
            ]
        )

    def test_zkteco_uses_first_entry_and_last_exit(self):
        resultado = agrupar_entrada_salida(self.registros, usar_primera_ultima=False)
        ana = resultado.loc[resultado["nombre"] == "Ana"].iloc[0]
        beto = resultado.loc[resultado["nombre"] == "Beto"].iloc[0]

        self.assertEqual(ana["Entrada"], time(8, 5))
        self.assertEqual(ana["Salida"], time(17, 10))
        self.assertEqual(beto["Entrada"], time(16, 0))
        self.assertEqual(beto["Salida"], time(16, 0))

    def test_hikvision_uses_first_and_last_punch(self):
        resultado = agrupar_entrada_salida(self.registros, usar_primera_ultima=True)
        ana = resultado.loc[resultado["nombre"] == "Ana"].iloc[0]

        self.assertEqual(ana["Entrada"], time(8, 5))
        self.assertEqual(ana["Salida"], time(17, 10))

    def test_daily_excel_export_creates_workbook(self):
        datos = pd.DataFrame(
            [{"nombre": "Ana", "fecha_hora": pd.Timestamp("2026-05-04 08:05:00")}]
        )
        with tempfile.TemporaryDirectory() as directory:
            salida = exportar_reporte(
                datos,
                self.fecha,
                self.fecha,
                {"marca": "hikvision", "empleados": ["Ana"]},
                "Prueba",
                "Diario",
                directory,
            )

            self.assertIsNotNone(salida)
            self.assertTrue(os.path.exists(salida))