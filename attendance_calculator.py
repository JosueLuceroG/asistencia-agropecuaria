"""
Attendance calculation logic - extracted from main v4.py
Handles report building, entry/salida grouping, and detail computation.
Optimized with pre-computation and vectorized operations.
"""
from datetime import datetime, timedelta, date
import threading
from collections import OrderedDict
from config_manager import (
    limpiar_nombre, normalizar_horario, obtener_horario_empleado,
    obtener_horario_para_fecha, obtener_horarios_config,
    obtener_mapa_dias_no_laborados, es_dia_no_laborado,
    motivo_dia_no_laborado, es_dia_laboral, hora_vacia,
    minutos_de_hora, segundos_de_hora, rango_fechas,
    parse_hora_config, entero_no_negativo, formato_hora,
    formato_entrada_reporte, formato_salida_reporte, marca_checador
)
from theme import HORA_CORTE_SALIDA_MIN, get_theme

# ─── GLOBALS ────────────────────────────────────────────────────
datos_memoria_crudos = None
checador_memoria = ""
ultimo_reporte = ""
titulo_actual = ""

# ─── HELPERS ────────────────────────────────────────────────────
def formato_duracion(minutos):
    if minutos is None or minutos < 0:
        return "---"
    horas = minutos // 60
    mins = minutos % 60
    return f"{horas}:{mins:02d}"

def preparar_base_asistencia(df_crudo):
    import pandas as pd
    if df_crudo is None or df_crudo.empty:
        return pd.DataFrame(columns=["nombre", "fecha_hora", "Fecha_D", "Hora_D"])
    df = df_crudo.dropna(subset=["fecha_hora", "nombre"]).copy()
    df = df[~df["nombre"].str.contains("admin", case=False, na=False)]
    df["nombre"] = df["nombre"].apply(limpiar_nombre)
    df["Fecha_D"] = df["fecha_hora"].dt.date
    df["Hora_D"] = df["fecha_hora"].dt.time
    return df

def obtener_entrada_salida_por_corte(horas):
    horas = sorted([h for h in horas if not hora_vacia(h)], key=segundos_de_hora)
    if not horas:
        return None, None
    entradas = [h for h in horas if minutos_de_hora(h) < HORA_CORTE_SALIDA_MIN]
    salidas = [h for h in horas if minutos_de_hora(h) >= HORA_CORTE_SALIDA_MIN]
    entrada = entradas[0] if entradas else None
    if salidas:
        salida = salidas[-1]
    else:
        salida = entrada if entrada else horas[-1]
    return entrada, salida

def obtener_entrada_salida_primera_ultima(horas):
    horas = sorted([h for h in horas if not hora_vacia(h)], key=segundos_de_hora)
    if not horas:
        return None, None
    return horas[0], horas[-1]

def usa_primera_ultima_checador(checador_info):
    return bool(checador_info and marca_checador(checador_info) == "hikvision")

