from __future__ import annotations

import csv
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# =============================================================================
# CONFIGURACION
# =============================================================================
RUTA = Path(r"D:\OneDrive - MAPFRE\Escritorio\Yo\TODO IBNR\2026\Cierre Septiembre_2026\no vida")

ARCHIVOS_NO_VIDA = {
    "IBNR_DETALLE_1_10_202608.csv": "accidentes",
    "IBNR_DETALLE_1_20_202608.csv": "incendios",
    "IBNR_DETALLE_1_21_202608.csv": "robo",
    "IBNR_DETALLE_1_22_202608.csv": "deshonestidad",
    "IBNR_DETALLE_1_23_202608.csv": "ramos tecnicos",
    "IBNR_DETALLE_1_24_202608.csv": "responsabilidad civil",
    "IBNR_DETALLE_1_25_202608.csv": "transporte",
    "IBNR_DETALLE_1_26_202608.csv": "cascos",
    "IBNR_DETALLE_1_27_202608.csv": "multiriesgo",
    "IBNR_DETALLE_1_30_202608.csv": "autos",
    "IBNR_DETALLE_1_31_202608.csv": "soat",
    "IBNR_DETALLE_1_40_202608.csv": "cauciones",
}

ARCHIVO_VIDA = "IBNR_DETALLE_2_202608.csv"
ATIPICOS_XLSX = Path(r"D:\OneDrive - MAPFRE\Archivos de Hernandez Bello, Diana Patricia - IBNR REVISION\Atipicos\2026\Cierre Septiembre\Atipicos_202608.xlsx")
OPERATIVO_XLSX = Path(r"D:\OneDrive - MAPFRE\Escritorio\Yo\TODO IBNR\py\Triangulos nuevo\Inputs\Operativo.xlsx")
RETENCION_XLSX = Path(r"D:\OneDrive - MAPFRE\Archivos de Hernandez Bello, Diana Patricia - IBNR REVISION\Atipicos\2026\Cierre Septiembre\Retención_2021_2026.xlsx")

# Retencion historica: MP corresponde a NO VIDA y MPV corresponde a VIDA.
# Se usa cuando el año de FEC_SINI es anterior a 2021.
RETENCION_HISTORICA_TXT = {
    "NO_VIDA": RUTA / "Data_Historica_MP_202512 (1).txt",
    "VIDA": RUTA / "Data_Historica_MPV_202512 (5).txt",
}
BD_RETENCION_HISTORICA = RUTA / "retencion_historica_temporal.sqlite"
CHUNKSIZE_RETENCION_HISTORICA = 300_000
SALIDA_VIDA_XLSX = RUTA / "Triangulos_mensuales_IBNR_vida.xlsx"
BD_TEMPORAL = RUTA / "triangulos_ibnr_temporal.sqlite"
TIPO_CAMBIO = 3.362
FECHA_INICIO = pd.Timestamp("2019-01-01")
CHUNKSIZE = 200_000
ORDEN_FECHA_AMBIGUA = "DMY"
BORRAR_BD_TEMPORAL_AL_FINAL = True

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
    "NOM_TERCERO", "PCT_REASEGURO"
]
ATIPICOS_MANUALES = {
    "100111426025001", "100120325000627", "100120323000125", "100130126006402",
    "100130125035464", "100130125026594", "100130125023463", "100130120021418",
    "100130122019364", "100130225005648", "100130225003691", "100130225003135",
    "100130225001909", "100130224001790", "100130223003989", "100130223000443",
    "100130211003635", "100130219007608", "100130220000144", "100171021000003",
    "100111426013439", "100111425020887", "100111426023623",
}
POLIZAS_EXCLUIDAS = {
    "2030812500020", "3010210003586", "3010510400074", "3020830003939",
    "3020910141442", "3020910141482", "3021033001120", "3021110187734"
}

def normalizar_nombre(x: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(x).strip().upper()).strip("_")

def normalizar_id(s: pd.Series) -> pd.Series:
    return (s.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
            .replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA, "<NA>": pd.NA}))

