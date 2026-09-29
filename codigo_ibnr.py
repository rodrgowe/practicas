

# 1 formatos de datos
# 1.2 calidad del dato
# 2 nuevas columnas
# 3 triangulos
# 4 triangulos chainladder
# 4.1 base triangulos chainladder
# 4.2 base neta
# 5 excel
# 6 logs

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
import os


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


RUTA = r"C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\7_Agosto IBNR\00_DATA_CIERRE_DETALLADA\DATOS_BRUTOS"
ruta_output = r'C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\suficiencia\base'
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

ruta_gen = r'C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\suficiencia\maestros'



os.chdir(ruta_gen)
ATIPICOS_XLSX = pd.read_excel(r"Atipicos_202608.xlsx")
OPERATIVO_XLSX = pd.read_excel(r"Operativo.xlsx")
RETENCION_XLSX = pd.read_excel(r"Retención_2021_2026.xlsx")
tc = pd.read_excel(r"TC_HISTO.xlsx")
ret_novida =  pd.read_csv("Data_Historica_MP_202512.txt", sep = ';')
ret_vida = pd.read_csv("Data_Historica_MPV_202512.txt", sep = ';')
maestros = pd.read_excel(r'maestros.xlsx')
tc.columns = ["AÑO_MES_MOV",'TIPO_CAMBIO']
ret_ant = pd.concat((ret_novida,ret_vida), axis = 0)
ret_ant = (
    ret_ant
    .sort_values('PCT_REASEGURO', ascending=False)
    .drop_duplicates(
        subset=['COD_CIA', 'COD_RAMO', 'NUM_SINI', 'NUM_EXP'],
        keep='first'
    )
)

RETENCION_XLSX = (
    RETENCION_XLSX
    .sort_values('PCT_RET', ascending=False)
    .drop_duplicates(
        subset=['NUM_SINI', 'NUM_EXP', 'COD_COB'],
        keep='first'
    )
)

RETENCION_XLSX['PCT_RET'] = np.where(RETENCION_XLSX['PCT_RET']>1,1,RETENCION_XLSX['PCT_RET'])
ret_ant['PCT_REASEGURO'] = ret_ant['PCT_REASEGURO']/100

# seleccion de archivos
archivos = os.listdir(RUTA)
archivos1 = [x for x in archivos if '207'not in x and '_28_' not in x and '_11_' not in x]
archivos1
#atipicos
operati = OPERATIVO_XLSX[['NUM_SINI']].drop_duplicates()
operati['ATIPICOS'] = 'S'
atipi1 = ATIPICOS_XLSX[['NUM_SINI']]
atipi1['ATIPICOS'] = 'S'
atipi1['tipo'] = 'cuantia'
operati['tipo'] = 'operativa'
atipi = pd.concat((atipi1,operati), axis = 0)

atipi = atipi.drop_duplicates()
atipi = atipi.drop_duplicates(subset='NUM_SINI')
atipi.to_csv(r'atipi')
ret_ant.columns = ['COD_CIA', 'COD_RAMO', 'NUM_SINI', 'NUM_EXP', 'PCT_RETENCION']
RETENCION_XLSX.columns = ['NUM_SINI', 'NUM_EXP', 'COD_COB', 'PCT_RETENCION', 'ANIO_OPER']
ret_ant['PCT_RETENCION'].value_counts()
RETENCION_XLSX['PCT_RETENCION'].value_counts()

#%%

# extraer polizas
polizas = []
polizas_vida = [] 
'esto se usa para cuando pidan polizas para agregarles el COD_NIVEL'

#=====================================================================#


RUTA = r'C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\7_Agosto IBNR\00_DATA_CIERRE_DETALLADA\DATOS_BRUTOS'
archivos = os.listdir(RUTA)