def agrupar_entrada_salida(df_rango, usar_primera_ultima=False):
    import pandas as pd
    if df_rango.empty:
        return pd.DataFrame(columns=["nombre", "Fecha_D", "Entrada", "Salida"])

    columnas_grupo = ["nombre", "Fecha_D"]
    datos = df_rango.dropna(subset=["Hora_D"])
    if datos.empty:
        return pd.DataFrame(columns=["nombre", "Fecha_D", "Entrada", "Salida"])

    # Hikvision uses the first/last punch. Pandas groupby avoids materializing
    # a Python list and sorting it for every employee/day.
    if usar_primera_ultima:
        return (
            datos.groupby(columnas_grupo, as_index=False)["Hora_D"]
            .agg(Entrada="min", Salida="max")
        )

    # ZKTeco considers the first pre-cutoff punch as entry and the last
    # post-cutoff punch as exit. Keep the prior fallback behavior when there
    # is no post-cutoff punch.
    minutos = datos["Hora_D"].map(minutos_de_hora)
    entradas = (
        datos.loc[minutos < HORA_CORTE_SALIDA_MIN]
        .groupby(columnas_grupo, as_index=False)["Hora_D"]
        .min()
        .rename(columns={"Hora_D": "Entrada"})
    )
    salidas = (
        datos.loc[minutos >= HORA_CORTE_SALIDA_MIN]
        .groupby(columnas_grupo, as_index=False)["Hora_D"]
        .max()
        .rename(columns={"Hora_D": "Salida"})
    )
    ultimos = (
        datos.groupby(columnas_grupo, as_index=False)["Hora_D"]
        .max()
        .rename(columns={"Hora_D": "Ultima"})
    )
    reporte = ultimos.merge(entradas, on=columnas_grupo, how="left").merge(salidas, on=columnas_grupo, how="left")
    reporte["Entrada"] = reporte["Entrada"].where(reporte["Entrada"].notna(), reporte["Ultima"])
    reporte["Salida"] = reporte["Salida"].where(reporte["Salida"].notna(), reporte["Entrada"])
    return reporte[columnas_grupo + ["Entrada", "Salida"]]

# Cache for repeated attendance-detail calculations. It is bounded so reports
# for many employees/months cannot grow process memory indefinitely.
_DETALLES_CACHE_MAXSIZE = 10_000
_detalles_cache = OrderedDict()


def limpiar_cache_detalles():
    """Clear calculated-detail cache after an external schedule change."""
    _detalles_cache.clear()


def _obtener_detalle_cache(cache_key):
    result = _detalles_cache.get(cache_key)
    if result is not None:
        _detalles_cache.move_to_end(cache_key)
        return dict(result)
    return None


def _guardar_detalle_cache(cache_key, result):
    if not cache_key:
        return
    _detalles_cache[cache_key] = dict(result)
    _detalles_cache.move_to_end(cache_key)
    if len(_detalles_cache) > _DETALLES_CACHE_MAXSIZE:
        _detalles_cache.popitem(last=False)


def _invertir_hora_str(h):
    """Quickly convert a time object to minutes for comparison - optimized vs minutos_de_hora."""
    return h.hour * 60 + h.minute


