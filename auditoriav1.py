import os
import hashlib
import csv
import logging
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

# Importaciones de Google Drive
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- CONFIGURACIÓN ---
DIRECTORIO_BASE = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_INVENTARIO = os.path.join(DIRECTORIO_BASE, 'inventario_maestro.csv')
ARCHIVO_LOG = os.path.join(DIRECTORIO_BASE, 'historial_auditoria.txt')
ARCHIVO_CREDENCIALES = os.path.join(DIRECTORIO_BASE, 'credentials.json')
ARCHIVO_TOKEN = os.path.join(DIRECTORIO_BASE, 'token.json')

ARCHIVOS_SISTEMA = [os.path.basename(__file__), 'inventario_maestro.csv', 'historial_auditoria.txt', 'credentials.json', 'token.json']
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']

logging.basicConfig(filename=ARCHIVO_LOG, level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s', encoding='utf-8')

class AppAuditoria:
    def __init__(self, root):
        self.root = root
        self.root.title("Deduplicador Pro - Drive + Red + Vista")
        self.root.geometry("650x750")
        self.root.resizable(False, False)
        
        self.evento_pausa = threading.Event()
        self.evento_pausa.set()
        self.esta_pausado = False

        self.duplicados_count = 0
        self.hashes_vistos = set()
        self.archivos_totales = 0
        self.ruta_activa = ""

        self.construir_interfaz()

    def construir_interfaz(self):
        ttk.Label(self.root, text="Panel de Auditoría Híbrida (Drive + NAS)", font=("Arial", 14, "bold")).pack(pady=10)
        
        # --- PANEL DE CREDENCIALES DE RED ---
        f_red = ttk.LabelFrame(self.root, text=" 🔐 1. Conexión a Servidor / NAS ")
        f_red.pack(fill="x", padx=20, pady=5)

        ttk.Label(f_red, text="Ruta (ej. \\\\FILESSERVER):").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.entry_ruta = ttk.Entry(f_red, width=40)
        self.entry_ruta.insert(0, r"\\FILESSERVER")
        self.entry_ruta.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(f_red, text="Usuario:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.entry_usuario = ttk.Entry(f_red, width=40)
        self.entry_usuario.insert(0, r"CREDIMARA0\ADMINISTRADOR")
        self.entry_usuario.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(f_red, text="Contraseña:").grid(row=2, column=0, padx=5, pady=5, sticky="e")
        self.entry_password = ttk.Entry(f_red, width=40, show="*") 
        self.entry_password.insert(0, "SAcm$231205")
        self.entry_password.grid(row=2, column=1, padx=5, pady=5)

        f_btn_red = ttk.Frame(f_red)
        f_btn_red.grid(row=3, column=0, columnspan=2, pady=10)
        ttk.Button(f_btn_red, text="🔗 Conectar", command=self.conectar_servidor).pack(side="left", padx=5)
        ttk.Button(f_btn_red, text="👁️ Validar Archivos", command=self.abrir_vista_archivos).pack(side="left", padx=5)

        # --- PANEL GOOGLE DRIVE ---
        f_drive = ttk.LabelFrame(self.root, text=" ☁️ 2. Progreso Google Drive ")
        f_drive.pack(fill="x", padx=20, pady=5)
        self.lbl_drive = ttk.Label(f_drive, text="Esperando inicio...")
        self.lbl_drive.pack(pady=5)
        self.prog_drive = ttk.Progressbar(f_drive, orient="horizontal", mode="indeterminate")
        self.prog_drive.pack(fill="x", padx=10, pady=5)

        # --- PANEL NAS ---
        f_nas = ttk.LabelFrame(self.root, text=" 💾 3. Progreso NAS Local / Servidor ")
        f_nas.pack(fill="x", padx=20, pady=5)
        self.lbl_nas = ttk.Label(f_nas, text="Esperando conexión de red...")
        self.lbl_nas.pack(pady=5)
        self.prog_nas = ttk.Progressbar(f_nas, orient="horizontal", mode="determinate")
        self.prog_nas.pack(fill="x", padx=10, pady=5)

        # --- ESTADÍSTICAS ---
        f_res = ttk.Frame(self.root)
        f_res.pack(fill="x", padx=20, pady=5)
        self.lbl_archivos = ttk.Label(f_res, text="📄 Archivos Totales: 0", font=("Arial", 10, "bold"))
        self.lbl_archivos.pack(side="left", padx=10)
        self.lbl_duplicados = ttk.Label(f_res, text="⚠️ Duplicados Detectados: 0", font=("Arial", 10, "bold"), foreground="red")
        self.lbl_duplicados.pack(side="left", padx=10)

        # --- CONTROLES ---
        f_btns = ttk.Frame(self.root)
        f_btns.pack(pady=15)
        self.btn_iniciar = ttk.Button(f_btns, text="🚀 Iniciar Auditoría Completa", command=self.lanzar_hilo, state="disabled")
        self.btn_iniciar.pack(side="left", padx=5)
        self.btn_pausa = ttk.Button(f_btns, text="⏸️ Pausar", command=self.alternar_pausa, state="disabled")
        self.btn_pausa.pack(side="left", padx=5)

    def conectar_servidor(self):
        ruta = self.entry_ruta.get().strip()
        usuario = self.entry_usuario.get().strip()
        password = self.entry_password.get().strip()

        if not ruta:
            messagebox.showwarning("Aviso", "Por favor ingresa una ruta.")
            return

        comando = f'net use "{ruta}" "{password}" /user:"{usuario}"'
        
        try:
            self.lbl_nas.config(text="Autenticando en el servidor...")
            self.root.update()
            
            resultado = subprocess.run(comando, shell=True, capture_output=True, text=True)
            
            if resultado.returncode == 0 or "Error de sistema 1219" in resultado.stderr: 
                messagebox.showinfo("Éxito", f"Conectado correctamente a:\n{ruta}")
                self.ruta_activa = ruta
                self.btn_iniciar.config(state="normal")
                self.lbl_nas.config(text="Listo para iniciar auditoría local.")
                logging.info(f"Conectado exitosamente a {ruta}")
            else:
                messagebox.showerror("Error de Red", f"Fallo al conectar:\n{resultado.stderr}")
                self.lbl_nas.config(text="Error de conexión.")
        except Exception as e:
            messagebox.showerror("Error Crítico", str(e))

    def abrir_vista_archivos(self):
        ruta = self.entry_ruta.get().strip()
        if not os.path.exists(ruta):
            messagebox.showwarning("Aviso", "La ruta no existe o aún no te has conectado. Haz clic en 'Conectar' primero.")
            return

        win_archivos = tk.Toplevel(self.root)
        win_archivos.title(f"Validador de Archivos - {ruta}")
        win_archivos.geometry("700x500")

        tree = ttk.Treeview(win_archivos, columns=("Ruta", "Tamaño"), selectmode="extended")
        tree.heading("#0", text="Nombre", anchor='w')
        tree.heading("Ruta", text="Ruta Relativa", anchor='w')
        tree.heading("Tamaño", text="Tamaño (KB)", anchor='w')
        
        tree.column("#0", width=250)
        tree.column("Ruta", width=300)
        tree.column("Tamaño", width=100)

        scrollbar = ttk.Scrollbar(win_archivos, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=10)

        archivos_encontrados = 0
        for root_dir, dirs, files in os.walk(ruta):
            for f in files:
                if f in ARCHIVOS_SISTEMA: continue
                ruta_completa = os.path.join(root_dir, f)
                try:
                    tamano = os.path.getsize(ruta_completa) // 1024 
                    tree.insert("", "end", text=f, values=(root_dir.replace(ruta, ""), f"{tamano} KB"))
                    archivos_encontrados += 1
                except: pass
                
                if archivos_encontrados > 1000:
                    tree.insert("", "end", text="... y más archivos", values=("", ""))
                    return
            break

    def alternar_pausa(self):
        if not self.esta_pausado:
            self.evento_pausa.clear()
            self.esta_pausado = True
            self.btn_pausa.config(text="▶️ Continuar")
            logging.info("PROCESO PAUSADO POR EL USUARIO")
        else:
            self.evento_pausa.set()
            self.esta_pausado = False
            self.btn_pausa.config(text="⏸️ Pausar")
            logging.info("PROCESO REANUDADO POR EL USUARIO")

    def lanzar_hilo(self):
        self.btn_iniciar.config(state="disabled")
        self.btn_pausa.config(state="normal")
        threading.Thread(target=self.proceso_principal, daemon=True).start()

    def proceso_principal(self):
        try:
            with open(ARCHIVO_INVENTARIO, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['Origen', 'Nombre', 'Ruta', 'Tamano', 'Fecha', 'Hash'])
                writer.writeheader()
                
                # --- FASE 1: GOOGLE DRIVE ---
                self.root.after(0, lambda: self.lbl_drive.config(text="Autenticando y obteniendo metadatos..."))
                service = obtener_servicio_drive()
                self.root.after(0, self.prog_drive.start)
                page_token = None
                
                while True:
                    self.evento_pausa.wait() 
                    
                    results = service.files().list(
                        q="trashed = false", 
                        fields="nextPageToken, files(name, size, md5Checksum, modifiedTime, webViewLink)", 
                        pageToken=page_token
                    ).execute()
                    
                    for d in results.get('files', []):
                        self.evento_pausa.wait()
                        nombre = d.get('name', 'Desconocido')
                        size = int(d.get('size', 0))
                        h = d.get('md5Checksum', 'N/A')
                        fecha = d.get('modifiedTime', '-')
                        link = d.get('webViewLink', '-')
                        
                        writer.writerow({'Origen': 'Drive', 'Nombre': nombre, 'Ruta': link, 'Tamano': size, 'Fecha': fecha, 'Hash': h})
                        self.root.after(0, self.actualizar_progreso_drive, nombre, h)
                    
                    page_token = results.get('nextPageToken')
                    if not page_token: break
                
                self.root.after(0, self.prog_drive.stop)
                self.root.after(0, lambda: self.lbl_drive.config(text="Fase Drive Completada. ✅"))

                # --- FASE 2: NAS / SERVIDOR LOCAL ---
                self.root.after(0, lambda: self.lbl_nas.config(text="Contando archivos en servidor..."))
                archivos_lista = []
                for r, d, files in os.walk(self.ruta_activa):
                    for name in files: 
                        archivos_lista.append(os.path.join(r, name))
                
                total = len(archivos_lista)
                for i, ruta in enumerate(archivos_lista):
                    self.evento_pausa.wait() 
                    
                    nombre = os.path.basename(ruta)
                    if nombre in ARCHIVOS_SISTEMA: continue
                    
                    h = self.calcular_hash_md5(ruta)
                    try:
                        size = os.path.getsize(ruta)
                        fecha = datetime.fromtimestamp(os.path.getmtime(ruta)).strftime('%Y-%m-%dT%H:%M:%S')
                    except:
                        size = 0
                        fecha = '-'
                        
                    writer.writerow({'Origen': 'NAS', 'Nombre': nombre, 'Ruta': ruta, 'Tamano': size, 'Fecha': fecha, 'Hash': h})
                    
                    progreso = (i / total) * 100 if total > 0 else 100
                    self.root.after(0, self.actualizar_progreso_nas, progreso, nombre, h)

            self.root.after(0, self.finalizar_proceso)

        except Exception as e:
            logging.error(f"Error: {e}")
            self.root.after(0, lambda: messagebox.showerror("Error", f"Ocurrió un error en el proceso:\n{e}"))

    def actualizar_progreso_drive(self, nombre, h):
        self.lbl_drive.config(text=f"Procesando en nube: {nombre[:30]}...")
        self.actualizar_contadores(h)

    def actualizar_progreso_nas(self, progreso, nombre, h):
        self.prog_nas['value'] = progreso
        self.lbl_nas.config(text=f"Procesando local: {nombre[:30]}...")
        self.actualizar_contadores(h)

    def actualizar_contadores(self, h):
        self.archivos_totales += 1
        if h != "ERROR" and h != "N/A" and h in self.hashes_vistos: 
            self.duplicados_count += 1
        else: 
            self.hashes_vistos.add(h)
            
        self.lbl_archivos.config(text=f"📄 Archivos Totales: {self.archivos_totales}")
        self.lbl_duplicados.config(text=f"⚠️ Duplicados Detectados: {self.duplicados_count}")

    def finalizar_proceso(self):
        self.btn_pausa.config(state="disabled")
        self.lbl_nas.config(text="Fase NAS Completada. ✅")
        messagebox.showinfo("Fin", "Auditoría completa finalizada. Revisa el inventario_maestro.csv")

    def calcular_hash_md5(self, ruta):
        hash_md5 = hashlib.md5()
        try:
            # Optimizacion de lectura para red local (Chunks de 1MB)
            with open(ruta, "rb") as f:
                for chunk in iter(lambda: f.read(1048576), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except: 
            return "ERROR"

def obtener_servicio_drive():
    creds = None
    if os.path.exists(ARCHIVO_TOKEN):
        creds = Credentials.from_authorized_user_file(ARCHIVO_TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(ARCHIVO_CREDENCIALES, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(ARCHIVO_TOKEN, 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

if __name__ == "__main__":
    root = tk.Tk()
    app = AppAuditoria(root)
    root.mainloop()