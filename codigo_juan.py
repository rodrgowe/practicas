"""
ETL IBNR: base de incurridos + triángulos (no vida y vida).

Flujo: leer -> fechas -> enriquecer (maestros, atípicos, retención)
       -> incurrido -> validar/cuadrar -> agrupar -> triángulos -> validar triángulos -> exportar

REGLAS DE NEGOCIO (marcadas con  # REVISAR  donde hay que confirmar):
  1. Tipo de cambio FIJO (TIPO_CAMBIO_FIJO) para todo el proceso. TC_HISTO.xlsx ya no se usa.
  2. Fechas: parser propio (parsear_fechas) que respeta cada formato (ISO, dd/mm/aaaa,
     aaaammdd, serial de Excel, etc.), resuelve día/mes solo cuando es inequívoco o por
     ORDEN_FECHA_AMBIGUA, y reporta lo que no pudo leer. (pandas 'mixed' + dayfirst
     invierte día y mes en las fechas ISO.)
  3. Retención, según la fecha de siniestro (RET_FECHA_BASE):
       - mes de siniestro <= RET_CORTE_HIST (202103): histórico (MP no vida / MPV vida),
         el % del txt se divide entre 100.
       - mes de siniestro >  RET_CORTE_HIST (>= 202104): Retención_2021_2026.xlsx.
         Si la clave NUM_SINI+NUM_EXP+COD_COB está repetida se desempata por año
         (ANIO_OPER = año de PERIODO).
       - Si no se encuentra en SU fuente -> 1 (no se prueba la otra fuente).
       - Todo % > 1 se topa en 1. Póliza ESSALUD: 20% fijo.
  4. Vida usa la misma fórmula de dólares que el resto (división por TC).
  5. Filas AUTOS 3 transferidas a AUTOS 1/2 se reclasifican como AUTOS 1/2.
  6. dif < 0: se exportan como error y en el triángulo se llevan al desarrollo 0
     (TRATO_DIF_NEGATIVO), para que la columna B sea siempre desarrollo 0.
  7. Atípicos por cuantía: por siniestro se suma el incurrido bruto en USD y se marcan
     (ATIPICOS='S') los que superan el percentil superior o quedan por debajo del inferior.
       - NO VIDA: por archivo, P2.5 / P97.5 (ATIPICOS_NV_POR_RAMO = True los calcula por NUEVO RAMO).
       - VIDA: por NUEVO RAMO del maestro, P1 / P99, ANTES de separar ESSALUD-ACC.
     Se marcan en la base y se EXCLUYEN del triángulo (EXCLUIR_ATIPICOS_TRIANGULO = True),
     igual que los atípicos operativos de Operativo.xlsx.
  8. Triángulos en bruto (INCURRIDO_MON) y neto (INCURRIDO_MON_NETO): 4 Excel
     (no vida bruto / no vida neto / vida bruto / vida neto), una hoja por ramo, cuadrados.
  9. main() NO exporta: corre y revisa. Exportar es un paso aparte: exportar(res).
 10. Validación de triángulos: triángulo INICIAL (todo el incurrido, incluye atípicos) vs
     FINAL (el que va al Excel), bruto y neto, con puente por ramo. Ver validacion_triangulos(),
     exportar_validacion() y ver_triangulos().

Uso (por celdas):
    res, rev = main()                  # correr + revisión
    rev["alertas"]                     # lo primero que hay que mirar
    rev["puente_bruto_nv"]             # inicial -> final por ramo (no vida, bruto)
    ver_triangulos(res, "SOAT")        # esquinas del triángulo inicial y final, bruto y neto
    exportar(res)                      # solo cuando los outputs tengan sentido
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# 0. CONFIGURACIÓN
# =============================================================================
RUTA = Path(r"C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\7_Agosto IBNR\00_DATA_CIERRE_DETALLADA\DATOS_BRUTOS")
RUTA_OUTPUT = Path(r"C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\suficiencia\base")
RUTA_TRIANGULOS = RUTA_OUTPUT.parent / "triangulos"  # REVISAR: carpeta de los Excel de triángulos
RUTA_MAESTROS = Path(r"C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\suficiencia\maestros")
RUTA_CANAL = Path(r"C:/Users/crolaz1/Downloads")

SUFIJO = "082026"

COLUMNAS = [
    "COD_CIA", "COD_SECTOR", "COD_SUBSEC", "COD_RAMO", "GRUPO", "SUBGRUPO",
    "COD_MODALIDAD", "COD_AGT", "NUM_SINI", "NUM_POLIZA", "NUM_POLIZA_GRUPO",
    "PERIODO", "TIP_EST_SINI", "NUM_EXP", "TIP_EXP", "TIP_EST_EXP",
    "MCA_EXP_RECOBRO", "TIP_EXP_AFEC", "TIP_MVTO", "SUB_TIP_MVTO", "COD_CIA_COA",
    "COD_COB", "COD_CTO_RVA", "TIP_EST_COB", "NUM_MVTO", "FEC_SINI",
    "FEC_DENU_SINI", "FEC_APER_EXP", "FEC_REAP_EXP", "FEC_TERM_EXP",
    "FEC_TERM_SINI", "FEC_MVTO", "IMP_VAL", "IMP_MVTO_VAL", "IMP_LIQ",
    "IMP_MVTO_LIQ", "IMP_PAG", "IMP_MVTO_PAG", "COD_MON", "NUM_LIQ", "COD_USR",
    "FEC_ACTU", "PCT_COA", "PAGO_BRUTO", "RESERVA_BRUTO", "PAGO_NETO",
    "RESERVA_NETA", "FEC_SIN", "FEC_MTO", "FEC_DEN_SINI", "FEC_APE_EXP",
    "EXP_MIN", "MOV_MIN", "ATIPICO", "TIPO_IBNR", "COD_TIP_VEHI",
    "DES_COD_TIP_VEHI", "NIVEL_DANO", "PPD", "CANTIDAD", "VAL_CAMBIO",
    "NOM_TERCERO", "PCT_REASEGURO",
]

# Columnas que se leen como texto (para no perder ceros/precisión) y cómo se tratan
COLS_ID = ["COD_CIA", "COD_RAMO", "NUM_SINI", "NUM_POLIZA", "NUM_EXP", "COD_COB", "PERIODO"]
COLS_TXT = ["SUBGRUPO", "TIP_EXP"]
COLS_FECHA = ["FEC_SINI", "FEC_MVTO"]
COLS_NUM = ["COD_MON", "PAGO_BRUTO", "RESERVA_BRUTO"]

# Clasificación de archivos por nombre
EXCLUIR_EN_NOMBRE = ("207", "_28_", "_11_")
EXTENSIONES_IGNORADAS = (".xlsx", ".xls", ".db", ".ini", ".zip", ".sqlite", ".tmp", ".log")
MARCA_VIDA = "_2_"
MARCA_AUTOS = "_30_"

# Moneda
TIPO_CAMBIO_FIJO = 3.362  # soles por dólar; COD_MON 1 = soles, 2 = dólares

# Fechas
ORDEN_FECHA_AMBIGUA = "DMY"      # "DMY" o "MDY": solo se usa cuando día y mes son <= 12 y distintos
AÑO_MIN_VALIDO = 1980            # fechas fuera de [AÑO_MIN_VALIDO, AÑO_MAX_VALIDO] se tratan como inválidas
AÑO_MAX_VALIDO = 2100

# Autos
SUBGRUPOS_AUTOS = ["AUTOS 1", "AUTOS 2", "AUTOS 3"]
TIP_EXP_TRANSFERIBLES = ["RDR", "RCT", "RAC", "RAA"]

# Vida
POLIZA_ESSALUD = 6362159900003
RET_ESSALUD = 0.20  # retención fija de la póliza ESSALUD

# Retención
RET_FECHA_BASE = "FEC_SINI"   # REVISAR: fecha que decide la fuente ("FEC_SINI" o "FEC_MVTO")
RET_CORTE_HIST = 202103       # año-mes: <= usa histórico, > usa Retención_2021_2026.xlsx
CLAVE_RET_HIST = ["COD_CIA", "COD_RAMO", "NUM_SINI", "NUM_EXP"]
CLAVE_RET_XLSX = ["NUM_SINI", "NUM_EXP", "COD_COB"]
CHUNKSIZE_RET_HIST = 500_000

# Triángulos
EXCLUIR_ATIPICOS_TRIANGULO = True  # excluye ATIPICOS == 'S' del triángulo

# Atípicos por cuantía: percentiles del incurrido por siniestro
PERCENTIL_INF = 0.025           # NO VIDA
PERCENTIL_SUP = 0.975
ATIPICOS_NV_POR_RAMO = False    # REVISAR: False = por archivo; True = por NUEVO RAMO dentro de cada archivo
PERCENTIL_INF_VIDA = 0.01       # VIDA: por NUEVO RAMO, antes de separar ESSALUD-ACC
PERCENTIL_SUP_VIDA = 0.99
COL_ATIPICO_PERCENTIL = "INCURRIDO_DOL"  # bruto en USD (comparable entre monedas)  # REVISAR
TRATO_DIF_NEGATIVO = "a_cero"   # "a_cero" | "excluir"  # REVISAR
DESDE_OCURRENCIA = 201901       # el triángulo empieza en este año-mes (celda A2)
MES_CORTE = 202608              # REVISAR: último mes de cierre (para los controles)

# Triángulos en Excel
# (4 archivos: no vida / vida x bruto / neto; una hoja por ramo; índices desde A2, valores desde B2)
COL_TRIANGULO_BRUTO = "INCURRIDO_MON"        # bruto, en la moneda del ramo
COL_TRIANGULO_NETO = "INCURRIDO_MON_NETO"    # neto de reaseguro, en la moneda del ramo
TRIANGULO_ACUMULADO = False     # False = incremental (como el original)  # REVISAR
TRIANGULO_FUTURO_VACIO = False  # True = deja en blanco las celdas no observadas

# Umbrales de alerta de la revisión
UMBRALES = {
    "factor_min": 0.95,          # factor edad-a-edad ponderado
    "factor_max": 3.0,
    "pct_celdas_neg": 0.05,      # % de celdas observadas negativas
    "salto_retencion": 0.20,     # variación anual de retención implícita
    "pct_atipico": 0.30,         # % del incurrido bruto que es atípico (ramo-año)
    "pct_atipico_total": 0.15,   # % del incurrido bruto que es atípico (ramo, todo el periodo)
    "pct_error_dif": 0.01,       # dif<0 sobre incurrido total
    "mult_mov_ultimo_mes": 3.0,  # último mes vs mediana de los 12 previos
    "tol_puente": 1e-6,          # tolerancia relativa del puente inicial -> final
}

# Procesos opcionales (se llaman a mano con extras())
DESDE_CANAL = 201901  # inclusivo

COLS_MONTOS = [
    "INCURRIDO_SOL", "INCURRIDO_DOL", "INCURRIDO_SOL_NETO",
    "INCURRIDO_DOL_NETO", "INCURRIDO_MON", "INCURRIDO_MON_NETO", "RESERVA_BRUTO", "PAGO_BRUTO",
]
CLAVES_BASE = [
    "NUEVO RAMO", "NUM_SINI", "AÑO_SINI", "AÑO_MOV", "AÑO_MES_OCU",
    "AÑO_MES_MOV", "dif", "COD_MON", "ATIPICOS", "TIPO_ATIPICO",
]

log = logging.getLogger("ibnr")


# =============================================================================
# 1. UTILIDADES DE LECTURA (ids, números, fechas)
# =============================================================================
_NULOS = {"", "NAN", "NONE", "NULL", "<NA>", "NAT"}


def normalizar_nombre(x: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(x).strip().upper()).strip("_")


def _expandir_cientifico(txt: str) -> str:
    try:
        d = Decimal(txt.replace(",", "."))
        if d == d.to_integral_value():
            return format(d.quantize(Decimal("1")), "f")
    except (InvalidOperation, ValueError):
        pass
    return txt


def normalizar_id(s: pd.Series) -> pd.Series:
    """Texto limpio para llaves: sin espacios, sin '.0' final, sin notación científica,
    y con nulos como NA."""
    x = s.astype("string").str.strip()
    x = x.mask(x.str.upper().isin(_NULOS))
    x = x.str.replace(r"\.0+$", "", regex=True)
    sci = x.str.contains(r"^\d+(?:[.,]\d+)?[eE][+-]?\d+$", na=False)
    if sci.any():
        x = x.copy()
        x[sci] = x[sci].map(_expandir_cientifico)
    return x


def numero(s: pd.Series) -> pd.Series:
    """Texto -> float. Rápido si ya es numérico; si no, entiende '1,234.56', '1.234,56' y '12,5'."""
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return s.astype("float64")
    x = s.astype("string").str.strip().str.replace(" ", "", regex=False)
    out = pd.to_numeric(x, errors="coerce").astype("float64")
    pend = out.isna() & x.notna() & x.ne("")
    if pend.any():
        y = x[pend]
        ambos = y.str.contains(",", regex=False, na=False) & y.str.contains(".", regex=False, na=False)
        coma_dec = ambos & (y.str.rfind(",") > y.str.rfind("."))
        y = y.where(~coma_dec, y.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
        punto_dec = ambos & ~coma_dec
        y = y.where(~punto_dec, y.str.replace(",", "", regex=False))
        solo_coma = y.str.contains(",", regex=False, na=False) & ~y.str.contains(".", regex=False, na=False)
        y = y.where(~solo_coma, y.str.replace(",", ".", regex=False))
        out[pend] = pd.to_numeric(y, errors="coerce").astype("float64")
    return out


_RE_ISO = r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\D.*)?$"
_RE_SEP = r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:\D.*)?$"
_NAT = np.datetime64("NaT", "ns")


def _construir_fechas(y: pd.Series, m: pd.Series, d: pd.Series) -> np.ndarray:
    """Año/mes/día numéricos -> datetime64[ns]; NaT si no es una fecha real (31/02, mes 13, año fuera de rango)."""
    out = np.full(len(y), _NAT, dtype="datetime64[ns]")
    ok = (y.notna() & m.notna() & d.notna() & y.between(AÑO_MIN_VALIDO, AÑO_MAX_VALIDO)).to_numpy()
    if ok.any():
        partes = pd.DataFrame({"year": y[ok], "month": m[ok], "day": d[ok]}).astype("int64")
        out[ok] = pd.to_datetime(partes, errors="coerce").to_numpy(dtype="datetime64[ns]")
    return out


def parsear_fechas(s: pd.Series, orden: str = ORDEN_FECHA_AMBIGUA):
    """Convierte una columna de fechas en texto a datetime64[ns]. Devuelve (fechas, estadísticas).

    Se parsean solo los valores ÚNICOS (rápido) y cada formato se resuelve con su propia regla:
      - serial de Excel (20000-80000), ISO (aaaa-mm-dd[ hora]), aaaammdd, ddmmaaaa/mmddaaaa compacto,
      - dd/mm/aaaa o mm/dd/aaaa (separadores - / .; año de 2 o 4 dígitos; hora opcional),
      - cualquier otro texto (p. ej. '05-Mar-2021') por el parser general de pandas.
    En dd/mm vs mm/dd: si un lado es > 12 el orden es inequívoco; si ambos <= 12 y distintos, manda `orden`.
    Fechas inexistentes o con año fuera de [AÑO_MIN_VALIDO, AÑO_MAX_VALIDO] quedan en NaT y se cuentan.
    """
    orden = orden.upper()
    if orden not in {"DMY", "MDY"}:
        raise ValueError("ORDEN_FECHA_AMBIGUA debe ser DMY o MDY")
    dmy_default = orden == "DMY"

    raw = s.astype("string").str.strip()
    raw = raw.mask(raw.str.upper().isin(_NULOS))
    codes, uniq = pd.factorize(raw)
    u = pd.Series(np.asarray(uniq, dtype=object).astype(str))
    n = len(u)
    w = np.bincount(codes[codes >= 0], minlength=n) if n else np.zeros(0, dtype=int)

    res = np.full(n, _NAT, dtype="datetime64[ns]")
    hecho = np.zeros(n, dtype=bool)
    amb = np.full(n, "", dtype=object)   # '', 'dmy' (inequívoco), 'mdy' (inequívoco), 'amb'

    if n:
        # 1) serial de Excel
        nums = pd.to_numeric(u, errors="coerce")
        m_serial = nums.between(20_000, 80_000).to_numpy()
        if m_serial.any():
            dias = pd.to_timedelta(np.floor(nums[m_serial]), unit="D")
            res[m_serial] = (pd.Timestamp("1899-12-30") + dias).to_numpy(dtype="datetime64[ns]")
            hecho |= m_serial

        # 2) ISO aaaa-mm-dd (con o sin hora)
        iso = u.str.extract(_RE_ISO)
        m_iso = (iso[0].notna().to_numpy()) & ~hecho
        if m_iso.any():
            res[m_iso] = _construir_fechas(*(pd.to_numeric(iso.loc[m_iso, c]) for c in (0, 1, 2)))
            hecho |= m_iso

        # 3) compacto de 8 dígitos: aaaammdd; si no es válido, ddmmaaaa / mmddaaaa
        m_comp = u.str.fullmatch(r"\d{8}").fillna(False).to_numpy() & ~hecho
        if m_comp.any():
            c = u[m_comp]
            ymd = _construir_fechas(pd.to_numeric(c.str[:4]), pd.to_numeric(c.str[4:6]), pd.to_numeric(c.str[6:8]))
            ymd_ok = ~np.isnat(ymd) & pd.to_numeric(c.str[:4]).between(1900, 2200).to_numpy()
            a, b, y = (pd.to_numeric(c.str[:2]), pd.to_numeric(c.str[2:4]), pd.to_numeric(c.str[4:]))
            dmy_ineq, mdy_ineq = (a > 12) & (b <= 12), (b > 12) & (a <= 12)
            ambigua = (a <= 12) & (b <= 12) & (a != b)
            usar_dmy = dmy_ineq | ((a <= 12) & (b <= 12) & dmy_default)
            alt = _construir_fechas(y, b.where(usar_dmy, a), a.where(usar_dmy, b))
            res[m_comp] = np.where(ymd_ok, ymd, alt)
            tipo = np.where(ymd_ok, "", np.where(dmy_ineq, "dmy", np.where(mdy_ineq, "mdy", np.where(ambigua, "amb", ""))))
            amb[m_comp] = tipo
            hecho |= m_comp

        # 4) dd/mm/aaaa o mm/dd/aaaa
        sp = u.str.extract(_RE_SEP)
        m_sp = (sp[0].notna().to_numpy()) & ~hecho
        if m_sp.any():
            a, b, y = (pd.to_numeric(sp.loc[m_sp, c]) for c in (0, 1, 2))
            dos_digitos = sp.loc[m_sp, 2].str.len().eq(2)   # '21' -> 2021; '0001' sigue siendo año 1 (inválido)
            y = y.where(~dos_digitos, np.where(y <= 69, 2000 + y, 1900 + y))
            dmy_ineq, mdy_ineq = (a > 12) & (b <= 12), (b > 12) & (a <= 12)
            ambigua = (a <= 12) & (b <= 12) & (a != b)
            usar_dmy = dmy_ineq | ((a <= 12) & (b <= 12) & dmy_default)
            res[m_sp] = _construir_fechas(y, b.where(usar_dmy, a), a.where(usar_dmy, b))
            amb[m_sp] = np.where(dmy_ineq, "dmy", np.where(mdy_ineq, "mdy", np.where(ambigua, "amb", "")))
            hecho |= m_sp

        # 5) cualquier otro texto: parser general, valor por valor
        m_otro = ~hecho
        if m_otro.any():
            otros = pd.to_datetime(u[m_otro], errors="coerce", format="mixed", dayfirst=dmy_default)
            res[m_otro] = otros.to_numpy(dtype="datetime64[ns]")

        # 6) rango válido
        anios = pd.Series(res).dt.year
        fuera = (~np.isnat(res)) & ~anios.between(AÑO_MIN_VALIDO, AÑO_MAX_VALIDO).to_numpy()
        res[fuera] = _NAT
    else:
        fuera = np.zeros(0, dtype=bool)

    out_arr = np.where(codes >= 0, res[np.maximum(codes, 0)] if n else _NAT, _NAT)
    out = pd.Series(out_arr.astype("datetime64[ns]"), index=s.index)

    stats = {
        "n": int(len(s)),
        "nulos_origen": int(raw.isna().sum()),
        "no_parseadas": int(w[np.isnat(res) & ~fuera].sum()) if n else 0,
        "fuera_rango": int(w[fuera].sum()) if n else 0,
        "dmy_inequivocas": int(w[amb == "dmy"].sum()) if n else 0,
        "mdy_inequivocas": int(w[amb == "mdy"].sum()) if n else 0,
        "ambiguas": int(w[amb == "amb"].sum()) if n else 0,
        "min": out.min(),
        "max": out.max(),
    }
    return out, stats


# =============================================================================
# 2. MAESTROS
# =============================================================================
@dataclass
class Retencion:
    hist: dict            # {"NO_VIDA": Series(clave -> pct), "VIDA": Series(clave -> pct)}
    xlsx_llave: pd.Series  # clave NUM_SINI+NUM_EXP+COD_COB (no repetidas) -> pct
    xlsx_concat: pd.Series  # clave + año de operación -> pct
    xlsx_repetidas: set
    stats: dict


@dataclass
class Maestros:
    ramos: pd.DataFrame
    atipicos: pd.DataFrame
    ret: Retencion


def elegir_columna(cols, opciones, obligatoria: bool = True):
    disponibles = {normalizar_nombre(c): c for c in cols}
    for op in opciones:
        if normalizar_nombre(op) in disponibles:
            return disponibles[normalizar_nombre(op)]
    if obligatoria:
        raise KeyError(f"Falta una columna. Alternativas aceptadas: {list(opciones)}; columnas: {list(cols)}")
    return None


def _separador_por_cabecera(ruta: Path) -> str:
    with ruta.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        linea = f.readline()
    conteos = {s: linea.count(s) for s in (";", ",", "\t", "|")}
    return max(conteos, key=conteos.get)


def _clave_hist(df: pd.DataFrame) -> pd.Series:
    partes = [normalizar_id(df[c]).fillna("") for c in CLAVE_RET_HIST]
    return partes[0] + partes[1] + partes[2] + partes[3]


def cargar_retencion_historica(ruta: Path) -> tuple[pd.Series, dict]:
    """txt histórico (MP o MPV) -> Series clave -> % de retención (ya dividido entre 100 y topado en 1).
    Si la clave se repite se conserva la primera aparición."""
    sep = _separador_por_cabecera(ruta)
    req = CLAVE_RET_HIST + ["PCT_REASEGURO"]
    lector = pd.read_csv(
        ruta, sep=sep, dtype=str, encoding="utf-8-sig", encoding_errors="replace",
        usecols=lambda c: normalizar_nombre(c) in req, chunksize=CHUNKSIZE_RET_HIST, low_memory=False,
    )
    partes = []
    for parte in lector:
        parte.columns = [normalizar_nombre(c) for c in parte.columns]
        faltan = [c for c in req if c not in parte.columns]
        if faltan:
            raise ValueError(f"{ruta.name}: faltan columnas {faltan}; separador detectado: {sep!r}")
        pct = numero(parte["PCT_REASEGURO"].astype("string").str.replace("%", "", regex=False)) / 100.0
        partes.append(pd.DataFrame({"clave": _clave_hist(parte), "pct": pct}).dropna())
    h = pd.concat(partes, ignore_index=True)

    dup = h.duplicated("clave", keep=False)
    conflictos = int(h[dup].groupby("clave")["pct"].nunique().gt(1).sum()) if dup.any() else 0
    stats = {
        "filas": int(len(h)),
        "claves_repetidas": int(h.loc[dup, "clave"].nunique()),
        "claves_con_conflicto": conflictos,
        "topadas_en_1": int((h["pct"] > 1).sum()),
        "negativas": int((h["pct"] < 0).sum()),
    }
    h["pct"] = h["pct"].clip(upper=1.0)
    h = h.drop_duplicates("clave", keep="first")
    return h.set_index("clave")["pct"], stats


def cargar_retencion_xlsx(ruta: Path):
    """Retención_2021_2026.xlsx -> (mapa por llave, mapa por llave+año, llaves repetidas, estadísticas)."""
    ret = pd.read_excel(ruta, dtype=str)
    ret.columns = [normalizar_nombre(c) for c in ret.columns]
    cs, ce, cc = (elegir_columna(ret.columns, [c]) for c in CLAVE_RET_XLSX)
    ca = elegir_columna(ret.columns, ["ANIO_OPER", "ANO_OPER"])
    cp = elegir_columna(ret.columns, ["PCT_RET", "PORC_RET", "PCT_RETENCION"])
    cr = elegir_columna(ret.columns, ["REPETICION"], obligatoria=False)
    for c in (cs, ce, cc, ca):
        ret[c] = normalizar_id(ret[c]).fillna("")
    ret["LLAVE"] = ret[cs] + ret[ce] + ret[cc]
    ret["CONCAT"] = ret["LLAVE"] + ret[ca]

    pct_txt = ret[cp].astype("string")
    es_pct = pct_txt.str.contains("%", regex=False, na=False)
    pct = numero(pct_txt.str.replace("%", "", regex=False))
    ret["PCT"] = pct.where(~es_pct, pct / 100.0)   # '20%' -> 0.20; 0.2 se queda como 0.2
    stats = {
        "filas": int(len(ret)),
        "pct_nulo": int(ret["PCT"].isna().sum()),
        "topadas_en_1": int((ret["PCT"] > 1).sum()),
        "negativas": int((ret["PCT"] < 0).sum()),
    }
    ret["PCT"] = ret["PCT"].clip(upper=1.0)
    ret = ret[ret["LLAVE"] != ""]

    repetidas = set(ret.loc[ret["LLAVE"].duplicated(keep=False), "LLAVE"])
    if cr:
        repetidas |= set(ret.loc[ret[cr].astype("string").str.upper().str.contains("REPET", na=False), "LLAVE"])
    con_pct = ret.dropna(subset=["PCT"])
    simples = con_pct[~con_pct["LLAVE"].isin(repetidas)]
    mapa_llave = simples.drop_duplicates("LLAVE", keep="last").set_index("LLAVE")["PCT"]

    dup = con_pct.duplicated("CONCAT", keep=False)
    stats["llaves_repetidas"] = len(repetidas)
    stats["claves_anio_con_conflicto"] = int(con_pct[dup].groupby("CONCAT")["PCT"].nunique().gt(1).sum()) if dup.any() else 0
    mapa_concat = con_pct.drop_duplicates("CONCAT", keep="last").set_index("CONCAT")["PCT"]
    return mapa_llave, mapa_concat, repetidas, stats


def cargar_maestros() -> Maestros:
    # Ramos y moneda
    ramos = pd.read_excel(RUTA_MAESTROS / "maestros.xlsx")
    ramos = ramos[["COD_RAMO", "SUBGRUPO", "NUEVO RAMO", "MONEDA"]].copy()
    ramos["COD_RAMO"] = normalizar_id(ramos["COD_RAMO"])
    ramos["SUBGRUPO"] = ramos["SUBGRUPO"].astype("string").str.strip()

    # Atípicos operativos (los de cuantía se calculan por percentil en marcar_atipicos_percentil)
    op = pd.read_excel(RUTA_MAESTROS / "Operativo.xlsx", dtype=str)
    op.columns = [normalizar_nombre(c) for c in op.columns]
    atipicos = (
        pd.DataFrame({"NUM_SINI": normalizar_id(op["NUM_SINI"])}).dropna().drop_duplicates()
        .assign(ATIPICOS="S", TIPO_ATIPICO="operativa")
    )

    # Retención histórica: MP = no vida, MPV = vida
    hist_nv, st_nv = cargar_retencion_historica(RUTA_MAESTROS / "Data_Historica_MP_202512.txt")
    hist_v, st_v = cargar_retencion_historica(RUTA_MAESTROS / "Data_Historica_MPV_202512.txt")
    # Retención 2021 en adelante
    llave, concat, repetidas, st_x = cargar_retencion_xlsx(RUTA_MAESTROS / "Retención_2021_2026.xlsx")

    stats = {"hist_no_vida": st_nv, "hist_vida": st_v, "xlsx": st_x}
    for nombre, st in stats.items():
        log.info("Retención %s: %s", nombre, st)
    ret = Retencion({"NO_VIDA": hist_nv, "VIDA": hist_v}, llave, concat, repetidas, stats)
    return Maestros(ramos, atipicos, ret)


def cargar_canal() -> pd.DataFrame:
    nov = pd.read_excel(RUTA_CANAL / "polizas_novida_CODCANAL3.xlsx")
    vid = pd.read_excel(RUTA_CANAL / "polizas_vida_CODCANAL3 1.xlsx")
    sct = pd.read_excel(RUTA_CANAL / "SCTR_CODCANAL3.xlsx")
    vid.columns = sct.columns = ["ID", "NUM_POLIZA", "CODCANAL3"]
    tot = pd.concat([nov, vid, sct], ignore_index=True)[["NUM_POLIZA", "CODCANAL3"]].drop_duplicates()
    tot["NUM_POLIZA"] = normalizar_id(tot["NUM_POLIZA"])
    dup = tot["NUM_POLIZA"].duplicated().sum()
    if dup:
        log.warning("Canal: %s pólizas con más de un canal; se conserva el primero", dup)
        tot = tot.drop_duplicates("NUM_POLIZA", keep="first")
    return tot


# =============================================================================
# 3. LECTURA Y CLASIFICACIÓN
# =============================================================================
def clasificar_archivo(nombre: str, excluir: tuple[str, ...] = EXCLUIR_EN_NOMBRE) -> str | None:
    if not (RUTA / nombre).is_file():
        return None
    if nombre.startswith(("~$", ".")) or Path(nombre).suffix.lower() in EXTENSIONES_IGNORADAS:
        return None
    if any(marca in nombre for marca in excluir):
        return None
    if MARCA_VIDA in nombre:
        return "vida"
    if MARCA_AUTOS in nombre:
        return "autos"
    return "general"


def inspeccionar_archivo(ruta: Path) -> tuple[str, bool]:
    """Detecta separador (el que da exactamente len(COLUMNAS) columnas) y si trae cabecera."""
    with ruta.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        primera = f.readline()
    n = len(COLUMNAS)
    conteos = {s: primera.count(s) for s in (";", ",", "\t", "|")}
    exactos = [s for s, c in conteos.items() if c == n - 1]
    if not exactos:
        mejor = max(conteos, key=conteos.get)
        raise ValueError(f"{ruta.name}: {conteos[mejor] + 1} columnas con separador {mejor!r}, se esperaban {n}")
    sep = exactos[0]

    celdas = [c.strip().strip('"') for c in primera.rstrip("\r\n").split(sep)]
    if len({normalizar_nombre(c) for c in celdas} & set(COLUMNAS)) >= 10:
        return sep, True
    # Sin nombres reconocibles: si PAGO_BRUTO de la primera fila no es un número, es una cabecera con otros nombres
    v = celdas[COLUMNAS.index("PAGO_BRUTO")].replace(",", ".")
    return sep, bool(pd.isna(pd.to_numeric(v, errors="coerce")))


def leer_archivo(ruta: Path) -> pd.DataFrame:
    """Lee el detalle: llaves y fechas como texto, montos numéricos (con control de lo no numérico)."""
    sep, cabecera = inspeccionar_archivo(ruta)
    df = pd.read_csv(
        ruta, sep=sep, header=0 if cabecera else None, names=COLUMNAS,
        dtype={c: str for c in COLS_ID + COLS_TXT + COLS_FECHA + COLS_NUM},
        encoding="utf-8-sig", encoding_errors="replace", low_memory=False,
    )
    for c in COLS_ID:
        df[c] = normalizar_id(df[c])
    for c in COLS_TXT:
        df[c] = df[c].astype("string").str.strip()
    no_num = pd.Series(False, index=df.index)
    for c in COLS_NUM:
        bruto = df[c].astype("string").str.strip()
        df[c] = numero(bruto)
        no_num |= df[c].isna() & bruto.notna() & bruto.ne("")
    df["FLAG_MONTO_NO_NUM"] = no_num
    return df


def dividir_autos(df: pd.DataFrame, etiqueta: str = "") -> tuple[dict[str, pd.DataFrame], dict]:
    """AUTOS 1/2 reciben los expedientes transferibles (TIP_EXP_TRANSFERIBLES) de
    AUTOS 3 de sus siniestros; AUTOS 3 se queda sin ellos.

    Cada fila de AUTOS 3 se asigna a UN solo subgrupo: si el siniestro está en
    AUTOS 1 y en AUTOS 2, va a AUTOS 1 (prioridad fija) y no se copia a AUTOS 2.
    Devuelve (partes, stats). stats trae los controles de reclasificación."""
    otros = set(df["SUBGRUPO"].dropna().unique()) - set(SUBGRUPOS_AUTOS)
    if otros:
        log.warning("Autos: subgrupos no procesados: %s", otros)

    a3 = df[df["SUBGRUPO"] == "AUTOS 3"]
    a3_transf = a3[a3["TIP_EXP"].isin(TIP_EXP_TRANSFERIBLES)]

    base1 = df[df["SUBGRUPO"] == "AUTOS 1"]
    base2 = df[df["SUBGRUPO"] == "AUTOS 2"]
    sini1 = set(base1["NUM_SINI"].dropna().unique())
    sini2 = set(base2["NUM_SINI"].dropna().unique())

    # Prioridad: AUTOS 1; el resto (solo en AUTOS 2) va a AUTOS 2.
    mask1 = a3_transf["NUM_SINI"].isin(sini1)
    mask2 = a3_transf["NUM_SINI"].isin(sini2) & ~mask1
    con1 = a3_transf[mask1].copy()
    con2 = a3_transf[mask2].copy()
    con1["SUBGRUPO"] = "AUTOS 1"
    con2["SUBGRUPO"] = "AUTOS 2"

    partes = {
        "AUTOS 1": pd.concat([base1, con1], ignore_index=True),
        "AUTOS 2": pd.concat([base2, con2], ignore_index=True),
    }
    quitadas = a3_transf.index[mask1 | mask2]
    partes["AUTOS 3"] = a3.drop(index=quitadas)

    # Controles: lo que habría pasado con la lógica anterior (copiar a ambos)
    en_ambos = a3_transf[mask1 & a3_transf["NUM_SINI"].isin(sini2)]
    sol_ambos = _sol_origen(en_ambos) if len(en_ambos) else 0.0
    n_partes = sum(len(p) for p in partes.values())
    stats = {
        "ARCHIVO": etiqueta,
        "FILAS_ORIGEN": len(df),
        "FILAS_TRAS_DIVIDIR": n_partes,
        "SOL_ORIGEN": _sol_origen(df),
        "SOL_TRAS_DIVIDIR": sum(_sol_origen(p) for p in partes.values()),
        "FILAS_A_AUTOS1": len(con1),
        "FILAS_A_AUTOS2": len(con2),
        "SINI_EN_AUTOS1_Y_2": len(sini1 & sini2 & set(a3_transf["NUM_SINI"].unique())),
        "FILAS_A3_EN_AMBOS": len(en_ambos),
        "SOL_A3_EN_AMBOS": sol_ambos,
        "SUBGRUPOS_NO_PROCESADOS": ", ".join(sorted(map(str, otros))),
        "FILAS_SUBGRUPOS_NO_PROCESADOS": int(df["SUBGRUPO"].isin(otros).sum()) if otros else 0,
    }
    if stats["SINI_EN_AUTOS1_Y_2"]:
        log.warning(
            "Autos: %s siniestros están en AUTOS 1 y AUTOS 2; sus filas de AUTOS 3 (%s filas, %s soles) "
            "se asignaron solo a AUTOS 1",
            stats["SINI_EN_AUTOS1_Y_2"], len(en_ambos), f"{sol_ambos:,.0f}",
        )
    log.info("AUTOS 1: %s filas transferidas desde AUTOS 3 | AUTOS 2: %s", len(con1), len(con2))
    return partes, stats


# =============================================================================
# 4. ENRIQUECIMIENTO Y CÁLCULO
# =============================================================================
def preparar_fechas(df: pd.DataFrame):
    """Devuelve (df con fechas y derivadas, estadísticas de calidad de fechas)."""
    df = df.copy()
    df["FEC_SINI"], st_s = parsear_fechas(df["FEC_SINI"])
    df["FEC_MVTO"], st_m = parsear_fechas(df["FEC_MVTO"])
    df["AÑO_SINI"] = df["FEC_SINI"].dt.year.astype("Int64")
    df["AÑO_MOV"] = df["FEC_MVTO"].dt.year.astype("Int64")
    df["AÑO_MES_OCU"] = (df["FEC_SINI"].dt.year * 100 + df["FEC_SINI"].dt.month).astype("Int64")
    df["AÑO_MES_MOV"] = (df["FEC_MVTO"].dt.year * 100 + df["FEC_MVTO"].dt.month).astype("Int64")
    df["dif"] = (
        (df["FEC_MVTO"].dt.year - df["FEC_SINI"].dt.year) * 12
        + (df["FEC_MVTO"].dt.month - df["FEC_SINI"].dt.month)
    ).astype("Int64")
    stats = {
        "FECHAS_NULAS_ORIGEN": st_s["nulos_origen"] + st_m["nulos_origen"],
        "FECHAS_NO_PARSEADAS": st_s["no_parseadas"] + st_m["no_parseadas"],
        "FECHAS_FUERA_RANGO": st_s["fuera_rango"] + st_m["fuera_rango"],
        "FECHAS_DMY_INEQUIVOCAS": st_s["dmy_inequivocas"] + st_m["dmy_inequivocas"],
        "FECHAS_MDY_INEQUIVOCAS": st_s["mdy_inequivocas"] + st_m["mdy_inequivocas"],
        "FECHAS_AMBIGUAS": st_s["ambiguas"] + st_m["ambiguas"],
        "FEC_SINI_MIN": st_s["min"], "FEC_SINI_MAX": st_s["max"],
        "FEC_MVTO_MIN": st_m["min"], "FEC_MVTO_MAX": st_m["max"],
    }
    return df, stats


def _es_essalud(df: pd.DataFrame) -> pd.Series:
    """Máscara de la póliza ESSALUD (NUM_POLIZA puede venir como texto)."""
    return pd.to_numeric(df["NUM_POLIZA"], errors="coerce") == POLIZA_ESSALUD


def asignar_retencion(df: pd.DataFrame, ret: Retencion, vida: bool) -> pd.DataFrame:
    """PCT_RET según la fecha de siniestro: <= RET_CORTE_HIST histórico, > RET_CORTE_HIST XLSX.
    No hay respaldo cruzado: si no está en su fuente, 1. ESSALUD pisa todo con RET_ESSALUD."""
    df = df.copy()
    n = len(df)
    fecha = df[RET_FECHA_BASE]
    ym = (fecha.dt.year * 100 + fecha.dt.month).to_numpy(dtype="float64")
    usar_hist = np.nan_to_num(ym, nan=np.inf) <= RET_CORTE_HIST
    usar_xlsx = np.nan_to_num(ym, nan=-np.inf) > RET_CORTE_HIST

    hist = ret.hist["VIDA" if vida else "NO_VIDA"]
    k_hist = _clave_hist(df)
    llave = df["NUM_SINI"].fillna("") + df["NUM_EXP"].fillna("") + df["COD_COB"].fillna("")
    anio = pd.to_numeric(df["PERIODO"], errors="coerce") // 100
    anio = anio.where(anio.notna(), df["FEC_MVTO"].dt.year)
    concat = llave + anio.astype("Int64").astype("string").fillna("")

    def buscar_hist(idx: np.ndarray) -> np.ndarray:
        return k_hist.iloc[idx].map(hist).to_numpy(dtype="float64")

    def buscar_xlsx(idx: np.ndarray) -> np.ndarray:
        lx = llave.iloc[idx]
        p = lx.map(ret.xlsx_llave)
        p = p.where(~lx.isin(ret.xlsx_repetidas), concat.iloc[idx].map(ret.xlsx_concat))
        return p.to_numpy(dtype="float64")

    pct = np.full(n, np.nan)
    otra = np.zeros(n, dtype=bool)
    ih, ix = np.flatnonzero(usar_hist), np.flatnonzero(usar_xlsx)
    if len(ih):
        pct[ih] = buscar_hist(ih)
        falta = ih[np.isnan(pct[ih])]
        if len(falta):
            otra[falta] = ~np.isnan(buscar_xlsx(falta))
    if len(ix):
        pct[ix] = buscar_xlsx(ix)
        falta = ix[np.isnan(pct[ix])]
        if len(falta):
            otra[falta] = ~np.isnan(buscar_hist(falta))

    df["RET_FUENTE"] = np.where(usar_hist, "HIST", np.where(usar_xlsx, "XLSX", "SIN_FECHA"))
    df["RET_ENCONTRADA"] = ~np.isnan(pct)
    df["RET_OTRA_FUENTE"] = otra
    df["PCT_RET"] = np.where(np.isnan(pct), 1.0, pct)

    es_essalud = _es_essalud(df).to_numpy()
    df.loc[es_essalud, "PCT_RET"] = RET_ESSALUD
    df.loc[es_essalud, "RET_FUENTE"] = "ESSALUD"
    df.loc[es_essalud, "RET_ENCONTRADA"] = True
    df.loc[es_essalud, "RET_OTRA_FUENTE"] = False
    df["RET_IMPUTADA"] = ~df["RET_ENCONTRADA"]
    return df


def enriquecer(df: pd.DataFrame, m: Maestros, vida: bool = False) -> pd.DataFrame:
    n0 = len(df)
    df = df.copy()
    df["TIPO_CAMBIO"] = TIPO_CAMBIO_FIJO
    df = df.merge(m.ramos, on=["COD_RAMO", "SUBGRUPO"], how="left", validate="many_to_one")
    df = df.merge(m.atipicos, on="NUM_SINI", how="left", validate="many_to_one")
    if len(df) != n0:
        raise RuntimeError(f"Los merges cambiaron el número de filas: {n0} -> {len(df)}")

    df["ATIPICOS"] = df["ATIPICOS"].astype(object).fillna("N")
    df["TIPO_ATIPICO"] = df["TIPO_ATIPICO"].astype(object)
    return asignar_retencion(df, m.ret, vida)


def calcular_incurrido(df: pd.DataFrame) -> pd.DataFrame:
    """Moneda por fila (no por archivo). Bruto y neto en SOL, DOL y moneda del ramo.
    Montos nulos cuentan como 0 (reserva y pago por separado)."""
    df = df.copy()
    bruto = df["RESERVA_BRUTO"].fillna(0.0) + df["PAGO_BRUTO"].fillna(0.0)
    tc = df["TIPO_CAMBIO"]

    df["INCURRIDO_SOL"] = bruto * np.where(df["COD_MON"] == 1, 1, tc)
    df["INCURRIDO_DOL"] = bruto / np.where(df["COD_MON"] == 2, 1, tc)
    df["INCURRIDO_SOL_NETO"] = df["INCURRIDO_SOL"] * df["PCT_RET"]
    df["INCURRIDO_DOL_NETO"] = df["INCURRIDO_DOL"] * df["PCT_RET"]

    es_pen = df["MONEDA"].eq("PEN")
    df["INCURRIDO_MON"] = np.where(es_pen, df["INCURRIDO_SOL"], df["INCURRIDO_DOL"])
    df["INCURRIDO_MON_NETO"] = np.where(es_pen, df["INCURRIDO_SOL_NETO"], df["INCURRIDO_DOL_NETO"])
    return df


def _sol_origen(df_raw: pd.DataFrame) -> float:
    """Incurrido en soles recalculado directamente del archivo (control independiente)."""
    bruto = df_raw["RESERVA_BRUTO"].fillna(0.0) + df_raw["PAGO_BRUTO"].fillna(0.0)
    return float((bruto * np.where(df_raw["COD_MON"] == 1, 1.0, TIPO_CAMBIO_FIJO)).sum())


def marcar_atipicos_percentil(
    df: pd.DataFrame,
    etiqueta: str,
    q_inf: float = PERCENTIL_INF,
    q_sup: float = PERCENTIL_SUP,
    por_ramo: bool = False,
):
    """Suma el incurrido por siniestro y marca como atípicos (ATIPICOS='S') los
    siniestros > percentil q_sup o < percentil q_inf (desigualdad estricta).
    por_ramo=False: los percentiles se calculan sobre todo el archivo.
    por_ramo=True : se calculan por NUEVO RAMO (con el ramo tal como está en `df`).
    Devuelve (df marcado, detalle por siniestro, estadísticas)."""
    claves = ["NUEVO RAMO", "NUM_SINI"] if por_ramo else ["NUM_SINI"]
    s = (
        df.groupby(claves)[COL_ATIPICO_PERCENTIL].sum().dropna()
        .rename("INCURRIDO").reset_index()
    )
    if s.empty:
        return df, pd.DataFrame(), {}

    if por_ramo:
        q = s.groupby("NUEVO RAMO")["INCURRIDO"].quantile([q_inf, q_sup]).unstack()
        q.columns = ["P_INF", "P_SUP"]
        s = s.merge(q, left_on="NUEVO RAMO", right_index=True, how="left")
    else:
        s["P_INF"], s["P_SUP"] = s["INCURRIDO"].quantile([q_inf, q_sup]).tolist()

    s["TIPO_ATIPICO"] = np.select(
        [s["INCURRIDO"] > s["P_SUP"], s["INCURRIDO"] < s["P_INF"]],
        ["percentil_sup", "percentil_inf"],
        default="",
    )
    marc = s[s["TIPO_ATIPICO"] != ""]

    ya_maestro = set(df.loc[df["ATIPICOS"] == "S", "NUM_SINI"].unique())
    detalle = marc[claves + ["TIPO_ATIPICO", "INCURRIDO", "P_INF", "P_SUP"]].copy()
    detalle["YA_ATIPICO_MAESTRO"] = detalle["NUM_SINI"].isin(ya_maestro)
    detalle["ARCHIVO"] = etiqueta

    # Marca a nivel fila (todas las filas del siniestro heredan el tipo)
    df = df.copy()
    df["TIPO_ATIPICO"] = df["TIPO_ATIPICO"].astype(object)
    cruce = df[claves].merge(
        marc[claves + ["TIPO_ATIPICO"]].rename(columns={"TIPO_ATIPICO": "_T"}),
        on=claves, how="left",
    )
    nuevo_tipo = pd.Series(cruce["_T"].to_numpy(), index=df.index)
    nuevo = nuevo_tipo.notna() & (df["ATIPICOS"] == "N")
    df.loc[nuevo, "TIPO_ATIPICO"] = nuevo_tipo[nuevo]
    df.loc[nuevo, "ATIPICOS"] = "S"

    total = s["INCURRIDO"].sum()
    stats = {
        "N_SINI": len(s),
        "N_ATIP_SUP": int((s["TIPO_ATIPICO"] == "percentil_sup").sum()),
        "N_ATIP_INF": int((s["TIPO_ATIPICO"] == "percentil_inf").sum()),
        "PCT_MONTO_ATIP_PERCENTIL": float(marc["INCURRIDO"].sum() / total) if total else np.nan,
    }
    if por_ramo:
        for ramo, g in s.groupby("NUEVO RAMO"):
            log.info("%s | %s | atípicos percentil: %s sup (>%.0f), %s inf (<%.0f) de %s siniestros",
                     etiqueta, ramo, int((g["TIPO_ATIPICO"] == "percentil_sup").sum()), g["P_SUP"].iloc[0],
                     int((g["TIPO_ATIPICO"] == "percentil_inf").sum()), g["P_INF"].iloc[0], len(g))
    else:
        stats["P_INF"], stats["P_SUP"] = float(s["P_INF"].iloc[0]), float(s["P_SUP"].iloc[0])
        log.info("%s | atípicos percentil: %s sup (>%.0f), %s inf (<%.0f) de %s siniestros",
                 etiqueta, stats["N_ATIP_SUP"], stats["P_SUP"], stats["N_ATIP_INF"], stats["P_INF"], stats["N_SINI"])
    return df, detalle, stats


# =============================================================================
# 5. VALIDACIÓN POR FILA
# =============================================================================
def detectar_errores(df: pd.DataFrame, etiqueta: str) -> pd.DataFrame:
    reglas = {
        "FECHA_NULA": df["FEC_SINI"].isna() | df["FEC_MVTO"].isna(),
        "DIF_NEGATIVO": (df["dif"] < 0).fillna(False).astype(bool),
        "SIN_RAMO_MAESTRO": df["NUEVO RAMO"].isna(),
        "SIN_MONEDA_MAESTRO": df["MONEDA"].isna(),
        "COD_MON_INVALIDO": ~df["COD_MON"].isin([1, 2]),
        "MONTO_NO_NUMERICO": df["FLAG_MONTO_NO_NUM"],
    }
    partes = [df[mask].assign(MOTIVO=motivo, ARCHIVO=etiqueta) for motivo, mask in reglas.items() if mask.any()]
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


# =============================================================================
# 6. PROCESO POR ARCHIVO
# =============================================================================
def agrupar_base(df: pd.DataFrame, extra: tuple[str, ...] = ()) -> pd.DataFrame:
    claves = CLAVES_BASE + list(extra)
    return df.groupby(claves, as_index=False, dropna=False)[COLS_MONTOS].sum()


def procesar(df_raw: pd.DataFrame, m: Maestros, etiqueta: str, vida: bool = False):
    """Devuelve (base, errores, cuadre, detalle de atípicos por percentil)."""
    log.info("Procesando %s (%s filas)", etiqueta, len(df_raw))
    n_raw = len(df_raw)
    bruto_raw = float((df_raw["RESERVA_BRUTO"].fillna(0.0) + df_raw["PAGO_BRUTO"].fillna(0.0)).sum())
    sol_origen = _sol_origen(df_raw)
    montos_nulos = int((df_raw["RESERVA_BRUTO"].isna() | df_raw["PAGO_BRUTO"].isna()).sum())
    montos_no_num = int(df_raw["FLAG_MONTO_NO_NUM"].sum())

    df, st_fechas = preparar_fechas(df_raw)
    df = enriquecer(df, m, vida)
    df = calcular_incurrido(df)
    bruto_enr = float((df["RESERVA_BRUTO"].fillna(0.0) + df["PAGO_BRUTO"].fillna(0.0)).sum())
    if not np.isclose(bruto_raw, bruto_enr):
        log.warning("%s: bruto origen %.2f != bruto enriquecido %.2f", etiqueta, bruto_raw, bruto_enr)

    filas_eliminadas, sol_eliminado = 0, 0.0
    detalle_atip, stats_atip = pd.DataFrame(), {}
    if vida:
        # ELIMINAR se quita primero (salvo ESSALUD, que se conserva como ESSALUD-ACC)
        mask = df["NUEVO RAMO"].eq("ELIMINAR") & ~_es_essalud(df)
        filas_eliminadas = int(mask.sum())
        sol_eliminado = float(df.loc[mask, "INCURRIDO_SOL"].sum())
        df = df[~mask]
        # Atípicos de vida: por NUEVO RAMO del maestro, ANTES de separar ESSALUD-ACC
        df, detalle_atip, stats_atip = marcar_atipicos_percentil(
            df, etiqueta, PERCENTIL_INF_VIDA, PERCENTIL_SUP_VIDA, por_ramo=True
        )
        df["NUEVO RAMO"] = np.where(_es_essalud(df), "ESSALUD-ACC", df["NUEVO RAMO"])
    else:
        df, detalle_atip, stats_atip = marcar_atipicos_percentil(
            df, etiqueta, PERCENTIL_INF, PERCENTIL_SUP, por_ramo=ATIPICOS_NV_POR_RAMO
        )

    errores = detectar_errores(df, etiqueta)
    extra = ("FEC_SINI", "FEC_MVTO") if vida else ()
    base = agrupar_base(df, extra)

    sol_df, sol_base = float(df["INCURRIDO_SOL"].sum()), float(base["INCURRIDO_SOL"].sum())
    if not np.isclose(sol_df, sol_base):
        log.warning("%s: la base no cuadra con el detalle (%.2f vs %.2f)", etiqueta, sol_df, sol_base)
    if not np.isclose(sol_origen - sol_eliminado, sol_base):
        log.warning("%s: la base no cuadra con el archivo original (%.2f vs %.2f)", etiqueta, sol_origen - sol_eliminado, sol_base)

    fuente = df["RET_FUENTE"]
    cuadre = {
        "ARCHIVO": etiqueta,
        "FILAS_ORIGEN": n_raw,
        "FILAS_ELIMINADAS": filas_eliminadas,
        "BRUTO_ORIGEN": bruto_raw,
        "BRUTO_ENRIQUECIDO": bruto_enr,
        "SOL_ORIGEN": sol_origen,
        "SOL_ELIMINADO": sol_eliminado,
        "SOL_DETALLE": sol_df,
        "SOL_BASE": sol_base,
        "SIN_RAMO": int(df["NUEVO RAMO"].isna().sum()),
        "DIF_NEGATIVOS": int((df["dif"] < 0).sum()),
        "COD_MON_INVALIDO": int((~df["COD_MON"].isin([1, 2])).sum()),
        "MONTOS_NULOS": montos_nulos,
        "MONTOS_NO_NUMERICOS": montos_no_num,
        **st_fechas,
        "RET_FILAS_HIST": int((fuente == "HIST").sum()),
        "RET_HIST_ENCONTRADA": int(((fuente == "HIST") & df["RET_ENCONTRADA"]).sum()),
        "RET_FILAS_XLSX": int((fuente == "XLSX").sum()),
        "RET_XLSX_ENCONTRADA": int(((fuente == "XLSX") & df["RET_ENCONTRADA"]).sum()),
        "RET_SIN_FECHA": int((fuente == "SIN_FECHA").sum()),
        "RETENCION_IMPUTADA_1": int(df["RET_IMPUTADA"].sum()),
        "RET_OTRA_FUENTE": int(df["RET_OTRA_FUENTE"].sum()),
        "FILAS_ESSALUD_RET_20": int(_es_essalud(df).sum()),
        **stats_atip,
    }
    log.info("%s | sin ramo=%s | dif<0=%s | fechas no leídas=%s | ret imputada=%s (otra fuente=%s)",
             etiqueta, cuadre["SIN_RAMO"], cuadre["DIF_NEGATIVOS"], cuadre["FECHAS_NO_PARSEADAS"],
             cuadre["RETENCION_IMPUTADA_1"], cuadre["RET_OTRA_FUENTE"])
    return base, errores, cuadre, detalle_atip


# =============================================================================
# 7. TRIÁNGULOS
# =============================================================================
def construir_triangulos(base: pd.DataFrame, col: str = "INCURRIDO_MON_NETO",
                         excluir_atipicos: bool | None = None, silencioso: bool = False) -> dict[str, pd.DataFrame]:
    """Un triángulo (incremental) por NUEVO RAMO, siempre cuadrado (a x a):
    filas = meses de ocurrencia desde DESDE_OCURRENCIA hasta MES_CORTE,
    columnas = desarrollos 0 .. a-1.
    excluir_atipicos=None usa EXCLUIR_ATIPICOS_TRIANGULO; False arma el triángulo INICIAL (con atípicos)."""
    if excluir_atipicos is None:
        excluir_atipicos = EXCLUIR_ATIPICOS_TRIANGULO
    b = base.dropna(subset=["NUEVO RAMO", "AÑO_MES_OCU", "dif"])
    if len(b) < len(base) and not silencioso:
        log.warning("Triángulos: %s filas de la base sin ramo/fecha quedan fuera", len(base) - len(b))
    if excluir_atipicos:
        b = b[b["ATIPICOS"] == "N"]
    if DESDE_OCURRENCIA is not None:
        b = b[b["AÑO_MES_OCU"] >= DESDE_OCURRENCIA]
    if TRATO_DIF_NEGATIVO == "excluir":
        b = b[b["dif"] >= 0]
    else:  # "a_cero"
        b = b.assign(dif=b["dif"].clip(lower=0))

    triangulos: dict[str, pd.DataFrame] = {}
    for ramo, g in b.groupby("NUEVO RAMO"):
        t = g.pivot_table(index="AÑO_MES_OCU", columns="dif", values=col, aggfunc="sum", fill_value=0)
        t.index = t.index.astype(int)
        t.columns = t.columns.astype(int)
        t = t.sort_index().sort_index(axis=1)

        inicio = DESDE_OCURRENCIA if DESDE_OCURRENCIA is not None else t.index.min()
        f_min = pd.to_datetime(str(inicio), format="%Y%m")
        fin = max(MES_CORTE, int(t.index.max())) if MES_CORTE is not None else int(t.index.max())
        f_max = pd.to_datetime(str(fin), format="%Y%m")
        meses = pd.date_range(f_min, f_max, freq="MS").strftime("%Y%m").astype(int).tolist()
        devs = list(range(len(meses)))  # cuadrado: nº de desarrollos = nº de meses

        t = t.reindex(index=meses, columns=devs).fillna(0)
        t.index.name = "AÑO_MES_OCU"
        t.columns.name = "dif"

        if not silencioso and not np.isclose(t.values.sum(), g[col].sum()):
            log.warning("Triángulo %s no cuadra con la base (movimientos fuera del cuadrado: posteriores al corte?)", ramo)
        triangulos[ramo] = t
    return triangulos


# =============================================================================
# 8. EXPORTACIÓN
# =============================================================================
def exportar_tabla(df: pd.DataFrame, nombre: str) -> None:
    if len(df) >= 1_000_000:  # límite de Excel
        ruta = RUTA_OUTPUT / f"{nombre}.csv"
        df.to_csv(ruta, index=False)
    else:
        ruta = RUTA_OUTPUT / f"{nombre}.xlsx"
        df.to_excel(ruta, index=False)
    log.info("Exportado %s (%s filas)", ruta.name, len(df))


def _hoja_unica(texto: str, usados: set[str]) -> str:
    limpio = re.sub(r"[\[\]:*?/\\]", "_", str(texto)) or "SIN_NOMBRE"
    hoja = limpio[:31]
    if len(limpio) > 31:
        log.warning("Nombre de hoja truncado a 31 caracteres: %s", texto)
    k = 1
    while hoja.lower() in usados:
        k += 1
        hoja = f"{limpio[:28]}_{k}"
    usados.add(hoja.lower())
    return hoja


def exportar_triangulos_excel(triangulos: dict[str, pd.DataFrame], nombre: str) -> Path | None:
    """Un Excel con una hoja por ramo (hoja = nombre del NUEVO RAMO).
    Fila 1 = encabezados (A1 = AÑO_MES_OCU, B1.. = desarrollo); índices desde A2, valores desde B2."""
    if not triangulos:
        return None
    RUTA_TRIANGULOS.mkdir(parents=True, exist_ok=True)
    ruta = RUTA_TRIANGULOS / f"{nombre}.xlsx"
    usados: set[str] = set()
    with pd.ExcelWriter(ruta, engine="openpyxl") as xw:
        for ramo, t in triangulos.items():
            out = t.cumsum(axis=1) if TRIANGULO_ACUMULADO else t.copy()
            if TRIANGULO_FUTURO_VACIO:
                n = len(out)
                futuro = np.add.outer(np.arange(n), out.columns.to_numpy(dtype=int)) > n - 1
                out = out.mask(futuro)
            out.index.name = "AÑO_MES_OCU"
            out.columns.name = None
            out.to_excel(xw, sheet_name=_hoja_unica(ramo, usados))
    log.info("Exportado %s (%s hojas)", ruta, len(triangulos))
    return ruta


# =============================================================================
# 9. PROCESOS OPCIONALES
# =============================================================================
def extraer_polizas() -> None:
    """Listas de pólizas únicas (vida / no vida) para pedir COD_CANAL."""
    idx = COLUMNAS.index("NUM_POLIZA")
    no_vida, vida = [], []
    for nombre in sorted(os.listdir(RUTA)):
        tipo = clasificar_archivo(nombre, excluir=("_28_", "_207_"))
        if tipo is None:
            continue
        sep, cabecera = inspeccionar_archivo(RUTA / nombre)
        s = pd.read_csv(RUTA / nombre, sep=sep, header=0 if cabecera else None, usecols=[idx], dtype=str,
                        encoding="utf-8-sig", encoding_errors="replace").iloc[:, 0]
        (vida if tipo == "vida" else no_vida).extend(normalizar_id(s).dropna().unique())
    pd.DataFrame({"NUM_POLIZA": list(dict.fromkeys(no_vida))}).to_excel(RUTA_OUTPUT / "polizas_novida.xlsx", index=False)
    pd.DataFrame({"NUM_POLIZA": list(dict.fromkeys(vida))}).to_excel(RUTA_OUTPUT / "polizas_vida.xlsx", index=False)


def incurrido_por_canal(m: Maestros) -> None:
    canal = cargar_canal()
    partes = []
    for nombre in sorted(os.listdir(RUTA)):
        tipo = clasificar_archivo(nombre, excluir=("207", "_28_"))
        if tipo is None:
            continue
        df, _ = preparar_fechas(leer_archivo(RUTA / nombre))
        df = calcular_incurrido(enriquecer(df, m, vida=(tipo == "vida")))
        df = df.merge(canal, on="NUM_POLIZA", how="left", validate="many_to_one")
        partes.append(
            df.groupby(["NUEVO RAMO", "AÑO_MES_OCU", "CODCANAL3"], as_index=False, dropna=False)[COLS_MONTOS].sum()
        )
    inc = pd.concat(partes, ignore_index=True)
    inc = inc[inc["AÑO_MES_OCU"] >= DESDE_CANAL]
    res = inc.groupby(["NUEVO RAMO", "CODCANAL3"], as_index=False, dropna=False)[COLS_MONTOS].sum()
    exportar_tabla(res, f"incurrido_canal_{SUFIJO}")


# =============================================================================
# 10. VALIDACIÓN DE TRIÁNGULOS: INICIAL vs FINAL, BRUTO Y NETO
# =============================================================================
def _puente(base: pd.DataFrame, col: str, finales: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Puente por NUEVO RAMO (en miles de la moneda del ramo):
    TOTAL_BASE -> FUERA_VENTANA -> INICIAL -> atípicos -> FINAL_ESPERADO  vs  FINAL_TRIANGULO (suma de celdas)."""
    b = base.copy()
    b["NUEVO RAMO"] = b["NUEVO RAMO"].fillna("(SIN RAMO)")
    ocu = b["AÑO_MES_OCU"]
    en_vent = ocu.notna() if DESDE_OCURRENCIA is None else (ocu >= DESDE_OCURRENCIA).fillna(False)
    en_vent = en_vent.astype(bool)
    atip = b["ATIPICOS"].eq("S")
    tipo = b["TIPO_ATIPICO"]
    excl = EXCLUIR_ATIPICOS_TRIANGULO

    def suma(mask: pd.Series) -> pd.Series:
        return b.loc[mask].groupby("NUEVO RAMO")[col].sum()

    t = pd.DataFrame({
        "TOTAL_BASE": b.groupby("NUEVO RAMO")[col].sum(),
        "FUERA_VENTANA": suma(~en_vent),
        "INICIAL": suma(en_vent),
        "ATIP_OPERATIVOS": suma(en_vent & atip & tipo.eq("operativa")),
        "ATIP_PERCENTIL_SUP": suma(en_vent & atip & tipo.eq("percentil_sup")),
        "ATIP_PERCENTIL_INF": suma(en_vent & atip & tipo.eq("percentil_inf")),
        "ATIP_OTROS": suma(en_vent & atip & ~tipo.isin(["operativa", "percentil_sup", "percentil_inf"])),
    }).fillna(0.0)
    if TRATO_DIF_NEGATIVO == "excluir":
        quedan = en_vent & ~(atip & excl) & (b["dif"] < 0).fillna(False).astype(bool)
        t["DIF_NEG_EXCLUIDO"] = suma(quedan).reindex(t.index).fillna(0.0)
    else:
        t["DIF_NEG_EXCLUIDO"] = 0.0
    atip_total = t[["ATIP_OPERATIVOS", "ATIP_PERCENTIL_SUP", "ATIP_PERCENTIL_INF", "ATIP_OTROS"]].sum(axis=1)
    t["FINAL_ESPERADO"] = t["INICIAL"] - (atip_total if excl else 0.0) - t["DIF_NEG_EXCLUIDO"]
    t["FINAL_TRIANGULO"] = pd.Series({r: float(tri.values.sum()) for r, tri in finales.items()}).reindex(t.index).fillna(0.0)
    t["DIFERENCIA"] = t["FINAL_ESPERADO"] - t["FINAL_TRIANGULO"]
    t = t / 1000.0
    total = t.sum().to_frame("TOTAL").T
    t = pd.concat([t, total]).rename_axis("NUEVO RAMO")
    return t.reset_index()


