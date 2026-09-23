import pandas as pd
import os
import re

archivo_origen = 'Reporte_Duplicados.csv'
directorio_salida = 'Reportes_Automatizados'

def extraer_clasificacion_dinamica(fila):
    origen = str(fila.get('Origen', '')).strip().upper()
    ruta = str(fila.get('Ruta Local / Link Web', ''))

    if origen == 'DRIVE' or 'http' in ruta:
        return 'GOOGLE_DRIVE'

    # Normalizamos la ruta para usar siempre barras invertidas
    partes = ruta.replace('/', '\\').split('\\')
    
    if len(partes) > 4:
        carpeta_principal = partes[4].strip().upper()
        
        # Lógica de evasión: Saltamos carpetas genéricas para encontrar la real
        carpetas_genericas = ['BACKUP', 'RESPALDO', 'NUEVA CARPETA', 'ESCRITORIO', 'COMPARTIDO', 'PRIVADO']
        
        if carpeta_principal in carpetas_genericas and len(partes) > 5:
            carpeta_principal = partes[5].strip().upper()

        # Limpiamos caracteres extraños
        carpeta_limpia = re.sub(r'[^A-Z0-9\s_-]', '', carpeta_principal).strip()
        return carpeta_limpia if carpeta_limpia else 'OTROS_NAS'
    
    return 'OTROS_NAS'

def procesar_reporte_definitivo():
    os.makedirs(directorio_salida, exist_ok=True)
    
    # 1. Filtro de basura (Limpiamos el ruido primero)
    extensiones_basura = ('.db', '.tmp', '.dll', '.exe', '.mui', '.blb', '.xsd', '.pyc', '.xml', '.prg', '.enc', '.thumb.tmp')

    print("⏳ Cargando el reporte masivo...")
    try:
        # Intentamos con punto y coma (generado por tu app), si falla probamos tabulador
        try:
            df = pd.read_csv(archivo_origen, sep=';', dtype=str)
        except:
            df = pd.read_csv(archivo_origen, sep='\t', dtype=str)
    except Exception as e:
        print(f"❌ No se encontró el archivo o hay un error: {e}")
        return

    # Limpieza de valores nulos
    df['Ruta Local / Link Web'] = df['Ruta Local / Link Web'].fillna('')
    df['Nombre del Archivo'] = df['Nombre del Archivo'].fillna('')
    df['Hash MD5'] = df['Hash MD5'].fillna('')
    
    print("🧹 Separando archivos de sistema...")
    filtro_basura = df['Nombre del Archivo'].str.lower().str.endswith(extensiones_basura)
    df_basura = df[filtro_basura]
    df_util = df[~filtro_basura].copy()

    if not df_basura.empty:
        df_basura.to_csv(os.path.join(directorio_salida, '00_ARCHIVOS_SISTEMA_IGNORAR.csv'), sep=';', index=False, encoding='utf-8-sig')

    print("🧠 Analizando rutas y deduciendo departamentos...")
    # Creamos una clasificación preliminar
    df_util['Clasificacion_Base'] = df_util.apply(extraer_clasificacion_dinamica, axis=1)

    print("🔗 Herencia por Hash: Vinculando Google Drive con carpetas NAS locales...")
    # --- LA MAGIA: MAPEO VECTORIAL ---
    # 1. Aislamos solo los archivos locales que sí descubrieron un departamento válido
    df_nas_utiles = df_util[(df_util['Origen'].str.upper() == 'NAS') & (df_util['Clasificacion_Base'] != 'OTROS_NAS')]
    
    # 2. Creamos un diccionario donde la llave es el Hash y el valor es el Departamento (ej: '16f3f3...': 'YORMELIN')
    # drop_duplicates asegura que tomemos la primera coincidencia en caso de que un archivo esté en 2 deptos distintos
    mapa_hashes = df_nas_utiles.drop_duplicates(subset=['Hash MD5']).set_index('Hash MD5')['Clasificacion_Base'].to_dict()
    
    # 3. Aplicamos el diccionario. Si un archivo de Drive tiene el mismo Hash, adopta el nombre del departamento
    df_util['Grupo_Final'] = df_util['Hash MD5'].map(mapa_hashes).fillna(df_util['Clasificacion_Base'])

    print("📂 Generando reportes fraccionados...")
    grupos = df_util.groupby('Grupo_Final')
    
    for nombre_grupo, df_grupo in grupos:
        nombre_archivo = f"Duplicados_{nombre_grupo[:40]}.csv" 
        ruta_salida = os.path.join(directorio_salida, nombre_archivo)
        
        # Borramos las columnas temporales para que el reporte Excel quede limpio
        df_exportar = df_grupo.drop(columns=['Clasificacion_Base', 'Grupo_Final'])
        df_exportar.to_csv(ruta_salida, sep=';', index=False, encoding='utf-8-sig')
        print(f"✅ {nombre_grupo}: {len(df_grupo):,} archivos")

    print("\n🚀 ¡Auditoría inteligente finalizada!")

if __name__ == "__main__":
    procesar_reporte_definitivo()