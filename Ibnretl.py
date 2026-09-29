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
  5. dif < 0: se exportan como error; se mantienen en triángulos salvo que
     EXCLUIR_DIF_NEGATIVO = True.
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

# Retención
CLAVE_RET_HIST = ["COD_CIA", "COD_RAMO", "NUM_SINI", "NUM_EXP"]
CLAVE_RET_XLSX = ["NUM_SINI", "NUM_EXP", "COD_COB"]
PRIORIDAD_RET = ["PCT_RET_XLSX", "PCT_RET_HIST"]  # REVISAR

# Triángulos
EXCLUIR_ATIPICOS_TRIANGULO = False
EXCLUIR_DIF_NEGATIVO = False  # REVISAR
DESDE_OCURRENCIA = None       # p.ej. 201901 para filtrar triángulos

# Procesos opcionales
RUN_POLIZAS = False   # exporta listas de pólizas (vida / no vida)
RUN_CANAL = False     # incurrido por ramo x canal
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

    # Atípicos (cuantía tiene prioridad sobre operativo)
    atip = (
        pd.read_excel(RUTA_MAESTROS / "Atipicos_202608.xlsx")[["NUM_SINI"]]
        .drop_duplicates()
        .assign(ATIPICOS="S", TIPO_ATIPICO="cuantia")
    )
    oper = (
        pd.read_excel(RUTA_MAESTROS / "Operativo.xlsx")[["NUM_SINI"]]
        .drop_duplicates()
        .assign(ATIPICOS="S", TIPO_ATIPICO="operativa")
    )
    atipicos = pd.concat([atip, oper], ignore_index=True).drop_duplicates("NUM_SINI", keep="first")

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

    df["ATIPICOS"] = df["ATIPICOS"].fillna("N")

    pct = pd.Series(np.nan, index=df.index)
    for col in PRIORIDAD_RET:
        pct = pct.fillna(df[col])
    df["RET_IMPUTADA"] = pct.isna()
    df["PCT_RET"] = pct.fillna(1.0)
    return df


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
    """Devuelve (base, errores, cuadre)."""
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
    if vida:
        df["NUEVO RAMO"] = np.where(df["NUM_POLIZA"] == POLIZA_ESSALUD, "ESSALUD-ACC", df["NUEVO RAMO"])
        mask = df["NUEVO RAMO"].eq("ELIMINAR")
        filas_eliminadas = int(mask.sum())
        df = df[~mask]

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
    }
    log.info("%s | TC nulos=%s | sin ramo=%s | dif<0=%s | ret imputada=%s",
             etiqueta, cuadre["TC_NULOS"], cuadre["SIN_RAMO"],
             cuadre["DIF_NEGATIVOS"], cuadre["RETENCION_IMPUTADA_1"])
    return base, errores, cuadre


# =============================================================================
# 6. TRIÁNGULOS
# =============================================================================
def construir_triangulos(base: pd.DataFrame, col: str = "INCURRIDO_MON_NETO") -> dict[str, pd.DataFrame]:
    """Un triángulo (incremental) por NUEVO RAMO, con meses y desarrollos completos."""
    b = base.dropna(subset=["NUEVO RAMO", "AÑO_MES_OCU", "dif"])
    if len(b) < len(base):
        log.warning("Triángulos: %s filas de la base sin ramo/fecha quedan fuera", len(base) - len(b))
    if EXCLUIR_ATIPICOS_TRIANGULO:
        b = b[b["ATIPICOS"] == "N"]
    if DESDE_OCURRENCIA is not None:
        b = b[b["AÑO_MES_OCU"] >= DESDE_OCURRENCIA]
    if EXCLUIR_DIF_NEGATIVO:
        b = b[b["dif"] >= 0]

    triangulos: dict[str, pd.DataFrame] = {}
    for ramo, g in b.groupby("NUEVO RAMO"):
        t = g.pivot_table(index="AÑO_MES_OCU", columns="dif", values=col, aggfunc="sum", fill_value=0)
        t.index = t.index.astype(int)
        t.columns = t.columns.astype(int)
        t = t.sort_index().sort_index(axis=1)

        f_min = pd.to_datetime(str(t.index.min()), format="%Y%m")
        f_max = pd.to_datetime(str(t.index.max()), format="%Y%m")
        meses = pd.date_range(f_min, f_max, freq="MS").strftime("%Y%m").astype(int).tolist()
        devs = list(range(int(t.columns.min()), int(t.columns.max()) + 1))

        t = t.reindex(index=meses, columns=devs).fillna(0)
        t.index.name = "AÑO_MES_OCU"
        t.columns.name = "dif"

        if not np.isclose(t.values.sum(), g[col].sum()):
            log.warning("Triángulo %s no cuadra con la base", ramo)
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


def exportar_triangulos(triangulos: dict[str, pd.DataFrame], nombre: str) -> None:
    ruta = RUTA_OUTPUT / f"{nombre}.xlsx"
    usados: set[str] = set()
    with pd.ExcelWriter(ruta) as xw:
        for ramo, t in triangulos.items():
            hoja = re.sub(r"[\[\]:*?/\\]", "_", str(ramo))[:28]
            k, candidata = 1, hoja
            while candidata in usados:
                k += 1
                candidata = f"{hoja}_{k}"
            usados.add(candidata)
            t.to_excel(xw, sheet_name=candidata)
    log.info("Exportado %s (%s hojas)", ruta.name, len(triangulos))


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
# 9. MAIN
# =============================================================================
def main() -> None:
    RUTA_OUTPUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(RUTA_OUTPUT / f"log_{SUFIJO}.txt", encoding="utf-8")],
    )

    m = cargar_maestros()
    bases_nv, bases_v, errores, cuadres = [], [], [], []

    for nombre in sorted(os.listdir(RUTA)):
        tipo = clasificar_archivo(nombre)
        if tipo is None:
            log.info("Omitido: %s", nombre)
            continue

        df = leer_archivo(RUTA / nombre)
        partes = dividir_autos(df) if tipo == "autos" else {nombre: df}

        for etiqueta, parte in partes.items():
            base, err, cuadre = procesar(parte, m, f"{nombre} | {etiqueta}", vida=(tipo == "vida"))
            (bases_v if tipo == "vida" else bases_nv).append(base)
            if not err.empty:
                errores.append(err.assign(ES_VIDA=(tipo == "vida")))
            cuadres.append(cuadre)

    cuadres_df = pd.DataFrame(cuadres)
    exportar_tabla(cuadres_df, f"cuadres_{SUFIJO}")

    err_all = pd.concat(errores, ignore_index=True) if errores else pd.DataFrame()
    for es_vida, sufijo_arch in [(False, ""), (True, "_vida")]:
        bases = bases_v if es_vida else bases_nv
        if not bases:
            continue
        base = pd.concat(bases, ignore_index=True)
        exportar_tabla(base, f"base{sufijo_arch}_final_{SUFIJO}")
        exportar_triangulos(construir_triangulos(base), f"triangulos{sufijo_arch}_{SUFIJO}")
        if not err_all.empty:
            e = err_all[err_all["ES_VIDA"] == es_vida]
            if not e.empty:
                exportar_tabla(e, f"errores{sufijo_arch}_final_{SUFIJO}")

    if RUN_POLIZAS:
        extraer_polizas()
    if RUN_CANAL:
        incurrido_por_canal(m)


if __name__ == "__main__":
    main()