def validacion_triangulos(res: dict) -> dict:
    """Por segmento ('nv', 'v'): puentes bruto/neto y, por ramo, los triángulos
    inicial / final / diferencia en bruto y neto."""
    out: dict = {}
    for k in ("nv", "v"):
        base = res[f"base_{k}"]
        if base.empty:
            continue
        ini_b = construir_triangulos(base, COL_TRIANGULO_BRUTO, excluir_atipicos=False, silencioso=True)
        ini_n = construir_triangulos(base, COL_TRIANGULO_NETO, excluir_atipicos=False, silencioso=True)
        fin_b, fin_n = res[f"tri_{k}_bruto"], res[f"tri_{k}_neto"]
        tri = {}
        for ramo in ini_b:
            vacio = ini_b[ramo] * 0.0
            fb, fn = fin_b.get(ramo, vacio), fin_n.get(ramo, vacio)
            tri[ramo] = {
                "inicial_bruto": ini_b[ramo], "final_bruto": fb, "diferencia_bruto": ini_b[ramo].sub(fb, fill_value=0.0),
                "inicial_neto": ini_n[ramo], "final_neto": fn, "diferencia_neto": ini_n[ramo].sub(fn, fill_value=0.0),
            }
        out[k] = {
            "puente_bruto": _puente(base, COL_TRIANGULO_BRUTO, fin_b),
            "puente_neto": _puente(base, COL_TRIANGULO_NETO, fin_n),
            "triangulos": tri,
        }
    return out


