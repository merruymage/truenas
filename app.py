import os
import sys
import hashlib
import json
import logging
import threading
import queue
import webbrowser
import subprocess
import csv
import time
import math
import sqlite3 
import socket 
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc

# APIs de Google
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError

# ==========================================
# 1. CONSTANTES Y CONFIGURACIÓN GLOBAL
# ==========================================
# Adaptación Senior para que funcione perfecto como .exe compilado o como .py
if getattr(sys, 'frozen', False):
    DIRECTORIO_BASE = os.path.dirname(sys.executable)
else:
    DIRECTORIO_BASE = os.path.dirname(os.path.abspath(__file__))

ARCHIVO_REPORTE = os.path.join(DIRECTORIO_BASE, 'Reporte_Duplicados.csv') 
ARCHIVO_LOG = os.path.join(DIRECTORIO_BASE, 'historial_auditoria.txt')
ARCHIVO_TOKEN = os.path.join(DIRECTORIO_BASE, 'token.json')
SCOPES = ['https://www.googleapis.com/auth/drive']

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
        self.root.title("Auditoría NAS & Drive - Panel Gerencial (Colaborativo)")
        self.root.geometry("1050x820") 
        
        # --- PREVENCIÓN DE CIERRE ACCIDENTAL ---
        self.escaneo_en_curso = False
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)
        
        self.archivo_config = os.path.join(DIRECTORIO_BASE, 'config.json')
        self.config = self.cargar_configuracion()
        
        self.evento_pausa = threading.Event()
        self.evento_pausa.set() 
        
        self.lock_datos = threading.Lock() 
        self.cola_mensajes = queue.Queue() 
        
        self.archivos_por_tamano = defaultdict(list)
        self.duplicados_detectados = defaultdict(list)
        
        self.lista_duplicados_plana = []
        self.pagina_actual = 1
        self.items_por_pagina = 2000
        self.total_paginas = 1
        
        self.nombre_equipo = socket.gethostname()
        try:
            self.usuario_local = os.getlogin()
        except:
            self.usuario_local = "Desconocido"
        
        self.inicializar_bd() 
        self.construir_interfaz()
        self.procesar_cola()
        
        self.cargar_datos_desde_bd()

    def cerrar_aplicacion(self):
        """Mitigación de errores humanos al cerrar la ventana"""
        if self.escaneo_en_curso:
            if not messagebox.askyesno("Advertencia Crítica", "Hay un escaneo ejecutándose en segundo plano.\n\nSi cierra la aplicación ahora, el proceso se interrumpirá abruptamente y podría perder el avance no guardado.\n\n¿Está seguro de que desea salir?"):
                return
        self.root.destroy()

    @property
    def archivo_bd(self):
        return self.config.get("ruta_bd", os.path.join(DIRECTORIO_BASE, 'sesion_guardada.db'))

    @property
    def archivo_reporte(self):
        return self.config.get("ruta_reporte", os.path.join(DIRECTORIO_BASE, 'Reporte_Duplicados.csv'))

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
                "ruta_credenciales_google": os.path.join(DIRECTORIO_BASE, 'credentials.json'),
                "ruta_bd": os.path.join(DIRECTORIO_BASE, 'sesion_guardada.db'),
                "ruta_reporte": os.path.join(DIRECTORIO_BASE, 'Reporte_Duplicados.csv')
            }
            with open(self.archivo_config, 'w') as f:
                json.dump(default, f)
            return default

    # ------------------------------------------
    # MÓDULO DE BASE DE DATOS Y CONEXIÓN
    # ------------------------------------------
    def inicializar_bd(self):
        try:
            conn = sqlite3.connect(self.archivo_bd, timeout=15.0)
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS duplicados (
                    hash_id TEXT, origen TEXT, nombre TEXT, 
                    size TEXT, fecha TEXT, link TEXT, id_real TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_hash ON duplicados(hash_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_id_real ON duplicados(id_real)')
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Error inicializando BD en {self.archivo_bd}: {e}")

    def guardar_sesion_en_bd(self):
        try:
            conn = sqlite3.connect(self.archivo_bd, timeout=15.0)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM duplicados')
            
            datos_para_insertar = []
            for h, lista in self.duplicados_detectados.items():
                if len(lista) > 1:
                    for arch in lista:
                        datos_para_insertar.append((
                            arch['hash'], arch['origen'], arch['nombre'], 
                            arch['size'], arch.get('fecha', 'N/A'), arch['link'], arch['id']
                        ))
            
            cursor.executemany('''
                INSERT INTO duplicados (hash_id, origen, nombre, size, fecha, link, id_real)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', datos_para_insertar)
            
            conn.commit()
            conn.close()
            self.cola_mensajes.put(("log", "[SISTEMA] 💾 Sesión guardada en Base de Datos corporativa."))
        except Exception as e:
            self.cola_mensajes.put(("log", f"[SISTEMA] ⚠️ Error guardando en BD: {e}"))

    def cargar_datos_desde_bd(self, modo_sincronizacion=False):
        try:
            if not os.path.exists(self.archivo_bd):
                return
                
            conn = sqlite3.connect(self.archivo_bd, timeout=15.0)
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='duplicados'")
            if not cursor.fetchone():
                conn.close()
                return

            cursor.execute('SELECT hash_id, origen, nombre, size, fecha, link, id_real FROM duplicados')
            filas = cursor.fetchall()
            conn.close()

            if not filas and not modo_sincronizacion:
                return 

            self.duplicados_detectados.clear()
            for fila in filas:
                data = {
                    'hash': fila[0], 'origen': fila[1], 'nombre': fila[2],
                    'size': fila[3], 'fecha': fila[4], 'link': fila[5], 'id': fila[6]
                }
                self.duplicados_detectados[fila[0]].append(data)
            
            if modo_sincronizacion:
                self.cola_mensajes.put(("log", "[SISTEMA] 🔄 Datos sincronizados con la Base de Datos central."))
            else:
                self.cola_mensajes.put(("log", f"[SISTEMA] 💾 Sesión recuperada desde: {self.archivo_bd}"))
                self.cola_mensajes.put(("estado", "Resultados colaborativos listos."))
            
            self.cola_mensajes.put(("actualizar_tabla", None))
        except Exception as e:
            logging.error(f"Error cargando desde BD: {e}")

    def eliminar_registro_bd(self, id_real):
        try:
            conn = sqlite3.connect(self.archivo_bd, timeout=15.0)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM duplicados WHERE id_real = ?', (id_real,))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Error eliminando de BD: {e}")

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
            raise FileNotFoundError("No se encontró credentials.json. Verifique la ruta en Ajustes.")
            
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
                            self.cola_mensajes.put(("log", f"[DRIVE] ⚠️ Fluctuación de red. Reintentando ({intento+1}/{max_reintentos})..."))
                            time.sleep(3) 
                            if "RefreshError" in error_str and os.path.exists(ARCHIVO_TOKEN):
                                os.remove(ARCHIVO_TOKEN)
                                creds = None 
                        else:
                            raise Exception(f"Fallo persistente: {e}")
                    else:
                        raise e 

            with open(ARCHIVO_TOKEN, 'w') as token:
                token.write(creds.to_json())
                
        return build('drive', 'v3', credentials=creds)

    # ------------------------------------------
    # MÓDULO DE INTERFAZ GRÁFICA Y AJUSTES
    # ------------------------------------------
    def abrir_ventana_ajustes(self):
        v_ajustes = tk.Toplevel(self.root)
        v_ajustes.title("⚙️ Configuración del Sistema")
        v_ajustes.geometry("600x680") 
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
            # Validación estricta para evitar errores humanos
            if not ruta:
                return
            if not (ruta.startswith("\\\\") or ":" in ruta):
                messagebox.showwarning("Ruta Inválida", "Por favor ingrese una ruta de red válida (ej: \\\\Servidor\\Datos) o local (ej: D:\\Datos).", parent=v_ajustes)
                return
            if ruta not in self.listbox_rutas.get(0, tk.END):
                self.listbox_rutas.insert(tk.END, ruta)
                self.ent_nueva_ruta.delete(0, tk.END)
                
        def quitar_ruta():
            seleccion = self.listbox_rutas.curselection()
            if seleccion: self.listbox_rutas.delete(seleccion)

        ttk.Button(f_controles, text="➕ Agregar", command=agregar_ruta).pack(side="left", padx=2)
        ttk.Button(f_controles, text="❌ Quitar", command=quitar_ruta).pack(side="left", padx=2)

        ttk.Label(f_nas, text="Usuario de red global (Opcional):").pack(anchor="w", padx=5, pady=(5,0))
        ent_user = ttk.Entry(f_nas, width=55)
        ent_user.insert(0, self.config.get("usuario_red", ""))
        ent_user.pack(padx=5, pady=2)
        ttk.Label(f_nas, text="Contraseña global (Oculta):").pack(anchor="w", padx=5)
        ent_pass = ttk.Entry(f_nas, width=55, show="*")
        ent_pass.insert(0, self.config.get("password_red", ""))
        ent_pass.pack(padx=5, pady=2)

        f_drive = ttk.LabelFrame(v_ajustes, text=" ☁️ API de Google Drive ")
        f_drive.pack(fill="x", padx=10, pady=5)
        ttk.Label(f_drive, text="Archivo JSON de Credenciales:").pack(anchor="w", padx=5)
        f_drive_rut = ttk.Frame(f_drive)
        f_drive_rut.pack(fill="x", padx=5, pady=2)
        ent_cred = ttk.Entry(f_drive_rut, width=45)
        ent_cred.insert(0, self.config.get("ruta_credenciales_google", ""))
        ent_cred.pack(side="left")
        def buscar_json():
            a = filedialog.askopenfilename(title="Credenciales JSON", filetypes=[("JSON", "*.json")])
            if a: ent_cred.delete(0, tk.END); ent_cred.insert(0, a)
        ttk.Button(f_drive_rut, text="📁 Buscar", command=buscar_json).pack(side="left", padx=5)

        f_salida = ttk.LabelFrame(v_ajustes, text=" 📂 Rutas de BD y Reportes (Colaboración) ")
        f_salida.pack(fill="x", padx=10, pady=5)
        ttk.Label(f_salida, text="Ruta BD (SQLite .db):").pack(anchor="w", padx=5)
        f_bd = ttk.Frame(f_salida)
        f_bd.pack(fill="x", padx=5, pady=2)
        ent_bd = ttk.Entry(f_bd, width=45)
        ent_bd.insert(0, self.archivo_bd)
        ent_bd.pack(side="left")
        def buscar_bd():
            a = filedialog.asksaveasfilename(title="Base de Datos", defaultextension=".db", filetypes=[("DB", "*.db")])
            if a: ent_bd.delete(0, tk.END); ent_bd.insert(0, a)
        ttk.Button(f_bd, text="📁 Buscar", command=buscar_bd).pack(side="left", padx=5)

        ttk.Label(f_salida, text="Ruta Reporte (.csv):").pack(anchor="w", padx=5)
        f_rep = ttk.Frame(f_salida)
        f_rep.pack(fill="x", padx=5, pady=2)
        ent_rep = ttk.Entry(f_rep, width=45)
        ent_rep.insert(0, self.archivo_reporte)
        ent_rep.pack(side="left")
        def buscar_rep():
            a = filedialog.asksaveasfilename(title="Reporte", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
            if a: ent_rep.delete(0, tk.END); ent_rep.insert(0, a)
        ttk.Button(f_rep, text="📁 Buscar", command=buscar_rep).pack(side="left", padx=5)

        def guardar():
            rutas_actualizadas = list(self.listbox_rutas.get(0, tk.END))
            if not rutas_actualizadas:
                messagebox.showwarning("Atención", "Debe dejar al menos una ruta NAS.", parent=v_ajustes)
                return

            r_cred = ent_cred.get().strip()
            if r_cred != self.config.get("ruta_credenciales_google") and os.path.exists(ARCHIVO_TOKEN):
                os.remove(ARCHIVO_TOKEN) 
                
            self.config["rutas_nas"] = rutas_actualizadas
            self.config["usuario_red"] = ent_user.get().strip()
            self.config["password_red"] = ent_pass.get().strip()
            self.config["ruta_credenciales_google"] = r_cred
            self.config["ruta_bd"] = ent_bd.get().strip()
            self.config["ruta_reporte"] = ent_rep.get().strip()
            
            with open(self.archivo_config, 'w') as f:
                json.dump(self.config, f)
                
            self.conectar_nas_local()
            self.inicializar_bd() 
            self.cargar_datos_desde_bd(modo_sincronizacion=True) 
            messagebox.showinfo("Éxito", "Ajustes guardados correctamente.", parent=v_ajustes)
            v_ajustes.destroy()
            
        ttk.Button(v_ajustes, text="💾 Guardar Cambios", command=guardar).pack(pady=10)

    def construir_interfaz(self):
        f_top = ttk.Frame(self.root)
        f_top.pack(fill="x", padx=10, pady=10)
        
        self.btn_iniciar = ttk.Button(f_top, text="🚀 Iniciar Nuevo Escaneo", command=self.iniciar_escaneo)
        self.btn_iniciar.pack(side="left", padx=5)

        self.btn_pausa = ttk.Button(f_top, text="⏸️ Pausar", command=self.alternar_pausa, state="disabled")
        self.btn_pausa.pack(side="left", padx=5)
        
        self.btn_sync = ttk.Button(f_top, text="🔄 Sincronizar BD Central", command=lambda: self.cargar_datos_desde_bd(modo_sincronizacion=True))
        self.btn_sync.pack(side="left", padx=5)

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

        f_paginacion = ttk.Frame(self.root)
        f_paginacion.pack(fill="x", padx=10, pady=2)
        
        self.btn_ant = ttk.Button(f_paginacion, text="◀ Página Anterior", state="disabled", command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=5)
        self.lbl_pagina = ttk.Label(f_paginacion, text="Página 1 de 1", font=("Arial", 10, "bold"))
        self.lbl_pagina.pack(side="left", padx=10)
        self.btn_sig = ttk.Button(f_paginacion, text="Página Siguiente ▶", state="disabled", command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=5)

        f_consola = ttk.LabelFrame(self.root, text=" 💻 Monitor de Avance (Multihilo) ")
        f_consola.pack(fill="x", padx=10, pady=5)
        
        # --- NUEVO: Barra de progreso visual para mejor UX ---
        self.progreso = ttk.Progressbar(f_consola, orient="horizontal", mode="indeterminate")
        self.progreso.pack(fill="x", padx=5, pady=2)

        self.consola = tk.Text(f_consola, height=6, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9), state="disabled") 
        scroll_consola = ttk.Scrollbar(f_consola, command=self.consola.yview)
        self.consola.configure(yscrollcommand=scroll_consola.set)
        scroll_consola.pack(side="right", fill="y")
        self.consola.pack(side="left", fill="x", expand=True, padx=5, pady=2)

        f_acciones = ttk.Frame(self.root)
        f_acciones.pack(fill="x", padx=10, pady=5)
        
        self.btn_preview = ttk.Button(f_acciones, text="👁️ Ver Archivo", state="disabled", command=self.abrir_vista_previa)
        self.btn_preview.pack(side="left", padx=5)
        self.btn_abrir_excel = ttk.Button(f_acciones, text="📊 Abrir Reporte Excel", state="disabled", command=self.abrir_reporte_excel)
        self.btn_abrir_excel.pack(side="left", padx=5)
        self.btn_borrar = ttk.Button(f_acciones, text="🗑️ Mover a Cuarentena/Papelera", state="disabled", command=self.borrar_seguro)
        self.btn_borrar.pack(side="right", padx=5)

    # ------------------------------------------
    # MÓDULO CORE: LÓGICA DE ESCANEO
    # ------------------------------------------
    def alternar_pausa(self):
        if self.evento_pausa.is_set():
            self.evento_pausa.clear() 
            self.btn_pausa.config(text="▶️ Reanudar")
            self.progreso.stop()
            self.cola_mensajes.put(("log", "[SISTEMA] ⚠️ ESCANEO PAUSADO POR EL USUARIO"))
        else:
            self.evento_pausa.set() 
            self.btn_pausa.config(text="⏸️ Pausar")
            self.progreso.start()
            self.cola_mensajes.put(("log", "[SISTEMA] ▶️ ESCANEO REANUDADO"))

    def iniciar_escaneo(self):
        try:
            conn = sqlite3.connect(self.archivo_bd, timeout=15.0)
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM duplicados")
            count = cursor.fetchone()[0]
            conn.close()

            if count > 0:
                respuesta = messagebox.askyesno("Nueva Auditoría", "Ya existe una base de datos con resultados.\n\nSi inicia un escaneo masivo, perderá la auditoría actual y se reemplazará todo.\n\n¿Desea escanear TODO desde cero?")
                if not respuesta:
                    return
        except Exception:
            pass 
            
        self.escaneo_en_curso = True
        self.progreso.start() # Activar feedback visual
        
        self.btn_iniciar.config(state="disabled")
        self.btn_ajustes.config(state="disabled")
        self.btn_abrir_excel.config(state="disabled")
        self.btn_sync.config(state="disabled")
        self.btn_pausa.config(state="normal")
        self.lbl_estado.config(text="Estado: Escaneando en paralelo...")
        
        self.archivos_por_tamano.clear()
        self.duplicados_detectados.clear()
        self.lista_duplicados_plana.clear()
        
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
                            self.cola_mensajes.put(("log", f"[DRIVE] ⚠️ Micro-corte. Reconectando ({intento+1}/{max_reintentos})..."))
                            time.sleep(4)
                        else:
                            raise Exception(f"Inestabilidad Drive: {e}")
                
                archivos = resultados.get('files', [])
                for f in archivos:
                    if 'size' in f and 'md5Checksum' in f:
                        fecha_raw = f.get('modifiedTime', 'Desconocida').replace('T', ' ')[:19]
                        data = {'origen': 'Drive', 'id': f['id'], 'nombre': f['name'], 'link': f['webViewLink'], 'size': f['size'], 'hash': f['md5Checksum'], 'fecha': fecha_raw}
                        with self.lock_datos:
                            self.archivos_por_tamano[f['size']].append(data)
                        total_drive += 1
                
                if total_drive % 1000 == 0:
                    self.cola_mensajes.put(("log", f"[DRIVE] ☁️ Metadatos descargados: {total_drive}"))
                
                page_token = resultados.get('nextPageToken')
                if not page_token: break
            self.cola_mensajes.put(("log", f"[DRIVE] ✅ Finalizado. ({total_drive} archivos)"))
        except Exception as e:
            self.cola_mensajes.put(("error_log", f"[DRIVE] ❌ Error: {e}"))

    def escanear_ruta_nas(self, ruta_activa, id_hilo):
        self.cola_mensajes.put(("log", f"[NAS-{id_hilo}] 💾 Indexando: {ruta_activa}"))
        if not os.path.exists(ruta_activa):
            self.cola_mensajes.put(("error_log", f"[NAS-{id_hilo}] ❌ Ruta inalcanzable."))
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
                        self.cola_mensajes.put(("log", f"[NAS-{id_hilo}] 💾 {contador_nas} archivos mapeados..."))
                except Exception:
                    pass 
        self.cola_mensajes.put(("log", f"[NAS-{id_hilo}] ✅ Finalizado. ({contador_nas} archivos)"))

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

            self.cola_mensajes.put(("estado", "Calculando MD5 para duplicados..."))
            self.cola_mensajes.put(("log", "[SISTEMA] 🔍 Extracción terminada. Iniciando análisis de Hashes..."))
            
            sospechosos_nas = []
            for tamano, lista_archivos in self.archivos_por_tamano.items():
                if len(lista_archivos) > 1:
                    for archivo in lista_archivos:
                        if archivo['origen'] == 'NAS': sospechosos_nas.append(archivo)
                        else: self.duplicados_detectados[archivo['hash']].append(archivo)

            total_sospechosos = len(sospechosos_nas)
            self.cola_mensajes.put(("log", f"[SISTEMA] 🚨 {total_sospechosos} sospechosos locales."))

            procesados_hash = 0
            with ThreadPoolExecutor(max_workers=6) as executor_hash: 
                for sospechoso in sospechosos_nas:
                    self.evento_pausa.wait() 
                    md5 = self.calcular_hash_md5(sospechoso['id'])
                    if md5:
                        sospechoso['hash'] = md5
                        with self.lock_datos: self.duplicados_detectados[md5].append(sospechoso)
                    procesados_hash += 1
                    if procesados_hash % 100 == 0 or procesados_hash == total_sospechosos:
                        self.cola_mensajes.put(("log", f"[SISTEMA] ⚙️ Hashes: {procesados_hash} / {total_sospechosos} calculados"))

            self.guardar_sesion_en_bd() 
            self.generar_reporte_excel()

            self.archivos_por_tamano.clear() 
            gc.collect() 
            self.cola_mensajes.put(("log", "[SISTEMA] 🧹 Memoria RAM liberada y optimizada."))

            self.cola_mensajes.put(("log", "[SISTEMA] 🏆 AUDITORÍA COMPLETADA EXITOSAMENTE."))
            self.cola_mensajes.put(("estado", "Auditoría finalizada. Resultados listos."))
            self.cola_mensajes.put(("actualizar_tabla", None))

        except Exception as e:
            self.cola_mensajes.put(("error", f"Error general: {e}"))
        finally:
            self.cola_mensajes.put(("fin_escaneo", None))

    def calcular_hash_md5(self, ruta):
        """Lectura optimizada de disco (I/O Tuning). Chunks de 64KB en vez de 4KB"""
        hash_md5 = hashlib.md5()
        try:
            with open(ruta, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""): 
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            return None

    def generar_reporte_excel(self):
        try:
            with open(self.archivo_reporte, 'w', newline='', encoding='utf-8-sig') as f_csv:
                writer = csv.writer(f_csv, delimiter=';') 
                writer.writerow(['Hash MD5', 'Origen', 'Nombre del Archivo', 'Tamaño (Bytes)', 'Fecha de Modificación', 'Ruta Local / Link Web'])
                hay_datos = False
                for h, lista in self.duplicados_detectados.items():
                    if len(lista) > 1:
                        hay_datos = True
                        for arch in lista:
                            writer.writerow([arch['hash'], arch['origen'], arch['nombre'], arch['size'], arch.get('fecha', 'N/A'), arch['link']])
                if hay_datos:
                    self.cola_mensajes.put(("log", f"[SISTEMA] 📊 Reporte guardado en {self.archivo_reporte}"))
        except PermissionError:
             self.cola_mensajes.put(("error_log", "[SISTEMA] ⚠️ El archivo Excel está abierto. Ciérrelo para poder actualizar el reporte."))
        except Exception as e:
            self.cola_mensajes.put(("error_log", f"[SISTEMA] ⚠️ Error reporte: {e}"))

    # ------------------------------------------
    # MÓDULO DE UI, PAGINACIÓN Y MANEJO DE ERRORES
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
                    if tipo == "log": logging.info(valor)
                    else: logging.error(valor)
                elif tipo == "estado":
                    self.lbl_estado.config(text=f"Estado: {valor}")
                elif tipo == "actualizar_tabla":
                    self.preparar_paginacion()
                    self.poblar_tabla()
                elif tipo == "fin_escaneo":
                    self.escaneo_en_curso = False
                    self.progreso.stop()
                elif tipo == "error":
                    self.lbl_estado.config(text="Detenido por Error.")
                    self.escaneo_en_curso = False
                    self.progreso.stop()
                    self.btn_iniciar.config(state="normal")
                    self.btn_ajustes.config(state="normal")
                    self.btn_sync.config(state="normal")
                    self.btn_pausa.config(state="disabled")
                    messagebox.showerror("Error Crítico", valor)
        except queue.Empty:
            pass
        self.root.after(100, self.procesar_cola)

    def preparar_paginacion(self):
        self.lista_duplicados_plana = []
        for h, lista in self.duplicados_detectados.items():
            if len(lista) > 1:
                for arch in lista:
                    self.lista_duplicados_plana.append(arch)
        total_items = len(self.lista_duplicados_plana)
        self.total_paginas = max(1, math.ceil(total_items / self.items_por_pagina))
        if self.pagina_actual > self.total_paginas: self.pagina_actual = self.total_paginas

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.poblar_tabla(desde_paginacion=True)

    def pagina_siguiente(self):
        if self.pagina_actual < self.total_paginas:
            self.pagina_actual += 1
            self.poblar_tabla(desde_paginacion=True)

    def poblar_tabla(self, desde_paginacion=False):
        for i in self.tree.get_children(): self.tree.delete(i)
        inicio = (self.pagina_actual - 1) * self.items_por_pagina
        fin = inicio + self.items_por_pagina
        pagina_datos = self.lista_duplicados_plana[inicio:fin]
        for arch in pagina_datos:
            self.tree.insert("", "end", values=(arch['hash'], arch['origen'], arch['nombre'], arch['size'], arch.get('fecha', 'N/A'), arch['link']), tags=(arch['id'],))
        
        self.lbl_pagina.config(text=f"Página {self.pagina_actual} de {self.total_paginas}")
        self.btn_ant.config(state="normal" if self.pagina_actual > 1 else "disabled")
        self.btn_sig.config(state="normal" if self.pagina_actual < self.total_paginas else "disabled")
        
        if not desde_paginacion:
            self.btn_iniciar.config(state="normal")
            self.btn_ajustes.config(state="normal")
            self.btn_sync.config(state="normal")
            self.btn_pausa.config(state="disabled")
            if os.path.exists(self.archivo_reporte): self.btn_abrir_excel.config(state="normal")

    def al_seleccionar_item(self, event):
        if self.tree.selection():
            self.btn_preview.config(state="normal")
            self.btn_borrar.config(state="normal")

    def abrir_vista_previa(self):
        item = self.tree.selection()[0]
        origen = self.tree.item(item, "values")[1]
        link = self.tree.item(item, "values")[5] 
        if origen == "Drive": webbrowser.open(link)
        else:
            try: os.startfile(link)
            except Exception as e: messagebox.showerror("Error", f"Fallo al abrir: {e}")

    def abrir_reporte_excel(self):
        if os.path.exists(self.archivo_reporte):
            try: os.startfile(self.archivo_reporte)
            except Exception as e: messagebox.showerror("Error", f"No se pudo abrir el reporte: {e}")

    def borrar_seguro(self):
        item = self.tree.selection()[0]
        hash_id, origen, nombre, tamano, fecha, link = self.tree.item(item, "values")
        id_real = self.tree.item(item, "tags")[0]

        if messagebox.askyesno("Confirmación Crítica", f"¿Mover '{nombre}' a cuarentena/papelera?"):
            try:
                if origen == "Drive":
                    servicio = self.obtener_servicio_drive()
                    servicio.files().update(fileId=id_real, body={'trashed': True}).execute()
                    self.cola_mensajes.put(("log", f"[ACCIÓN] 🗑️ [{self.nombre_equipo}\\{self.usuario_local}] movió a Papelera Drive: {nombre}"))
                else:
                    cuarentena_dir = os.path.join(DIRECTORIO_BASE, '_CUARENTENA_DUPLICADOS')
                    os.makedirs(cuarentena_dir, exist_ok=True)
                    # --- NUEVO: Control de error de Permisos (Archivo en uso) ---
                    try:
                        os.rename(link, os.path.join(cuarentena_dir, nombre))
                        self.cola_mensajes.put(("log", f"[ACCIÓN] 📦 [{self.nombre_equipo}\\{self.usuario_local}] movió a Cuarentena NAS: {link}"))
                    except PermissionError:
                        messagebox.showwarning("Archivo Bloqueado", f"No se puede mover el archivo:\n\n{nombre}\n\nActualmente está abierto o en uso por otro empleado/programa. Ciérrelo e intente de nuevo.")
                        return 
                
                self.tree.delete(item)
                self.eliminar_registro_bd(id_real)
                
                self.lista_duplicados_plana = [arch for arch in self.lista_duplicados_plana if arch['id'] != id_real]
                self.total_paginas = max(1, math.ceil(len(self.lista_duplicados_plana) / self.items_por_pagina))
                self.lbl_pagina.config(text=f"Página {self.pagina_actual} de {self.total_paginas}")
                
                if hash_id in self.duplicados_detectados:
                    self.duplicados_detectados[hash_id] = [a for a in self.duplicados_detectados[hash_id] if a['id'] != id_real]
                    if len(self.duplicados_detectados[hash_id]) < 2:
                        del self.duplicados_detectados[hash_id]

            except Exception as e:
                messagebox.showerror("Error", f"Fallo al procesar: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = AppAuditoriaAvanzada(root)
    root.mainloop()