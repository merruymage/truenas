import os
import hashlib
import json
import logging
import threading
import queue
import webbrowser
import subprocess
import csv
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# APIs de Google
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError

# ==========================================
# 1. CONSTANTES Y CONFIGURACIÓN GLOBAL
# ==========================================
DIRECTORIO_BASE = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_REPORTE = os.path.join(DIRECTORIO_BASE, 'Reporte_Duplicados.csv') 
ARCHIVO_LOG = os.path.join(DIRECTORIO_BASE, 'historial_auditoria.txt')
ARCHIVO_TOKEN = os.path.join(DIRECTORIO_BASE, 'token.json')
ARCHIVO_SESION = os.path.join(DIRECTORIO_BASE, 'sesion_guardada.json') # <--- NUEVO: Archivo de "Save Game"
SCOPES = ['https://www.googleapis.com/auth/drive']

# Filtro de exclusión para saltar carpetas pesadas del sistema
CARPETAS_EXCLUIDAS = {
    '$RECYCLE.BIN', 'SYSTEM VOLUME INFORMATION', 'WINDOWS', 
    'PROGRAM FILES', 'PROGRAM FILES (X86)', 'PROGRAMDATA', 
    'APPDATA', 'NODE_MODULES', '.GIT', '__PYCACHE__'
}

logging.basicConfig(filename=ARCHIVO_LOG, level=logging.INFO, 
                    format='%(asctime)s - [%(levelname)s] - %(message)s', encoding='utf-8')

