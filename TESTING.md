# Pruebas

## Suite automatizada

Desde la raíz del proyecto:

```powershell
python -m unittest discover -s tests -v
```

La suite no inicia la ventana principal, no usa checadores reales y no realiza conexiones de red. Cubre, entre otros:

- agrupación de entradas y salidas para ZKTeco e Hikvision;
- normalización y guardado atómico de la configuración;
- segmentación mensual de consultas Hikvision;
- caché local de asistencias, deduplicación y metadatos de sincronización;
- clasificación local del estado de los checadores;
- creación, validación, restauración y rollback de respaldos;
- permisos y sesiones de usuario.

También puedes validar que todos los módulos compilen:

```powershell
python -m compileall -q "main v4.py" config_manager.py attendance_calculator.py excel_exporter.py theme.py services device_connectors models config ui tests
```

## Diagnóstico manual de Hikvision

`test_hikvision.py` es una utilidad opcional para comprobar la conexión ISAPI contra un equipo autorizado. No contiene credenciales: se reciben por variables de entorno de la sesión actual.

```powershell
$env:HIKVISION_IP = "IP_DEL_EQUIPO"
$env:HIKVISION_USER = "USUARIO"
$env:HIKVISION_PASSWORD = "CONTRASENA"
python test_hikvision.py
```

Al cerrar la terminal, las variables se descartan. No subas credenciales ni respuestas del equipo al repositorio.