def exportar_validacion(res: dict, val: dict | None = None) -> Path | None:
    """validacion_triangulos_<SUFIJO>.xlsx en RUTA_OUTPUT: hoja PUENTE y una hoja por ramo con los
    triángulos inicial, final y diferencia (bruto y neto), uno debajo de otro."""
    val = val or validacion_triangulos(res)
    if not val:
        return None
    RUTA_OUTPUT.mkdir(parents=True, exist_ok=True)
    ruta = RUTA_OUTPUT / f"validacion_triangulos_{SUFIJO}.xlsx"
    nombres = {"nv": "NO VIDA", "v": "VIDA"}
    titulos = {
        "inicial_bruto": "INICIAL BRUTO (todo el incurrido, con atípicos)", "final_bruto": "FINAL BRUTO (el que va al Excel)",
        "diferencia_bruto": "DIFERENCIA BRUTO (inicial - final = lo excluido)",
        "inicial_neto": "INICIAL NETO (todo el incurrido, con atípicos)", "final_neto": "FINAL NETO (el que va al Excel)",
        "diferencia_neto": "DIFERENCIA NETO (inicial - final = lo excluido)",
    }
    usados: set[str] = {"puente"}
    with pd.ExcelWriter(ruta, engine="openpyxl") as xw:
        fila = 0
        for k, v in val.items():
            for tipo in ("bruto", "neto"):
                pd.DataFrame([[f"PUENTE {tipo.upper()} - {nombres[k]} (miles de la moneda del ramo)"]]).to_excel(
                    xw, sheet_name="PUENTE", startrow=fila, header=False, index=False)
                v[f"puente_{tipo}"].to_excel(xw, sheet_name="PUENTE", startrow=fila + 1, index=False)
                fila += len(v[f"puente_{tipo}"]) + 4
        for k, v in val.items():
            for ramo, tris in v["triangulos"].items():
                hoja = _hoja_unica(f"{ramo}" if k == "nv" else f"{ramo} (V)", usados)
                fila = 0
                for clave in ("inicial_bruto", "final_bruto", "diferencia_bruto", "inicial_neto", "final_neto", "diferencia_neto"):
                    pd.DataFrame([[titulos[clave]]]).to_excel(xw, sheet_name=hoja, startrow=fila, header=False, index=False)
                    tris[clave].to_excel(xw, sheet_name=hoja, startrow=fila + 1)
                    fila += len(tris[clave]) + 4
    log.info("Exportado %s", ruta.name)
    return ruta