def numero(s: pd.Series) -> pd.Series:
    x = s.astype("string").str.strip().str.replace(" ", "", regex=False)
    ambos = x.str.contains(",", na=False) & x.str.contains(r"\.", na=False)
    coma_decimal = ambos & (x.str.rfind(",") > x.str.rfind("."))
    x = x.where(~coma_decimal, x.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    punto_decimal = ambos & ~coma_decimal
    x = x.where(~punto_decimal, x.str.replace(",", "", regex=False))
    solo_coma = x.str.contains(",", na=False) & ~x.str.contains(r"\.", na=False)
    x = x.where(~solo_coma, x.str.replace(",", ".", regex=False))
    return pd.to_numeric(x, errors="coerce")

def detectar_separador(ruta: Path) -> str:
    with ruta.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        muestra = f.read(200_000)
    try:
        return csv.Sniffer().sniff(muestra, delimiters=";,\t").delimiter
    except csv.Error:
        linea = muestra.splitlines()[0] if muestra else ""
        conteos = {";": linea.count(";"), ",": linea.count(","), "\t": linea.count("\t")}
        return max(conteos, key=conteos.get)

def tiene_cabecera(ruta: Path, sep: str) -> bool:
    cols = pd.read_csv(ruta, sep=sep, nrows=0, encoding="utf-8-sig").columns
    return len(set(map(normalizar_nombre, cols)) & set(COLUMNAS)) >= 10

def convertir_fecha_mixta(s: pd.Series, orden_ambiguo: str = "DMY") -> pd.Series:
    orden_ambiguo = orden_ambiguo.upper()
    if orden_ambiguo not in {"DMY", "MDY"}:
        raise ValueError("ORDEN_FECHA_AMBIGUA debe ser DMY o MDY")
    raw = s.astype("string").str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    nums = pd.to_numeric(raw, errors="coerce")
    mask_excel = nums.between(20_000, 80_000, inclusive="both")
    out.loc[mask_excel] = pd.Timestamp("1899-12-30") + pd.to_timedelta(nums.loc[mask_excel], unit="D")
    pendientes = out.isna() & raw.notna()
    txt = raw.loc[pendientes].str.replace(r"\s+", " ", regex=True)
    iso_mask = txt.str.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\D.*)?$", na=False)
    if iso_mask.any():
        out.loc[txt.index[iso_mask]] = pd.to_datetime(txt.loc[iso_mask], errors="coerce", yearfirst=True)
    compact_mask = txt.str.match(r"^\d{8}$", na=False)
    if compact_mask.any():
        compact = txt.loc[compact_mask]
        es_aaaammdd = compact.str[:4].astype(int).between(1900, 2200)
        out.loc[compact.index[es_aaaammdd]] = pd.to_datetime(compact.loc[es_aaaammdd], format="%Y%m%d", errors="coerce")
        restantes = compact.loc[~es_aaaammdd]
        if not restantes.empty:
            fmt = "%d%m%Y" if orden_ambiguo == "DMY" else "%m%d%Y"
            out.loc[restantes.index] = pd.to_datetime(restantes, format=fmt, errors="coerce")
    pendientes = out.isna() & raw.notna()
    txt = raw.loc[pendientes]
    partes = txt.str.extract(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:\D.*)?$")
    validas = partes.notna().all(axis=1)
    if validas.any():
        idx = partes.index[validas]
        primero = pd.to_numeric(partes.loc[idx, 0])
        segundo = pd.to_numeric(partes.loc[idx, 1])
        usar_dmy = primero.gt(12) | (segundo.le(12) & (orden_ambiguo == "DMY"))
        usar_mdy = segundo.gt(12) | (primero.le(12) & (orden_ambiguo == "MDY"))
        if usar_dmy.any(): out.loc[idx[usar_dmy]] = pd.to_datetime(txt.loc[idx[usar_dmy]], errors="coerce", dayfirst=True)
        if usar_mdy.any(): out.loc[idx[usar_mdy]] = pd.to_datetime(txt.loc[idx[usar_mdy]], errors="coerce", dayfirst=False)
    pendientes = out.isna() & raw.notna()
    if pendientes.any():
        out.loc[pendientes] = pd.to_datetime(raw.loc[pendientes], errors="coerce", dayfirst=(orden_ambiguo == "DMY"))
    return out

def leer_ids_excel(ruta: Path) -> set[str]:
    df = pd.read_excel(ruta, dtype=str)
    df.columns = [normalizar_nombre(c) for c in df.columns]
    if "NUM_SINI" not in df.columns: raise KeyError(f"{ruta.name}: falta NUM_SINI")
    return set(normalizar_id(df["NUM_SINI"]).dropna())

def elegir_columna(cols: Iterable[str], opciones: Iterable[str], obligatoria=True):
    disponibles = {normalizar_nombre(c): c for c in cols}
    for op in opciones:
        if normalizar_nombre(op) in disponibles: return disponibles[normalizar_nombre(op)]
    if obligatoria: raise KeyError(f"Falta una columna. Alternativas aceptadas: {list(opciones)}")
    return None