# ==========================================
# 2. CLASE PRINCIPAL DE LA APLICACIÓN
# ==========================================
class AppAuditoriaAvanzada:
    def __init__(self, root):
        self.root = root
        self.root.title("Auditoría NAS & Drive - Panel Gerencial (Ing. Mariana)")
        self.root.geometry("950x750") 
        
        self.archivo_config = os.path.join(DIRECTORIO_BASE, 'config.json')
        self.config = self.cargar_configuracion()
        
        self.evento_pausa = threading.Event()
        self.evento_pausa.set() 
        
        self.lock_datos = threading.Lock() 
        
        self.cola_mensajes = queue.Queue() 
        self.archivos_por_tamano = defaultdict(list)
        self.duplicados_detectados = defaultdict(list)
        
        self.construir_interfaz()
        self.procesar_cola()
        
        # Al iniciar la app, intentamos cargar la sesión del día anterior
        self.intentar_cargar_sesion()

    def cargar_configuracion(self):
        if os.path.exists(self.archivo_config):
            with open(self.archivo_config, 'r') as f:
                conf = json.load(f)
                if "ruta_nas" in conf and "rutas_nas" not in conf:
                    conf["rutas_nas"] = [conf["ruta_nas"]]
                return conf
        else:
            default = {
                "rutas_nas": [r"\\FILESSERVER\Compartido"],
                "usuario_red": "",
                "password_red": "",
                "ruta_credenciales_google": os.path.join(DIRECTORIO_BASE, 'credentials.json')
            }
            with open(self.archivo_config, 'w') as f:
                json.dump(default, f)
            return default

    # ------------------------------------------
    # MÓDULO DE PERSISTENCIA (GUARDAR Y CARGAR)
    # ------------------------------------------
    def guardar_sesion(self):
        """Guarda el progreso de la auditoría en un archivo local (Save Game)"""
        try:
            with open(ARCHIVO_SESION, 'w', encoding='utf-8') as f:
                json.dump(self.duplicados_detectados, f)
        except Exception as e:
            self.cola_mensajes.put(("log", f"[SISTEMA] ⚠️ Error guardando sesión: {e}"))

    def intentar_cargar_sesion(self):
        """Carga los resultados de un escaneo anterior al abrir la app"""
        if os.path.exists(ARCHIVO_SESION):
            try:
                with open(ARCHIVO_SESION, 'r', encoding='utf-8') as f:
                    datos_guardados = json.load(f)
                    self.duplicados_detectados = defaultdict(list, datos_guardados)
                
                self.cola_mensajes.put(("log", "[SISTEMA] 💾 Sesión anterior detectada y recuperada con éxito."))
                self.cola_mensajes.put(("estado", "Resultados de la sesión anterior listos."))
                self.cola_mensajes.put(("actualizar_tabla", None))
            except Exception as e:
                logging.error(f"Error cargando sesión anterior: {e}")

    # ------------------------------------------
    # MÓDULO DE AJUSTES Y CONEXIONES (Mantenido igual)
    # ------------------------------------------
    def abrir_ventana_ajustes(self):
        v_ajustes = tk.Toplevel(self.root)
        v_ajustes.title("⚙️ Configuración del Sistema")
        v_ajustes.geometry("550x550") 
        v_ajustes.grab_set() 
        
        f_nas = ttk.LabelFrame(v_ajustes, text=" 💾 Rutas Locales / NAS ")
        f_nas.pack(fill="x", padx=10, pady=5)
        
        f_lista = ttk.Frame(f_nas)
        f_lista.pack(fill="x", padx=5, pady=5)
        
        self.listbox_rutas = tk.Listbox(f_lista, height=4, width=60)
        self.listbox_rutas.pack(side="left", fill="x", expand=True)
        
        scroll_rutas = ttk.Scrollbar(f_lista, command=self.listbox_rutas.yview)
        scroll_rutas.pack(side="right", fill="y")
        self.listbox_rutas.configure(yscrollcommand=scroll_rutas.set)
        
        for r in self.config.get("rutas_nas", []):
            self.listbox_rutas.insert(tk.END, r)

        f_controles = ttk.Frame(f_nas)
        f_controles.pack(fill="x", padx=5, pady=2)
        
        self.ent_nueva_ruta = ttk.Entry(f_controles, width=45)
        self.ent_nueva_ruta.pack(side="left", padx=2)
        
        def agregar_ruta():
            ruta = self.ent_nueva_ruta.get().strip()
            if ruta and ruta not in self.listbox_rutas.get(0, tk.END):
                self.listbox_rutas.insert(tk.END, ruta)
                self.ent_nueva_ruta.delete(0, tk.END)
                
        def quitar_ruta():
            seleccion = self.listbox_rutas.curselection()
            if seleccion:
                self.listbox_rutas.delete(seleccion)

        ttk.Button(f_controles, text="➕ Agregar", command=agregar_ruta).pack(side="left", padx=2)
        ttk.Button(f_controles, text="❌ Quitar", command=quitar_ruta).pack(side="left", padx=2)

        ttk.Label(f_nas, text="Usuario de red global (Opcional):").pack(anchor="w", padx=5, pady=(10,0))
        ent_user = ttk.Entry(f_nas, width=55)
        ent_user.insert(0, self.config.get("usuario_red", ""))
        ent_user.pack(padx=5, pady=2)
        
        ttk.Label(f_nas, text="Contraseña global (Oculta):").pack(anchor="w", padx=5)
        ent_pass = ttk.Entry(f_nas, width=55, show="*")
        ent_pass.insert(0, self.config.get("password_red", ""))
        ent_pass.pack(padx=5, pady=2)

        f_drive = ttk.LabelFrame(v_ajustes, text=" ☁️ API de Google Drive ")
        f_drive.pack(fill="x", padx=10, pady=10)
        ttk.Label(f_drive, text="Archivo JSON de Credenciales:").pack(anchor="w", padx=5)
        frame_ruta_drive = ttk.Frame(f_drive)
        frame_ruta_drive.pack(fill="x", padx=5, pady=2)
        ent_credenciales = ttk.Entry(frame_ruta_drive, width=45)
        ent_credenciales.insert(0, self.config.get("ruta_credenciales_google", ""))
        ent_credenciales.pack(side="left")

        def buscar_archivo_json():
            archivo = filedialog.askopenfilename(title="Seleccionar Credenciales JSON", filetypes=[("Archivos JSON", "*.json"), ("Todos", "*.*")])
            if archivo:
                ent_credenciales.delete(0, tk.END)
                ent_credenciales.insert(0, archivo)
        ttk.Button(frame_ruta_drive, text="📁 Buscar", command=buscar_archivo_json).pack(side="left", padx=5)

        def guardar():
            rutas_actualizadas = list(self.listbox_rutas.get(0, tk.END))
            if not rutas_actualizadas:
                messagebox.showwarning("Atención", "Debe dejar al menos una ruta NAS.", parent=v_ajustes)
                return

            ruta_cred_nueva = ent_credenciales.get().strip()
            if ruta_cred_nueva != self.config.get("ruta_credenciales_google") and os.path.exists(ARCHIVO_TOKEN):
                os.remove(ARCHIVO_TOKEN) 
                
            self.config["rutas_nas"] = rutas_actualizadas
            self.config["usuario_red"] = ent_user.get().strip()
            self.config["password_red"] = ent_pass.get().strip()
            self.config["ruta_credenciales_google"] = ruta_cred_nueva
            
            with open(self.archivo_config, 'w') as f:
                json.dump(self.config, f)
                
            self.conectar_nas_local()
            messagebox.showinfo("Éxito", "Ajustes guardados correctamente.", parent=v_ajustes)
            v_ajustes.destroy()
            
        ttk.Button(v_ajustes, text="💾 Guardar Cambios", command=guardar).pack(pady=10)

    def conectar_nas_local(self):
        user = self.config.get("usuario_red")
        pwd = self.config.get("password_red")
        rutas = self.config.get("rutas_nas", [])
        
        if user and pwd:
            for ruta in rutas:
                comando = ["net", "use", ruta, pwd, f"/user:{user}"]
                subprocess.run(comando, capture_output=True, text=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)

    def obtener_servicio_drive(self):
        creds = None
        ruta_credenciales = self.config.get("ruta_credenciales_google")
        
        if not os.path.exists(ruta_credenciales):
            raise FileNotFoundError("No se encontró credentials.json. Por favor, verifique la ruta en Ajustes.")
            
        if os.path.exists(ARCHIVO_TOKEN):
            creds = Credentials.from_authorized_user_file(ARCHIVO_TOKEN, SCOPES)
            
        if not creds or not creds.valid:
            max_reintentos = 3
            for intento in range(max_reintentos):
                try:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(ruta_credenciales, SCOPES)
                        creds = flow.run_local_server(port=0)
                    break 
                
                except (RefreshError, Exception) as e:
                    error_str = str(e)
                    if "SSL" in error_str or "EOF" in error_str or "RefreshError" in error_str or "Connection" in error_str:
                        if intento < max_reintentos - 1:
                            self.cola_mensajes.put(("log", f"[DRIVE] ⚠️ Fluctuación de red o token inválido. Reintentando ({intento+1}/{max_reintentos})..."))
                            time.sleep(3) 
                            
                            if "RefreshError" in error_str and os.path.exists(ARCHIVO_TOKEN):
                                os.remove(ARCHIVO_TOKEN)
                                creds = None 
                        else:
                            raise Exception(f"Fallo de conexión persistente con Google: {e}")
                    else:
                        raise e 

            with open(ARCHIVO_TOKEN, 'w') as token:
                token.write(creds.to_json())
                
        return build('drive', 'v3', credentials=creds)

    # ------------------------------------------
    # MÓDULO DE INTERFAZ GRÁFICA PRINCIPAL
    # ------------------------------------------
    def construir_interfaz(self):
        f_top = ttk.Frame(self.root)
        f_top.pack(fill="x", padx=10, pady=10)
        
        self.btn_iniciar = ttk.Button(f_top, text="🚀 Iniciar Escaneo en Paralelo", command=self.iniciar_escaneo)
        self.btn_iniciar.pack(side="left", padx=5)

        self.btn_pausa = ttk.Button(f_top, text="⏸️ Pausar", command=self.alternar_pausa, state="disabled")
        self.btn_pausa.pack(side="left", padx=5)

        self.btn_ajustes = ttk.Button(f_top, text="⚙️ Ajustes", command=self.abrir_ventana_ajustes)
        self.btn_ajustes.pack(side="left", padx=5)
        
        self.lbl_estado = ttk.Label(f_top, text="Estado: Esperando ordenes...", font=("Arial", 10, "italic"))
        self.lbl_estado.pack(side="left", padx=20)

        f_tabla = ttk.LabelFrame(self.root, text=" 📊 Centro de Decisiones - Archivos Duplicados ")
        f_tabla.pack(fill="both", expand=True, padx=10, pady=5)
        
        columnas = ("Hash", "Origen", "Nombre", "Tamaño (Bytes)", "Fecha", "Ruta/Link")
        self.tree = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="browse")
        for col in columnas:
            self.tree.heading(col, text=col)
            if col == "Origen": self.tree.column(col, width=60)
            elif col == "Tamaño (Bytes)": self.tree.column(col, width=90)
            elif col == "Fecha": self.tree.column(col, width=120)
            elif col == "Hash": self.tree.column(col, width=120)
            else: self.tree.column(col, width=180)
        
        scroll = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.al_seleccionar_item)

        f_consola = ttk.LabelFrame(self.root, text=" 💻 Monitor de Avance (Multihilo) ")
        f_consola.pack(fill="x", padx=10, pady=5)
        
        self.consola = tk.Text(f_consola, height=7, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9), state="disabled") 
        scroll_consola = ttk.Scrollbar(f_consola, command=self.consola.yview)
        self.consola.configure(yscrollcommand=scroll_consola.set)
        
        scroll_consola.pack(side="right", fill="y")
        self.consola.pack(side="left", fill="x", expand=True, padx=5, pady=5)

        f_acciones = ttk.Frame(self.root)
        f_acciones.pack(fill="x", padx=10, pady=5)
        
        self.btn_preview = ttk.Button(f_acciones, text="👁️ Ver Archivo", state="disabled", command=self.abrir_vista_previa)
        self.btn_preview.pack(side="left", padx=5)
        
        self.btn_abrir_excel = ttk.Button(f_acciones, text="📊 Abrir Reporte Excel", state="disabled", command=self.abrir_reporte_excel)
        self.btn_abrir_excel.pack(side="left", padx=5)
        
        self.btn_borrar = ttk.Button(f_acciones, text="🗑️ Mover a Cuarentena/Papelera", state="disabled", command=self.borrar_seguro)
        self.btn_borrar.pack(side="right", padx=5)

    def alternar_pausa(self):
        if self.evento_pausa.is_set():
            self.evento_pausa.clear() 
            self.btn_pausa.config(text="▶️ Reanudar")
            self.cola_mensajes.put(("log", "[SISTEMA] ⚠️ ESCANEO PAUSADO POR EL USUARIO"))
        else:
            self.evento_pausa.set() 
            self.btn_pausa.config(text="⏸️ Pausar")
            self.cola_mensajes.put(("log", "[SISTEMA] ▶️ ESCANEO REANUDADO"))

    # ------------------------------------------
    # MÓDULO CORE: ESCANEO EN PARALELO
    # ------------------------------------------
    def iniciar_escaneo(self):
        # Advertencia si ya hay una sesión cargada
        if os.path.exists(ARCHIVO_SESION) and self.duplicados_detectados:
            respuesta = messagebox.askyesno("Nueva Auditoría", "Ya existe una sesión de auditoría guardada.\n\nSi inicia un nuevo escaneo, los resultados actuales se sobrescribirán y perderá el avance anterior.\n\n¿Desea escanear todo nuevamente?")
            if not respuesta:
                return
            
        self.btn_iniciar.config(state="disabled")
        self.btn_ajustes.config(state="disabled")
        self.btn_abrir_excel.config(state="disabled")
        self.btn_pausa.config(state="normal")
        self.lbl_estado.config(text="Estado: Escaneando en paralelo...")
        
        self.archivos_por_tamano.clear()
        self.duplicados_detectados.clear()
        
        self.consola.config(state="normal")
        self.consola.delete(1.0, tk.END)
        self.consola.config(state="disabled")
        
        self.evento_pausa.set() 
        threading.Thread(target=self.hilo_orquestador_paralelo, daemon=True).start()

    def escanear_drive(self):
        try:
            self.cola_mensajes.put(("log", "[DRIVE] ☁️ Iniciando conexión y validando token..."))
            servicio = self.obtener_servicio_drive()
            page_token = None
            total_drive = 0
            
            while True:
                self.evento_pausa.wait() 
                
                max_reintentos = 3
                resultados = None
                
                for intento in range(max_reintentos):
                    try:
                        resultados = servicio.files().list(q="trashed = false", fields="nextPageToken, files(id, name, size, md5Checksum, webViewLink, modifiedTime)", pageToken=page_token).execute()
                        break 
                    except Exception as e:
                        if intento < max_reintentos - 1:
                            self.cola_mensajes.put(("log", f"[DRIVE] ⚠️ Micro-corte al descargar datos. Reconectando ({intento+1}/{max_reintentos})..."))
                            time.sleep(4)
                        else:
                            raise Exception(f"La conexión con Drive es muy inestable: {e}")
                
                archivos = resultados.get('files', [])
                
                for f in archivos:
                    if 'size' in f and 'md5Checksum' in f:
                        fecha_raw = f.get('modifiedTime', 'Desconocida').replace('T', ' ')[:19]
                        data = {'origen': 'Drive', 'id': f['id'], 'nombre': f['name'], 'link': f['webViewLink'], 'size': f['size'], 'hash': f['md5Checksum'], 'fecha': fecha_raw}
                        
                        with self.lock_datos:
                            self.archivos_por_tamano[f['size']].append(data)
                        
                        total_drive += 1
                
                if total_drive % 1000 == 0:
                    self.cola_mensajes.put(("log", f"[DRIVE] ☁️ Descargando metadatos... ({total_drive} procesados)"))
                
                page_token = resultados.get('nextPageToken')
                if not page_token:
                    break
            self.cola_mensajes.put(("log", f"[DRIVE] ✅ Finalizado. ({total_drive} archivos en total)"))
        except Exception as e:
            self.cola_mensajes.put(("error_log", f"[DRIVE] ❌ Error: {e}"))

    def escanear_ruta_nas(self, ruta_activa, id_hilo):
        self.cola_mensajes.put(("log", f"[NAS-{id_hilo}] 💾 Indexando: {ruta_activa}"))
        if not os.path.exists(ruta_activa):
            self.cola_mensajes.put(("error_log", f"[NAS-{id_hilo}] ❌ No se encuentra la ruta."))
            return

        contador_nas = 0
        for root_dir, dirs, files in os.walk(ruta_activa):
            self.evento_pausa.wait() 
            
            dirs[:] = [d for d in dirs if d.upper() not in CARPETAS_EXCLUIDAS]
            
            for name in files:
                ruta_completa = os.path.join(root_dir, name)
                try:
                    tamano = str(os.path.getsize(ruta_completa))
                    fecha_ts = os.path.getmtime(ruta_completa)
                    fecha_legible = datetime.fromtimestamp(fecha_ts).strftime('%Y-%m-%d %H:%M:%S')
                    data = {'origen': 'NAS', 'id': ruta_completa, 'nombre': name, 'link': ruta_completa, 'size': tamano, 'fecha': fecha_legible}
                    
                    with self.lock_datos:
                        self.archivos_por_tamano[tamano].append(data)
                    
                    contador_nas += 1
                    if contador_nas % 1500 == 0: 
                        self.cola_mensajes.put(("log", f"[NAS-{id_hilo}] 💾 {contador_nas} archivos escaneados..."))
                except Exception:
                    pass 
        self.cola_mensajes.put(("log", f"[NAS-{id_hilo}] ✅ Finalizado. ({contador_nas} archivos en total)"))

    def hilo_orquestador_paralelo(self):
        try:
            rutas_nas = self.config.get("rutas_nas", [])
            self.cola_mensajes.put(("log", "[SISTEMA] 🚀 INICIANDO ESCANEO EN PARALELO MULTI-HILO"))
            
            hilos_totales = len(rutas_nas) + 1
            
            with ThreadPoolExecutor(max_workers=hilos_totales) as executor:
                futuros = [executor.submit(self.escanear_drive)]
                for i, ruta in enumerate(rutas_nas):
                    futuros.append(executor.submit(self.escanear_ruta_nas, ruta, i+1))
                
                for futuro in as_completed(futuros):
                    futuro.result() 

            self.cola_mensajes.put(("estado", "Fase 1 completada. Calculando MD5 para duplicados..."))
            self.cola_mensajes.put(("log", "[SISTEMA] 🔍 Extracción terminada. Iniciando análisis de Hashes..."))
            
            sospechosos_nas = []
            for tamano, lista_archivos in self.archivos_por_tamano.items():
                if len(lista_archivos) > 1:
                    for archivo in lista_archivos:
                        if archivo['origen'] == 'NAS':
                            sospechosos_nas.append(archivo)
                        else:
                            self.duplicados_detectados[archivo['hash']].append(archivo)

            total_sospechosos = len(sospechosos_nas)
            self.cola_mensajes.put(("log", f"[SISTEMA] 🚨 {total_sospechosos} archivos locales sospechosos."))

            procesados_hash = 0
            with ThreadPoolExecutor(max_workers=6) as executor_hash: 
                for sospechoso in sospechosos_nas:
                    self.evento_pausa.wait() 
                    
                    md5 = self.calcular_hash_md5(sospechoso['id'])
                    if md5:
                        sospechoso['hash'] = md5
                        with self.lock_datos:
                            self.duplicados_detectados[md5].append(sospechoso)
                    
                    procesados_hash += 1
                    if procesados_hash % 100 == 0 or procesados_hash == total_sospechosos:
                        self.cola_mensajes.put(("log", f"[SISTEMA] ⚙️ Hashes: {procesados_hash} / {total_sospechosos} calculados"))

            self.generar_reporte_excel()
            self.guardar_sesion() # GUARDAMOS TODO AUTOMÁTICAMENTE AL TERMINAR

            self.cola_mensajes.put(("log", "[SISTEMA] 🏆 AUDITORÍA COMPLETADA EXITOSAMENTE."))
            self.cola_mensajes.put(("estado", "Auditoría finalizada. Resultados listos."))
            self.cola_mensajes.put(("actualizar_tabla", None))

        except Exception as e:
            self.cola_mensajes.put(("error", f"Error general: {e}"))

    def calcular_hash_md5(self, ruta):
        hash_md5 = hashlib.md5()
        try:
            with open(ruta, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            return None

    def generar_reporte_excel(self):
        try:
            with open(ARCHIVO_REPORTE, 'w', newline='', encoding='utf-8-sig') as f_csv:
                writer = csv.writer(f_csv, delimiter=';') 
                writer.writerow(['Hash MD5', 'Origen', 'Nombre del Archivo', 'Tamaño (Bytes)', 'Fecha de Modificación', 'Ruta Local / Link Web'])
                
                hay_datos = False
                for h, lista in self.duplicados_detectados.items():
                    if len(lista) > 1:
                        hay_datos = True
                        for arch in lista:
                            writer.writerow([arch['hash'], arch['origen'], arch['nombre'], arch['size'], arch.get('fecha', 'N/A'), arch['link']])
                
                if hay_datos:
                    self.cola_mensajes.put(("log", f"[SISTEMA] 📊 Reporte guardado: {ARCHIVO_REPORTE}"))
                else:
                    self.cola_mensajes.put(("log", "[SISTEMA] 👍 0 duplicados encontrados."))
        except Exception as e:
            self.cola_mensajes.put(("error_log", f"[SISTEMA] ⚠️ Error creando reporte excel: {e}"))

    # ------------------------------------------
    # MÓDULO DE ACTUALIZACIÓN UI Y AUDITORÍA
    # ------------------------------------------
    def procesar_cola(self):
        try:
            while True:
                tipo, valor = self.cola_mensajes.get_nowait()
                
                if tipo in ["log", "error_log"]:
                    fecha_hora_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    mensaje_pantalla = f"[{fecha_hora_actual}] {valor}"
                    
                    self.consola.config(state="normal")
                    self.consola.insert(tk.END, mensaje_pantalla + "\n")
                    self.consola.see(tk.END) 
                    self.consola.config(state="disabled")
                    
                    if tipo == "log":
                        logging.info(valor)
                    else:
                        logging.error(valor)

                elif tipo == "estado":
                    self.lbl_estado.config(text=f"Estado: {valor}")
                
                elif tipo == "actualizar_tabla":
                    self.poblar_tabla()
                
                elif tipo == "error":
                    self.lbl_estado.config(text="Detenido por Error.")
                    self.btn_iniciar.config(state="normal")
                    self.btn_ajustes.config(state="normal")
                    self.btn_pausa.config(state="disabled")
                    messagebox.showerror("Error Crítico", valor)
                    
        except queue.Empty:
            pass
        self.root.after(100, self.procesar_cola)

    def poblar_tabla(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
            
        for h, lista in self.duplicados_detectados.items():
            if len(lista) > 1:
                for arch in lista:
                    self.tree.insert("", "end", values=(arch['hash'], arch['origen'], arch['nombre'], arch['size'], arch.get('fecha', 'N/A'), arch['link']), tags=(arch['id'],))
        
        self.btn_iniciar.config(state="normal")
        self.btn_ajustes.config(state="normal")
        self.btn_pausa.config(state="disabled")
        
        if os.path.exists(ARCHIVO_REPORTE):
            self.btn_abrir_excel.config(state="normal")

    def al_seleccionar_item(self, event):
        if self.tree.selection():
            self.btn_preview.config(state="normal")
            self.btn_borrar.config(state="normal")

    def abrir_vista_previa(self):
        item = self.tree.selection()[0]
        origen = self.tree.item(item, "values")[1]
        link = self.tree.item(item, "values")[5] 
        if origen == "Drive":
            webbrowser.open(link)
        else:
            try: os.startfile(link)
            except Exception as e: messagebox.showerror("Error", f"Fallo al abrir: {e}")

    def abrir_reporte_excel(self):
        if os.path.exists(ARCHIVO_REPORTE):
            try:
                os.startfile(ARCHIVO_REPORTE)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir el reporte: {e}")

    def borrar_seguro(self):
        item = self.tree.selection()[0]
        hash_id, origen, nombre, tamano, fecha, link = self.tree.item(item, "values")
        id_real = self.tree.item(item, "tags")[0]

        if messagebox.askyesno("Confirmación Crítica", f"¿Mover '{nombre}' a cuarentena/papelera?"):
            try:
                if origen == "Drive":
                    servicio = self.obtener_servicio_drive()
                    servicio.files().update(fileId=id_real, body={'trashed': True}).execute()
                    self.cola_mensajes.put(("log", f"[ACCIÓN] 🗑️ DRIVE Papelera: {nombre} ({hash_id})"))
                else:
                    cuarentena_dir = os.path.join(DIRECTORIO_BASE, '_CUARENTENA_DUPLICADOS')
                    os.makedirs(cuarentena_dir, exist_ok=True)
                    os.rename(link, os.path.join(cuarentena_dir, nombre))
                    self.cola_mensajes.put(("log", f"[ACCIÓN] 📦 NAS Cuarentena: {link} ({hash_id})"))
                
                # 1. Eliminar de la vista
                self.tree.delete(item)
                
                # 2. Eliminar del diccionario en memoria RAM
                if hash_id in self.duplicados_detectados:
                    self.duplicados_detectados[hash_id] = [arch for arch in self.duplicados_detectados[hash_id] if arch['id'] != id_real]
                    # Si solo queda 1 archivo, ya no es duplicado, lo quitamos por completo
                    if len(self.duplicados_detectados[hash_id]) < 2:
                        del self.duplicados_detectados[hash_id]
                
                # 3. Guardar el nuevo estado (Actualiza la partida guardada)
                self.guardar_sesion()

            except Exception as e:
                messagebox.showerror("Error", f"Fallo al procesar: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = AppAuditoriaAvanzada(root)
    root.mainloop()