def ver_triangulos(res: dict, ramo: str, segmento: str = "nv", filas: int = 6, cols: int = 8) -> None:
    """Imprime las esquinas del triángulo INICIAL y FINAL (bruto y neto) de un ramo:
    primeras y últimas `filas` filas, primeras `cols` columnas."""
    base = res[f"base_{segmento}"]
    b = base[base["NUEVO RAMO"] == ramo]
    if b.empty:
        raise KeyError(f"No hay datos para el ramo {ramo!r} en el segmento {segmento!r}")
    for tipo, col in (("BRUTO", COL_TRIANGULO_BRUTO), ("NETO", COL_TRIANGULO_NETO)):
        ini = construir_triangulos(b, col, excluir_atipicos=False, silencioso=True).get(ramo)
        fin = construir_triangulos(b, col, silencioso=True).get(ramo)
        for nombre, t in (("INICIAL", ini), ("FINAL", fin)):
            if t is None:
                print(f"\n=== {ramo} | {nombre} {tipo}: vacío ===")
                continue
            print(f"\n=== {ramo} | {nombre} {tipo} | {t.shape[0]}x{t.shape[1]} | total {t.values.sum():,.0f} ===")
            print(t.iloc[:filas, :cols].round(0).to_string())
            print("   ...")
            print(t.iloc[-filas:, :cols].round(0).to_string(header=False))