def cargar_retencion(ruta: Path):
    ret = pd.read_excel(ruta, dtype=str)
    ret.columns = [normalizar_nombre(c) for c in ret.columns]
    cs = elegir_columna(ret.columns, ["NUM_SINI"]); ce = elegir_columna(ret.columns, ["NUM_EXP"])
    cc = elegir_columna(ret.columns, ["COD_COB"]); ca = elegir_columna(ret.columns, ["ANIO_OPER", "ANO_OPER"])
    cp = elegir_columna(ret.columns, ["PCT_RET", "PORC_RET", "PCT_RETENCION"])
    cr = elegir_columna(ret.columns, ["REPETICION"], False)
    for c in [cs, ce, cc, ca]: ret[c] = normalizar_id(ret[c]).fillna("")
    ret["LLAVE"] = ret[cs] + ret[ce] + ret[cc]
    ret["CONCAT"] = ret["LLAVE"] + ret[ca]
    ret["PCT"] = numero(ret[cp].astype("string").str.replace("%", "", regex=False))
    ret.loc[ret["PCT"] > 1, "PCT"] /= 100
    repetidas = set(ret.loc[ret["LLAVE"].duplicated(False), "LLAVE"])
    if cr: repetidas |= set(ret.loc[ret[cr].astype(str).str.upper().str.contains("REPET", na=False), "LLAVE"])
    simples = ret.loc[~ret["LLAVE"].isin(repetidas)].dropna(subset=["PCT"])
    mapa_llave = simples.drop_duplicates("LLAVE", keep="last").set_index("LLAVE")["PCT"].to_dict()
    mapa_concat = ret.dropna(subset=["PCT"]).drop_duplicates("CONCAT", keep="last").set_index("CONCAT")["PCT"].to_dict()
    return mapa_llave, mapa_concat, repetidas

def normalizar_id_cientifico(s: pd.Series) -> pd.Series:
    def convertir(valor):
        if pd.isna(valor): return None
        txt = str(valor).strip()
        if not txt or txt.upper() in {"NAN", "NONE", "<NA>"}: return None
        try:
            d = Decimal(txt.replace(",", "."))
            if d == d.to_integral_value(): return format(d.quantize(Decimal("1")), "f")
        except (InvalidOperation, ValueError): pass
        return re.sub(r"\.0$", "", txt)
    return s.map(convertir).astype("string")

def crear_concat_historico(df: pd.DataFrame) -> pd.Series:
    partes = [normalizar_id_cientifico(df[c]).fillna("") for c in ["COD_CIA", "COD_RAMO", "NUM_SINI", "NUM_EXP"]]
    return "'" + partes[0] + partes[1] + partes[2] + partes[3]

def obtener_fecha_minima_retencion_excel(ruta: Path) -> pd.Timestamp:
    ret = pd.read_excel(ruta, dtype=str); ret.columns = [normalizar_nombre(c) for c in ret.columns]
    for nombre in ["FEC_MVTO", "FECHA_MVTO", "FEC_INICIO", "FECHA_INICIO", "FEC_VIGENCIA", "FECHA_VIGENCIA", "FEC_ACTU", "FECHA"]:
        if nombre in ret.columns:
            fechas = convertir_fecha_mixta(ret[nombre], ORDEN_FECHA_AMBIGUA).dropna()
            if not fechas.empty: return fechas.min().normalize()
    ca = elegir_columna(ret.columns, ["ANIO_OPER", "ANO_OPER"])
    anios = pd.to_numeric(ret[ca], errors="coerce").dropna()
    if anios.empty: raise ValueError("No se pudo determinar la fecha minima del Excel de retencion")
    print("AVISO: el Excel no tiene fecha exacta. Se usa el 01/01 del menor ANIO_OPER.")
    return pd.Timestamp(year=int(anios.min()), month=1, day=1)

def abrir_bd_retencion_historica(ruta: Path) -> sqlite3.Connection:
    if ruta.exists(): ruta.unlink()
    con = sqlite3.connect(ruta); con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL")
    con.execute("CREATE TABLE ret_hist (tipo_base TEXT NOT NULL, concat TEXT NOT NULL, pct REAL NOT NULL, PRIMARY KEY (tipo_base, concat)) WITHOUT ROWID")
    return con

def cargar_retencion_historica_txt(rutas: dict[str, Path], con: sqlite3.Connection):
    for tipo_base, ruta in rutas.items():
        sep = detectar_separador(ruta)
        lector = pd.read_csv(ruta, sep=sep, dtype=str, encoding="utf-8-sig", chunksize=CHUNKSIZE_RETENCION_HISTORICA, low_memory=False, on_bad_lines="error")
        total_leidas = 0
        for i, parte in enumerate(lector, 1):
            parte.columns = [normalizar_nombre(c) for c in parte.columns]
            requeridas = ["COD_CIA", "COD_RAMO", "NUM_SINI", "NUM_EXP", "PCT_REASEGURO"]
            faltan = [c for c in requeridas if c not in parte.columns]
            if faltan: raise ValueError(f"{ruta.name}: faltan columnas {faltan}; separador detectado: {sep!r}")
            parte["CONCAT"] = crear_concat_historico(parte)
            parte["AUX_PCT"] = numero(parte["PCT_REASEGURO"].astype("string").str.replace("%", "", regex=False)) / 100.0
            parte = parte.dropna(subset=["CONCAT", "AUX_PCT"])
            filas = [(tipo_base, concat, float(aux_pct)) for concat, aux_pct in parte[["CONCAT", "AUX_PCT"]].itertuples(index=False, name=None)]
            con.executemany("INSERT OR IGNORE INTO ret_hist(tipo_base, concat, pct) VALUES (?, ?, ?)", filas)
            con.commit(); total_leidas += len(parte)
            print(f"Historico {tipo_base} | {ruta.name} | bloque {i} | filas validas acumuladas: {total_leidas:,}")
    con.execute("ANALYZE"); con.commit()

