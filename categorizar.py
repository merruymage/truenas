import pandas as pd #[cite: 1]
import os #[cite: 1]
import re #[cite: 1]

archivo_origen = 'Reporte_Duplicados.csv' #[cite: 1]
directorio_salida = 'Reportes_Automatizados' #[cite: 1]

def extraer_clasificacion_dinamica(fila): #[cite: 1]
    origen = str(fila.get('Origen', '')).strip().upper() #[cite: 1]
    ruta_original = str(fila.get('Ruta Local / Link Web', '')) #[cite: 1]
    ruta_upper = ruta_original.upper() 

    if origen == 'DRIVE' or 'HTTP' in ruta_upper:
        return 'GOOGLE_DRIVE'

    # 1. MAPEO DE AGRUPACIÓN POR SUBCARPETAS
    mapa_departamentos = {
        'SERVICIOS': [
            '\\SERVICIOS\\', 
            '\\24 HORAS\\', 
            '\\COORDINACION DE SERVICIOS\\', 
            '\\OPERACIONES DE SERVICIO\\'
        ],
        'EXPERIENCIA_CLIENTE': [
            '\\EXPERIENCIA DEL CLIENTE\\', 
            '\\ATENCION AL CLIENTE\\', 
            '\\PQRS\\'
        ],
        'TECNOLOGIA': [
            '\\SISTEMAS\\', 
            '\\TI\\', 
            '\\SOPORTE IT\\'
        ]
    }

    for categoria_oficial, variaciones in mapa_departamentos.items():
        for variacion in variaciones:
            if variacion in ruta_upper:
                return categoria_oficial

    # 2. LÓGICA DINÁMICA ORIGINAL (Fallback)
    partes = ruta_original.replace('/', '\\').split('\\') #[cite: 1]
    
    if len(partes) > 4: #[cite: 1]
        carpeta_principal = partes[4].strip().upper() #[cite: 1]
        
        carpetas_genericas = ['BACKUP', 'RESPALDO', 'NUEVA CARPETA', 'ESCRITORIO', 'COMPARTIDO', 'PRIVADO', 'USERS']
        
        if carpeta_principal in carpetas_genericas and len(partes) > 5: #[cite: 1]
            carpeta_principal = partes[5].strip().upper() #[cite: 1]

        carpeta_limpia = re.sub(r'[^A-Z0-9\s_-]', '', carpeta_principal).strip() #[cite: 1]
        return carpeta_limpia if carpeta_limpia else 'OTROS_NAS' #[cite: 1]
    
    return 'OTROS_NAS' #[cite: 1]

def procesar_reporte_definitivo(): #[cite: 1]
    os.makedirs(directorio_salida, exist_ok=True) #[cite: 1]
    
    # 1. Filtro de basura (Limpiamos el ruido primero) #[cite: 1]
    extensiones_basura = ('.db', '.tmp', '.dll', '.exe', '.mui', '.blb', '.xsd', '.pyc', '.xml', '.prg', '.enc', '.thumb.tmp') #[cite: 1]

    print("⏳ Cargando el reporte masivo...") #[cite: 1]
    try: #[cite: 1]
        # Intentamos con punto y coma (generado por tu app), si falla probamos tabulador #[cite: 1]
        try: #[cite: 1]
            df = pd.read_csv(archivo_origen, sep=';', dtype=str) #[cite: 1]
        except: #[cite: 1]
            df = pd.read_csv(archivo_origen, sep='\t', dtype=str) #[cite: 1]
    except Exception as e: #[cite: 1]
        print(f"❌ No se encontró el archivo o hay un error: {e}") #[cite: 1]
        return #[cite: 1]

    # Limpieza de valores nulos #[cite: 1]
    df['Ruta Local / Link Web'] = df['Ruta Local / Link Web'].fillna('') #[cite: 1]
    df['Nombre del Archivo'] = df['Nombre del Archivo'].fillna('') #[cite: 1]
    df['Hash MD5'] = df['Hash MD5'].fillna('') #[cite: 1]
    
    print("🧹 Separando archivos de sistema...") #[cite: 1]
    filtro_basura = df['Nombre del Archivo'].str.lower().str.endswith(extensiones_basura) #[cite: 1]
    df_basura = df[filtro_basura] #[cite: 1]
    df_util = df[~filtro_basura].copy() #[cite: 1]

    if not df_basura.empty: #[cite: 1]
        df_basura.to_csv(os.path.join(directorio_salida, '00_ARCHIVOS_SISTEMA_IGNORAR.csv'), sep=';', index=False, encoding='utf-8-sig') #[cite: 1]

    print("🧠 Analizando rutas y deduciendo departamentos...") #[cite: 1]
    # Creamos una clasificación preliminar #[cite: 1]
    df_util['Clasificacion_Base'] = df_util.apply(extraer_clasificacion_dinamica, axis=1) #[cite: 1]

    print("🔗 Herencia por Hash: Vinculando Google Drive con carpetas NAS locales...") #[cite: 1]
    # --- LA MAGIA: MAPEO VECTORIAL --- #[cite: 1]
    e
    # 1. Aislamos solo los archivos locales que sí descubrieron un departamento válido #[cite: 1]
    df_nas_utiles = df_util[(df_util['Origen'].str.upper() == 'NAS') & (df_util['Clasificacion_Base'] != 'OTROS_NAS')] #[cite: 1]
    
    # 2. Creamos un diccionario donde la llave es el Hash y el valor es el Departamento (ej: '16f3f3...': 'YORMELIN') #[cite: 1]
    # drop_duplicates asegura que tomemos la primera coincidencia en caso de que un archivo esté en 2 deptos distintos #[cite: 1]
    mapa_hashes = df_nas_utiles.drop_duplicates(subset=['Hash MD5']).set_index('Hash MD5')['Clasificacion_Base'].to_dict() #[cite: 1]
    
    # 3. Aplicamos el diccionario. Si un archivo de Drive tiene el mismo Hash, adopta el nombre del departamento #[cite: 1]
    df_util['Grupo_Final'] = df_util['Hash MD5'].map(mapa_hashes).fillna(df_util['Clasificacion_Base']) #[cite: 1]

    print("📂 Generando reportes fraccionados...") #[cite: 1]
    grupos = df_util.groupby('Grupo_Final') #[cite: 1]
    
    for nombre_grupo, df_grupo in grupos: #[cite: 1]
        nombre_archivo = f"Duplicados_{nombre_grupo[:40]}.csv"  #[cite: 1]
        ruta_salida = os.path.join(directorio_salida, nombre_archivo) #[cite: 1]
        
        # Borramos las columnas temporales para que el reporte Excel quede limpio #[cite: 1]
        df_exportar = df_grupo.drop(columns=['Clasificacion_Base', 'Grupo_Final']) #[cite: 1]
        df_exportar.to_csv(ruta_salida, sep=';', index=False, encoding='utf-8-sig') #[cite: 1]
        print(f"✅ {nombre_grupo}: {len(df_grupo):,} archivos") #[cite: 1]

    print("\n🚀 ¡Auditoría inteligente finalizada!") #[cite: 1]

if __name__ == "__main__": #[cite: 1]
    procesar_reporte_definitivo() #[cite: 1]