def calcular_detalles_asistencia(entrada, salida, horarios, falta=False, fecha=None, dias_no_laborados=None):
    # Quick cache check - build a simple key from the inputs
    cache_key = None
    if entrada is not None and salida is not None and fecha is not None:
        try:
            key_parts = [
                str(_invertir_hora_str(entrada)),
                str(_invertir_hora_str(salida)),
                horarios.get("entrada", ""),
                horarios.get("salida", ""),
                str(horarios.get("tolerancia_min", 0)),
                str(horarios.get("dias_laborales", [])),
                str(falta),
                fecha.isoformat() if fecha else "",
            ]
            cache_key = "|".join(key_parts)
            cached = _obtener_detalle_cache(cache_key)
            if cached is not None:
                return cached
        except Exception:
            cache_key = None

    if fecha is not None and es_dia_no_laborado(fecha, dias_no_laborados):
        if hora_vacia(entrada) or hora_vacia(salida):
            horas_trabajadas = "---"
        else:
            ent_min = minutos_de_hora(entrada)
            sal_min = minutos_de_hora(salida)
            if salida == entrada:
                horas_trabajadas = "---"
            else:
                if sal_min < ent_min:
                    sal_min += 24 * 60
                horas_trabajadas = formato_duracion(sal_min - ent_min)
        result = {
            "Retardo": "No cuenta", "Retardo_SN": "NO", "Tiempo_Retardo": "0 min",
            "Salida_Temprana": "No cuenta", "Horas_Trabajadas": horas_trabajadas,
            "Tiene_Retardo": False, "Tiene_Salida_Temprana": False,
            "Dia_Laboral": False, "Dia_No_Laboral": True,
            "Extra_Sabado": False, "Domingo_No_Laboral": False,
            "Sin_Entrada": False, "No_Laborado": True,
            "Motivo_No_Laborado": motivo_dia_no_laborado(fecha, dias_no_laborados)
        }
        _guardar_detalle_cache(cache_key, result)
        return result

    if falta:
        result = {
            "Retardo": "FALTA", "Retardo_SN": "FALTA", "Tiempo_Retardo": "FALTA",
            "Salida_Temprana": "FALTA", "Horas_Trabajadas": "0:00",
            "Tiene_Retardo": False, "Tiene_Salida_Temprana": False,
            "Dia_Laboral": True, "Dia_No_Laboral": False,
            "Extra_Sabado": False, "Domingo_No_Laboral": False,
            "Sin_Entrada": False, "No_Laborado": False, "Motivo_No_Laborado": ""
        }
        _guardar_detalle_cache(cache_key, result)
        return result

    dia_laboral = True if fecha is None else es_dia_laboral(fecha, horarios, dias_no_laborados)
    if fecha is not None and not dia_laboral:
        if hora_vacia(entrada) or hora_vacia(salida) or salida == entrada:
            horas_trabajadas = "---"
        else:
            ent_min = minutos_de_hora(entrada)
            sal_min = minutos_de_hora(salida)
            if sal_min < ent_min:
                sal_min += 24 * 60
            horas_trabajadas = formato_duracion(sal_min - ent_min)
        result = {
            "Retardo": "No cuenta", "Retardo_SN": "NO", "Tiempo_Retardo": "0 min",
            "Salida_Temprana": "No cuenta", "Horas_Trabajadas": horas_trabajadas,
            "Tiene_Retardo": False, "Tiene_Salida_Temprana": False,
            "Dia_Laboral": False, "Dia_No_Laboral": True,
            "Extra_Sabado": fecha.weekday() == 5, "Domingo_No_Laboral": fecha.weekday() == 6,
            "Sin_Entrada": False, "No_Laborado": False, "Motivo_No_Laborado": ""
        }
        _guardar_detalle_cache(cache_key, result)
        return result

    horarios = obtener_horario_para_fecha(horarios, fecha)
    hora_entrada_cfg = parse_hora_config(horarios.get("entrada"), "08:00")
    hora_salida_cfg = parse_hora_config(horarios.get("salida"), "17:00")
    tolerancia = entero_no_negativo(horarios.get("tolerancia_min", 10), 10)
    if hora_vacia(entrada) and not hora_vacia(salida):
        result = {
            "Retardo": "SIN ENTRADA", "Retardo_SN": "SIN ENTRADA", "Tiempo_Retardo": "SIN ENTRADA",
            "Salida_Temprana": "No", "Horas_Trabajadas": "---",
            "Tiene_Retardo": False, "Tiene_Salida_Temprana": False,
            "Dia_Laboral": True, "Dia_No_Laboral": False,
            "Extra_Sabado": False, "Domingo_No_Laboral": False,
            "Sin_Entrada": True, "No_Laborado": False, "Motivo_No_Laborado": ""
        }
        _guardar_detalle_cache(cache_key, result)
        return result

    ent_min = _invertir_hora_str(entrada)
    sal_min = _invertir_hora_str(salida)
    entrada_limite = _invertir_hora_str(hora_entrada_cfg) + tolerancia
    salida_esperada = _invertir_hora_str(hora_salida_cfg)
    retardo_min = ent_min - entrada_limite
    tiene_retardo = retardo_min > 0
    retardo = f"Si (+{retardo_min} min)" if tiene_retardo else "No"
    retardo_sn = "SI" if tiene_retardo else "NO"
    tiempo_retardo = f"{retardo_min} min" if tiene_retardo else "0 min"
    tiene_salida_temprana = salida != entrada and sal_min < salida_esperada
    salida_temprana = f"Si (-{salida_esperada - sal_min} min)" if tiene_salida_temprana else "No"
    if salida == entrada:
        horas_trabajadas = "---"
    else:
        if sal_min < ent_min:
            sal_min += 24 * 60
        horas_trabajadas = formato_duracion(sal_min - ent_min)

    result = {
        "Retardo": retardo, "Retardo_SN": retardo_sn, "Tiempo_Retardo": tiempo_retardo,
        "Salida_Temprana": salida_temprana, "Horas_Trabajadas": horas_trabajadas,
        "Tiene_Retardo": tiene_retardo, "Tiene_Salida_Temprana": tiene_salida_temprana,
        "Dia_Laboral": True, "Dia_No_Laboral": False,
        "Extra_Sabado": False, "Domingo_No_Laboral": False,
        "Sin_Entrada": False, "No_Laborado": False, "Motivo_No_Laborado": ""
    }
    _guardar_detalle_cache(cache_key, result)
    return result