def buscar_pct_historico(chunk: pd.DataFrame, tipo_base: str, con_hist: sqlite3.Connection) -> pd.Series:
    concat = crear_concat_historico(chunk)
    unicas = pd.DataFrame({"concat": concat.drop_duplicates()})
    con_hist.execute("DROP TABLE IF EXISTS temp.solicitudes")
    con_hist.execute("CREATE TEMP TABLE solicitudes (concat TEXT PRIMARY KEY) WITHOUT ROWID")
    con_hist.executemany("INSERT OR IGNORE INTO solicitudes(concat) VALUES (?)", [(x,) for x in unicas["concat"].tolist()])
    filas = con_hist.execute("SELECT s.concat, h.pct FROM solicitudes s JOIN ret_hist h ON h.concat=s.concat WHERE h.tipo_base=?", (tipo_base,)).fetchall()
    return concat.map(dict(filas)).astype(float)

def abrir_bd(ruta: Path) -> sqlite3.Connection:
    if ruta.exists(): ruta.unlink()
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL"); con.execute("PRAGMA temp_store=FILE"); con.execute("PRAGMA cache_size=-200000")
    con.execute("""CREATE TABLE agg (subgrupo TEXT NOT NULL, mes_sini TEXT NOT NULL, num_sini TEXT NOT NULL, mes_mvto TEXT NOT NULL, bruto_usd REAL NOT NULL, bruto_pen REAL NOT NULL, neto_usd REAL NOT NULL, neto_pen REAL NOT NULL, bruto_op_usd REAL NOT NULL, bruto_op_pen REAL NOT NULL, neto_op_usd REAL NOT NULL, neto_op_pen REAL NOT NULL, PRIMARY KEY (subgrupo, mes_sini, num_sini, mes_mvto)) WITHOUT ROWID""")
    return con

def insertar_resumen(con: sqlite3.Connection, resumen: pd.DataFrame):
    if resumen.empty: return
    con.executemany("""INSERT INTO agg VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(subgrupo, mes_sini, num_sini, mes_mvto) DO UPDATE SET bruto_usd=bruto_usd+excluded.bruto_usd, bruto_pen=bruto_pen+excluded.bruto_pen, neto_usd=neto_usd+excluded.neto_usd, neto_pen=neto_pen+excluded.neto_pen, bruto_op_usd=bruto_op_usd+excluded.bruto_op_usd, bruto_op_pen=bruto_op_pen+excluded.bruto_op_pen, neto_op_usd=neto_op_usd+excluded.neto_op_usd, neto_op_pen=neto_op_pen+excluded.neto_op_pen""", list(resumen.itertuples(index=False, name=None)))
    con.commit()

