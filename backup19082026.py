import os
import sys
import json
import zipfile
import threading
import logging
import time
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- LIBRERÍAS DE GOOGLE DRIVE ---
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# 0. CONTROL HORARIO Y NUBE
# ==========================================
def obtener_hora_venezuela():
    return datetime.utcnow() - timedelta(hours=4)

def subir_a_drive(ruta_archivo, drive_folder_id, logger, ui_log_callback):
    CREDENTIALS_FILE = 'credenciales_servicio.json'
    
    if not os.path.exists(CREDENTIALS_FILE):
        logger.warning(f"Omitiendo subida a Drive: No se encontró {CREDENTIALS_FILE}")
        return False
    if not drive_folder_id or drive_folder_id.strip() == "":
        return False

    try:
        nombre_archivo = os.path.basename(ruta_archivo)
        ui_log_callback(f"[DRIVE] Conectando a la nube para subir: {nombre_archivo}...\n")
        
        scopes = ['https://www.googleapis.com/auth/drive.file']
        creds = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)

        file_metadata = {
            'name': nombre_archivo,
            'parents': [drive_folder_id.strip()]
        }
        
        media = MediaFileUpload(ruta_archivo, resumable=True)
        request = service.files().create(body=file_metadata, media_body=media, fields='id')
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progreso = int(status.progress() * 100)
                if progreso % 20 == 0:
                    ui_log_callback(f"[DRIVE] {nombre_archivo} -> {progreso}% subido...\n")
        
        logger.info(f"Subida a Drive exitosa. File ID: {response.get('id')}")
        ui_log_callback(f"[DRIVE] ¡Subida completada al 100%! ({nombre_archivo})\n")
        return True

    except Exception as e:
        logger.error(f"Fallo crítico subiendo a Drive: {e}")
        ui_log_callback(f"[DRIVE ERROR] No se pudo subir {os.path.basename(ruta_archivo)}: {e}\n")
        return False

# ==========================================
# 1. FUNCIONES CORE Y MOTOR DE RESPALDO
# ==========================================

def get_dir_size(path):
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += get_dir_size(entry.path)
    except Exception: pass 
    return total