# =============================================================================
# 11. REVISIÓN ACTUARIAL (¿tienen sentido los outputs?)
# =============================================================================
def resumen_anual(base: pd.DataFrame) -> pd.DataFrame:
    """Incurrido bruto/neto en miles de USD por ramo y año de ocurrencia, con retención
    implícita y peso de atípicos."""
    k = ["NUEVO RAMO", "AÑO_SINI"]
    g = base.groupby(k, as_index=False, dropna=False)[["INCURRIDO_DOL", "INCURRIDO_DOL_NETO"]].sum()
    at = (
        base[base["ATIPICOS"] == "S"].groupby(k, dropna=False)["INCURRIDO_DOL"].sum().rename("DOL_ATIPICO").reset_index()
    )
    g = g.merge(at, on=k, how="left").fillna({"DOL_ATIPICO": 0.0})
    bruto = g["INCURRIDO_DOL"].where(g["INCURRIDO_DOL"] != 0)
    g["RET_IMPLICITA"] = g["INCURRIDO_DOL_NETO"] / bruto
    g["PCT_ATIPICO"] = g["DOL_ATIPICO"] / bruto
    for c in ["INCURRIDO_DOL", "INCURRIDO_DOL_NETO", "DOL_ATIPICO"]:
        g[c + "_MILES"] = g[c] / 1000
    return g.drop(columns=["INCURRIDO_DOL", "INCURRIDO_DOL_NETO", "DOL_ATIPICO"])