def preparar_chunk(chunk, mapa_llave, mapa_concat, repetidas, atipicos_normales, atipicos_op, tipo_base, producto=None, con_hist=None, fecha_min_retencion_excel=None):
    chunk.columns = COLUMNAS
    for c in ["NUM_SINI", "NUM_POLIZA", "NUM_EXP", "COD_COB", "PERIODO", "SUBGRUPO", "COD_RAMO"]: chunk[c] = normalizar_id(chunk[c])
    if tipo_base == "NO_VIDA": chunk["SUBGRUPO_SALIDA"] = producto
    elif tipo_base == "VIDA":
        cod_ramo = pd.to_numeric(chunk["COD_RAMO"], errors="coerce"); subgrupo_original = chunk["SUBGRUPO"]
        chunk = chunk.loc[subgrupo_original.notna() & subgrupo_original.ne("SEGURO DE VIDA FONDO INVERSION")].copy()
        subgrupo_original = chunk["SUBGRUPO"]; cod_ramo = pd.to_numeric(chunk["COD_RAMO"], errors="coerce")
        chunk["SUBGRUPO_SALIDA"] = np.where(cod_ramo.eq(611) & subgrupo_original.eq("OTROS"), "DESGRAVAMEN", subgrupo_original)
        chunk["SUBGRUPO_SALIDA"] = np.where(pd.Series(chunk["SUBGRUPO_SALIDA"], index=chunk.index).eq("VG MINSA"), "VIDA GRUPO", chunk["SUBGRUPO_SALIDA"])
        chunk["SUBGRUPO_SALIDA"] = np.where(pd.Series(chunk["SUBGRUPO_SALIDA"], index=chunk.index).eq("OTROS"), "MASIVOS_OTROS", chunk["SUBGRUPO_SALIDA"])
    else: raise ValueError(f"tipo_base no valido: {tipo_base}")
    chunk["FEC_SINI_DT"] = convertir_fecha_mixta(chunk["FEC_SINI"], ORDEN_FECHA_AMBIGUA)
    chunk["FEC_MVTO_DT"] = convertir_fecha_mixta(chunk["FEC_MVTO"], ORDEN_FECHA_AMBIGUA)
    chunk["MES_SINI"] = chunk["FEC_SINI_DT"].dt.strftime("%Y-%m"); chunk["MES_MVTO"] = chunk["FEC_MVTO_DT"].dt.strftime("%Y-%m")
    chunk = chunk.loc[chunk["FEC_SINI_DT"].ge(FECHA_INICIO) & chunk["FEC_MVTO_DT"].ge(FECHA_INICIO)].copy()
    columnas_vacias = ["SUBGRUPO", "MES_SINI", "NUM_SINI", "MES_MVTO", "BRUTO_USD", "BRUTO_PEN", "NETO_USD", "NETO_PEN", "BRUTO_OP_USD", "BRUTO_OP_PEN", "NETO_OP_USD", "NETO_OP_PEN"]
    if chunk.empty: return pd.DataFrame(columns=columnas_vacias)
    pago = numero(chunk["PAGO_BRUTO"]).fillna(0.0); reserva = numero(chunk["RESERVA_BRUTO"]).fillna(0.0); moneda = numero(chunk["COD_MON"]); incurrido = pago + reserva
    chunk["BRUTO_USD"] = incurrido / np.where(moneda.eq(1), TIPO_CAMBIO, 1.0)
    chunk["BRUTO_PEN"] = incurrido * np.where(moneda.eq(2), TIPO_CAMBIO, 1.0)
    periodo_num = pd.to_numeric(chunk["PERIODO"], errors="coerce")
    anio = periodo_num.floordiv(100).astype("Int64").astype("string")
    anio_fecha = chunk["FEC_MVTO_DT"].dt.year.astype("Int64").astype("string")
    anio = anio.where(anio.ne("<NA>"), anio_fecha).replace("<NA>", "")
    llave = chunk["NUM_SINI"].fillna("") + chunk["NUM_EXP"].fillna("") + chunk["COD_COB"].fillna("")
    concat = llave + anio.fillna("")
    pct_excel = llave.map(mapa_llave); pct_excel = pct_excel.where(~llave.isin(repetidas), concat.map(mapa_concat))

    # CORRECCION UNICA: replica =SI(AÑO(FEC_SINI)<2021; ...; ...)
    usar_historico = chunk["FEC_SINI_DT"].dt.year.lt(2021)
    pct_historico = pd.Series(np.nan, index=chunk.index, dtype=float)
    if usar_historico.any():
        pct_historico.loc[usar_historico] = buscar_pct_historico(chunk.loc[usar_historico], tipo_base, con_hist)
    pct = pct_excel.where(~usar_historico, pct_historico).fillna(1.0)
    chunk["NETO_USD"] = chunk["BRUTO_USD"] * pct; chunk["NETO_PEN"] = chunk["BRUTO_PEN"] * pct
    excluir_normal = chunk["NUM_SINI"].isin(atipicos_normales) | chunk["NUM_POLIZA"].isin(POLIZAS_EXCLUIDAS)
    chunk = chunk.loc[~excluir_normal].copy(); excluir_op = chunk["NUM_SINI"].isin(atipicos_op)
    chunk["BRUTO_OP_USD"] = chunk["BRUTO_USD"].where(~excluir_op, 0.0); chunk["BRUTO_OP_PEN"] = chunk["BRUTO_PEN"].where(~excluir_op, 0.0)
    chunk["NETO_OP_USD"] = chunk["NETO_USD"].where(~excluir_op, 0.0); chunk["NETO_OP_PEN"] = chunk["NETO_PEN"].where(~excluir_op, 0.0)
    chunk["SUBGRUPO"] = pd.Series(chunk["SUBGRUPO_SALIDA"], index=chunk.index).astype("string").fillna("SIN_SUBGRUPO")
    chunk = chunk.dropna(subset=["MES_SINI", "MES_MVTO", "NUM_SINI"])
    claves = ["SUBGRUPO", "MES_SINI", "NUM_SINI", "MES_MVTO"]
    valores = ["BRUTO_USD", "BRUTO_PEN", "NETO_USD", "NETO_PEN", "BRUTO_OP_USD", "BRUTO_OP_PEN", "NETO_OP_USD", "NETO_OP_PEN"]
    return chunk.groupby(claves, as_index=False, observed=True)[valores].sum()

MESES_ES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Oct", 11: "Nov", 12: "Dic"}

