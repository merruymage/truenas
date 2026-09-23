import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
import pandas as pd
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageTk

class VisorDepurador:
    def __init__(self, root):
        self.root = root
        self.root.title("✨ Visor y Depurador Inteligente de Duplicados")
        self.root.geometry("1250x800")
        
        self.df = None
        self.csv_actual = ""
        
        self.datos_a_insertar = []
        self.insertados = 0
        self.total_insertar = 0
        
        self.lista_a_procesar = []
        self.total_procesar = 0
        self.procesados_actual = 0
        self.borrados_fisicos = 0
        self.modo_proceso = ""
        self.pausado = False
        
        # NUEVO: Diccionario para recordar el color original (par/impar) de cada fila
        self.tags_originales = {}
        
        self.construir_interfaz()

    def construir_interfaz(self):
        # --- BARRA SUPERIOR (AJUSTES) ---
        f_top = tb.Frame(self.root, padding=15)
        f_top.pack(fill=X)
        
        tb.Button(f_top, text="📁 Cargar CSV", bootstyle="info", width=15, command=self.cargar_csv).pack(side=LEFT, padx=5)
        tb.Button(f_top, text="💾 Guardar CSV", bootstyle="success-outline", width=15, command=lambda: self.guardar_csv(True)).pack(side=LEFT, padx=5)
        
        self.lbl_estado = tb.Label(f_top, text="Esperando archivo...", font=("Segoe UI", 11, "italic"), bootstyle="secondary")
        self.lbl_estado.pack(side=RIGHT, padx=10)

        # --- PANEL DIVIDIDO ---
        self.paned = tb.Panedwindow(self.root, orient=HORIZONTAL, bootstyle="info")
        self.paned.pack(fill=BOTH, expand=True, padx=15, pady=5)
        
        # PANEL IZQUIERDO: Lista de Archivos
        f_izq = tb.Frame(self.paned)
        self.paned.add(f_izq, weight=7) # 70% del espacio
        
        columnas = ("Accion", "Nombre", "Tamaño", "Fecha", "Ruta")
        self.tree = tb.Treeview(f_izq, columns=columnas, show="headings", selectmode="extended", bootstyle="info")
        self.tree.heading("Accion", text="ESTADO")
        self.tree.heading("Nombre", text="NOMBRE DEL ARCHIVO")
        self.tree.heading("Tamaño", text="TAMAÑO")
        self.tree.heading("Fecha", text="FECHA MODIFICACIÓN")
        self.tree.heading("Ruta", text="RUTA LOCAL")
        
        self.tree.column("Accion", width=90, anchor="center")
        self.tree.column("Nombre", width=250)
        self.tree.column("Tamaño", width=80, anchor="e")
        self.tree.column("Fecha", width=140, anchor="center")
        self.tree.column("Ruta", width=400)
        
        self.tree.tag_configure('grupo_par', background='#f8f9fa')
        self.tree.tag_configure('grupo_impar', background='#ffffff')
        self.tree.tag_configure('eliminar', background='#f8d7da', foreground='#721c24') 
        
        scroll_y = tb.Scrollbar(f_izq, orient=VERTICAL, command=self.tree.yview, bootstyle="info-round")
        self.tree.configure(yscroll=scroll_y.set)
        scroll_y.pack(side=RIGHT, fill=Y)
        self.tree.pack(fill=BOTH, expand=True)
        
        self.tree.bind("<<TreeviewSelect>>", self.al_seleccionar)
        self.tree.bind("<Double-1>", self.abrir_archivo_nativo)
        
        # PANEL DERECHO: Vista Previa y Progreso
        f_der = tb.LabelFrame(self.paned, text=" 👁️ Vista Previa / Progreso ", padding=15, bootstyle="info")
        self.paned.add(f_der, weight=3) # 30% del espacio
        
        self.lbl_preview = tk.Label(f_der, text="Seleccione un archivo\npara ver su contenido.", justify="center", wraplength=300, font=("Segoe UI", 11), fg="#444444")
        self.lbl_preview.pack(fill=BOTH, expand=True)
        
        self.progreso_var = tk.DoubleVar()
        self.barra_progreso = tb.Progressbar(f_der, variable=self.progreso_var, bootstyle="success-striped", maximum=100)

        # --- BARRA INFERIOR (ACCIONES) ---
        f_bottom = tb.Frame(self.root, padding=15)
        f_bottom.pack(fill=X)
        
        self.btn_magico = tb.Button(f_bottom, text="🪄 Auto-Marcar (Salvar 1 Más Reciente)", bootstyle="warning", command=self.auto_marcar_duplicados)
        self.btn_magico.pack(side=LEFT, padx=5)
        
        self.btn_marcar = tb.Button(f_bottom, text="❌ Marcar Selección", bootstyle="secondary", command=lambda: self.procesar_seleccion_manual('ELIMINAR'))
        self.btn_marcar.pack(side=LEFT, padx=5)
        
        self.btn_desmarcar = tb.Button(f_bottom, text="✅ Desmarcar", bootstyle="secondary-outline", command=lambda: self.procesar_seleccion_manual(''))
        self.btn_desmarcar.pack(side=LEFT, padx=5)
        
        self.btn_eliminar = tb.Button(f_bottom, text="🗑️ ELIMINAR FÍSICAMENTE", bootstyle="danger", command=self.eliminar_fisicamente)
        self.btn_eliminar.pack(side=RIGHT, padx=5)
        
        self.btn_pausa = tb.Button(f_bottom, text="⏸️ Pausar", bootstyle="warning-outline", command=self.alternar_pausa, state="disabled")
        self.btn_pausa.pack(side=RIGHT, padx=5)

    def bloquear_interfaz(self, bloqueado=True):
        estado = "disabled" if bloqueado else "normal"
        self.btn_magico.config(state=estado)
        self.btn_marcar.config(state=estado)
        self.btn_desmarcar.config(state=estado)
        self.btn_eliminar.config(state=estado)

    def mostrar_pantalla_carga(self, texto="Procesando..."):
        self.lbl_preview.config(text=texto, font=("Segoe UI", 16, "bold"), fg="#2c3e50")
        self.barra_progreso.pack(fill=X, side=BOTTOM, pady=20)
        self.progreso_var.set(0)
        self.root.update()

    def ocultar_pantalla_carga(self):
        self.barra_progreso.pack_forget()
        self.lbl_preview.config(text="Seleccione un archivo\npara ver su contenido.", font=("Segoe UI", 11), fg="#444444")

    # ==========================================
    # CARGA (MULTIHILO EN PARALELO)
    # ==========================================
    def cargar_csv(self):
        archivo = filedialog.askopenfilename(title="Seleccionar CSV", filetypes=[("Archivos CSV", "*.csv")])
        if not archivo: return
        try:
            self.csv_actual = archivo
            self.df = pd.read_csv(archivo, sep=';', dtype=str).fillna("")
            if 'Accion_Usuario' not in self.df.columns:
                self.df['Accion_Usuario'] = "" 
            self.iniciar_poblado()
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al leer CSV:\n{e}")

    def iniciar_poblado(self):
        self.tree.delete(*self.tree.get_children())
        self.bloquear_interfaz(True)
        self.mostrar_pantalla_carga("⚙️ Distribuyendo procesos\nen paralelo...")
        
        threading.Thread(target=self._orquestador_multihilo_preparar, daemon=True).start()

    def _orquestador_multihilo_preparar(self):
        self.datos_a_insertar = []
        df_dict = self.df.to_dict('index') 
        
        tags_dict = {}
        if 'Hash MD5' in self.df.columns:
            color_par = True
            for _, indices_grupo in self.df.groupby('Hash MD5', sort=False).groups.items():
                tag = 'grupo_par' if color_par else 'grupo_impar'
                color_par = not color_par
                for idx in indices_grupo:
                    tags_dict[idx] = tag
        else:
            for idx in df_dict.keys(): tags_dict[idx] = 'grupo_impar'

        # Guardamos en memoria el color original para usarlo más adelante sin parpadeos
        self.tags_originales = tags_dict 

        def procesar_fragmento(indices):
            resultados = []
            for idx in indices:
                fila = df_dict[idx]
                ruta = str(fila.get('Ruta Local / Link Web', ''))
                if not ruta.startswith('http'):
                    accion = fila.get('Accion_Usuario', '')
                    tags = ('eliminar',) if accion == 'ELIMINAR' else (tags_dict[idx],)
                    valores = (accion, fila.get('Nombre del Archivo', ''), fila.get('Tamaño (Bytes)', ''), fila.get('Fecha de Modificación', ''), ruta)
                    resultados.append((idx, valores, tags))
            return resultados

        indices_totales = list(df_dict.keys())
        tamano_chunk = max(1, len(indices_totales) // 4)
        fragmentos = [indices_totales[i:i + tamano_chunk] for i in range(0, len(indices_totales), tamano_chunk)]

        with ThreadPoolExecutor(max_workers=4) as executor:
            futuros = [executor.submit(procesar_fragmento, frag) for frag in fragmentos]
            for futuro in as_completed(futuros):
                self.datos_a_insertar.extend(futuro.result())

        self.total_insertar = len(self.datos_a_insertar)
        self.insertados = 0
        self.root.after(0, self._insertar_lote) 

    def _insertar_lote(self):
        lote_tamano = 1000 
        lote = self.datos_a_insertar[self.insertados : self.insertados + lote_tamano]

        for iid, values, tags in lote:
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)

        self.insertados += len(lote)
        
        porcentaje = (self.insertados / self.total_insertar) * 100 if self.total_insertar > 0 else 100
        self.progreso_var.set(porcentaje)

        if self.insertados < self.total_insertar:
            self.lbl_preview.config(text=f"📊 Renderizando interfaz...\n\n{self.insertados} / {self.total_insertar}")
            self.lbl_estado.config(text=f"Renderizando: {self.insertados}/{self.total_insertar}")
            self.root.after(5, self._insertar_lote) 
        else:
            self.ocultar_pantalla_carga()
            self.lbl_estado.config(text=f"✅ Total cargados: {self.total_insertar}")
            self.bloquear_interfaz(False)

    # ==========================================
    # AUTO-MARCAR MASIVO (ACTUALIZACIÓN SUAVE)
    # ==========================================
    def auto_marcar_duplicados(self):
        if self.df is None or 'Hash MD5' not in self.df.columns: return
        if not messagebox.askyesno("Auto-Depurar", "¿Marcar automáticamente todos los clones?\n\nSe salvará el MÁS RECIENTE de cada grupo."): return

        self.bloquear_interfaz(True)
        self.mostrar_pantalla_carga("🪄 Auto-Marcando...\n(Análisis Paralelo)")
        
        threading.Thread(target=self._hilo_auto_marcar, daemon=True).start()

    def _hilo_auto_marcar(self):
        if 'Fecha de Modificación' in self.df.columns:
            self.df['Fecha_Orden'] = pd.to_datetime(self.df['Fecha de Modificación'], dayfirst=True, errors='coerce')
        
        grupos = [group for _, group in self.df.groupby('Hash MD5') if len(group) > 1]
        indices_a_marcar = []

        def analizar_grupo(group):
            if 'Fecha_Orden' in group.columns:
                group = group.sort_values(by='Fecha_Orden', ascending=False)
            return group.index.tolist()[1:]

        with ThreadPoolExecutor() as executor:
            futuros = [executor.submit(analizar_grupo, g) for g in grupos]
            for futuro in as_completed(futuros):
                indices_a_marcar.extend(futuro.result())

        if 'Fecha_Orden' in self.df.columns: self.df.drop(columns=['Fecha_Orden'], inplace=True)
        
        for idx in indices_a_marcar:
            self.df.at[idx, 'Accion_Usuario'] = 'ELIMINAR'
            
        # Volvemos al hilo principal para hacer la actualización visual rápida
        self.root.after(0, lambda: self._actualizacion_visual_masiva(indices_a_marcar))

    def _actualizacion_visual_masiva(self, indices):
        """ Actualiza la tabla al instante sin tener que borrarla ni recargarla """
        for idx in indices:
            if self.tree.exists(idx):
                valores = self.tree.item(idx, 'values')
                self.tree.item(idx, values=('ELIMINAR', valores[1], valores[2], valores[3], valores[4]), tags=('eliminar',))
        
        self.ocultar_pantalla_carga()
        self.bloquear_interfaz(False)
        messagebox.showinfo("Listo", f"Se han marcado {len(indices)} clones desechables.")

    # ==========================================
    # MARCADO MANUAL SIN PESTAÑEO (IN-PLACE UPDATE)
    # ==========================================
    def procesar_seleccion_manual(self, accion):
        seleccion = self.tree.selection()
        if not seleccion: return
        
        # Iteramos sobre los seleccionados y los actualizamos directamente en la tabla
        for item in seleccion:
            idx = int(item)
            
            # 1. Actualizar memoria Pandas
            self.df.at[idx, 'Accion_Usuario'] = accion
            
            # 2. Actualizar texto en la tabla (Columna "Estado")
            valores_actuales = self.tree.item(item, 'values')
            nuevos_valores = (accion, valores_actuales[1], valores_actuales[2], valores_actuales[3], valores_actuales[4])
            
            # 3. Asignar el color correcto (Rojo si elimina, su Gris/Blanco original si desmarca)
            tag_original = self.tags_originales.get(idx, 'grupo_impar')
            nuevo_tag = ('eliminar',) if accion == 'ELIMINAR' else (tag_original,)
            
            # 4. Aplicar el cambio instantáneo
            self.tree.item(item, values=nuevos_valores, tags=nuevo_tag)

    # ==========================================
    # ELIMINAR FÍSICAMENTE (CON PAUSA Y BARRA)
    # ==========================================
    def eliminar_fisicamente(self):
        archivos_a_borrar = self.df[self.df['Accion_Usuario'] == 'ELIMINAR']
        if archivos_a_borrar.empty: return
            
        if messagebox.askyesno("CUIDADO CRÍTICO", f"¿Eliminar {len(archivos_a_borrar)} archivos FÍSICAMENTE de los discos?\n\nEsta acción NO pasa por la papelera de reciclaje."):
            self.bloquear_interfaz(True)
            self.pausado = False
            self.btn_pausa.config(state="normal", text="⏸️ Pausar", bootstyle="warning")
            self.mostrar_pantalla_carga("🗑️ Iniciando borrado...")
            
            self.lista_a_procesar = archivos_a_borrar.index.tolist()
            self.total_procesar = len(self.lista_a_procesar)
            self.procesados_actual = 0
            self.borrados_fisicos = 0
            self._procesar_lote_borrado()

    def alternar_pausa(self):
        if not self.pausado:
            self.pausado = True
            self.btn_pausa.config(text="▶️ Reanudar", bootstyle="success")
            self.lbl_preview.config(text=f"⏸️ BORRADO PAUSADO\n\n{self.procesados_actual} / {self.total_procesar}", fg="#f39c12")
        else:
            self.pausado = False
            self.btn_pausa.config(text="⏸️ Pausar", bootstyle="warning")
            self._procesar_lote_borrado() 

    def _procesar_lote_borrado(self):
        if self.pausado: return

        lote_tamano = 30 
        lote = self.lista_a_procesar[self.procesados_actual : self.procesados_actual + lote_tamano]
        
        for idx in lote:
            ruta = str(self.df.at[idx, 'Ruta Local / Link Web'])
            if os.path.exists(ruta):
                try:
                    os.remove(ruta)
                    self.borrados_fisicos += 1
                except: pass
            
            self.df.drop(idx, inplace=True)
            if self.tree.exists(idx):
                self.tree.delete(idx)
            
        self.procesados_actual += len(lote)
        
        porcentaje = (self.procesados_actual / self.total_procesar) * 100
        self.progreso_var.set(porcentaje)
        
        if self.procesados_actual < self.total_procesar:
            self.lbl_preview.config(text=f"🔥 Borrando Físicamente...\n\n{self.procesados_actual} / {self.total_procesar}", fg="#e74c3c")
            self.root.after(10, self._procesar_lote_borrado)
        else:
            self.btn_pausa.config(state="disabled")
            self.guardar_csv(mostrar_mensaje=False) 
            self.ocultar_pantalla_carga()
            messagebox.showinfo("Limpieza Exitosa", f"Se han destruido {self.borrados_fisicos} archivos del almacenamiento.\n\nEl archivo CSV se ha actualizado automáticamente.")
            self.bloquear_interfaz(False)

    # ==========================================
    # UTILIDADES Y VISTA PREVIA
    # ==========================================
    def guardar_csv(self, mostrar_mensaje=True):
        if self.df is not None and self.csv_actual:
            try:
                self.df.to_csv(self.csv_actual, sep=';', index=False, encoding='utf-8-sig')
                if mostrar_mensaje:
                    messagebox.showinfo("Éxito", "Los cambios han sido respaldados en el CSV.")
                    self.lbl_estado.config(text="CSV Sincronizado.")
            except Exception as e:
                messagebox.showerror("Error", f"Error de escritura:\n{e}")

    def al_seleccionar(self, event):
        seleccion = self.tree.selection()
        if not seleccion: return
        index = seleccion[-1] 
        ruta = self.tree.item(index, 'values')[4] 
        self.mostrar_vista_previa(ruta)

    def mostrar_vista_previa(self, ruta):
        if not os.path.exists(ruta):
            self.lbl_preview.config(image='', text=f"⚠️ ARCHIVO FANTASMA\n\nEl sistema reporta que este archivo no existe en la ruta.\n\n{ruta}", fg="#e74c3c")
            return
            
        ext = os.path.splitext(ruta)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            try:
                img = Image.open(ruta)
                img.thumbnail((450, 450)) 
                self.foto = ImageTk.PhotoImage(img) 
                self.lbl_preview.config(image=self.foto, text="")
            except:
                self.lbl_preview.config(image='', text="Formato de imagen corrupto o no soportado.", fg="#e74c3c")
        elif ext in ['.txt', '.csv', '.py', '.json', '.xml']:
            try:
                with open(ruta, 'r', encoding='utf-8', errors='ignore') as f:
                    texto = f.read(1500) 
                self.lbl_preview.config(image='', text=texto, justify="left", anchor="nw", fg="#2c3e50")
            except:
                self.lbl_preview.config(image='', text="Error de decodificación de texto.", fg="#e74c3c")
        else:
            self.lbl_preview.config(image='', text=f"📄 {os.path.basename(ruta)}\n\nFormato '{ext}' bloqueado por seguridad.\nHaz doble clic en la lista lateral para abrirlo.", justify="center", fg="#7f8c8d")

    def abrir_archivo_nativo(self, event):
        seleccion = self.tree.selection()
        if seleccion:
            ruta = self.tree.item(seleccion[0], 'values')[4]
            if os.path.exists(ruta):
                try: os.startfile(ruta)
                except Exception as e: messagebox.showerror("Error de Sistema", str(e))
            else:
                messagebox.showwarning("Atención", "El archivo fue movido o borrado del disco.")

if __name__ == "__main__":
    app_theme = tb.Window(themename="flatly")
    app = VisorDepurador(app_theme)
    app_theme.mainloop()