def peso_atipicos(base: pd.DataFrame) -> pd.DataFrame:
    """Peso de los atípicos sobre el total, por NUEVO RAMO + TOTAL (misma ventana que los triángulos).
    Siniestros: cantidad y % . Montos: bruto y neto en miles de USD y % (con signo), y el bruto
    atípico separado por tipo (operativa / percentil_sup / percentil_inf)."""
    b = base
    if DESDE_OCURRENCIA is not None:
        b = b[b["AÑO_MES_OCU"] >= DESDE_OCURRENCIA]
    b = b.assign(**{"NUEVO RAMO": b["NUEVO RAMO"].fillna("(SIN RAMO)")})
    es_at = b["ATIPICOS"] == "S"

    def agrupar(d: pd.DataFrame, a: pd.Series) -> pd.DataFrame:
        r = pd.DataFrame({
            "N_SINIESTROS": d.groupby("NUEVO RAMO")["NUM_SINI"].nunique(),
            "N_SINIESTROS_ATIP": d[a].groupby("NUEVO RAMO")["NUM_SINI"].nunique(),
            "BRUTO_TOTAL_MILES": d.groupby("NUEVO RAMO")["INCURRIDO_DOL"].sum() / 1000,
            "BRUTO_ATIP_MILES": d[a].groupby("NUEVO RAMO")["INCURRIDO_DOL"].sum() / 1000,
            "NETO_TOTAL_MILES": d.groupby("NUEVO RAMO")["INCURRIDO_DOL_NETO"].sum() / 1000,
            "NETO_ATIP_MILES": d[a].groupby("NUEVO RAMO")["INCURRIDO_DOL_NETO"].sum() / 1000,
        })
        for t in ("operativa", "percentil_sup", "percentil_inf"):
            r[f"BRUTO_ATIP_{t.upper()}_MILES"] = (
                d[a & (d["TIPO_ATIPICO"] == t)].groupby("NUEVO RAMO")["INCURRIDO_DOL"].sum() / 1000
            )
        return r.fillna(0.0)

    r = agrupar(b, es_at)
    r.loc["TOTAL"] = r.sum()
    # los siniestros no se suman entre ramos si un NUM_SINI se repite; el TOTAL usa únicos reales
    r.loc["TOTAL", "N_SINIESTROS"] = b["NUM_SINI"].nunique()
    r.loc["TOTAL", "N_SINIESTROS_ATIP"] = b.loc[es_at, "NUM_SINI"].nunique()
    r = r.reset_index().rename(columns={"index": "NUEVO RAMO"})
    r["PCT_SINIESTROS_ATIP"] = r["N_SINIESTROS_ATIP"] / r["N_SINIESTROS"].where(r["N_SINIESTROS"] != 0)
    r["PCT_BRUTO_ATIP"] = r["BRUTO_ATIP_MILES"] / r["BRUTO_TOTAL_MILES"].where(r["BRUTO_TOTAL_MILES"] != 0)
    r["PCT_NETO_ATIP"] = r["NETO_ATIP_MILES"] / r["NETO_TOTAL_MILES"].where(r["NETO_TOTAL_MILES"] != 0)
    orden = ["NUEVO RAMO", "N_SINIESTROS", "N_SINIESTROS_ATIP", "PCT_SINIESTROS_ATIP",
             "BRUTO_TOTAL_MILES", "BRUTO_ATIP_MILES", "PCT_BRUTO_ATIP",
             "NETO_TOTAL_MILES", "NETO_ATIP_MILES", "PCT_NETO_ATIP"]
    return r[orden + [c for c in r.columns if c not in orden]]


def ratio_retencion(base: pd.DataFrame, por_anio: bool = False) -> pd.DataFrame:
    """Ratio de retención (neto / bruto, en USD) por NUEVO RAMO, con y sin atípicos.
    Usa la misma ventana que los triángulos (AÑO_MES_OCU >= DESDE_OCURRENCIA).
    DIF_PUNTOS = ratio sin atípicos - ratio con atípicos (en puntos de ratio).
    Con por_anio=True desagrega además por AÑO_SINI."""
    b = base
    if DESDE_OCURRENCIA is not None:
        b = b[b["AÑO_MES_OCU"] >= DESDE_OCURRENCIA]
    claves = ["NUEVO RAMO"] + (["AÑO_SINI"] if por_anio else [])

    def sumar(d: pd.DataFrame, suf: str) -> pd.DataFrame:
        g = d.groupby(claves, dropna=False)[["INCURRIDO_DOL", "INCURRIDO_DOL_NETO"]].sum() / 1000
        g.columns = [f"BRUTO_{suf}_MILES", f"NETO_{suf}_MILES"]
        return g

    r = sumar(b, "CON_ATIP").join(sumar(b[b["ATIPICOS"] == "N"], "SIN_ATIP"), how="left").reset_index()
    if not por_anio:
        tot = r.drop(columns=["NUEVO RAMO"]).sum(numeric_only=True).to_frame().T
        tot.insert(0, "NUEVO RAMO", "TOTAL")
        r = pd.concat([r, tot], ignore_index=True)

    for suf in ("CON_ATIP", "SIN_ATIP"):
        bruto = r[f"BRUTO_{suf}_MILES"].where(r[f"BRUTO_{suf}_MILES"] != 0)
        r[f"RATIO_RET_{suf}"] = r[f"NETO_{suf}_MILES"] / bruto
    r["DIF_PUNTOS"] = r["RATIO_RET_SIN_ATIP"] - r["RATIO_RET_CON_ATIP"]

    orden = claves + ["RATIO_RET_CON_ATIP", "RATIO_RET_SIN_ATIP", "DIF_PUNTOS"]
    return r[orden + [c for c in r.columns if c not in orden]]


def tc_implicito(base: pd.DataFrame) -> pd.DataFrame:
    """SOL/DOL por mes de movimiento = TC efectivamente aplicado. Con TC fijo debe ser siempre TIPO_CAMBIO_FIJO."""
    g = base.groupby("AÑO_MES_MOV")[["INCURRIDO_SOL", "INCURRIDO_DOL"]].sum()
    g = g[g["INCURRIDO_DOL"].abs() > 1].sort_index()
    g["TC_IMPLICITO"] = g["INCURRIDO_SOL"] / g["INCURRIDO_DOL"]
    g["DIF_VS_FIJO"] = g["TC_IMPLICITO"] - TIPO_CAMBIO_FIJO
    return g.reset_index()


def movimiento_mensual(base: pd.DataFrame, col: str = "INCURRIDO_MON_NETO", n: int = 13) -> pd.DataFrame:
    """Incurrido incremental por mes de movimiento (diagonales recientes), en miles."""
    g = (
        base.dropna(subset=["NUEVO RAMO", "AÑO_MES_MOV"])
        .groupby(["NUEVO RAMO", "AÑO_MES_MOV"])[col].sum()
        .unstack(fill_value=0)
    )
    return g.iloc[:, -n:] / 1000


def _diagonal_actual(t: pd.DataFrame) -> pd.Series:
    """Incurrido acumulado observado hoy para cada mes de ocurrencia."""
    cum = t.cumsum(axis=1)
    n = len(cum)
    vals = []
    for r in range(n):
        dev = min(n - 1 - r, int(cum.columns.max()))
        vals.append(cum.iloc[r][dev] if dev in cum.columns else np.nan)
    return pd.Series(vals, index=t.index)