def nombre_hoja(texto: str, usados: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "_", texto).strip() or "SIN_NOMBRE"; base = base[:31]; nombre, n = base, 2
    while nombre.lower() in usados:
        suf = f"_{n}"; nombre = base[:31-len(suf)] + suf; n += 1
    usados.add(nombre.lower()); return nombre

def nombre_archivo_seguro(texto: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", texto).strip() or "SIN_NOMBRE"

def rango_meses(inicio_texto: str, fin_texto: str) -> list[str]:
    inicio = max(pd.Timestamp(inicio_texto + "-01"), FECHA_INICIO); fin = pd.Timestamp(fin_texto + "-01")
    return [] if fin < inicio else pd.period_range(inicio, fin, freq="M").astype(str).tolist()

def diferencia_meses(mes_inicial: str, mes_final: str) -> int:
    return int(pd.Period(mes_final, freq="M").ordinal - pd.Period(mes_inicial, freq="M").ordinal)

def crear_hoja_triangulo_tabular(wb, con, subgrupo, etiqueta, campo, usados):
    # Mostrar elementos sin datos, equivalente a la opcion de una tabla dinamica.
    # Todas las hojas usan el mismo rango completo de meses disponible en la base,
    # aunque un subgrupo no tenga movimientos o siniestros en alguno de esos meses.
    limites = con.execute(
        "SELECT MIN(mes_sini), MAX(mes_sini), MIN(mes_mvto), MAX(mes_mvto) FROM agg"
    ).fetchone()
    if not limites or limites[0] is None: return

    # Las filas comienzan en FECHA_INICIO y llegan hasta el ultimo mes de siniestro.
    # Las columnas comienzan en FECHA_INICIO y llegan hasta el ultimo mes de movimiento.
    # Los meses sin registros se conservan y se exportan con valor 0.00.
    meses_sini = rango_meses(FECHA_INICIO.strftime("%Y-%m"), limites[1])
    meses_mvto = rango_meses(FECHA_INICIO.strftime("%Y-%m"), limites[3])
    if len(meses_mvto) + 2 > 16_384: raise ValueError(f"{subgrupo}: el triangulo supera 16,384 columnas de Excel")
    ws = wb.create_sheet(nombre_hoja(f"{subgrupo}_{etiqueta}", usados)); ws.sheet_view.showGridLines = False; ws.freeze_panes = "C3"
    ws.column_dimensions["A"].width = 12; ws.column_dimensions["B"].width = 10
    azul = PatternFill("solid", fgColor="B7DEE8"); verde = PatternFill("solid", fgColor="C6E0B4"); borde = Side(style="thin", color="8EA9DB")
    formato_num = '#,##0.00;[Red](#,##0.00);0.00'
    ws.cell(1, 1, "Año FEC_SINI"); ws.cell(1, 2, "Mes FEC_SINI"); ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1); ws.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)
    col, bloques_anio = 3, {}
    for mes in meses_mvto:
        fecha = pd.Timestamp(mes + "-01"); bloques_anio.setdefault(fecha.year, [col, col])[1] = col; ws.cell(2, col, MESES_ES[fecha.month]); ws.column_dimensions[get_column_letter(col)].width = 13; col += 1
    for anio, (c1, c2) in bloques_anio.items():
        if c1 != c2: ws.merge_cells(start_row=1, start_column=c1, end_row=1, end_column=c2)
        ws.cell(1, c1, anio)
    for row in ws.iter_rows(min_row=1, max_row=2, min_col=1, max_col=2+len(meses_mvto)):
        for cell in row: cell.fill = azul; cell.font = Font(bold=True, color="000000"); cell.alignment = Alignment(horizontal="center", vertical="center"); cell.border = Border(bottom=borde)
    consulta = f"SELECT mes_sini, mes_mvto, SUM({campo}) FROM agg WHERE subgrupo=? GROUP BY mes_sini, mes_mvto ORDER BY mes_sini, mes_mvto"
    datos = {(a, b): float(v or 0.0) for a, b, v in con.execute(consulta, (subgrupo,))}; fila, filas_por_anio = 3, {}
    for mes_sini in meses_sini:
        fecha_sini = pd.Timestamp(mes_sini + "-01"); filas_por_anio.setdefault(fecha_sini.year, [fila, fila])[1] = fila; ws.cell(fila, 2, MESES_ES[fecha_sini.month])
        for j, mes_mvto in enumerate(meses_mvto, start=3):
            c = ws.cell(fila, j, datos.get((mes_sini, mes_mvto), 0.0)); c.number_format = formato_num; c.alignment = Alignment(horizontal="right")
        fila += 1
    for anio, (f1, f2) in filas_por_anio.items():
        if f1 != f2: ws.merge_cells(start_row=f1, start_column=1, end_row=f2, end_column=1)
        c = ws.cell(f1, 1, anio); c.font = Font(bold=True); c.alignment = Alignment(horizontal="center", vertical="top")
    for r in range(3, fila): ws.cell(r, 2).alignment = Alignment(horizontal="left")
    for f1, _ in filas_por_anio.values():
        for c in range(1, 3 + len(meses_mvto)): ws.cell(f1, c).border = Border(top=borde)
    ws.auto_filter.ref = f"A2:{get_column_letter(2+len(meses_mvto))}{fila-1}"
    max_desarrollo = max(0, diferencia_meses(meses_sini[0], meses_mvto[-1]))
    if max_desarrollo + 3 > 16_384: raise ValueError(f"{subgrupo}: el incremental supera 16,384 columnas de Excel")
    fila_titulo_inc = fila + 2; fila_cabecera_inc = fila_titulo_inc + 1; fila_inicio_inc = fila_cabecera_inc + 1
    ws.cell(fila_titulo_inc, 1, "TRIANGULO INCREMENTAL"); ws.cell(fila_titulo_inc, 1).font = Font(bold=True, size=12)
    ws.cell(fila_cabecera_inc, 1, "Año FEC_SINI"); ws.cell(fila_cabecera_inc, 2, "Mes FEC_SINI")
    for desarrollo in range(max_desarrollo + 1): ws.cell(fila_cabecera_inc, 3 + desarrollo, desarrollo); ws.column_dimensions[get_column_letter(3 + desarrollo)].width = 13
    for cell in ws[fila_cabecera_inc][:3 + max_desarrollo]: cell.fill = verde; cell.font = Font(bold=True, color="000000"); cell.alignment = Alignment(horizontal="center", vertical="center"); cell.border = Border(bottom=borde)
    fila_inc = fila_inicio_inc; filas_inc_por_anio = {}; ultimo_mvto = pd.Period(meses_mvto[-1], freq="M")
    for mes_sini in meses_sini:
        fecha_sini = pd.Timestamp(mes_sini + "-01"); periodo_sini = pd.Period(mes_sini, freq="M"); filas_inc_por_anio.setdefault(fecha_sini.year, [fila_inc, fila_inc])[1] = fila_inc; ws.cell(fila_inc, 2, MESES_ES[fecha_sini.month])
        for desarrollo in range(max_desarrollo + 1):
            periodo_mvto = periodo_sini + desarrollo; c = ws.cell(fila_inc, 3 + desarrollo)
            if periodo_mvto <= ultimo_mvto: c.value = datos.get((mes_sini, str(periodo_mvto)), 0.0); c.number_format = formato_num; c.alignment = Alignment(horizontal="right")
            else: c.value = None
        fila_inc += 1
    for anio, (f1, f2) in filas_inc_por_anio.items():
        if f1 != f2: ws.merge_cells(start_row=f1, start_column=1, end_row=f2, end_column=1)
        c = ws.cell(f1, 1, anio); c.font = Font(bold=True); c.alignment = Alignment(horizontal="center", vertical="top")
    for r in range(fila_inicio_inc, fila_inc): ws.cell(r, 2).alignment = Alignment(horizontal="left")
    for f1, _ in filas_inc_por_anio.values():
        for c in range(1, 4 + max_desarrollo): ws.cell(f1, c).border = Border(top=borde)