def construir_reporte_asistencia(df_crudo, fecha_inicio, fecha_fin, horarios=None, incluir_faltas=True, empleados_asignados=None, usar_primera_ultima=False):
    import pandas as pd
    horarios = horarios or obtener_horarios_config()
    dias_no_laborados = obtener_mapa_dias_no_laborados()
    base = preparar_base_asistencia(df_crudo)
    empleados_config = {limpiar_nombre(emp) for emp in (empleados_asignados or []) if str(emp).strip()}
    empleados_especiales = set()
    for especial in normalizar_horario(horarios).get("especiales", []):
        empleados_especiales.update(limpiar_nombre(emp) for emp in especial.get("empleados", []) if str(emp).strip())
    empleados_permitidos = empleados_config | empleados_especiales
    if empleados_permitidos and not base.empty:
        base = base[base["nombre"].isin(empleados_permitidos)].copy()
    empleados_base = set(base["nombre"].unique()) if not base.empty else set()
    empleados = sorted(empleados_base | empleados_permitidos)
    fechas_periodo = rango_fechas(fecha_inicio, fecha_fin)
    df_rango = base[(base["Fecha_D"] >= fecha_inicio) & (base["Fecha_D"] <= fecha_fin)].copy()

    # ─── OPTIMIZATION: Pre-compute employee horarios dict ONCE ───
    # This avoids calling normalizar_horario/obtener_horario_empleado repeatedly
    horario_empleados = {}
    for emp in empleados:
        horario_empleados[emp] = obtener_horario_empleado(emp, horarios)

    if df_rango.empty:
        reporte = pd.DataFrame(columns=["nombre", "Fecha_D", "Entrada", "Salida", "Falta"])
    else:
        reporte = agrupar_entrada_salida(df_rango, usar_primera_ultima=usar_primera_ultima)
        reporte["Falta"] = False

    # ─── OPTIMIZATION: Build faltas using set operations ───
    if incluir_faltas and empleados:
        if not reporte.empty:
            existentes = set(zip(reporte["nombre"], reporte["Fecha_D"]))
        else:
            existentes = set()
        faltas = []
        for emp in empleados:
            horario_emp = horario_empleados[emp]
            for fecha in fechas_periodo:
                if es_dia_laboral(fecha, horario_emp, dias_no_laborados) and (emp, fecha) not in existentes:
                    faltas.append({"nombre": emp, "Fecha_D": fecha, "Entrada": None, "Salida": None, "Falta": True})
        if faltas:
            reporte = pd.concat([reporte, pd.DataFrame(faltas)], ignore_index=True)

    if reporte.empty:
        return reporte, base, fechas_periodo

    # itertuples avoids Series allocation for every row and is measurably
    # faster on monthly reports with many employees.
    detalles = [
        calcular_detalles_asistencia(
            row.Entrada,
            row.Salida,
            horario_empleados.get(row.nombre) or obtener_horario_empleado(row.nombre, horarios),
            bool(row.Falta),
            row.Fecha_D,
            dias_no_laborados,
        )
        for row in reporte.itertuples(index=False)
    ]
    detalles_df = pd.DataFrame(detalles)
    reporte = pd.concat([reporte.reset_index(drop=True), detalles_df], axis=1)
    reporte = reporte.sort_values(by=["nombre", "Fecha_D"]).reset_index(drop=True)
    return reporte, base, fechas_periodo