def factores_desarrollo(t: pd.DataFrame, max_dev: int = 12) -> pd.Series:
    """Factores edad-a-edad ponderados (sobre acumulado) usando solo celdas observadas."""
    cum = t.cumsum(axis=1)
    n = len(cum)
    out = {}
    for c in [c for c in cum.columns if 0 <= c < max_dev and (c + 1) in cum.columns]:
        filas = [r for r in range(n) if r + c + 1 <= n - 1]
        den = cum.iloc[filas][c].sum()
        out[f"{c}->{c + 1}"] = cum.iloc[filas][c + 1].sum() / den if den else np.nan
    return pd.Series(out, dtype=float)


def diagnosticar_triangulo(ramo: str, t: pd.DataFrame):
    n = len(t)
    diag = _diagonal_actual(t)
    mask_obs = np.array([[(r + int(c) <= n - 1) for c in t.columns] for r in range(n)])
    obs = t.values[mask_obs]
    no_cero = obs[obs != 0]
    f = factores_desarrollo(t)
    fila = {
        "RAMO": ramo,
        "MES_INI": int(t.index.min()),
        "MES_FIN": int(t.index.max()),
        "ULTIMO_MES_CON_DATOS": int(t.index[(t != 0).any(axis=1)].max()) if (t != 0).any().any() else None,
        "N_DESARROLLOS": t.shape[1],
        "INCURRIDO_ACUM_ACTUAL_MILES": diag.sum() / 1000,
        "ULT3_MESES_MILES": diag.tail(3).sum() / 1000,
        "MESES_RECIENTES_CERO(6)": int((diag.tail(6) <= 0).sum()),
        "FILAS_ACUM_NEGATIVO": int((diag < 0).sum()),
        "PCT_CELDAS_NEG": float((no_cero < 0).mean()) if len(no_cero) else 0.0,
        "FACTOR_MIN": f.min(),
        "FACTOR_MAX": f.max(),
    }
    return fila, f


def _alertas_cuadres(c: pd.DataFrame, alertas: list[str]) -> None:
    """Alertas de proceso, fechas, monedas y retención a partir de la tabla de cuadres."""
    if c.empty:
        return
    for _, r in c[~np.isclose(c["BRUTO_ORIGEN"], c["BRUTO_ENRIQUECIDO"])].iterrows():
        alertas.append(f"[CUADRE] {r['ARCHIVO']}: bruto origen != enriquecido (merge duplica/pierde filas)")
    for _, r in c[~np.isclose(c["SOL_DETALLE"], c["SOL_BASE"])].iterrows():
        alertas.append(f"[CUADRE] {r['ARCHIVO']}: base agrupada no cuadra con el detalle")
    for _, r in c[~np.isclose(c["SOL_ORIGEN"] - c["SOL_ELIMINADO"], c["SOL_BASE"])].iterrows():
        alertas.append(f"[CUADRE] {r['ARCHIVO']}: la base no cuadra con el archivo original ({r['SOL_ORIGEN'] - r['SOL_ELIMINADO']:,.0f} vs {r['SOL_BASE']:,.0f} soles)")
    for _, r in c[c["FILAS_ELIMINADAS"] > 0].iterrows():
        alertas.append(f"[INFO] {r['ARCHIVO']}: {r['FILAS_ELIMINADAS']} filas eliminadas (ramo ELIMINAR)")

    for _, r in c.iterrows():
        a = r["ARCHIVO"]
        if r["FECHAS_NO_PARSEADAS"]:
            alertas.append(f"[FECHAS] {a}: {r['FECHAS_NO_PARSEADAS']} fechas no se pudieron leer")
        if r["FECHAS_FUERA_RANGO"]:
            alertas.append(f"[FECHAS] {a}: {r['FECHAS_FUERA_RANGO']} fechas fuera de {AÑO_MIN_VALIDO}-{AÑO_MAX_VALIDO} (quedan sin fecha)")
        if ORDEN_FECHA_AMBIGUA.upper() == "DMY" and r["FECHAS_MDY_INEQUIVOCAS"]:
            alertas.append(f"[FECHAS] {a}: {r['FECHAS_MDY_INEQUIVOCAS']} fechas solo se entienden como mm/dd; hay formatos mezclados y {r['FECHAS_AMBIGUAS']} fechas ambiguas se leyeron como dd/mm")
        if ORDEN_FECHA_AMBIGUA.upper() == "MDY" and r["FECHAS_DMY_INEQUIVOCAS"]:
            alertas.append(f"[FECHAS] {a}: {r['FECHAS_DMY_INEQUIVOCAS']} fechas solo se entienden como dd/mm; hay formatos mezclados y {r['FECHAS_AMBIGUAS']} fechas ambiguas se leyeron como mm/dd")
        if pd.notna(r["FEC_SINI_MAX"]) and r["FEC_SINI_MAX"].year * 100 + r["FEC_SINI_MAX"].month > MES_CORTE:
            alertas.append(f"[FECHAS] {a}: siniestros con fecha posterior al corte ({r['FEC_SINI_MAX'].date()})")
        if pd.notna(r["FEC_MVTO_MAX"]) and r["FEC_MVTO_MAX"].year * 100 + r["FEC_MVTO_MAX"].month > MES_CORTE:
            alertas.append(f"[FECHAS] {a}: movimientos con fecha posterior al corte ({r['FEC_MVTO_MAX'].date()})")
        if r["COD_MON_INVALIDO"]:
            alertas.append(f"[MONEDA] {a}: {r['COD_MON_INVALIDO']} filas con COD_MON distinto de 1 y 2 (conversión no confiable)")
        if r["MONTOS_NO_NUMERICOS"]:
            alertas.append(f"[MONTOS] {a}: {r['MONTOS_NO_NUMERICOS']} montos no numéricos (se tomaron como 0)")
        if r["RET_OTRA_FUENTE"]:
            alertas.append(f"[RETENCION] {a}: {r['RET_OTRA_FUENTE']} filas sin retención en su fuente (se puso 1) que sí existe en la otra fuente")
        if r["RET_SIN_FECHA"]:
            alertas.append(f"[RETENCION] {a}: {r['RET_SIN_FECHA']} filas sin {RET_FECHA_BASE} (retención = 1)")


def _alertas_autos(a, alertas: list[str]) -> None:
    """Controles de la reclasificación AUTOS 3 -> AUTOS 1/2."""
    if a is None or a.empty:
        return
    for _, r in a.iterrows():
        n = r["ARCHIVO"]
        if r["FILAS_TRAS_DIVIDIR"] != r["FILAS_ORIGEN"]:
            alertas.append(f"[AUTOS] {n}: tras dividir quedan {r['FILAS_TRAS_DIVIDIR']} filas de {r['FILAS_ORIGEN']} (se pierden o duplican filas)")
        if abs(r["SOL_TRAS_DIVIDIR"] - r["SOL_ORIGEN"]) > 1.0:
            alertas.append(f"[AUTOS] {n}: la división cambia el incurrido ({r['SOL_ORIGEN']:,.0f} -> {r['SOL_TRAS_DIVIDIR']:,.0f} soles)")
        if r["FILAS_SUBGRUPOS_NO_PROCESADOS"]:
            alertas.append(f"[AUTOS] {n}: {r['FILAS_SUBGRUPOS_NO_PROCESADOS']} filas con SUBGRUPO no reconocido ({r['SUBGRUPOS_NO_PROCESADOS']}) quedan fuera")
        if r["SINI_EN_AUTOS1_Y_2"]:
            alertas.append(f"[AUTOS] {n}: {r['SINI_EN_AUTOS1_Y_2']} siniestros están en AUTOS 1 y 2; sus {r['FILAS_A3_EN_AMBOS']} filas de AUTOS 3 ({r['SOL_A3_EN_AMBOS']:,.0f} soles) se asignaron solo a AUTOS 1")


def _alertas_retencion_maestros(stats: dict, alertas: list[str]) -> None:
    for nombre, st in stats.items():
        if st.get("negativas"):
            alertas.append(f"[RETENCION] {nombre}: {st['negativas']} % de retención negativos")
        if st.get("claves_con_conflicto"):
            alertas.append(f"[RETENCION] {nombre}: {st['claves_con_conflicto']} claves repetidas con % distintos (histórico: se usa la primera)")
        if st.get("claves_anio_con_conflicto"):
            alertas.append(f"[RETENCION] {nombre}: {st['claves_anio_con_conflicto']} claves+año repetidas con % distintos (se usa la última)")
        if st.get("topadas_en_1"):
            alertas.append(f"[INFO] Retención {nombre}: {st['topadas_en_1']} % mayores a 1 se toparon en 1")