def exportar_excel(con, salida):
    wb = Workbook(); wb.remove(wb.active); usados = set()
    specs = [("Bruto_USD", "bruto_usd"), ("Bruto_PEN", "bruto_pen"), ("Neto_USD", "neto_usd"), ("Neto_PEN", "neto_pen"), ("Bruto_OP_USD", "bruto_op_usd"), ("Bruto_OP_PEN", "bruto_op_pen"), ("Neto_OP_USD", "neto_op_usd"), ("Neto_OP_PEN", "neto_op_pen")]
    subgrupos = [r[0] for r in con.execute("SELECT DISTINCT subgrupo FROM agg ORDER BY subgrupo")]
    for subgrupo in subgrupos:
        for etiqueta, campo in specs: crear_hoja_triangulo_tabular(wb, con, subgrupo, etiqueta, campo, usados); print(f"Hoja creada: {subgrupo}_{etiqueta}")
    if not wb.worksheets: ws = wb.create_sheet("SIN_DATOS"); ws["A1"] = "No se encontraron datos para exportar"
    wb.save(salida); return len(subgrupos), len(subgrupos) * len(specs)

def procesar_archivo(ruta_csv, tipo_base, producto, con, mapa_llave, mapa_concat, repetidas, atipicos_normales, atipicos_op, con_hist, fecha_min_retencion_excel):
    sep = detectar_separador(ruta_csv); header_existente = tiene_cabecera(ruta_csv, sep)
    lector = pd.read_csv(ruta_csv, sep=sep, header=0 if header_existente else None, dtype=str, encoding="utf-8-sig", chunksize=CHUNKSIZE, low_memory=False, on_bad_lines="error")
    total = 0
    for i, chunk in enumerate(lector, 1):
        if header_existente:
            chunk.columns = [normalizar_nombre(c) for c in chunk.columns]; faltan = [c for c in COLUMNAS if c not in chunk.columns]
            if faltan: raise ValueError(f"{ruta_csv.name}: faltan columnas: {faltan}")
            chunk = chunk[COLUMNAS]
        elif len(chunk.columns) != len(COLUMNAS): raise ValueError(f"{ruta_csv.name}: {len(chunk.columns)} columnas; esperadas: {len(COLUMNAS)}; separador: {sep!r}")
        total += len(chunk)
        resumen = preparar_chunk(chunk, mapa_llave, mapa_concat, repetidas, atipicos_normales, atipicos_op, tipo_base=tipo_base, producto=producto, con_hist=con_hist, fecha_min_retencion_excel=fecha_min_retencion_excel)
        insertar_resumen(con, resumen); print(f"{producto} | Bloque {i:,} | Filas leidas: {total:,} | Grupos: {len(resumen):,}")
    print(f"Base terminada: {ruta_csv.name} | Filas: {total:,} | Separador: {sep!r} | Cabecera: {header_existente}"); return total