for x in archivos:
    if "_2_" not in x and "_28_" not in x and "_207_" not in x:
        df = pd.read_csv(os.path.join(RUTA, x), sep=';')
        df.columns = COLUMNAS
        polizas.extend(df['NUM_POLIZA'].unique())

    elif "_2_" in x:
        df = pd.read_csv(os.path.join(RUTA, x), sep=';')
        df.columns = COLUMNAS
        polizas_vida.extend(df['NUM_POLIZA'].unique())
    else:
        print(x)

polizas_nv = pd.DataFrame({'NUM_POLIZA': polizas})
polizas_v = pd.DataFrame({'NUM_POLIZA': polizas_vida})
#%%
#lectura archivos
nov = pd.read_excel(r'C:/Users/crolaz1/Downloads/polizas_novida_CODCANAL3.xlsx')
vid = pd.read_excel(r'C:/Users/crolaz1/Downloads/polizas_vida_CODCANAL3 1.xlsx')
sct = pd.read_excel(r'C:/Users/crolaz1/Downloads/SCTR_CODCANAL3.xlsx')

nov.columns
vid.columns = ['ID', 'NUM_POLIZA', 'CODCANAL3']
sct.columns = ['ID', 'NUM_POLIZA', 'CODCANAL3']


tot = pd.concat((nov,vid,sct),axis = 0)
tot = tot[['NUM_POLIZA', 'CODCANAL3']].drop_duplicates()

inc = []

archivos2 = [x for x in archivos if '207'not in x and '_28_' not in x]

