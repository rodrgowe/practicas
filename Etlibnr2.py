"""
ETL IBNR: base de incurridos + triángulos (no vida y vida).

Flujo: leer -> fechas -> enriquecer (TC, maestros, atípicos, retención)
       -> incurrido -> validar/cuadrar -> agrupar -> triángulos -> exportar

DECISIONES A REVISAR (marcadas con  # REVISAR  en el código):
  1. Vida usa la misma fórmula de dólares que el resto (división por TC).
  2. Prioridad de retención: XLSX (2021-2026) primero, luego histórico, luego 1.
     (En el código original, por el orden de los sufijos del merge, la
     prioridad real era la inversa.)
  3. Filas AUTOS 3 transferidas a AUTOS 1/2 se reclasifican como AUTOS 1/2.
  4. PCT_RET ya no forma parte del groupby de la base.
  5. dif < 0: se exportan como error y en el triángulo se llevan al
     desarrollo 0 (TRATO_DIF_NEGATIVO), para que la columna B sea siempre dev 0.
  6. Atípicos por cuantía: por siniestro se suma el incurrido bruto en USD y se marcan
     (ATIPICOS='S') los que superan el percentil superior o quedan por debajo del inferior.
       - NO VIDA: por archivo, P2.5 / P97.5 (ATIPICOS_NV_POR_RAMO = True los calcula por NUEVO RAMO).
       - VIDA: por NUEVO RAMO del maestro, P1 / P99, ANTES de separar ESSALUD-ACC.
     Se marcan en la base y se EXCLUYEN del triángulo (EXCLUIR_ATIPICOS_TRIANGULO = True);
     esto incluye también los atípicos operativos del maestro (Operativo.xlsx), en vida y
     no vida. Ya no se usa Atipicos_202608.xlsx: lo reemplaza el cálculo por percentil.
  7. Póliza ESSALUD: retención fija de 20% (RET_ESSALUD).
  8. main() NO exporta: corre y revisa. Exportar es un paso aparte: exportar(res).

Uso (por celdas):
    res, rev = main()      # correr + revisión actuarial
    rev["alertas"]         # lo primero que hay que mirar
    exportar(res)          # solo cuando los outputs tengan sentido
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
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

# Clasificación de archivos por nombre
EXCLUIR_EN_NOMBRE = ("207", "_28_", "_11_")
MARCA_VIDA = "_2_"
MARCA_AUTOS = "_30_"

# Autos
SUBGRUPOS_AUTOS = ["AUTOS 1", "AUTOS 2", "AUTOS 3"]
TIP_EXP_TRANSFERIBLES = ["RDR", "RCT", "RAC", "RAA"]

# Vida
POLIZA_ESSALUD = 6362159900003
RET_ESSALUD = 0.20  # retención fija de la póliza ESSALUD

# Retención
CLAVE_RET_HIST = ["COD_CIA", "COD_RAMO", "NUM_SINI", "NUM_EXP"]
CLAVE_RET_XLSX = ["NUM_SINI", "NUM_EXP", "COD_COB"]
PRIORIDAD_RET = ["PCT_RET_XLSX", "PCT_RET_HIST"]  # REVISAR

# Triángulos
EXCLUIR_ATIPICOS_TRIANGULO = True  # excluye ATIPICOS == 'S' del triángulo

# Atípicos por cuantía: percentiles del incurrido por siniestro
PERCENTIL_INF = 0.025           # NO VIDA
PERCENTIL_SUP = 0.975
ATIPICOS_NV_POR_RAMO = False    # REVISAR: False = por archivo (como estaba); True = por NUEVO RAMO dentro de cada archivo
PERCENTIL_INF_VIDA = 0.01       # VIDA: por NUEVO RAMO, antes de separar ESSALUD-ACC
PERCENTIL_SUP_VIDA = 0.99
COL_ATIPICO_PERCENTIL = "INCURRIDO_DOL"  # bruto en USD (comparable entre monedas)  # REVISAR
TRATO_DIF_NEGATIVO = "a_cero"   # "a_cero" | "excluir"  # REVISAR
DESDE_OCURRENCIA = 201901       # el triángulo empieza en este año-mes (celda A2)
MES_CORTE = 202608              # REVISAR: último mes de cierre (para los controles)

# Triángulos en Excel
# (un Excel para no vida y otro para vida, una hoja por ramo; índices desde A2, valores desde B2)
TRIANGULO_ACUMULADO = False     # False = incremental (como el original)  # REVISAR
TRIANGULO_FUTURO_VACIO = False  # True = deja en blanco las celdas no observadas

# Umbrales de alerta de la revisión
UMBRALES = {
    "factor_min": 0.95,          # factor edad-a-edad ponderado
    "factor_max": 3.0,
    "pct_celdas_neg": 0.05,      # % de celdas observadas negativas
    "salto_tc": 0.08,            # variación mensual del TC implícito
    "salto_retencion": 0.20,     # variación anual de retención implícita
    "pct_atipico": 0.30,         # % del incurrido bruto que es atípico
    "pct_error_dif": 0.01,       # dif<0 sobre incurrido total
    "mult_mov_ultimo_mes": 3.0,  # último mes vs mediana de los 12 previos
}

# Procesos opcionales (se llaman a mano con extras())
DESDE_CANAL = 201901  # inclusivo

COLS_MONTOS = [
    "INCURRIDO_SOL", "INCURRIDO_DOL", "INCURRIDO_SOL_NETO",
    "INCURRIDO_DOL_NETO", "INCURRIDO_MON", "INCURRIDO_MON_NETO",
]
CLAVES_BASE = [
    "NUEVO RAMO", "AÑO_SINI", "AÑO_MOV", "AÑO_MES_OCU",
    "AÑO_MES_MOV", "dif", "COD_MON", "ATIPICOS",
]

log = logging.getLogger("ibnr")


# =============================================================================
# 1. MAESTROS
# =============================================================================
@dataclass
class Maestros:
    tc: pd.DataFrame
    ramos: pd.DataFrame
    atipicos: pd.DataFrame
    ret_hist: pd.DataFrame
    ret_xlsx: pd.DataFrame


def _dedup_max(df: pd.DataFrame, col: str, claves: list[str]) -> pd.DataFrame:
    """Una fila por clave, quedándose con el mayor valor de `col`."""
    return df.sort_values(col, ascending=False).drop_duplicates(subset=claves, keep="first")


def cargar_maestros() -> Maestros:
    # Tipo de cambio
    tc = pd.read_excel(RUTA_MAESTROS / "TC_HISTO.xlsx")
    tc.columns = ["AÑO_MES_MOV", "TIPO_CAMBIO"]
    tc["AÑO_MES_MOV"] = tc["AÑO_MES_MOV"].astype("Int64")

    # Ramos y moneda
    ramos = pd.read_excel(RUTA_MAESTROS / "maestros.xlsx")
    ramos = ramos[["COD_RAMO", "SUBGRUPO", "NUEVO RAMO", "MONEDA"]]

    # Atípicos operativos (los de cuantía ahora se calculan por percentil en marcar_atipicos_percentil)
    atipicos = (
        pd.read_excel(RUTA_MAESTROS / "Operativo.xlsx")[["NUM_SINI"]]
        .drop_duplicates()
        .assign(ATIPICOS="S", TIPO_ATIPICO="operativa")
    )

    # Retención histórica (No vida + Vida)
    ret_hist = pd.concat(
        [
            pd.read_csv(RUTA_MAESTROS / "Data_Historica_MP_202512.txt", sep=";"),
            pd.read_csv(RUTA_MAESTROS / "Data_Historica_MPV_202512.txt", sep=";"),
        ],
        ignore_index=True,
    )
    ret_hist = _dedup_max(ret_hist, "PCT_REASEGURO", CLAVE_RET_HIST)
    ret_hist = ret_hist[CLAVE_RET_HIST + ["PCT_REASEGURO"]].rename(columns={"PCT_REASEGURO": "PCT_RET_HIST"})
    ret_hist["PCT_RET_HIST"] = (ret_hist["PCT_RET_HIST"] / 100).clip(upper=1)

    # Retención 2021-2026
    ret_xlsx = pd.read_excel(RUTA_MAESTROS / "Retención_2021_2026.xlsx")
    ret_xlsx = _dedup_max(ret_xlsx, "PCT_RET", CLAVE_RET_XLSX)
    ret_xlsx = ret_xlsx[CLAVE_RET_XLSX + ["PCT_RET"]].rename(columns={"PCT_RET": "PCT_RET_XLSX"})
    ret_xlsx["PCT_RET_XLSX"] = ret_xlsx["PCT_RET_XLSX"].clip(upper=1)

    return Maestros(tc, ramos, atipicos, ret_hist, ret_xlsx)


def cargar_canal() -> pd.DataFrame:
    nov = pd.read_excel(RUTA_CANAL / "polizas_novida_CODCANAL3.xlsx")
    vid = pd.read_excel(RUTA_CANAL / "polizas_vida_CODCANAL3 1.xlsx")
    sct = pd.read_excel(RUTA_CANAL / "SCTR_CODCANAL3.xlsx")
    vid.columns = sct.columns = ["ID", "NUM_POLIZA", "CODCANAL3"]
    tot = pd.concat([nov, vid, sct], ignore_index=True)[["NUM_POLIZA", "CODCANAL3"]].drop_duplicates()
    dup = tot["NUM_POLIZA"].duplicated().sum()
    if dup:
        log.warning("Canal: %s pólizas con más de un canal; se conserva el primero", dup)
        tot = tot.drop_duplicates("NUM_POLIZA", keep="first")
    return tot


# =============================================================================
# 2. LECTURA Y CLASIFICACIÓN
# =============================================================================
def clasificar_archivo(nombre: str, excluir: tuple[str, ...] = EXCLUIR_EN_NOMBRE) -> str | None:
    if not (RUTA / nombre).is_file():
        return None
    if any(marca in nombre for marca in excluir):
        return None
    if MARCA_VIDA in nombre:
        return "vida"
    if MARCA_AUTOS in nombre:
        return "autos"
    return "general"


def leer_archivo(ruta: Path) -> pd.DataFrame:
    df = pd.read_csv(ruta, sep=";", low_memory=False)
    if df.shape[1] != len(COLUMNAS):
        raise ValueError(f"{ruta.name}: {df.shape[1]} columnas, se esperaban {len(COLUMNAS)}")
    df.columns = COLUMNAS
    return df


def dividir_autos(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """AUTOS 1/2 reciben los expedientes transferibles de AUTOS 3 de sus
    siniestros; AUTOS 3 se queda sin ellos."""
    otros = set(df["SUBGRUPO"].unique()) - set(SUBGRUPOS_AUTOS)
    if otros:
        log.warning("Autos: subgrupos no procesados: %s", otros)

    a3 = df[df["SUBGRUPO"] == "AUTOS 3"]
    a3_transf = a3[a3["TIP_EXP"].isin(TIP_EXP_TRANSFERIBLES)]

    partes: dict[str, pd.DataFrame] = {}
    transferidos: set = set()
    for y in ["AUTOS 1", "AUTOS 2"]:
        base_y = df[df["SUBGRUPO"] == y]
        con = a3_transf[a3_transf["NUM_SINI"].isin(base_y["NUM_SINI"].unique())].copy()
        con["SUBGRUPO"] = y  # REVISAR: se reclasifican como AUTOS 1/2
        repetidos = transferidos & set(con["NUM_SINI"].unique())
        if repetidos:
            log.warning("Autos: %s siniestros transferidos a más de un subgrupo", len(repetidos))
        transferidos |= set(con["NUM_SINI"].unique())
        log.info("%s: %s filas transferidas desde AUTOS 3", y, len(con))
        partes[y] = pd.concat([base_y, con], ignore_index=True)

    mask_quitar = a3["NUM_SINI"].isin(transferidos) & a3["TIP_EXP"].isin(TIP_EXP_TRANSFERIBLES)
    partes["AUTOS 3"] = a3[~mask_quitar]
    return partes


# =============================================================================
# 3. ENRIQUECIMIENTO Y CÁLCULO
# =============================================================================
def preparar_fechas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["FEC_SINI"] = pd.to_datetime(df["FEC_SINI"], format="mixed", dayfirst=True)
    df["FEC_MVTO"] = pd.to_datetime(df["FEC_MVTO"], format="mixed", dayfirst=True)
    df["AÑO_SINI"] = df["FEC_SINI"].dt.year.astype("Int64")
    df["AÑO_MOV"] = df["FEC_MVTO"].dt.year.astype("Int64")
    df["AÑO_MES_OCU"] = (df["FEC_SINI"].dt.year * 100 + df["FEC_SINI"].dt.month).astype("Int64")
    df["AÑO_MES_MOV"] = (df["FEC_MVTO"].dt.year * 100 + df["FEC_MVTO"].dt.month).astype("Int64")
    df["dif"] = (
        (df["FEC_MVTO"].dt.year - df["FEC_SINI"].dt.year) * 12
        + (df["FEC_MVTO"].dt.month - df["FEC_SINI"].dt.month)
    ).astype("Int64")
    return df


def enriquecer(df: pd.DataFrame, m: Maestros) -> pd.DataFrame:
    n0 = len(df)
    df = df.merge(m.tc, on="AÑO_MES_MOV", how="left", validate="many_to_one")
    df = df.merge(m.ramos, on=["COD_RAMO", "SUBGRUPO"], how="left", validate="many_to_one")
    df = df.merge(m.atipicos, on="NUM_SINI", how="left", validate="many_to_one")
    df = df.merge(m.ret_hist, on=CLAVE_RET_HIST, how="left", validate="many_to_one")
    df = df.merge(m.ret_xlsx, on=CLAVE_RET_XLSX, how="left", validate="many_to_one")
    if len(df) != n0:
        raise RuntimeError(f"Los merges cambiaron el número de filas: {n0} -> {len(df)}")

    df["ATIPICOS"] = df["ATIPICOS"].astype(object).fillna("N")
    df["TIPO_ATIPICO"] = df["TIPO_ATIPICO"].astype(object)

    pct = pd.Series(np.nan, index=df.index)
    for col in PRIORIDAD_RET:
        pct = pct.fillna(df[col])
    df["RET_IMPUTADA"] = pct.isna()
    df["PCT_RET"] = pct.fillna(1.0)

    # Póliza ESSALUD: retención fija, pisa cualquier otra fuente
    es_essalud = _es_essalud(df)
    df.loc[es_essalud, "PCT_RET"] = RET_ESSALUD
    df.loc[es_essalud, "RET_IMPUTADA"] = False
    return df


def _es_essalud(df: pd.DataFrame) -> pd.Series:
    """Máscara de la póliza ESSALUD (NUM_POLIZA puede venir como texto)."""
    return pd.to_numeric(df["NUM_POLIZA"], errors="coerce") == POLIZA_ESSALUD


def calcular_incurrido(df: pd.DataFrame) -> pd.DataFrame:
    """Moneda por fila (no por archivo). Bruto y neto en SOL, DOL y moneda del ramo."""
    df = df.copy()
    bruto = df["RESERVA_BRUTO"] + df["PAGO_BRUTO"]
    tc = df["TIPO_CAMBIO"]

    df["INCURRIDO_SOL"] = bruto * np.where(df["COD_MON"] == 1, 1, tc)
    df["INCURRIDO_DOL"] = bruto / np.where(df["COD_MON"] == 2, 1, tc)
    df["INCURRIDO_SOL_NETO"] = df["INCURRIDO_SOL"] * df["PCT_RET"]
    df["INCURRIDO_DOL_NETO"] = df["INCURRIDO_DOL"] * df["PCT_RET"]

    es_pen = df["MONEDA"].eq("PEN")
    df["INCURRIDO_MON"] = np.where(es_pen, df["INCURRIDO_SOL"], df["INCURRIDO_DOL"])
    df["INCURRIDO_MON_NETO"] = np.where(es_pen, df["INCURRIDO_SOL_NETO"], df["INCURRIDO_DOL_NETO"])
    return df


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
# 4. VALIDACIÓN
# =============================================================================
def detectar_errores(df: pd.DataFrame, etiqueta: str) -> pd.DataFrame:
    reglas = {
        "FECHA_NULA": df["FEC_SINI"].isna() | df["FEC_MVTO"].isna(),
        "DIF_NEGATIVO": (df["dif"] < 0).fillna(False).astype(bool),
        "TC_NULO": df["TIPO_CAMBIO"].isna(),
        "SIN_RAMO_MAESTRO": df["NUEVO RAMO"].isna(),
        "SIN_MONEDA_MAESTRO": df["MONEDA"].isna(),
    }
    partes = [df[mask].assign(MOTIVO=motivo, ARCHIVO=etiqueta) for motivo, mask in reglas.items() if mask.any()]
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


# =============================================================================
# 5. PROCESO POR ARCHIVO
# =============================================================================
def agrupar_base(df: pd.DataFrame, extra: tuple[str, ...] = ()) -> pd.DataFrame:
    claves = CLAVES_BASE + list(extra)
    return df.groupby(claves, as_index=False, dropna=False)[COLS_MONTOS].sum()


def procesar(df_raw: pd.DataFrame, m: Maestros, etiqueta: str, vida: bool = False):
    """Devuelve (base, errores, cuadre, detalle de atípicos por percentil)."""
    log.info("Procesando %s (%s filas)", etiqueta, len(df_raw))
    n_raw = len(df_raw)
    bruto_raw = (df_raw["RESERVA_BRUTO"] + df_raw["PAGO_BRUTO"]).sum()

    df = preparar_fechas(df_raw)
    df = enriquecer(df, m)
    df = calcular_incurrido(df)
    bruto_enr = (df["RESERVA_BRUTO"] + df["PAGO_BRUTO"]).sum()
    if not np.isclose(bruto_raw, bruto_enr):
        log.warning("%s: bruto origen %.2f != bruto enriquecido %.2f", etiqueta, bruto_raw, bruto_enr)

    filas_eliminadas = 0
    detalle_atip, stats_atip = pd.DataFrame(), {}
    if vida:
        # ELIMINAR se quita primero (salvo ESSALUD, que se conserva como ESSALUD-ACC)
        mask = df["NUEVO RAMO"].eq("ELIMINAR") & ~_es_essalud(df)
        filas_eliminadas = int(mask.sum())
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

    sol_df, sol_base = df["INCURRIDO_SOL"].sum(), base["INCURRIDO_SOL"].sum()
    if not np.isclose(sol_df, sol_base):
        log.warning("%s: la base no cuadra con el detalle (%.2f vs %.2f)", etiqueta, sol_df, sol_base)

    cuadre = {
        "ARCHIVO": etiqueta,
        "FILAS_ORIGEN": n_raw,
        "FILAS_ELIMINADAS": filas_eliminadas,
        "BRUTO_ORIGEN": bruto_raw,
        "BRUTO_ENRIQUECIDO": bruto_enr,
        "SOL_DETALLE": sol_df,
        "SOL_BASE": sol_base,
        "TC_NULOS": int(df["TIPO_CAMBIO"].isna().sum()),
        "SIN_RAMO": int(df["NUEVO RAMO"].isna().sum()),
        "DIF_NEGATIVOS": int((df["dif"] < 0).sum()),
        "RETENCION_IMPUTADA_1": int(df["RET_IMPUTADA"].sum()),
        "FILAS_ESSALUD_RET_20": int(_es_essalud(df).sum()),
        **stats_atip,
    }
    log.info("%s | TC nulos=%s | sin ramo=%s | dif<0=%s | ret imputada=%s",
             etiqueta, cuadre["TC_NULOS"], cuadre["SIN_RAMO"],
             cuadre["DIF_NEGATIVOS"], cuadre["RETENCION_IMPUTADA_1"])
    return base, errores, cuadre, detalle_atip


# =============================================================================
# 6. TRIÁNGULOS
# =============================================================================
def construir_triangulos(base: pd.DataFrame, col: str = "INCURRIDO_MON_NETO") -> dict[str, pd.DataFrame]:
    """Un triángulo (incremental) por NUEVO RAMO, siempre cuadrado (a x a):
    filas = meses de ocurrencia desde DESDE_OCURRENCIA hasta MES_CORTE,
    columnas = desarrollos 0 .. a-1."""
    b = base.dropna(subset=["NUEVO RAMO", "AÑO_MES_OCU", "dif"])
    if len(b) < len(base):
        log.warning("Triángulos: %s filas de la base sin ramo/fecha quedan fuera", len(base) - len(b))
    if EXCLUIR_ATIPICOS_TRIANGULO:
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

        if not np.isclose(t.values.sum(), g[col].sum()):
            log.warning("Triángulo %s no cuadra con la base (movimientos fuera del cuadrado: posteriores al corte?)", ramo)
        triangulos[ramo] = t
    return triangulos


# =============================================================================
# 7. EXPORTACIÓN
# =============================================================================
def exportar_tabla(df: pd.DataFrame, nombre: str) -> None:
    if len(df) >= 1_000_000:  # límite de Excel
        ruta = RUTA_OUTPUT / f"{nombre}.csv"
        df.to_csv(ruta, index=False)
    else:
        ruta = RUTA_OUTPUT / f"{nombre}.xlsx"
        df.to_excel(ruta, index=False)
    log.info("Exportado %s (%s filas)", ruta.name, len(df))


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

            limpio = re.sub(r"[\[\]:*?/\\]", "_", str(ramo))
            hoja = limpio[:31]
            if len(limpio) > 31:
                log.warning("Nombre de hoja truncado a 31 caracteres: %s", ramo)
            k = 1
            while hoja.lower() in usados:
                k += 1
                hoja = f"{limpio[:28]}_{k}"
            usados.add(hoja.lower())
            out.to_excel(xw, sheet_name=hoja)
    log.info("Exportado %s (%s hojas)", ruta, len(triangulos))
    return ruta


# =============================================================================
# 8. PROCESOS OPCIONALES
# =============================================================================
def extraer_polizas() -> None:
    """Listas de pólizas únicas (vida / no vida) para pedir COD_CANAL."""
    idx = COLUMNAS.index("NUM_POLIZA")
    no_vida, vida = [], []
    for nombre in sorted(os.listdir(RUTA)):
        tipo = clasificar_archivo(nombre, excluir=("_28_", "_207_"))
        if tipo is None:
            continue
        s = pd.read_csv(RUTA / nombre, sep=";", usecols=[idx]).iloc[:, 0].unique()
        (vida if tipo == "vida" else no_vida).extend(s)
    pd.DataFrame({"NUM_POLIZA": pd.unique(no_vida)}).to_excel(RUTA_OUTPUT / "polizas_novida.xlsx", index=False)
    pd.DataFrame({"NUM_POLIZA": pd.unique(vida)}).to_excel(RUTA_OUTPUT / "polizas_vida.xlsx", index=False)


def incurrido_por_canal(m: Maestros) -> None:
    canal = cargar_canal()
    partes = []
    for nombre in sorted(os.listdir(RUTA)):
        if clasificar_archivo(nombre, excluir=("207", "_28_")) is None:
            continue
        df = calcular_incurrido(enriquecer(preparar_fechas(leer_archivo(RUTA / nombre)), m))
        df = df.merge(canal, on="NUM_POLIZA", how="left", validate="many_to_one")
        partes.append(
            df.groupby(["NUEVO RAMO", "AÑO_MES_OCU", "CODCANAL3"], as_index=False, dropna=False)[COLS_MONTOS].sum()
        )
    inc = pd.concat(partes, ignore_index=True)
    inc = inc[inc["AÑO_MES_OCU"] >= DESDE_CANAL]
    res = inc.groupby(["NUEVO RAMO", "CODCANAL3"], as_index=False, dropna=False)[COLS_MONTOS].sum()
    exportar_tabla(res, f"incurrido_canal_{SUFIJO}")


# =============================================================================
# 9. REVISIÓN ACTUARIAL (¿tienen sentido los outputs?)
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


def tc_implicito(base: pd.DataFrame) -> pd.DataFrame:
    """SOL/DOL por mes de movimiento = TC efectivamente aplicado. Debe ser una serie suave."""
    g = base.groupby("AÑO_MES_MOV")[["INCURRIDO_SOL", "INCURRIDO_DOL"]].sum()
    g = g[g["INCURRIDO_DOL"].abs() > 1].sort_index()
    g["TC_IMPLICITO"] = g["INCURRIDO_SOL"] / g["INCURRIDO_DOL"]
    g["VAR_MES"] = g["TC_IMPLICITO"].pct_change()
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


def revisar(res: dict) -> dict:
    """Controles de sentido actuarial. No exporta nada. Devuelve tablas + 'alertas'."""
    alertas: list[str] = []
    rev: dict = {}
    u = UMBRALES
    segmentos = {"nv": "NO VIDA", "v": "VIDA"}

    # --- cuadres de proceso ---
    c = res["cuadres"]
    if not c.empty:
        mal = c[~np.isclose(c["BRUTO_ORIGEN"], c["BRUTO_ENRIQUECIDO"])]
        for _, r in mal.iterrows():
            alertas.append(f"[CUADRE] {r['ARCHIVO']}: bruto origen != enriquecido (merge duplica/pierde filas)")
        mal = c[~np.isclose(c["SOL_DETALLE"], c["SOL_BASE"])]
        for _, r in mal.iterrows():
            alertas.append(f"[CUADRE] {r['ARCHIVO']}: base agrupada no cuadra con el detalle")
        for _, r in c[c["FILAS_ELIMINADAS"] > 0].iterrows():
            alertas.append(f"[INFO] {r['ARCHIVO']}: {r['FILAS_ELIMINADAS']} filas eliminadas (ramo ELIMINAR)")
        rev["cuadres"] = c

    if not res["atipicos_percentil"].empty:
        rev["atipicos_percentil"] = res["atipicos_percentil"]

    total_sol = 0.0
    for k, nombre in segmentos.items():
        base = res[f"base_{k}"]
        tris = res[f"tri_{k}"]
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

        # --- tipo de cambio efectivamente aplicado ---
        tc = tc_implicito(base)
        rev[f"tc_implicito_{k}"] = tc
        for _, r in tc[tc["VAR_MES"].abs() > u["salto_tc"]].iterrows():
            alertas.append(f"[{nombre}] TC implícito salta {r['VAR_MES']:.1%} en {int(r['AÑO_MES_MOV'])}")

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

        # --- triángulos ---
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
        for motivo in ["TC_NULO", "SIN_RAMO_MAESTRO", "SIN_MONEDA_MAESTRO", "FECHA_NULA"]:
            n = int((err["MOTIVO"] == motivo).sum())
            if n:
                alertas.append(f"[ERRORES] {n} filas con {motivo} (quedan fuera de sumas/triángulos)")

    rev["alertas"] = pd.DataFrame({"ALERTA": alertas})
    print(f"\n=== REVISIÓN: {len(alertas)} alertas ===")
    for a in alertas:
        print(" -", a)
    for k in ("nv", "v"):
        d = rev.get(f"diagnostico_triangulos_{k}")
        if d is not None:
            print(f"\n--- Diagnóstico triángulos {segmentos[k]} ---")
            print(d.round(3).to_string(index=False))
    print("\nTablas en rev:", ", ".join(rev.keys()))
    return rev


# =============================================================================
# 10. CORRER / EXPORTAR / MAIN
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


def correr() -> dict:
    """Procesa todos los archivos. No exporta."""
    m = cargar_maestros()
    bases_nv, bases_v, errores, cuadres, atipicos = [], [], [], [], []

    for nombre in sorted(os.listdir(RUTA)):
        tipo = clasificar_archivo(nombre)
        if tipo is None:
            log.info("Omitido: %s", nombre)
            continue

        df = leer_archivo(RUTA / nombre)
        partes = dividir_autos(df) if tipo == "autos" else {nombre: df}

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
    return {
        "base_nv": base_nv,
        "base_v": base_v,
        "errores": pd.concat(errores, ignore_index=True) if errores else pd.DataFrame(),
        "cuadres": pd.DataFrame(cuadres),
        "atipicos_percentil": pd.concat(atipicos, ignore_index=True) if atipicos else pd.DataFrame(),
        "tri_nv": construir_triangulos(base_nv) if not base_nv.empty else {},
        "tri_v": construir_triangulos(base_v) if not base_v.empty else {},
    }


def exportar(res: dict, bases: bool = True, errores: bool = True, cuadres: bool = True,
             atipicos: bool = True, triangulos: bool = True) -> None:
    """Exporta lo ya revisado.
    Bases, errores, cuadres y atípicos -> RUTA_OUTPUT.
    Triángulos -> RUTA_TRIANGULOS: triangulos_no_vida_<SUFIJO>.xlsx y triangulos_vida_<SUFIJO>.xlsx,
    con una hoja por ramo."""
    RUTA_OUTPUT.mkdir(parents=True, exist_ok=True)
    if cuadres:
        exportar_tabla(res["cuadres"], f"cuadres_{SUFIJO}")
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
        for clave, nombre in [("tri_nv", f"triangulos_no_vida_{SUFIJO}"), ("tri_v", f"triangulos_vida_{SUFIJO}")]:
            ruta = exportar_triangulos_excel(res[clave], nombre)
            if ruta:
                print(f"Triángulos: {ruta} ({len(res[clave])} hojas)")


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