def main():
    if not RUTA.exists(): raise FileNotFoundError(f"No existe la ruta: {RUTA}")
    rutas_requeridas = [RUTA / n for n in ARCHIVOS_NO_VIDA] + [RUTA / ARCHIVO_VIDA, ATIPICOS_XLSX, OPERATIVO_XLSX, RETENCION_XLSX] + list(RETENCION_HISTORICA_TXT.values())
    faltantes = [str(r) for r in rutas_requeridas if not r.exists()]
    if faltantes: raise FileNotFoundError("No existen estos archivos:\n" + "\n".join(faltantes))
    atipicos_normales = leer_ids_excel(ATIPICOS_XLSX) | ATIPICOS_MANUALES; atipicos_op = leer_ids_excel(OPERATIVO_XLSX)
    mapa_llave, mapa_concat, repetidas = cargar_retencion(RETENCION_XLSX)
    fecha_min_retencion_excel = obtener_fecha_minima_retencion_excel(RETENCION_XLSX); print(f"Fecha minima del Excel de retencion: {fecha_min_retencion_excel.date()}")
    con_hist = abrir_bd_retencion_historica(BD_RETENCION_HISTORICA); cargar_retencion_historica_txt(RETENCION_HISTORICA_TXT, con_hist)
    total_general = 0; salidas_creadas = []
    try:
        for nombre, producto in ARCHIVOS_NO_VIDA.items():
            con = abrir_bd(BD_TEMPORAL)
            try:
                total_general += procesar_archivo(RUTA / nombre, "NO_VIDA", producto, con, mapa_llave, mapa_concat, repetidas, atipicos_normales, atipicos_op, con_hist, fecha_min_retencion_excel)
                con.execute("ANALYZE"); con.commit(); salida_producto = RUTA / f"Triangulos_mensuales_IBNR_{nombre_archivo_seguro(producto)}.xlsx"
                subgrupos, hojas = exportar_excel(con, salida_producto); salidas_creadas.append(salida_producto)
                print(f"Excel No Vida terminado: {salida_producto}"); print(f"Clasificaciones: {subgrupos} | Hojas: {hojas}")
            finally:
                con.close()
                if BORRAR_BD_TEMPORAL_AL_FINAL and BD_TEMPORAL.exists(): BD_TEMPORAL.unlink()
        con = abrir_bd(BD_TEMPORAL)
        try:
            total_general += procesar_archivo(RUTA / ARCHIVO_VIDA, "VIDA", "vida", con, mapa_llave, mapa_concat, repetidas, atipicos_normales, atipicos_op, con_hist, fecha_min_retencion_excel)
            con.execute("ANALYZE"); con.commit(); subgrupos_vida, hojas_vida = exportar_excel(con, SALIDA_VIDA_XLSX); salidas_creadas.append(SALIDA_VIDA_XLSX)
            print(f"Excel Vida terminado: {SALIDA_VIDA_XLSX}"); print(f"Clasificaciones Vida: {subgrupos_vida} | Hojas: {hojas_vida}")
        finally:
            con.close()
            if BORRAR_BD_TEMPORAL_AL_FINAL and BD_TEMPORAL.exists(): BD_TEMPORAL.unlink()
        print("\nPROCESO TERMINADO"); print(f"Fecha inicial de siniestro: {FECHA_INICIO.date()}"); print(f"Filas leidas entre todas las bases: {total_general:,}"); print(f"Archivos Excel creados: {len(salidas_creadas)}")
        for salida in salidas_creadas: print(f" - {salida}")
    finally:
        con_hist.close()
        if BORRAR_BD_TEMPORAL_AL_FINAL and BD_RETENCION_HISTORICA.exists(): BD_RETENCION_HISTORICA.unlink()
        if BD_TEMPORAL.exists(): BD_TEMPORAL.unlink()

main()