def resumen_horarios_especiales(horarios, empleados_reporte=None):
    empleados_validos = None
    if empleados_reporte is not None:
        empleados_validos = {limpiar_nombre(emp).lower() for emp in empleados_reporte if str(emp).strip()}
    filas = []
    for especial in normalizar_horario(horarios).get("especiales", []):
        empleados = sorted({limpiar_nombre(emp) for emp in especial.get("empleados", []) if str(emp).strip()})
        if empleados_validos is not None:
            empleados = [emp for emp in empleados if limpiar_nombre(emp).lower() in empleados_validos]
        if empleados:
            filas.append({
                "nombre": especial.get("nombre", "Horario especial"),
                "entrada": especial.get("entrada", ""),
                "salida": especial.get("salida", ""),
                "tolerancia": especial.get("tolerancia_min", 0),
                "empleados": empleados
            })
    return filas

def calcular_filas_dashboard_mensual(reporte, fechas_periodo, horarios, col_emp):
    import pandas as pd
    filas = []
    dias_no_laborados = obtener_mapa_dias_no_laborados()

    # Pre-compute all horarios for all employees
    todos_empleados = sorted(reporte[col_emp].unique())
    horario_empleados = {}
    for emp in todos_empleados:
        horario_empleados[emp] = obtener_horario_empleado(emp, horarios)
    from config_manager import obtener_horario_especial_empleado as get_special

    # Pre-compute laboral days and no-laborados
    fechas_laborales = {d for d in fechas_periodo if es_dia_laboral(d, horarios, dias_no_laborados)}

    for emp in todos_empleados:
        emp_data = reporte[reporte[col_emp] == emp]
        horario_emp = horario_empleados[emp]
        especial = get_special(emp, horarios)

        fechas_con_checada_laboral = set()
        fechas_sabado_extra = set()
        fechas_domingo_info = set()

        for row in emp_data.itertuples(index=False):
            if bool(row.Falta):
                continue
            fecha = row.Fecha_D
            if es_dia_no_laborado(fecha, dias_no_laborados):
                continue
            if es_dia_laboral(fecha, horario_emp, dias_no_laborados):
                fechas_con_checada_laboral.add(fecha)
            elif fecha.weekday() == 5:
                fechas_sabado_extra.add(fecha)
            elif fecha.weekday() == 6:
                fechas_domingo_info.add(fecha)

        faltas = int(emp_data["Falta"].fillna(False).astype(bool).sum()) if "Falta" in emp_data else 0
        trabajados = len(fechas_con_checada_laboral)
        sabados_extra = len(fechas_sabado_extra)
        domingos_info = len(fechas_domingo_info)
        dias_programados = len(fechas_laborales)
        retardos = int(emp_data["Tiene_Retardo"].fillna(False).astype(bool).sum()) if "Tiene_Retardo" in emp_data else 0
        salidas_temp = int(emp_data["Tiene_Salida_Temprana"].fillna(False).astype(bool).sum()) if "Tiene_Salida_Temprana" in emp_data else 0
        sin_salida = sum(
            not bool(row.Falta)
            and row.Fecha_D in fechas_laborales
            and not hora_vacia(row.Entrada)
            and row.Entrada == row.Salida
            for row in emp_data.itertuples(index=False)
        )

        asistencia_pct = ((trabajados + sabados_extra) / dias_programados) if dias_programados else 0
        filas.append({
            "empleado": emp,
            "horario": especial.get("nombre", "General") if especial else "General",
            "programados": dias_programados, "trabajados": trabajados,
            "sabados_extra": sabados_extra, "domingos_info": domingos_info,
            "asistencia_pct": asistencia_pct, "retardos": retardos,
            "faltas": faltas, "sin_salida": sin_salida, "salidas_temp": salidas_temp
        })
    return filas

from config_manager import obtener_horario_especial_empleado