def revisar(res: dict) -> dict:
    """Controles de sentido actuarial. No exporta nada. Devuelve tablas + 'alertas'.
    Las alertas y el diagnóstico de triángulos se calculan sobre el NETO; el diagnóstico
    del bruto queda en rev['diagnostico_triangulos_<nv|v>_bruto'] (sin alertas)."""
    alertas: list[str] = []
    rev: dict = {}
    u = UMBRALES
    segmentos = {"nv": "NO VIDA", "v": "VIDA"}

    # --- cuadres de proceso, fechas, monedas y retención ---
    c = res["cuadres"]
    _alertas_cuadres(c, alertas)
    if not c.empty:
        rev["cuadres"] = c
        rev["retencion_fuentes"] = c[[
            "ARCHIVO", "RET_FILAS_HIST", "RET_HIST_ENCONTRADA", "RET_FILAS_XLSX", "RET_XLSX_ENCONTRADA",
            "RET_SIN_FECHA", "RETENCION_IMPUTADA_1", "RET_OTRA_FUENTE", "FILAS_ESSALUD_RET_20",
        ]]
        rev["calidad_fechas"] = c[[
            "ARCHIVO", "FECHAS_NULAS_ORIGEN", "FECHAS_NO_PARSEADAS", "FECHAS_FUERA_RANGO", "FECHAS_DMY_INEQUIVOCAS",
            "FECHAS_MDY_INEQUIVOCAS", "FECHAS_AMBIGUAS", "FEC_SINI_MIN", "FEC_SINI_MAX", "FEC_MVTO_MIN", "FEC_MVTO_MAX",
        ]]
    _alertas_retencion_maestros(res.get("ret_stats", {}), alertas)
    _alertas_autos(res.get("autos_stats"), alertas)
    if res.get("autos_stats") is not None and not res["autos_stats"].empty:
        rev["reclasificacion_autos"] = res["autos_stats"]

    if not res["atipicos_percentil"].empty:
        rev["atipicos_percentil"] = res["atipicos_percentil"]

    # --- puente inicial -> final de los triángulos ---
    val = validacion_triangulos(res)
    rev["validacion_triangulos"] = val
    for k, nombre in segmentos.items():
        if k not in val:
            continue
        for tipo in ("bruto", "neto"):
            p = val[k][f"puente_{tipo}"]
            rev[f"puente_{tipo}_{k}"] = p
            for _, r in p[p["NUEVO RAMO"] != "TOTAL"].iterrows():
                tol = u["tol_puente"] * max(1.0, abs(r["INICIAL"]))
                if abs(r["DIFERENCIA"]) > tol:
                    alertas.append(f"[PUENTE] {nombre} {r['NUEVO RAMO']} {tipo}: el triángulo final no cuadra con inicial - exclusiones (dif {r['DIFERENCIA']:,.1f} miles)")

    total_sol = 0.0
    for k, nombre in segmentos.items():
        base = res[f"base_{k}"]
        tris = res[f"tri_{k}_neto"]
        tris_bruto = res[f"tri_{k}_bruto"]
        if base.empty:
            continue
        total_sol += base["INCURRIDO_SOL"].sum()

        # --- año de ocurrencia: tamaño, retención implícita y atípicos ---
        ra = resumen_anual(base)
        rev[f"resumen_anual_{k}"] = ra
        rec = ra[ra["AÑO_SINI"] >= (DESDE_OCURRENCIA // 100 if DESDE_OCURRENCIA else 0)]
        for _, r in rec.iterrows():
            if pd.notna(r["RET_IMPLICITA"]) and (r["RET_IMPLICITA"] > 1.0001 or r["RET_IMPLICITA"] < 0):
                alertas.append(f"[{nombre}] {r['NUEVO RAMO']} {r['AÑO_SINI']}: retención implícita {r['RET_IMPLICITA']:.2f} fuera de [0,1]")
            if pd.notna(r["PCT_ATIPICO"]) and r["PCT_ATIPICO"] > u["pct_atipico"]:
                alertas.append(f"[{nombre}] {r['NUEVO RAMO']} {r['AÑO_SINI']}: atípicos = {r['PCT_ATIPICO']:.0%} del incurrido bruto")
            if r["INCURRIDO_DOL_MILES"] < 0:
                alertas.append(f"[{nombre}] {r['NUEVO RAMO']} {r['AÑO_SINI']}: incurrido bruto total negativo")
        for ramo, g in rec.sort_values("AÑO_SINI").groupby("NUEVO RAMO"):
            salto = g["RET_IMPLICITA"].diff().abs()
            for _, r in g[salto > u["salto_retencion"]].iterrows():
                alertas.append(f"[{nombre}] {ramo} {r['AÑO_SINI']}: retención implícita cambia >{u['salto_retencion']:.0%} vs año previo")

        # --- peso de los atípicos sobre el total, por ramo ---
        pa = peso_atipicos(base)
        rev[f"peso_atipicos_{k}"] = pa
        for _, r in pa[pa["NUEVO RAMO"] != "TOTAL"].iterrows():
            if pd.notna(r["PCT_BRUTO_ATIP"]) and abs(r["PCT_BRUTO_ATIP"]) > u["pct_atipico_total"]:
                alertas.append(f"[{nombre}] {r['NUEVO RAMO']}: atípicos = {r['PCT_BRUTO_ATIP']:.1%} del incurrido bruto total ({int(r['N_SINIESTROS_ATIP'])} de {int(r['N_SINIESTROS'])} siniestros)")

        # --- ratio de retención por ramo, con y sin atípicos ---
        rev[f"ratio_retencion_{k}"] = ratio_retencion(base)

        # --- conversión de moneda: con TC fijo el TC implícito debe ser siempre el fijo ---
        tc = tc_implicito(base)
        rev[f"tc_implicito_{k}"] = tc
        for _, r in tc[tc["DIF_VS_FIJO"].abs() > 1e-6].iterrows():
            alertas.append(f"[{nombre}] TC implícito {r['TC_IMPLICITO']:.4f} != fijo {TIPO_CAMBIO_FIJO} en {int(r['AÑO_MES_MOV'])} (revisar COD_MON)")

        # --- diagonales recientes ---
        base_mov = base[base["ATIPICOS"] == "N"] if EXCLUIR_ATIPICOS_TRIANGULO else base
        mm = movimiento_mensual(base_mov)
        rev[f"movimiento_mensual_{k}"] = mm
        if mm.shape[1] >= 3:
            for ramo, fila in mm.iterrows():
                previo = fila.iloc[:-1].abs().median()
                if previo > 0 and abs(fila.iloc[-1]) > u["mult_mov_ultimo_mes"] * previo:
                    alertas.append(f"[{nombre}] {ramo}: movimiento de {fila.index[-1]} = {fila.iloc[-1]:,.0f} miles, muy distinto a la mediana previa ({previo:,.0f})")

        # --- corte ---
        if base["AÑO_MES_MOV"].max() > MES_CORTE:
            alertas.append(f"[{nombre}] hay movimientos posteriores al corte {MES_CORTE}: máx {base['AÑO_MES_MOV'].max()}")
        if base["AÑO_MES_OCU"].max() > MES_CORTE:
            alertas.append(f"[{nombre}] hay siniestros ocurridos posteriores al corte {MES_CORTE}: máx {base['AÑO_MES_OCU'].max()}")

        # --- triángulos (neto: con alertas) ---
        filas, factores = [], {}
        for ramo, t in tris.items():
            fila, f = diagnosticar_triangulo(ramo, t)
            filas.append(fila)
            factores[ramo] = f
            if fila["ULTIMO_MES_CON_DATOS"] is None or fila["ULTIMO_MES_CON_DATOS"] < MES_CORTE:
                alertas.append(f"[{nombre}] {ramo}: último mes de ocurrencia con datos = {fila['ULTIMO_MES_CON_DATOS']}, el corte es {MES_CORTE}")
            if fila["MESES_RECIENTES_CERO(6)"] >= 3:
                alertas.append(f"[{nombre}] {ramo}: {fila['MESES_RECIENTES_CERO(6)']} de los últimos 6 meses sin incurrido")
            if fila["PCT_CELDAS_NEG"] > u["pct_celdas_neg"]:
                alertas.append(f"[{nombre}] {ramo}: {fila['PCT_CELDAS_NEG']:.1%} de celdas observadas negativas")
            if fila["FILAS_ACUM_NEGATIVO"]:
                alertas.append(f"[{nombre}] {ramo}: {fila['FILAS_ACUM_NEGATIVO']} meses de ocurrencia con incurrido acumulado negativo")
            if pd.notna(fila["FACTOR_MIN"]) and fila["FACTOR_MIN"] < u["factor_min"]:
                alertas.append(f"[{nombre}] {ramo}: factor edad-a-edad mínimo {fila['FACTOR_MIN']:.2f} (<1 = desarrollo negativo)")
            if pd.notna(fila["FACTOR_MAX"]) and fila["FACTOR_MAX"] > u["factor_max"]:
                alertas.append(f"[{nombre}] {ramo}: factor edad-a-edad máximo {fila['FACTOR_MAX']:.2f} (poca data o atípicos)")
        if filas:
            rev[f"diagnostico_triangulos_{k}"] = pd.DataFrame(filas)
            rev[f"factores_{k}"] = pd.DataFrame(factores).T

        # --- triángulos (bruto: solo diagnóstico) ---
        if tris_bruto:
            rev[f"diagnostico_triangulos_{k}_bruto"] = pd.DataFrame(
                [diagnosticar_triangulo(ramo, t)[0] for ramo, t in tris_bruto.items()]
            )

    # --- errores de datos ---
    err = res["errores"]
    if not err.empty:
        rev["errores_resumen"] = (
            err.groupby(["ES_VIDA", "MOTIVO", "NUEVO RAMO"], dropna=False)
            .agg(FILAS=("MOTIVO", "size"), INCURRIDO_SOL=("INCURRIDO_SOL", "sum"))
            .reset_index()
        )
        neg = err[err["MOTIVO"] == "DIF_NEGATIVO"]["INCURRIDO_SOL"].sum()
        if total_sol and abs(neg) / abs(total_sol) > u["pct_error_dif"]:
            alertas.append(f"[ERRORES] dif<0 pesa {abs(neg) / abs(total_sol):.1%} del incurrido total")
        for motivo in ["SIN_RAMO_MAESTRO", "SIN_MONEDA_MAESTRO", "FECHA_NULA", "COD_MON_INVALIDO", "MONTO_NO_NUMERICO"]:
            n = int((err["MOTIVO"] == motivo).sum())
            if n:
                alertas.append(f"[ERRORES] {n} filas con {motivo} (quedan fuera de sumas/triángulos)")

    rev["alertas"] = pd.DataFrame({"ALERTA": alertas})
    print(f"\n=== REVISIÓN: {len(alertas)} alertas ===")
    for a in alertas:
        print(" -", a)
    for k in ("nv", "v"):
        p = rev.get(f"puente_bruto_{k}")
        if p is not None:
            cols = ["NUEVO RAMO", "TOTAL_BASE", "FUERA_VENTANA", "INICIAL", "ATIP_OPERATIVOS", "ATIP_PERCENTIL_SUP",
                    "ATIP_PERCENTIL_INF", "FINAL_ESPERADO", "FINAL_TRIANGULO", "DIFERENCIA"]
            print(f"\n--- Puente inicial -> final, BRUTO, {segmentos[k]} (miles de la moneda del ramo) ---")
            print(p[cols].round(1).to_string(index=False))
    for k in ("nv", "v"):
        d = rev.get(f"diagnostico_triangulos_{k}")
        if d is not None:
            print(f"\n--- Diagnóstico triángulos {segmentos[k]} (neto) ---")
            print(d.round(3).to_string(index=False))
    for k in ("nv", "v"):
        d = rev.get(f"ratio_retencion_{k}")
        if d is not None:
            print(f"\n--- Ratio de retención (neto/bruto, USD) {segmentos[k]} ---")
            print(d[["NUEVO RAMO", "RATIO_RET_CON_ATIP", "RATIO_RET_SIN_ATIP", "DIF_PUNTOS"]].round(4).to_string(index=False))
    for k in ("nv", "v"):
        d = rev.get(f"peso_atipicos_{k}")
        if d is not None:
            print(f"\n--- Peso de atípicos sobre el total {segmentos[k]} (miles USD) ---")
            print(d[["NUEVO RAMO", "N_SINIESTROS", "N_SINIESTROS_ATIP", "PCT_SINIESTROS_ATIP",
                     "BRUTO_ATIP_MILES", "PCT_BRUTO_ATIP", "NETO_ATIP_MILES", "PCT_NETO_ATIP"]].round(3).to_string(index=False))
    print("\nTablas en rev:", ", ".join(rev.keys()))
    return rev


# =============================================================================
# 12. CORRER / EXPORTAR / MAIN
# =============================================================================
def _config_log() -> None:
    if log.handlers:
        return
    RUTA_OUTPUT.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.StreamHandler(), logging.FileHandler(RUTA_OUTPUT / f"log_{SUFIJO}.txt", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False


def cerrar_log() -> None:
    """Cierra y libera el archivo log_*.txt (útil si Excel/Windows lo deja bloqueado
    o si vas a borrarlo/moverlo). La próxima corrida lo vuelve a abrir sola."""
    for h in list(log.handlers):
        h.flush()
        h.close()
        log.removeHandler(h)


def correr() -> dict:
    """Procesa todos los archivos. No exporta."""
    m = cargar_maestros()
    bases_nv, bases_v, errores, cuadres, atipicos, autos_stats = [], [], [], [], [], []

    for nombre in sorted(os.listdir(RUTA)):
        tipo = clasificar_archivo(nombre)
        if tipo is None:
            log.info("Omitido: %s", nombre)
            continue

        df = leer_archivo(RUTA / nombre)
        if tipo == "autos":
            partes, st_autos = dividir_autos(df, nombre)
            autos_stats.append(st_autos)
        else:
            partes = {nombre: df}

        for etiqueta, parte in partes.items():
            base, err, cuadre, det_atip = procesar(parte, m, f"{nombre} | {etiqueta}", vida=(tipo == "vida"))
            if not det_atip.empty:
                atipicos.append(det_atip)
            (bases_v if tipo == "vida" else bases_nv).append(base)
            if not err.empty:
                errores.append(err.assign(ES_VIDA=(tipo == "vida")))
            cuadres.append(cuadre)

    base_nv = pd.concat(bases_nv, ignore_index=True) if bases_nv else pd.DataFrame()
    base_v = pd.concat(bases_v, ignore_index=True) if bases_v else pd.DataFrame()

    def triangulos(base: pd.DataFrame, col: str) -> dict[str, pd.DataFrame]:
        return construir_triangulos(base, col) if not base.empty else {}

    return {
        "base_nv": base_nv,
        "base_v": base_v,
        "errores": pd.concat(errores, ignore_index=True) if errores else pd.DataFrame(),
        "cuadres": pd.DataFrame(cuadres),
        "atipicos_percentil": pd.concat(atipicos, ignore_index=True) if atipicos else pd.DataFrame(),
        "ret_stats": m.ret.stats,
        "autos_stats": pd.DataFrame(autos_stats),
        "tri_nv_bruto": triangulos(base_nv, COL_TRIANGULO_BRUTO),
        "tri_nv_neto": triangulos(base_nv, COL_TRIANGULO_NETO),
        "tri_v_bruto": triangulos(base_v, COL_TRIANGULO_BRUTO),
        "tri_v_neto": triangulos(base_v, COL_TRIANGULO_NETO),
    }


def exportar(res: dict, bases: bool = True, errores: bool = True, cuadres: bool = True,
             atipicos: bool = True, triangulos: bool = True, validacion: bool = True) -> None:
    """Exporta lo ya revisado.
    Bases, errores, cuadres, atípicos y validación -> RUTA_OUTPUT.
    Triángulos -> RUTA_TRIANGULOS, 4 archivos con una hoja por ramo:
      triangulos_no_vida_bruto_<SUFIJO>.xlsx, triangulos_no_vida_neto_<SUFIJO>.xlsx,
      triangulos_vida_bruto_<SUFIJO>.xlsx,    triangulos_vida_neto_<SUFIJO>.xlsx"""
    RUTA_OUTPUT.mkdir(parents=True, exist_ok=True)
    if cuadres:
        exportar_tabla(res["cuadres"], f"cuadres_{SUFIJO}")
        if not res.get("autos_stats", pd.DataFrame()).empty:
            exportar_tabla(res["autos_stats"], f"reclasificacion_autos_{SUFIJO}")
    if atipicos and not res["atipicos_percentil"].empty:
        exportar_tabla(res["atipicos_percentil"], f"atipicos_percentil_{SUFIJO}")
    for es_vida, suf in [(False, ""), (True, "_vida")]:
        base = res["base_v" if es_vida else "base_nv"]
        if bases and not base.empty:
            exportar_tabla(base, f"base{suf}_final_{SUFIJO}")
        err = res["errores"]
        if errores and not err.empty:
            e = err[err["ES_VIDA"] == es_vida]
            if not e.empty:
                exportar_tabla(e, f"errores{suf}_final_{SUFIJO}")
    if triangulos:
        for seg, nom_seg in [("nv", "no_vida"), ("v", "vida")]:
            for tipo in ("bruto", "neto"):
                tri = res[f"tri_{seg}_{tipo}"]
                ruta = exportar_triangulos_excel(tri, f"triangulos_{nom_seg}_{tipo}_{SUFIJO}")
                if ruta:
                    print(f"Triángulos {nom_seg} {tipo}: {ruta} ({len(tri)} hojas)")
    if validacion:
        ruta = exportar_validacion(res)
        if ruta:
            print(f"Validación de triángulos: {ruta}")


def extras() -> None:
    """Procesos aparte (exportan directo): listas de pólizas e incurrido por canal."""
    _config_log()
    extraer_polizas()
    incurrido_por_canal(cargar_maestros())


def main():
    _config_log()
    res = correr()
    rev = revisar(res)
    return res, rev


if __name__ == "__main__":
    res, rev = main()