for x in archivos2:
    if "_207_" not in x:
        print(x)
        df = pd.read_csv(os.path.join(RUTA,x), sep = ';')
        df.columns = COLUMNAS
        print(f"negocio: {df['SUBGRUPO'][0]}")
        print(df.shape)
        df['FEC_SINI'] = pd.to_datetime(df['FEC_SINI'], format = 'mixed', dayfirst = True)
        df['FEC_MVTO'] = pd.to_datetime(df['FEC_MVTO'], format = 'mixed', dayfirst = True)
        df['AÑO_MES_OCU'] = df['FEC_SINI'].dt.strftime("%Y%m")
        df['AÑO_MES_MOV'] = df['FEC_MVTO'].dt.strftime("%Y%m")
        df['dif'] = (
            (df['FEC_MVTO'].dt.year - df['FEC_SINI'].dt.year) * 12
            + (df['FEC_MVTO'].dt.month - df['FEC_SINI'].dt.month)
        )
        df['AÑO_MES_MOV'] = df['AÑO_MES_MOV'].astype(int)
        df['AÑO_SINI'] = df['FEC_SINI'].dt.year
        df['AÑO_MOV'] = df['FEC_MVTO'].dt.year
        
        
        df1 = df.merge(tc, on = 'AÑO_MES_MOV', how = "left",validate='many_to_one')

        df1 = df1.merge(maestros, on = ['COD_RAMO','SUBGRUPO'], how = "left",validate='many_to_one')
        df1 = df1.merge(atipi, on= 'NUM_SINI', how = 'left',validate='many_to_one')
        print(df1.shape)
        df1 = df1.merge(ret_ant, on = ['COD_CIA', 'COD_RAMO', 'NUM_SINI', 'NUM_EXP'], how = 'left',validate='many_to_one')
        df1 = df1.merge(RETENCION_XLSX, on = ['NUM_SINI', 'NUM_EXP', 'COD_COB'], how = 'left', suffixes = ('_xlsx','_ant'))
        df1['PCT_RET'] = (df1['PCT_RETENCION_xlsx'].fillna(df1['PCT_RETENCION_ant']).fillna(1))
        print(df1.shape, 'retencion', df1['PCT_RET'].isna().sum())
        df1['PCT_RET'] = df1['PCT_RET'].fillna(1)
        print(df1.shape, 'retencion', df1['PCT_RET'].isna().sum())

        df1['INCURRIDO_SOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) * np.where(df1['COD_MON'] == 1,1,df1['TIPO_CAMBIO'])
        df1['INCURRIDO_DOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) / np.where(df1['COD_MON'] == 2,1,df1['TIPO_CAMBIO'])
        
        df1 = df1.merge(tot, how = 'left', on = 'NUM_POLIZA')
        
        df1['INCURRIDO_SOL_NETO'] = df1['INCURRIDO_SOL']*df1['PCT_RET']
        df1['INCURRIDO_DOL_NETO'] = df1['INCURRIDO_DOL']*df1['PCT_RET']
        print(df1.shape, 'retencion', df1['INCURRIDO_DOL_NETO'].isna().sum())
        
        
        
        df1['INCURRIDO_MON'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL'],df1['INCURRIDO_DOL'])
        df1['INCURRIDO_MON_NETO'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL_NETO'],df1['INCURRIDO_DOL_NETO'])
        df1['ATIPICOS'] = df1['ATIPICOS'].fillna('N')

        anadir = df1[(df1['ATIPICOS']=='N')].groupby(['AÑO_SINI'], as_index = False).agg({'INCURRIDO_DOL':'sum','INCURRIDO_DOL_NETO':'sum'})
        
        cols_groupby = [
            'NUEVO RAMO','AÑO_SINI','AÑO_MOV','AÑO_MES_OCU',
            'AÑO_MES_MOV','dif','PCT_RET','COD_MON','ATIPICOS'
        ]
        
        print("VERIFICAR ----------------\n", df1[cols_groupby].isna().sum())
        
        #triangulo y moneda

        
        base = df1.groupby(['NUEVO RAMO','AÑO_MES_OCU','CODCANAL3'], as_index=False).agg({'INCURRIDO_SOL':'sum',
                                                                                        'INCURRIDO_DOL':'sum',
                                                                                        'INCURRIDO_SOL_NETO':'sum',
                                                                                        'INCURRIDO_DOL_NETO':'sum',
                                                                                        'INCURRIDO_MON':'sum',
                                                                                        'INCURRIDO_MON_NETO':'sum'})
        
        inc.append(base)



inc = pd.concat(inc, axis = 0)
inc['AÑO_MES_OCU'] = inc['AÑO_MES_OCU'].astype(int)
inc1 = inc[inc['AÑO_MES_OCU']>201901] 
inc1.columns

inc2 = inc1[['NUEVO RAMO','CODCANAL3', 'INCURRIDO_SOL',
       'INCURRIDO_DOL', 'INCURRIDO_SOL_NETO', 'INCURRIDO_DOL_NETO',
       'INCURRIDO_MON', 'INCURRIDO_MON_NETO']]



a = inc1[['NUEVO RAMO','CODCANAL3']].value_counts()

base = df1.groupby(['NUEVO RAMO','CODCANAL3'], as_index=False).agg({'INCURRIDO_SOL':'sum',
                                                                                'INCURRIDO_DOL':'sum',
                                                                                'INCURRIDO_SOL_NETO':'sum',
                                                                                'INCURRIDO_DOL_NETO':'sum',
                                                                                'INCURRIDO_MON':'sum',
                                                                                'INCURRIDO_MON_NETO':'sum'})





#%%
polizas_nv.to_excel(r'C:/Users/crolaz1/Downloads/polizas_novida.xlsx')
polizas_v.to_excel(r'C:/Users/crolaz1/Downloads/polizas_vida.xlsx')


#%%
'esto es para generales y autos, tenemos que hacer una validación de la consistencia'
# empezamos generales y autos


ramos = []
bases = []
errores = []
compara = []

for x in archivos1:
    #generales sin vida ni autos ni amf
    if "_2_" not in x and "_30_" not in x and "_28_" not in x and "_11_" not in x:
        
        df = pd.read_csv(os.path.join(RUTA,x), sep = ';')
        
        df.columns = COLUMNAS
        print(f"negocio: {x}")
        print(df.shape)
        df['FEC_SINI'] = pd.to_datetime(df['FEC_SINI'], format = 'mixed', dayfirst = True)
        df['FEC_MVTO'] = pd.to_datetime(df['FEC_MVTO'], format = 'mixed', dayfirst = True)
        df['AÑO_MES_OCU'] = df['FEC_SINI'].dt.strftime("%Y%m")
        df['AÑO_MES_MOV'] = df['FEC_MVTO'].dt.strftime("%Y%m")
        df['dif'] = (
            (df['FEC_MVTO'].dt.year - df['FEC_SINI'].dt.year) * 12
            + (df['FEC_MVTO'].dt.month - df['FEC_SINI'].dt.month)
        )
        errores.append(df[df['dif']<0])
        df['AÑO_MES_MOV'] = df['AÑO_MES_MOV'].astype(int)
        df['AÑO_SINI'] = df['FEC_SINI'].dt.year
        df['AÑO_MOV'] = df['FEC_MVTO'].dt.year

        #inicio

        df1 = df.merge(tc, on = 'AÑO_MES_MOV', how = "left",validate='many_to_one')

        df1 = df1.merge(maestros, on = ['COD_RAMO','SUBGRUPO'], how = "left",validate='many_to_one')
        df1 = df1.merge(atipi, on= 'NUM_SINI', how = 'left',validate='many_to_one')
        print(df1.shape)
        df1 = df1.merge(ret_ant, on = ['COD_CIA', 'COD_RAMO', 'NUM_SINI', 'NUM_EXP'], how = 'left',validate='many_to_one')
        df1 = df1.merge(RETENCION_XLSX, on = ['NUM_SINI', 'NUM_EXP', 'COD_COB'], how = 'left', suffixes = ('_xlsx','_ant'))
        df1['PCT_RET'] = (df1['PCT_RETENCION_xlsx'].fillna(df1['PCT_RETENCION_ant']).fillna(1))
        print(df1.shape, 'retencion', df1['PCT_RET'].isna().sum())
        df1['PCT_RET'] = df1['PCT_RET'].fillna(1)
        print(df1.shape, 'retencion', df1['PCT_RET'].isna().sum())

        df1['INCURRIDO_SOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) * np.where(df1['COD_MON'] == 1,1,df1['TIPO_CAMBIO'])
        df1['INCURRIDO_DOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) / np.where(df1['COD_MON'] == 2,1,df1['TIPO_CAMBIO'])
        errores_tc = df1[
            (df1['COD_MON'] != 1) &
            (df1['TIPO_CAMBIO'].isna())
        ]
        errores.append(errores_tc)
        #neto

        df1['INCURRIDO_SOL_NETO'] = df1['INCURRIDO_SOL']*df1['PCT_RET']
        df1['INCURRIDO_DOL_NETO'] = df1['INCURRIDO_DOL']*df1['PCT_RET']
        print(df1.shape, 'retencion', df1['INCURRIDO_DOL_NETO'].isna().sum())
        
        
        
        df1['INCURRIDO_MON'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL'],df1['INCURRIDO_DOL'])
        df1['INCURRIDO_MON_NETO'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL_NETO'],df1['INCURRIDO_DOL_NETO'])
        df1['ATIPICOS'] = df1['ATIPICOS'].fillna('N')

        anadir = df1[(df1['ATIPICOS']=='N')].groupby(['AÑO_SINI'], as_index = False).agg({'INCURRIDO_DOL':'sum','INCURRIDO_DOL_NETO':'sum'})
        
        cols_groupby = [
            'NUEVO RAMO','AÑO_SINI','AÑO_MOV','AÑO_MES_OCU',
            'AÑO_MES_MOV','dif','PCT_RET','COD_MON','ATIPICOS'
        ]
        
        print("VERIFICAR ----------------\n", df1[cols_groupby].isna().sum())
        
        #triangulo y moneda
        negocio = df1['SUBGRUPO'].unique()
        base = df1.groupby(['NUEVO RAMO','AÑO_SINI','AÑO_MOV','AÑO_MES_OCU','AÑO_MES_MOV','dif','PCT_RET','COD_MON','ATIPICOS'], as_index=False).agg({'INCURRIDO_SOL':'sum',
                                                                                                                                   'INCURRIDO_DOL':'sum',
                                                                                                                                   'INCURRIDO_SOL_NETO':'sum',
                                                                                                                                   'INCURRIDO_DOL_NETO':'sum',
                                                                                                                                   'INCURRIDO_MON':'sum',
                                                                                                                                   'INCURRIDO_MON_NETO':'sum'})
        
        anadir2 = base[(base['ATIPICOS']=='N')].groupby(['AÑO_SINI'], as_index = False).agg({'INCURRIDO_DOL':'sum','INCURRIDO_DOL_NETO':'sum'})
        anadir3 = pd.concat((anadir,anadir2), axis = 1)
        anadir3['sub'] = base['NUEVO RAMO'].unique()[0]
        compara.append(anadir3)
        
        
        triangulo = df1.pivot_table(index = 'AÑO_MES_OCU', columns= 'dif', values= 'INCURRIDO_MON_NETO', aggfunc='sum', fill_value = 0)           
        # Normalizar el índice y las columnas DEL TRIÁNGULO
        triangulo.index = pd.to_numeric(triangulo.index, errors='coerce').astype(int)
        triangulo.columns = pd.to_numeric(triangulo.columns,errors='coerce').astype(int)            
        # Ordenar
        triangulo = triangulo.sort_index().sort_index(axis=1)
        # Crear meses completos usando el índice real del triángulo
        fecha_min = pd.to_datetime(str(triangulo.index.min()),format='%Y%m')
        fecha_max = pd.to_datetime(str(triangulo.index.max()),format='%Y%m')
        meses_completos = (pd.date_range( start=fecha_min, end=fecha_max,freq='MS').strftime('%Y%m').astype(int).tolist())
        # Crear desarrollos completos usando las columnas reales
        desarrollos_completos = list(range(int(triangulo.columns.min()),int(triangulo.columns.max()) + 1))
        # Completar filas, columnas y celdas vacías
        triangulo = triangulo.reindex(index=meses_completos, columns=desarrollos_completos).fillna(0)
        triangulo.index.name = 'AÑO_MES_OCU'
        triangulo.columns.name = 'dif'
        ramos.append(triangulo)
        bases.append(base)
        

    # autos
    elif "_30_" in x:

        df = pd.read_csv(os.path.join(RUTA,x), sep = ';')
        df.columns = COLUMNAS
        sini_transferidos = set()
        for y in ['AUTOS 1', 'AUTOS 2', 'AUTOS 3']:
            print(f"negocio: {y}")
            df1 = df[df['SUBGRUPO'] == y]
            print(df1.shape, "inicio")
            if y in ['AUTOS 1', 'AUTOS 2']:

                df_a3 = df[(df['SUBGRUPO'] == 'AUTOS 3') & (df['TIP_EXP'].isin(['RDR', 'RCT', 'RAC', 'RAA']))]
                sini = df1['NUM_SINI'].unique().tolist()
                df_con = df_a3[df_a3['NUM_SINI'].isin(sini)]

                sini_transferidos.update(df_con['NUM_SINI'].unique())

                print(df_con.shape, "de autos 3")
                df1 = pd.concat((df1,df_con), axis = 0)

            elif y =='AUTOS 3':
                
                # quitar SOLO los registros transferidos
                df1 = df1[~(
                        df1['NUM_SINI'].isin(sini_transferidos)
                        &
                        df1['TIP_EXP'].isin(['RDR', 'RCT', 'RAC', 'RAA'])
                    )]
                
            print(df1.shape, "finales")

            df1['FEC_SINI'] = pd.to_datetime(df1['FEC_SINI'], format = 'mixed', dayfirst = True)
            df1['FEC_MVTO'] = pd.to_datetime(df1['FEC_MVTO'], format = 'mixed', dayfirst = True)
            df1['AÑO_MES_OCU'] = df1['FEC_SINI'].dt.strftime("%Y%m")
            df1['AÑO_MES_MOV'] = df1['FEC_MVTO'].dt.strftime("%Y%m")
            df1['dif'] = (
                (df1['FEC_MVTO'].dt.year - df1['FEC_SINI'].dt.year) * 12
                + (df1['FEC_MVTO'].dt.month - df1['FEC_SINI'].dt.month)
            )
            df1['AÑO_SINI'] = df1['FEC_SINI'].dt.year
            df1['AÑO_MOV'] = df1['FEC_MVTO'].dt.year
            errores.append(df1[df1['dif']<0])
            df1['AÑO_MES_MOV'] = df1['AÑO_MES_MOV'].astype(int)
            df1 = df1.merge(tc, on = 'AÑO_MES_MOV', how = "left",validate='many_to_one')
            df1 = df1.merge(maestros, on = ['COD_RAMO','SUBGRUPO'], how = "left",validate='many_to_one')
            df1 = df1.merge(atipi, on= 'NUM_SINI', how = 'left',validate='many_to_one')
            print(df1.shape)
            df1 = df1.merge(ret_ant, on = ['COD_CIA', 'COD_RAMO', 'NUM_SINI', 'NUM_EXP'], how = 'left',validate='many_to_one')
            df1 = df1.merge(RETENCION_XLSX, on = ['NUM_SINI', 'NUM_EXP', 'COD_COB'], how = 'left',validate='many_to_one', suffixes = ('_xlsx','_ant'))
            df1['PCT_RET'] = (df1['PCT_RETENCION_xlsx'].fillna(df1['PCT_RETENCION_ant']).fillna(1))
            print(df1.shape, 'retencion', df1['PCT_RET'].isna().sum())
            df1['PCT_RET'] = df1['PCT_RET'].fillna(1)
            print(df1.shape, 'retencion', df1['PCT_RET'].isna().sum())
            print(df1.shape)
            
            df1['INCURRIDO_SOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) * np.where(df1['COD_MON'] == 1,1,df1['TIPO_CAMBIO'])
            df1['INCURRIDO_DOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) / np.where(df1['COD_MON'] == 2,1,df1['TIPO_CAMBIO'])
            
            errores_tc = df1[
                (df1['COD_MON'] != 1) &
                (df1['TIPO_CAMBIO'].isna())
            ]
            errores.append(errores_tc)
            
            df1['INCURRIDO_SOL_NETO'] = df1['INCURRIDO_SOL']*df1['PCT_RET']
            df1['INCURRIDO_DOL_NETO'] = df1['INCURRIDO_DOL']*df1['PCT_RET']

            df1['INCURRIDO_MON'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL'],df1['INCURRIDO_DOL'])
            df1['INCURRIDO_MON_NETO'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL_NETO'],df1['INCURRIDO_DOL_NETO'])
            df1['ATIPICOS'] = df1['ATIPICOS'].fillna('N')
            print(df1.shape, 'retencion', df1['INCURRIDO_DOL_NETO'].isna().sum())
            #triangulo y moneda
            
                        
            cols_groupby = [
                'NUEVO RAMO','AÑO_SINI','AÑO_MOV','AÑO_MES_OCU',
                'AÑO_MES_MOV','dif','PCT_RET','COD_MON','ATIPICOS'
            ]
            
            print("VERIFICAR ----------------\n", df1[cols_groupby].isna().sum())
            
            negocio = df1['NUEVO RAMO'].unique()
            base = df1.groupby(['NUEVO RAMO','AÑO_SINI','AÑO_MOV','AÑO_MES_OCU','AÑO_MES_MOV','dif','PCT_RET','COD_MON','ATIPICOS'], as_index=False).agg({'INCURRIDO_SOL':'sum',
                                                                                                                          'INCURRIDO_DOL':'sum',
                                                                                                                          'INCURRIDO_SOL_NETO':'sum',
                                                                                                                          'INCURRIDO_DOL_NETO':'sum',
                                                                                                                          'INCURRIDO_MON':'sum',
                                                                                                                          'INCURRIDO_MON_NETO':'sum'})

            triangulo = df1.pivot_table(index = 'AÑO_MES_OCU', columns= 'dif', values= 'INCURRIDO_MON_NETO', aggfunc='sum', fill_value = 0)           
            # Normalizar el índice y las columnas DEL TRIÁNGULO
            triangulo.index = pd.to_numeric(triangulo.index, errors='coerce').astype(int)
            triangulo.columns = pd.to_numeric(triangulo.columns,errors='coerce').astype(int)            
            # Ordenar
            triangulo = triangulo.sort_index().sort_index(axis=1)
            # Crear meses completos usando el índice real del triángulo
            fecha_min = pd.to_datetime(str(triangulo.index.min()),format='%Y%m')
            fecha_max = pd.to_datetime(str(triangulo.index.max()),format='%Y%m')
            meses_completos = (pd.date_range( start=fecha_min, end=fecha_max,freq='MS').strftime('%Y%m').astype(int).tolist())
            # Crear desarrollos completos usando las columnas reales
            desarrollos_completos = list(range(int(triangulo.columns.min()),int(triangulo.columns.max()) + 1))
            # Completar filas, columnas y celdas vacías
            triangulo = triangulo.reindex(index=meses_completos, columns=desarrollos_completos).fillna(0)
            triangulo.index.name = 'AÑO_MES_OCU'
            triangulo.columns.name = 'dif'
            
            ramos.append(triangulo)
            bases.append(base)


anadir3 = pd.concat(compara, axis = 0)
bases = pd.concat(bases,axis = 0)
errores = pd.concat(errores,axis = 0)
anadir3['INCURRIDO_DOL'] = anadir3['INCURRIDO_DOL']/1000
anadir3['INCURRIDO_DOL_NETO'] = anadir3['INCURRIDO_DOL_NETO']/1000
anadir3[(anadir3['sub'] == 'RESPONSABILIDAD CIVIL')]

a =bases.groupby(['NUEVO RAMO','AÑO_SINI'],as_index= False).agg({'INCURRIDO_DOL':'sum',
                                  'INCURRIDO_DOL_NETO':'sum'})

a['PCT_RET'] = a['INCURRIDO_DOL_NETO'] / a['INCURRIDO_DOL']


#%%
#exportamos la base no vida

errores.to_excel(os.path.join(ruta_output ,r'errores_final_082026.xlsx'))
bases.to_excel(os.path.join(ruta_output ,r'base_final_082026.xlsx'))

#%%

ramos_v = []
bases_v = []
errores_v = []

for x in archivos1:
    #vida
    
    if "_2_" in x:
        
        df = pd.read_csv(os.path.join(RUTA,x), sep = ';')
                
        df.columns = COLUMNAS
        print(f"negocio: {x}")
        print(df.shape)
        
        df['FEC_SINI'] = pd.to_datetime(df['FEC_SINI'], format = 'mixed', dayfirst = True)
        df['FEC_MVTO'] = pd.to_datetime(df['FEC_MVTO'], format = 'mixed', dayfirst = True)
        df['AÑO_MES_OCU'] = df['FEC_SINI'].dt.strftime("%Y%m")
        df['AÑO_MES_MOV'] = df['FEC_MVTO'].dt.strftime("%Y%m")
        df['dif'] = (
            (df['FEC_MVTO'].dt.year - df['FEC_SINI'].dt.year) * 12
            + (df['FEC_MVTO'].dt.month - df['FEC_SINI'].dt.month)
        )
        errores_v.append(df[df['dif']<0])
        
        df['AÑO_MES_MOV'] = df['AÑO_MES_MOV'].astype(int)
        df1 = df.merge(tc, on = 'AÑO_MES_MOV', how = "left",validate='many_to_one')
        df1 = df1.merge(maestros, on = ['COD_RAMO','SUBGRUPO'], how = "left",validate='many_to_one')
        df1 = df1.merge(atipi, on= 'NUM_SINI', how = 'left',validate='many_to_one')
        print(df1.shape)
        df1 = df1.merge(ret_ant, on = ['COD_CIA', 'COD_RAMO', 'NUM_SINI', 'NUM_EXP'], how = 'left',validate='many_to_one')
        df1 = df1.merge(RETENCION_XLSX, on = ['NUM_SINI', 'NUM_EXP', 'COD_COB'], how = 'left',validate='many_to_one', suffixes = ('_xlsx','_ant'))
        df1['PCT_RET'] = (df1['PCT_RETENCION_xlsx'].fillna(df1['PCT_RETENCION_ant']).fillna(1))
        df1['PCT_RET'] = df1['PCT_RET'].fillna(1)
        print(df1.shape)
        df1['INCURRIDO_SOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) * np.where(df1['COD_MON'] == 1,1,df1['TIPO_CAMBIO'])
        df1['INCURRIDO_DOL'] = (df1['RESERVA_BRUTO'] + df1['PAGO_BRUTO']) * np.where(df1['COD_MON'] == 1,1,df1['TIPO_CAMBIO'])

        df1['INCURRIDO_SOL_NETO'] = df1['INCURRIDO_SOL']*df1['PCT_RET']
        df1['INCURRIDO_DOL_NETO'] = df1['INCURRIDO_DOL']*df1['PCT_RET']

        df1['INCURRIDO_MON'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL'],df1['INCURRIDO_DOL'])
        df1['INCURRIDO_MON_NETO'] = np.where(df1['MONEDA'].unique()[0] == "PEN",df1['INCURRIDO_SOL_NETO'],df1['INCURRIDO_DOL_NETO'])

        df1['ATIPICOS'] = df1['ATIPICOS'].fillna('N')
        df1['NUEVO RAMO'] = np.where(df1['NUM_POLIZA'] == 6362159900003,'ESSALUD-ACC',df1['NUEVO RAMO'])
        print(df1.shape)
        df1 = df1[df1['NUEVO RAMO']!= 'ELIMINAR']
        print(df1['NUEVO RAMO'].value_counts())
        print(df1.shape)

        for y in df1['NUEVO RAMO'].unique():
            
            base = df1.groupby(['NUEVO RAMO','FEC_SINI','FEC_MVTO','AÑO_MES_OCU','AÑO_MES_MOV','dif','ATIPICOS','PCT_RET'], as_index=False).agg({'INCURRIDO_SOL':'sum',
                                                                                                                                    'INCURRIDO_DOL':'sum',
                                                                                                                                    'INCURRIDO_SOL_NETO':'sum',
                                                                                                                                    'INCURRIDO_DOL_NETO':'sum',
                                                                                                                                    'INCURRIDO_MON':'sum',
                                                                                                                                    'INCURRIDO_MON_NETO':'sum'})
            
            triangulo = df1.pivot_table(index = 'AÑO_MES_OCU', columns= 'dif', values= 'INCURRIDO_MON_NETO', aggfunc='sum')
            ramos_v.append(triangulo)
            bases_v.append(base)

bases_v = pd.concat(bases_v,axis = 0)
errores_v = pd.concat(errores_v, axis = 0)
bases_v['NUEVO RAMO'].value_counts()
bases_v['AÑO_MES_OCU'] = bases_v['AÑO_MES_OCU'].astype(int)

triangulo

b = bases_v[bases_v['AÑO_MES_OCU']>=201901]
#%%

errores_v.to_excel(r'C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\suficiencia\2025\base\errores_vida_final.xlsx')
bases_v.to_excel(r'C:\Users\crolaz1\OneDrive - MAPFRE\crolaz1_0\Lyz\6. Proyecto IBNR\2026\suficiencia\2025\base\base_vida_final.xlsx')