def setup_logger(origen_name, destino_path):
    timestamp = obtener_hora_venezuela().strftime("%H%M_%d%m%Y")
    log_filename = os.path.join(destino_path, f"LOG_{origen_name}_{timestamp}.txt")
    
    logger = logging.getLogger(f"Backup_{origen_name}_{timestamp}")
    logger.setLevel(logging.INFO)
    if logger.hasHandlers(): logger.handlers.clear()
        
    handler = logging.FileHandler(log_filename, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, timestamp, log_filename

def limpiar_respaldos_antiguos(destino, dias_retencion, logger, ui_log_callback):
    limite_tiempo = time.time() - (dias_retencion * 86400) 
    archivos_borrados = 0
    try:
        for archivo in os.listdir(destino):
            ruta_archivo = os.path.join(destino, archivo)
            if os.path.isfile(ruta_archivo) and (archivo.endswith('.zip') or archivo.startswith('LOG_')):
                if os.path.getmtime(ruta_archivo) < limite_tiempo:
                    os.remove(ruta_archivo)
                    logger.info(f"Rotación automática: Eliminado -> {archivo}")
                    archivos_borrados += 1
        if archivos_borrados > 0:
            ui_log_callback(f"[SISTEMA] Rotación completada: {archivos_borrados} archivos antiguos eliminados.\n")
    except Exception as e:
        logger.error(f"Error en limpieza automática: {e}")

# ---> MOTOR ACTUALIZADO PARA RECIBIR EL INTERRUPTOR DE DRIVE <---
def ejecutar_respaldo(origen, destino, drive_id, activar_nube, ui_log_callback, evento_pausa):
    try:
        origen_name = os.path.basename(os.path.normpath(origen))
        if not origen_name: origen_name = "ROOT"

        if not os.path.exists(origen):
            ui_log_callback(f"[ERROR] Ruta origen no accesible: {origen}\n")
            return

        os.makedirs(destino, exist_ok=True)
        logger, timestamp, log_filename = setup_logger(origen_name, destino)
        
        msg_inicio = f"--- INICIANDO RESPALDO LOCAL: {origen_name} ---\n"
        logger.info(msg_inicio.strip())
        ui_log_callback(msg_inicio)
        
        origen_size = get_dir_size(origen)
        ui_log_callback(f"[{origen_name}] Tamaño escaneado: {origen_size / (1024**2):.2f} MB\n")
        
        nombre_zip = f"{origen_name}_{timestamp}.zip"
        ruta_salida_zip = os.path.join(destino, nombre_zip)
        
        archivos_procesados, archivos_fallidos = 0, 0
        
        ui_log_callback(f"[{origen_name}] Comprimiendo archivos...\n")
        with zipfile.ZipFile(ruta_salida_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for folder_root, subfolders, files in os.walk(origen):
                for file in files:
                    evento_pausa.wait()
                    ruta_absoluta = os.path.join(folder_root, file)
                    ruta_relativa = os.path.relpath(ruta_absoluta, origen)
                    
                    try:
                        logger.info(f"Agregando: {ruta_relativa}")
                        zipf.write(ruta_absoluta, arcname=ruta_relativa)
                        archivos_procesados += 1
                    except PermissionError:
                        logger.warning(f"ACCESO DENEGADO (En Uso): {ruta_relativa}")
                        archivos_fallidos += 1
                    except Exception as e:
                        logger.error(f"ERROR: {ruta_relativa} -> {e}")
                        archivos_fallidos += 1
        
        zip_size = os.path.getsize(ruta_salida_zip)
        logger.info("RESPALDO COMPLETADO")
        resumen = f"[{origen_name}] ÉXITO LOCAL | {origen_size / (1024**2):.2f} MB -> ZIP: {zip_size / (1024**2):.2f} MB\n"
        ui_log_callback(resumen)
        
        # --- FASE DE INTEGRACIÓN CLOUD CONDICIONAL ---
        if activar_nube and drive_id:
            subir_a_drive(ruta_salida_zip, drive_id, logger, ui_log_callback)
            subir_a_drive(log_filename, drive_id, logger, ui_log_callback) 
        elif not activar_nube and drive_id:
            ui_log_callback(f"[{origen_name}] ⚠️ Subida a Google Drive omitida (Desactivada por el usuario).\n")

        limpiar_respaldos_antiguos(destino, 15, logger, ui_log_callback)
        
    except Exception as e:
        error_msg = f"[FATAL] Fallo en {origen}: {str(e)}\n"
        ui_log_callback(error_msg)
        if 'logger' in locals(): logger.error(error_msg)

# ==========================================
# 2. INTERFAZ GRÁFICA Y CONTROLADOR (MODO VISUAL)
# ==========================================

class BackupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sistema de Respaldos Híbrido (Red Local + Google Drive)")
        self.geometry("780x620")
        self.archivo_config = "config.json"
        self.respaldo_en_curso = False
        self.ultimo_respaldo = "" 
        self.evento_pausa = threading.Event()
        self.evento_pausa.set() 
        self.estado_pausa_ui = False
        
        self.cargar_configuracion()
        self.construir_ui()
        threading.Thread(target=self.motor_programacion, daemon=True).start()

    def cargar_configuracion(self):
        if os.path.exists(self.archivo_config):
            try:
                with open(self.archivo_config, 'r', encoding='utf-8') as f:
                    datos = json.load(f)
                    self.rutas = datos.get("rutas", [])
                    self.hora_programada = datos.get("hora_programada", "23:00")
                    self.ultimo_respaldo = datos.get("ultimo_respaldo", "")
                    self.usar_drive = datos.get("usar_drive", True) # Por defecto activo
            except:
                self.rutas = []
                self.hora_programada = "23:00"
                self.ultimo_respaldo = ""
                self.usar_drive = True
        else:
            self.rutas = []
            self.hora_programada = "23:00"
            self.ultimo_respaldo = ""
            self.usar_drive = True
            self.guardar_configuracion()

    def guardar_configuracion(self, event=None):
        hora_actualizada = self.entry_hora.get() if hasattr(self, 'entry_hora') else self.hora_programada
        estado_nube = self.var_usar_drive.get() if hasattr(self, 'var_usar_drive') else self.usar_drive
        
        datos = {
            "hora_programada": hora_actualizada, 
            "ultimo_respaldo": self.ultimo_respaldo, 
            "usar_drive": estado_nube,
            "rutas": self.rutas
        }
        try:
            with open(self.archivo_config, 'w', encoding='utf-8') as f:
                json.dump(datos, f, indent=4)
        except Exception as e:
            messagebox.showerror("Error de E/S", f"No se pudo guardar la configuración: {e}")

    def construir_ui(self):
        frame_top = ttk.LabelFrame(self, text="Configuración General (Hora Local Vzla)")
        frame_top.pack(pady=10, padx=10, fill="x")
        
        ttk.Label(frame_top, text="Hora (HH:MM):").pack(side="left", padx=5, pady=5)
        self.entry_hora = ttk.Entry(frame_top, width=8)
        self.entry_hora.insert(0, self.hora_programada)
        self.entry_hora.pack(side="left", padx=5)
        self.entry_hora.bind("<FocusOut>", self.guardar_configuracion)
        
        # ---> NUEVO BOTÓN DE INTERRUPTOR NUBE <---
        self.var_usar_drive = tk.BooleanVar(value=self.usar_drive)
        self.chk_drive = ttk.Checkbutton(frame_top, text="☁️ Activar Google Drive", variable=self.var_usar_drive, command=self.guardar_configuracion)
        self.chk_drive.pack(side="left", padx=15)
        
        ttk.Button(frame_top, text="⚙️ Rutas (Locales/Nube)", command=self.abrir_ajustes_rutas).pack(side="right", padx=5, pady=5)
        ttk.Button(frame_top, text="💾 Guardar", command=self.guardar_configuracion).pack(side="right", padx=5)
        
        frame_consola = ttk.LabelFrame(self, text="Consola de Operaciones")
        frame_consola.pack(pady=5, padx=10, fill="both", expand=True)
        
        self.txt_consola = tk.Text(frame_consola, bg="black", fg="#00FF00", font=("Consolas", 10))
        self.txt_consola.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        scrollbar = ttk.Scrollbar(frame_consola, command=self.txt_consola.yview)
        scrollbar.pack(side="right", fill="y")
        self.txt_consola.config(yscrollcommand=scrollbar.set)
        
        frame_bottom = ttk.Frame(self)
        frame_bottom.pack(pady=10, padx=10, fill="x")
        
        self.btn_ejecutar = ttk.Button(frame_bottom, text="🚀 EJECUTAR RESPALDOS", command=self.iniciar_respaldo)
        self.btn_ejecutar.pack(side="left", expand=True, fill="x", padx=5, ipady=5)
        self.btn_pausa = ttk.Button(frame_bottom, text="⏸ PAUSAR", command=self.toggle_pausa, state="disabled")
        self.btn_pausa.pack(side="right", expand=True, fill="x", padx=5, ipady=5)

    def print_consola(self, mensaje):
        self.txt_consola.insert(tk.END, mensaje)
        self.txt_consola.see(tk.END)
        self.update_idletasks()

    def toggle_pausa(self):
        if not self.estado_pausa_ui:
            self.evento_pausa.clear() 
            self.estado_pausa_ui = True
            self.btn_pausa.config(text="▶ REANUDAR")
            self.print_consola("\n[!] PROCESOS EN PAUSA. Se detendrán al finalizar el archivo en curso...\n")
        else:
            self.evento_pausa.set()
            self.estado_pausa_ui = False
            self.btn_pausa.config(text="⏸ PAUSAR")
            self.print_consola("\n[>] PROCESOS REANUDADOS.\n")

    def abrir_ajustes_rutas(self):
        ventana_ajustes = tk.Toplevel(self)
        ventana_ajustes.title("Gestor de Rutas y Nube")
        ventana_ajustes.geometry("750x450")
        ventana_ajustes.grab_set() 
        
        columnas = ("origen", "destino", "drive_id")
        tree = ttk.Treeview(ventana_ajustes, columns=columnas, show="headings", selectmode="browse")
        tree.heading("origen", text="Origen (Red Local)")
        tree.heading("destino", text="Destino Local")
        tree.heading("drive_id", text="ID Carpeta Google Drive")
        tree.column("origen", width=250)
        tree.column("destino", width=250)
        tree.column("drive_id", width=200)
        tree.pack(pady=10, padx=10, fill="both", expand=True)
        
        def refrescar_tabla():
            for item in tree.get_children(): tree.delete(item)
            for r in self.rutas:
                did = r.get("drive_id", "")
                tree.insert("", tk.END, values=(r["origen"], r["destino"], did))
                
        refrescar_tabla()
        
        frame_controles = ttk.Frame(ventana_ajustes)
        frame_controles.pack(pady=5, padx=10, fill="x")
        
        ttk.Label(frame_controles, text="Origen:").grid(row=0, column=0, sticky="w")
        entry_origen = ttk.Entry(frame_controles, width=35)
        entry_origen.grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(frame_controles, text="Buscar", command=lambda: entry_origen.insert(0, filedialog.askdirectory())).grid(row=0, column=2)
        
        ttk.Label(frame_controles, text="Destino:").grid(row=1, column=0, sticky="w")
        entry_destino = ttk.Entry(frame_controles, width=35)
        entry_destino.grid(row=1, column=1, padx=5, pady=2)
        ttk.Button(frame_controles, text="Buscar", command=lambda: entry_destino.insert(0, filedialog.askdirectory())).grid(row=1, column=2)

        ttk.Label(frame_controles, text="ID Google Drive (Opcional):").grid(row=2, column=0, sticky="w")
        entry_drive = ttk.Entry(frame_controles, width=35)
        entry_drive.grid(row=2, column=1, padx=5, pady=2)
        
        def btn_agregar_action():
            ori, des, drv = entry_origen.get().strip(), entry_destino.get().strip(), entry_drive.get().strip()
            if ori and des:
                self.rutas.append({"origen": ori, "destino": des, "drive_id": drv})
                self.guardar_configuracion()
                refrescar_tabla()
                entry_origen.delete(0, tk.END)
                entry_destino.delete(0, tk.END)
                entry_drive.delete(0, tk.END)
            else:
                messagebox.showwarning("Campos vacíos", "Debe proporcionar rutas de Origen y Destino.")
                
        def btn_eliminar_action():
            seleccion = tree.selection()
            if seleccion:
                origen_sel = tree.item(seleccion[0])['values'][0]
                self.rutas = [r for r in self.rutas if r["origen"] != origen_sel]
                self.guardar_configuracion()
                refrescar_tabla()

        ttk.Button(frame_controles, text="➕ Agregar Ruta", command=btn_agregar_action).grid(row=3, column=1, pady=10, sticky="w")
        ttk.Button(frame_controles, text="❌ Eliminar Seleccionado", command=btn_eliminar_action).grid(row=3, column=1, pady=10, sticky="e")

    def iniciar_respaldo(self):
        if not self.rutas:
            messagebox.showwarning("Sin rutas", "No hay rutas configuradas.")
            return
            
        self.respaldo_en_curso = True
        self.btn_ejecutar.config(state="disabled")
        self.btn_pausa.config(state="normal", text="⏸ PAUSAR")
        self.estado_pausa_ui = False
        self.evento_pausa.set() 
        
        estado_nube = self.var_usar_drive.get()
        modo_txt = "HÍBRIDOS" if estado_nube else "LOCALES (Nube Apagada)"
        self.print_consola("\n" + "="*45 + f"\nINICIANDO PROCESOS {modo_txt} ({obtener_hora_venezuela().strftime('%H:%M:%S')})\n" + "="*45 + "\n")
        
        hilos = []
        for config in self.rutas:
            drv = config.get("drive_id", "")
            hilo = threading.Thread(
                target=ejecutar_respaldo, 
                args=(config["origen"], config["destino"], drv, estado_nube, self.print_consola, self.evento_pausa)
            )
            hilos.append(hilo)
            hilo.start()
            
        threading.Thread(target=self.monitorear_hilos, args=(hilos,), daemon=True).start()

    def monitorear_hilos(self, hilos):
        for hilo in hilos: hilo.join() 
        self.respaldo_en_curso = False
        self.btn_ejecutar.config(state="normal")
        self.btn_pausa.config(state="disabled", text="⏸ PAUSAR")
        self.evento_pausa.set() 
        self.print_consola("\n--- TODAS LAS TAREAS HAN FINALIZADO ---\n")

    def motor_programacion(self):
        while True:
            ahora = obtener_hora_venezuela()
            hora_actual_str, fecha_actual_str = ahora.strftime("%H:%M"), ahora.strftime("%Y-%m-%d")
            hora_configurada = self.entry_hora.get() if hasattr(self, 'entry_hora') else self.hora_programada
            
            if hora_actual_str >= hora_configurada and self.ultimo_respaldo != fecha_actual_str and not self.respaldo_en_curso:
                self.print_consola("\n[SISTEMA] Activando ejecución automática / Modo Contingencia...\n")
                self.ultimo_respaldo = fecha_actual_str
                self.guardar_configuracion()
                self.after(0, self.iniciar_respaldo)
            time.sleep(10)

# ==========================================
# 3. CONTROLADOR DE ARQUITECTURA DUAL (MODO FANTASMA)
# ==========================================
def ejecucion_fantasma():
    if not os.path.exists("config.json"): return
    try:
        with open("config.json", 'r', encoding='utf-8') as f:
            datos = json.load(f)
            rutas = datos.get("rutas", [])
            ultimo_respaldo = datos.get("ultimo_respaldo", "")
            usar_drive = datos.get("usar_drive", True)
    except: return
    if not rutas: return

    fecha_actual_str = obtener_hora_venezuela().strftime("%Y-%m-%d")
    if ultimo_respaldo != fecha_actual_str:
        def consola_nula(m): pass 
        ev_dummy = threading.Event()
        ev_dummy.set()
        
        datos["ultimo_respaldo"] = fecha_actual_str
        try:
            with open("config.json", 'w', encoding='utf-8') as f: json.dump(datos, f, indent=4)
        except: pass
            
        for config in rutas:
            drv = config.get("drive_id", "")
            ejecutar_respaldo(config["origen"], config["destino"], drv, usar_drive, consola_nula, ev_dummy)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--auto": ejecucion_fantasma()
    else: BackupApp().mainloop()