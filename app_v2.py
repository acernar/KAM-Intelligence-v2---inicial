import streamlit as st
import pandas as pd
import json
import re
import time
import os
import shutil
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import numpy as np
from io import BytesIO
import base64

# ==================== BACKEND: GOOGLE SHEETS O JSON LOCAL ====================
# Detecta automáticamente el entorno:
# - En Streamlit Cloud: usa Google Sheets (datos persistentes en la nube)
# - En local (tu Mac): usa archivos JSON (comportamiento anterior sin cambios)
#
# Para activar Sheets en local también, agrega en Streamlit Secrets o en
# .streamlit/secrets.toml:
#   [gsheets]
#   credentials = { ... contenido del JSON de credenciales ... }
#   spreadsheet_id = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"

GSHEETS_SPREADSHEET_ID = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"

@st.cache_resource(ttl=300)
def _get_gsheets_client():
    """Inicializa y retorna el cliente de Google Sheets. Retorna None si no hay credenciales."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        SCOPES = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]

        creds_dict = None

        # Opción 1: Streamlit Secrets (Streamlit Cloud)
        if hasattr(st, 'secrets') and 'gsheets' in st.secrets:
            creds_dict = dict(st.secrets['gsheets']['credentials'])

        # Opción 2: Archivo local (desarrollo)
        elif os.path.exists('gsheets_credentials.json'):
            with open('gsheets_credentials.json', 'r') as f:
                creds_dict = json.load(f)

        if creds_dict:
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            client = gspread.authorize(creds)
            sh = client.open_by_key(GSHEETS_SPREADSHEET_ID)
            return sh
    except Exception:
        pass
    return None

def _gsheets_activo():
    """Retorna True si Google Sheets está disponible."""
    return _get_gsheets_client() is not None

def _leer_hoja(nombre_hoja):
    """Lee una hoja de Google Sheets y retorna lista de dicts."""
    try:
        sh = _get_gsheets_client()
        if sh is None:
            return None
        ws = sh.worksheet(nombre_hoja)
        registros = ws.get_all_records()
        return registros
    except Exception as e:
        st.sidebar.warning(f"⚠️ Error leyendo hoja '{nombre_hoja}': {e}")
        return None

def _escribir_hoja_completa(nombre_hoja, datos_dict):
    """Escribe un dict completo en una hoja de Google Sheets.
    Formato: cada registro es una fila. Los campos complejos (listas/dicts) se serializan como JSON string."""
    try:
        sh = _get_gsheets_client()
        if sh is None:
            return False
        ws = sh.worksheet(nombre_hoja)
        ws.clear()

        if not datos_dict:
            return True

        # Obtener todas las columnas posibles
        todas_columnas = set()
        for v in datos_dict.values():
            if isinstance(v, dict):
                todas_columnas.update(v.keys())
        columnas = sorted(list(todas_columnas))

        # Escribir cabecera
        filas = [columnas]

        # Escribir filas
        for key, registro in datos_dict.items():
            if isinstance(registro, dict):
                fila = []
                for col in columnas:
                    val = registro.get(col, '')
                    # Serializar listas y dicts como JSON string
                    if isinstance(val, (list, dict)):
                        val = json.dumps(val, ensure_ascii=False)
                    elif val is None:
                        val = ''
                    fila.append(str(val))
                filas.append(fila)

        ws.update(filas, value_input_option='RAW')
        return True
    except Exception as e:
        st.warning(f"⚠️ Error al escribir en Google Sheets: {e}")
        return False

def _leer_dict_desde_hoja(nombre_hoja, clave_primaria='id'):
    """Lee una hoja y retorna dict indexado por clave_primaria.
    Los campos JSON string se deserializan automáticamente."""
    try:
        registros = _leer_hoja(nombre_hoja)
        if registros is None:
            return None
        resultado = {}
        for row in registros:
            if not row.get(clave_primaria):
                continue
            key = row[clave_primaria]
            registro = {}
            for k, v in row.items():
                # Intentar deserializar JSON strings (postores, listas, etc.)
                if isinstance(v, str) and v.startswith(('[', '{')):
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                registro[k] = v
            resultado[key] = registro
        return resultado
    except Exception:
        return None


# ==================== CONFIGURACIÓN ====================
st.set_page_config(
    page_title="QUBITS KAM Intelligence v2",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos CSS personalizados QUBITS
st.markdown("""
    <style>
    :root {
        --qubits-primary: #5B6FFF;
        --qubits-dark: #2C3E50;
        --qubits-light: #F8F9FF;
        --qubits-accent: #FF6B6B;
    }
    
    body {
        background-color: var(--qubits-light);
    }
    
    .header-main {
        color: var(--qubits-primary);
        font-size: 32px;
        font-weight: bold;
        margin-bottom: 10px;
        text-align: center;
    }
    
    .subheader-main {
        color: var(--qubits-dark);
        font-size: 14px;
        text-align: center;
        margin-bottom: 20px;
    }
    
    .metric-card {
        background: linear-gradient(135deg, var(--qubits-primary) 0%, #7B8FFF 100%);
        color: white;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 0 4px 15px rgba(91, 111, 255, 0.2);
    }
    
    .alert-critical {
        background: #FFE6E6;
        padding: 15px;
        border-left: 4px solid #DC3545;
        border-radius: 5px;
        margin: 10px 0;
    }
    
    .alert-warning {
        background: #FFF3CD;
        padding: 15px;
        border-left: 4px solid #FFC107;
        border-radius: 5px;
        margin: 10px 0;
    }
    
    .alert-info {
        background: #D1ECF1;
        padding: 15px;
        border-left: 4px solid #17A2B8;
        border-radius: 5px;
        margin: 10px 0;
    }
    
    .alert-success {
        background: #D4EDDA;
        padding: 15px;
        border-left: 4px solid #28A745;
        border-radius: 5px;
        margin: 10px 0;
    }
    
    .card-cliente {
        background: white;
        border: 1px solid var(--qubits-primary);
        padding: 15px;
        border-radius: 8px;
        margin: 10px 0;
    }
    
    .renovacion-urgente {
        background: #FFE6E6;
        border-left: 4px solid #DC3545;
    }
    
    .renovacion-proxima {
        background: #FFF3CD;
        border-left: 4px solid #FFC107;
    }
    
    .renovacion-normal {
        background: #D1ECF1;
        border-left: 4px solid #17A2B8;
    }
    </style>
""", unsafe_allow_html=True)

# ==================== CARGAR DATOS ====================
ARCHIVO_PROCESOS_BASE = 'procesos_73.json'  # fuente histórica original (solo lectura, ya no se usa directo)
ARCHIVO_PROCESOS_NUEVOS = 'procesos_nuevos.json'  # archivo viejo de solo lo agregado (ya no se usa directo)
ARCHIVO_PROCESOS = 'procesos.json'  # ÚNICA base de datos editable: histórico + agregados, todo junto

COLUMNAS_OBLIGATORIAS = ['id', 'entidad', 'region', 'subcategoria', 'estado',
                          'resultadoAdjudicacion', 'proveedor', 'montoAdjudicado']

COLUMNAS_TODAS = COLUMNAS_OBLIGATORIAS + ['localidad', 'descripcion', 'categoria', 'tipo',
                                            'publicado', 'inicioCotz', 'finCotz', 'plazo',
                                            'areaUsuaria', 'cubo', 'ruc', 'prioridad',
                                            'oportunidad', 'tdrDisponible', 'diasVencidos']

def migrar_procesos_a_archivo_unico():
    """Se ejecuta una sola vez: si procesos.json no existe pero sí existen los archivos
    antiguos (procesos_73.json + procesos_nuevos.json), los fusiona en procesos.json.
    A partir de ahí, procesos.json es la única fuente y los archivos viejos no se vuelven a tocar."""
    import os
    if os.path.exists(ARCHIVO_PROCESOS):
        return
    
    data_unificada = {}
    
    if os.path.exists(ARCHIVO_PROCESOS_BASE):
        try:
            with open(ARCHIVO_PROCESOS_BASE, 'r', encoding='utf-8') as f:
                base = json.load(f)
            for p in base.get('procesos', []):
                if 'id' in p:
                    data_unificada[p['id']] = p
        except (json.JSONDecodeError, KeyError):
            pass
    
    if os.path.exists(ARCHIVO_PROCESOS_NUEVOS):
        try:
            with open(ARCHIVO_PROCESOS_NUEVOS, 'r', encoding='utf-8') as f:
                nuevos = json.load(f)
            for pid, p in nuevos.items():
                data_unificada[pid] = p
        except json.JSONDecodeError:
            pass
    
    if data_unificada:
        with open(ARCHIVO_PROCESOS, 'w', encoding='utf-8') as f:
            json.dump(data_unificada, f, ensure_ascii=False, indent=2)

def cargar_procesos_raw():
    """Carga procesos desde Google Sheets (nube) o JSON local (Mac)."""
    if _gsheets_activo():
        data = _leer_dict_desde_hoja('procesos', clave_primaria='id')
        if data:  # Solo usar Sheets si retorna datos reales (no dict vacío)
            return data
    # Fallback a JSON local
    migrar_procesos_a_archivo_unico()
    try:
        with open(ARCHIVO_PROCESOS, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def guardar_procesos_raw(data):
    """Guarda procesos en Google Sheets (nube) o JSON local (Mac)."""
    if _gsheets_activo():
        _escribir_hoja_completa('procesos', data)
    _crear_backup(ARCHIVO_PROCESOS)
    with open(ARCHIVO_PROCESOS, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def agregar_proceso_nuevo(proceso_dict):
    """Agrega o actualiza un proceso en la base de datos única, indexado por id"""
    data = cargar_procesos_raw()
    proceso_dict['_agregado_el'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    data[proceso_dict['id']] = proceso_dict
    guardar_procesos_raw(data)

def eliminar_proceso_nuevo(proceso_id):
    data = cargar_procesos_raw()
    if proceso_id in data:
        del data[proceso_id]
        guardar_procesos_raw(data)

def validar_proceso(proceso_dict):
    """Valida que un proceso tenga las columnas obligatorias. Devuelve lista de errores."""
    errores = []
    for col in COLUMNAS_OBLIGATORIAS:
        if col not in proceso_dict or proceso_dict[col] in (None, ''):
            errores.append(f"Falta el campo obligatorio: '{col}'")
    if 'montoAdjudicado' in proceso_dict:
        try:
            float(proceso_dict['montoAdjudicado'])
        except (ValueError, TypeError):
            errores.append("'montoAdjudicado' debe ser un número")
    return errores

SUBCATEGORIAS_CLAVE = [
    ('GOOGLE WORKSPACE', 'Colaboración'),
    ('OFFICE 365', 'Correo/Colaboración'),
    ('CORREO', 'Correo'),
    ('RESPALDO', 'Backup'),
    ('BACKUP', 'Backup'),
    ('VIDEOCONFERENCIA', 'Videoconferencia'),
    ('ALMACENAMIENTO', 'Almacenamiento'),
    ('AZURE', 'Nube/Azure'),
    ('AWS', 'Nube/AWS'),
    ('VOIP', 'VoIP'),
    ('TELEFON', 'Telefonía IP'),
    ('SEGURIDAD', 'Seguridad Web'),
    ('CAPACITAC', 'Capacitación'),
    ('SOFTWARE', 'Software'),
    ('NUBE', 'Nube'),
]

ESTADO_MAP_SEACE = {
    'culminado': 'Culminado',
    'en evaluación': 'En Evaluación',
    'en evaluacion': 'En Evaluación',
    'vigente': 'Vigente',
    'desierto': 'DESIERTO'
}

def parsear_texto_seace(texto):
    """Parsea el texto crudo copiado de una ficha de proceso SEACE y devuelve (proceso_dict, avisos)."""
    lines = [l.strip() for l in texto.strip().split('\n') if l.strip()]
    
    if not lines:
        return None, ["El texto está vacío."]
    
    avisos = []
    proceso = {}
    
    proceso['id'] = lines[0].strip()
    if not re.match(r'^[A-Z]{1,3}-[\w/-]+$', proceso['id']):
        avisos.append(f"La primera línea no parece un ID de proceso típico: '{lines[0]}'. Verifícalo.")
    
    if len(lines) > 1:
        proceso['descripcion'] = lines[1].strip()
    
    estado_encontrado = None
    idx_estado = None
    for i, l in enumerate(lines):
        l_clean = l.replace('●', '').strip().lower()
        if l_clean in ESTADO_MAP_SEACE:
            estado_encontrado = ESTADO_MAP_SEACE[l_clean]
            idx_estado = i
            break
    proceso['estado'] = estado_encontrado or ''
    if not estado_encontrado:
        avisos.append("No se encontró línea de estado (Culminado / En Evaluación / Vigente / Desierto). Complétalo manualmente.")
    
    entidad_idx = None
    for i, l in enumerate(lines):
        if '·' in l and i > (idx_estado or 0):
            entidad_idx = i
            break
    if entidad_idx is not None:
        partes = lines[entidad_idx].split('·')
        proceso['entidad'] = partes[0].strip()
        if len(partes) > 1:
            ubic_partes = [u.strip() for u in partes[1].strip().split('/')]
            proceso['region'] = ubic_partes[0] if ubic_partes else ''
            proceso['localidad'] = ubic_partes[-1] if ubic_partes else ''
    else:
        avisos.append("No se encontró la línea de entidad/ubicación (debe contener '·'). Complétala manualmente.")
    
    for l in lines:
        if 'INVITACI' in l.upper() or 'ADJUDICACI' in l.upper() or 'CONCURSO' in l.upper():
            match = re.search(r'(INVITACI[ÓO]N\s+\w+|ADJUDICACI[ÓO]N\s+\w+)', l.upper())
            if match:
                proceso['tipo'] = match.group(1).title()
            break
    
    etiquetas_fecha = {
        'PUBLICADO': 'publicado',
        'INICIO COTIZACIÓN': 'inicioCotz', 'INICIO COTIZACION': 'inicioCotz',
        'FIN COTIZACIÓN': 'finCotz', 'FIN COTIZACION': 'finCotz',
    }
    for i, l in enumerate(lines):
        l_upper = l.strip().upper()
        if l_upper in etiquetas_fecha and i + 1 < len(lines):
            try:
                fecha = datetime.strptime(lines[i + 1].strip(), '%d/%m/%Y')
                proceso[etiquetas_fecha[l_upper]] = fecha.strftime('%Y-%m-%d')
            except ValueError:
                avisos.append(f"No se pudo leer la fecha junto a '{l_upper}'. Complétala manualmente.")
        if l_upper in ('ÁREA USUARIA', 'AREA USUARIA') and i + 1 < len(lines):
            proceso['areaUsuaria'] = lines[i + 1].strip()
    
    for l in lines:
        match = re.search(r'CUBS?O:\s*([A-ZÁÉÍÓÚÑ\s]+?)(?=[A-Z][a-z]|$)', l)
        if match:
            proceso['cubo'] = match.group(1).strip()
            break
    
    desc_upper = proceso.get('descripcion', '').upper()
    proceso['subcategoria'] = ''
    for clave, valor in SUBCATEGORIAS_CLAVE:
        if clave in desc_upper:
            proceso['subcategoria'] = valor
            break
    if not proceso['subcategoria']:
        avisos.append("No se pudo inferir 'subcategoria' del texto — selecciónala manualmente abajo.")
    
    proceso['resultadoAdjudicacion'] = 'Adjudicado' if proceso['estado'] == 'Culminado' else (proceso['estado'] or 'Sin definir')
    proceso['proveedor'] = 'SIN DEFINIR'
    proceso['montoAdjudicado'] = 0
    proceso['categoria'] = 'TI'
    proceso['tdrDisponible'] = True
    
    return proceso, avisos

def cargar_procesos():
    """Carga la base de datos única de procesos (histórico + agregados, todo editable, sin caché
    para que cualquier edición se refleje de inmediato)"""
    data = cargar_procesos_raw()
    
    if not data:
        return pd.DataFrame()
    
    df = pd.DataFrame(list(data.values()))
    
    # Asegurar que existan todas las columnas usadas por el resto de la app, aunque sea vacías
    for col in COLUMNAS_TODAS:
        if col not in df.columns:
            df[col] = None
    
    # montoAdjudicado siempre numérico
    df['montoAdjudicado'] = pd.to_numeric(df['montoAdjudicado'], errors='coerce').fillna(0)
    
    return df

ARCHIVO_RENOVACIONES = 'renovaciones_editadas.json'

def cargar_renovaciones_editadas():
    """Carga las correcciones manuales de fechas de renovación hechas por el usuario"""
    try:
        with open(ARCHIVO_RENOVACIONES, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def guardar_renovacion_editada(proceso_id, fecha_inicio_real, plazo_meses, nota=""):
    """Guarda la fecha real de inicio de contrato ingresada por el usuario"""
    data = cargar_renovaciones_editadas()
    fecha_inicio = pd.Timestamp(fecha_inicio_real)
    fecha_renovacion = fecha_inicio + pd.DateOffset(months=int(plazo_meses))
    data[proceso_id] = {
        'fecha_inicio_real': fecha_inicio_real.strftime('%Y-%m-%d') if hasattr(fecha_inicio_real, 'strftime') else str(fecha_inicio_real),
        'plazo_meses': int(plazo_meses),
        'fecha_renovacion_confirmada': fecha_renovacion.strftime('%Y-%m-%d'),
        'nota': nota,
        'editado_el': datetime.now().strftime('%Y-%m-%d %H:%M')
    }
    with open(ARCHIVO_RENOVACIONES, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return fecha_renovacion

ARCHIVO_CONTACTOS = 'crm_contactos.json'
ARCHIVO_HISTORIAL = 'crm_historial.json'

def cargar_contactos():
    if _gsheets_activo():
        try:
            sh = _get_gsheets_client()
            ws = sh.worksheet('contactos')
            registros = ws.get_all_records()
            data = {}
            for r in registros:
                cliente = r.get('cliente', '')
                if not cliente:
                    continue
                if cliente not in data:
                    data[cliente] = []
                data[cliente].append({
                    'nombre': r.get('nombre', ''), 'cargo': r.get('cargo', ''),
                    'email': r.get('email', ''), 'telefono': r.get('telefono', ''),
                    'agregado_el': r.get('agregado_el', '')
                })
            return data
        except Exception:
            pass
    try:
        with open(ARCHIVO_CONTACTOS, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def guardar_contacto(cliente, nombre, cargo, email, telefono):
    data = cargar_contactos()
    if cliente not in data:
        data[cliente] = []
    data[cliente].append({
        'nombre': nombre, 'cargo': cargo, 'email': email, 'telefono': telefono,
        'agregado_el': datetime.now().strftime('%Y-%m-%d %H:%M')
    })
    if _gsheets_activo():
        try:
            sh = _get_gsheets_client()
            ws = sh.worksheet('contactos')
            # Aplanar a filas para Sheets
            filas = [['cliente', 'nombre', 'cargo', 'email', 'telefono', 'agregado_el']]
            for c, contactos in data.items():
                for ct in contactos:
                    filas.append([c, ct.get('nombre',''), ct.get('cargo',''), ct.get('email',''), ct.get('telefono',''), ct.get('agregado_el','')])
            ws.clear()
            ws.update(filas, value_input_option='RAW')
        except Exception:
            pass
    with open(ARCHIVO_CONTACTOS, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def eliminar_contacto(cliente, indice):
    data = cargar_contactos()
    if cliente in data and 0 <= indice < len(data[cliente]):
        data[cliente].pop(indice)
        if _gsheets_activo():
            try:
                sh = _get_gsheets_client()
                ws = sh.worksheet('contactos')
                filas = [['cliente', 'nombre', 'cargo', 'email', 'telefono', 'agregado_el']]
                for c, contactos in data.items():
                    for ct in contactos:
                        filas.append([c, ct.get('nombre',''), ct.get('cargo',''), ct.get('email',''), ct.get('telefono',''), ct.get('agregado_el','')])
                ws.clear()
                ws.update(filas, value_input_option='RAW')
            except Exception:
                pass
        with open(ARCHIVO_CONTACTOS, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def cargar_historial():
    if _gsheets_activo():
        try:
            sh = _get_gsheets_client()
            ws = sh.worksheet('historial')
            registros = ws.get_all_records()
            data = {}
            for r in registros:
                cliente = r.get('cliente', '')
                if not cliente:
                    continue
                if cliente not in data:
                    data[cliente] = []
                data[cliente].append({
                    'fecha': r.get('fecha', ''), 'nota': r.get('nota', ''),
                    'proxima_accion': r.get('proxima_accion', '')
                })
            return data
        except Exception:
            pass
    try:
        with open(ARCHIVO_HISTORIAL, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def guardar_historial(cliente, nota, proxima_accion):
    data = cargar_historial()
    if cliente not in data:
        data[cliente] = []
    data[cliente].append({
        'fecha': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'nota': nota, 'proxima_accion': proxima_accion
    })
    if _gsheets_activo():
        try:
            sh = _get_gsheets_client()
            ws = sh.worksheet('historial')
            filas = [['cliente', 'fecha', 'nota', 'proxima_accion']]
            for c, entradas in data.items():
                for h in entradas:
                    filas.append([c, h.get('fecha',''), h.get('nota',''), h.get('proxima_accion','')])
            ws.clear()
            ws.update(filas, value_input_option='RAW')
        except Exception:
            pass
    with open(ARCHIVO_HISTORIAL, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ==================== FUNCIONES PARA LICITACIONES ====================
ARCHIVO_LICITACIONES = 'licitaciones.json'
# Nombres de los archivos antiguos (arquitectura previa de dos archivos), usados solo para migración
ARCHIVO_LICITACIONES_BASE_LEGACY = 'licitaciones_base.json'
ARCHIVO_LICITACIONES_NUEVAS_LEGACY = 'licitaciones_nuevas.json'

COLUMNAS_LICITACION_OBLIGATORIAS = ['id', 'titulo', 'entidad', 'region', 'tipo_licitacion', 'estado']
COLUMNAS_LICITACION_TODAS = COLUMNAS_LICITACION_OBLIGATORIAS + ['monto_base', 'ganador', 'monto_adjudicado', 'empresas_participantes',
                                                                   'publicado', 'adjudicacion', 'inicio_contrato',
                                                                   'fin_contrato', 'duracion_dias', 'moneda', 'cui',
                                                                   'tdr_disponible', 'ruc_ganador', 'descripcion',
                                                                   'direccion_entidad', 'tipo_contratacion', 'telefono_entidad',
                                                                   'codigo_tipo_procedimiento']

# Estados posibles. Los primeros 2 implican ganador conocido; el resto, sin ganador aún o sin ganador posible.
ESTADOS_LICITACION = [
    "Contrato Firmado",
    "Adjudicado — pendiente de firma",
    "Publicado",
    "Abierto para participar",
    "En Evaluación",
    "Desierto — ninguna propuesta cumplió los requisitos",
    "Cancelado",
]
# Estados que implican que ya existe un ganador conocido. El resto (publicado, en evaluación,
# desierto, cancelado) puede legítimamente no tener ganador ni monto adjudicado todavía.
ESTADOS_CON_GANADOR = ["Contrato Firmado", "Adjudicado — pendiente de firma"]
# Categorías oficiales de objeto de contratación bajo Ley 30225 / D.S. 344-2018-EF.
# "Consultoría" sin más se entiende como Consultoría en General (sub-variante de Servicios
# en la práctica de SEACE); "Consultoría de Obras" es una categoría propia y distinta.
TIPOS_CONTRATACION = ["Bien", "Servicio", "Obra", "Consultoría", "Consultoría de Obras", ""]
# monto_base es informativo y queda fuera de los obligatorios: SEACE no siempre lo expone
# en la vista de procesos recién publicados, así que su ausencia nunca debe bloquear el guardado.

def _migrar_licitaciones_legacy_si_corresponde():
    """Migración única: si existen los archivos viejos (_base + _nuevas) y todavía no existe
    licitaciones.json, los fusiona en un solo diccionario y crea el archivo unificado.
    No borra los archivos viejos (quedan como respaldo), solo deja de usarlos."""
    import os
    if os.path.exists(ARCHIVO_LICITACIONES):
        return  # ya migrado, no hacer nada
    
    unificado = {}
    
    if os.path.exists(ARCHIVO_LICITACIONES_BASE_LEGACY):
        try:
            with open(ARCHIVO_LICITACIONES_BASE_LEGACY, 'r', encoding='utf-8') as f:
                data_base = json.load(f)
            for p in data_base.get('licitaciones', []):
                if p.get('id'):
                    unificado[p['id']] = p
        except (json.JSONDecodeError, KeyError):
            pass
    
    if os.path.exists(ARCHIVO_LICITACIONES_NUEVAS_LEGACY):
        try:
            with open(ARCHIVO_LICITACIONES_NUEVAS_LEGACY, 'r', encoding='utf-8') as f:
                data_nuevas = json.load(f)
            for pid, p in data_nuevas.items():
                unificado[pid] = p  # las nuevas/editadas tienen prioridad sobre el histórico
        except json.JSONDecodeError:
            pass
    
    if unificado:
        with open(ARCHIVO_LICITACIONES, 'w', encoding='utf-8') as f:
            json.dump(unificado, f, ensure_ascii=False, indent=2)

def cargar_licitaciones_raw():
    """Carga licitaciones desde Google Sheets (nube) o JSON local (Mac)."""
    if _gsheets_activo():
        data = _leer_dict_desde_hoja('licitaciones', clave_primaria='id')
        if data:  # Solo usar Sheets si retorna datos reales (no dict vacío)
            return data
    # Fallback a JSON local
    _migrar_licitaciones_legacy_si_corresponde()
    try:
        with open(ARCHIVO_LICITACIONES, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _crear_backup(archivo):
    """Crea una copia de seguridad del archivo JSON antes de sobreescribirlo.
    Guarda hasta 20 backups rotantes en la carpeta 'backups/' junto al archivo."""
    try:
        if not os.path.exists(archivo):
            return
        # Usar ruta absoluta relativa al archivo JSON, no al directorio de trabajo
        dir_base     = os.path.dirname(os.path.abspath(archivo))
        carpeta_backup = os.path.join(dir_base, 'backups')
        os.makedirs(carpeta_backup, exist_ok=True)
        nombre_base  = os.path.splitext(os.path.basename(archivo))[0]
        timestamp    = datetime.now().strftime('%Y%m%d_%H%M%S')
        destino      = os.path.join(carpeta_backup, f"{nombre_base}_{timestamp}.json")
        shutil.copy2(archivo, destino)
        # Rotar: conservar solo los 20 más recientes por archivo
        backups_existentes = sorted([
            f for f in os.listdir(carpeta_backup)
            if f.startswith(nombre_base + '_') and f.endswith('.json')
        ])
        for viejo in backups_existentes[:-20]:
            try:
                os.remove(os.path.join(carpeta_backup, viejo))
            except Exception:
                pass
    except Exception:
        pass  # El backup nunca debe interrumpir el guardado principal

def guardar_licitaciones_raw(data):
    """Guarda licitaciones en Google Sheets (nube) o JSON local (Mac)."""
    if _gsheets_activo():
        _escribir_hoja_completa('licitaciones', data)
    # Siempre guardar también en JSON local como respaldo
    _crear_backup(ARCHIVO_LICITACIONES)
    with open(ARCHIVO_LICITACIONES, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def agregar_licitacion_nueva(licitacion_dict):
    """Agrega o actualiza una licitación (funciona igual para una nueva o una edición de una existente)."""
    data = cargar_licitaciones_raw()
    licitacion_dict['_agregada_el'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    data[licitacion_dict['id']] = licitacion_dict
    guardar_licitaciones_raw(data)

def eliminar_licitacion_nueva(licitacion_id):
    data = cargar_licitaciones_raw()
    if licitacion_id in data:
        del data[licitacion_id]
        guardar_licitaciones_raw(data)

def validar_licitacion(licitacion_dict):
    """Valida que una licitación tenga los campos obligatorios. El ganador solo es
    obligatorio si el estado implica que ya se adjudicó (Contrato Firmado, etc.)"""
    errores = []
    for col in COLUMNAS_LICITACION_OBLIGATORIAS:
        if col not in licitacion_dict or licitacion_dict[col] in (None, ''):
            errores.append(f"Falta el campo obligatorio: '{col}'")
    if licitacion_dict.get('estado') in ESTADOS_CON_GANADOR and not licitacion_dict.get('ganador'):
        errores.append(f"El estado '{licitacion_dict.get('estado')}' implica un ganador, pero el campo 'ganador' está vacío.")
    if 'monto_base' in licitacion_dict:
        try:
            float(str(licitacion_dict['monto_base']).replace(',', ''))
        except (ValueError, TypeError):
            errores.append("'monto_base' debe ser un número")
    if 'monto_adjudicado' in licitacion_dict and licitacion_dict['monto_adjudicado']:
        try:
            float(str(licitacion_dict['monto_adjudicado']).replace(',', ''))
        except (ValueError, TypeError):
            errores.append("'monto_adjudicado' debe ser un número")
    return errores

def parsear_texto_licitacion(texto):
    """Parsea el texto crudo copiado de una ficha de licitación SEACE"""
    lines = [l.strip() for l in texto.strip().split('\n') if l.strip()]
    
    if not lines:
        return None, ["El texto está vacío."]
    
    avisos = []
    licitacion = {}
    
    # Línea 1: Título
    licitacion['titulo'] = lines[0].strip() if lines else ''
    
    # Buscar monto base (S/ XXX.XXX,XX)
    for l in lines[:10]:
        match = re.search(r'S/\s*([\d,\.]+)', l)
        if match:
            licitacion['monto_base'] = match.group(1)
            break
    
    if 'monto_base' not in licitacion:
        avisos.append("No se encontró monto base — complétalo manualmente")
    
    # Buscar estado
    estado_encontrado = False
    for l in lines:
        l_lower = l.lower()
        if 'contrato firmado' in l_lower or 'proceso cerrado' in l_lower:
            licitacion['estado'] = 'Contrato Firmado'
            estado_encontrado = True
            break
        elif 'desierto' in l_lower or 'ninguna propuesta' in l_lower or 'sin propuestas válidas' in l_lower:
            licitacion['estado'] = 'Desierto — ninguna propuesta cumplió los requisitos'
            estado_encontrado = True
            break
        elif 'en evaluación' in l_lower or 'en evaluacion' in l_lower:
            licitacion['estado'] = 'En Evaluación'
            estado_encontrado = True
            break
        elif 'abierto para participar' in l_lower or 'recibiendo propuestas' in l_lower:
            licitacion['estado'] = 'Abierto para participar'
            estado_encontrado = True
            break
        elif 'cancelado' in l_lower:
            licitacion['estado'] = 'Cancelado'
            estado_encontrado = True
            break
    
    if not estado_encontrado:
        licitacion['estado'] = ''
        avisos.append("No se identificó el estado del proceso — selecciónalo manualmente abajo.")
    
    # Buscar entidad y región — se ancla en el emoji 🏛 (más confiable que buscar '·',
    # que en algunos textos aparece antes en fechas de documentos tipo "2025-07-21 · DOCX")
    for i, l in enumerate(lines):
        l_limpia = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', l)
        if l_limpia.strip().startswith('🏛') and len(l_limpia.strip()) > 3:
            contenido = l_limpia.replace('🏛', '', 1).strip()
            
            if '·' in contenido:
                # Formato A: "ENTIDAD · Región📅fecha👥 N empresas..."
                partes = contenido.split('·')
                licitacion['entidad'] = partes[0].strip()
                resto = partes[1].strip() if len(partes) > 1 else ''
                match_region = re.match(r'^([A-Za-zÁÉÍÓÚÑáéíóúñ\s]+?)(?:📅|👥|$)', resto)
                licitacion['region'] = match_region.group(1).strip() if match_region else resto
            else:
                # Formato B: solo el nombre de la entidad en su línea, sin región explícita aquí.
                # Puede traer emojis pegados si la entidad y la fecha quedaron en la misma línea.
                match_solo_entidad = re.match(r'^([^📅👥]+)', contenido)
                licitacion['entidad'] = (match_solo_entidad.group(1).strip() if match_solo_entidad else contenido)
                licitacion['region'] = ''
            break
    
    if 'entidad' not in licitacion:
        avisos.append("No se encontró entidad — complétala manualmente")
    
    # Si no se encontró región junto a la entidad, intentar inferirla de la dirección
    # (se completa más abajo, después de extraer direccion_entidad)
    
    # Buscar número de empresas participantes
    for l in lines:
        match = re.search(r'(\d+)\s+empresas?\s+particip', l, re.IGNORECASE)
        if match:
            licitacion['empresas_participantes'] = int(match.group(1))
            break
    
    # Buscar tipo de contratación (Bienes/Servicios/Obras/Consultoría de Obras) + tipo de licitación.
    # En SEACE suelen venir pegados sin espacio: "BienLicitación Pública Abreviada"
    # Las 4 categorías oficiales de objeto de contratación bajo Ley 30225 son: Bienes, Servicios,
    # Obras y Consultoría de Obras (la "Consultoría" sin más se entiende como Consultoría en
    # General, una sub-variante de Servicios en la práctica de SEACE).
    # Cubre los 7 procedimientos de selección de la Ley 30225 / D.S. 344-2018-EF:
    # Licitación Pública, Concurso Público, Adjudicación Simplificada, Subasta Inversa
    # Electrónica, Selección de Consultores Individuales, Comparación de Precios y
    # Contratación Directa (este último para casos excepcionales: emergencia, proveedor
    # único, desabastecimiento, etc.)
    PATRON_TIPOS_LICITACION = (
        r'(Licitación Pública(?:\s+Abierta|\s+Abreviada)?|'
        r'Concurso Público(?:\s+(?:de Servicios|Abreviado))?|'
        r'Adjudicación Simplificada|'
        r'Contratación Directa|'
        r'Subasta Inversa Electrónica|'
        r'Comparación de Precios|'
        r'Selección de Consultores Individuales)'
    )
    match_tipo = re.search(
        r'(Bien|Servicio|Obra|Consultoría(?:\s+de\s+Obras)?)\s*' + PATRON_TIPOS_LICITACION,
        texto
    )
    if match_tipo:
        licitacion['tipo_contratacion'] = match_tipo.group(1).strip()
        licitacion['tipo_licitacion'] = match_tipo.group(2).strip()
    else:
        # Fallback: buscar solo el tipo de licitación sin el prefijo de objeto de contratación
        match_solo_tipo = re.search(PATRON_TIPOS_LICITACION, texto)
        if match_solo_tipo:
            licitacion['tipo_licitacion'] = match_solo_tipo.group(1).strip()
    
    if 'tipo_licitacion' not in licitacion:
        avisos.append("No se identificó tipo de licitación")
    if 'tipo_contratacion' not in licitacion:
        licitacion['tipo_contratacion'] = ''
        avisos.append("No se identificó el tipo de contratación (Bienes/Servicios/Obras/Consultoría de Obras) — selecciónalo manualmente abajo.")
    
    # Buscar ID. Dos formatos posibles:
    #   A) Sin espacio (el más común): "LP-ABR-1-2025-CENEPRED-1" -> se toma todo el bloque tal cual.
    #      También cubre "DIRECTA-DIRECTA-1-2026-SENCICO-1" (Contratación Directa).
    #   B) Con espacio tras el prefijo: "CP SER-SM-1-2026-SERNANP-1" -> lo que va ANTES del espacio
    #      (CP) es el código del tipo de procedimiento, no parte del ID. Lo que va DESPUÉS del
    #      espacio (SER-SM-1-2026-SERNANP-1) es el ID real de la licitación.
    # Se exige al menos un dígito en el resto para evitar falsos positivos con palabras como
    # "AREAS" (que empieza con el prefijo válido 'AS' seguido de espacio, sin ser un ID real).
    # Prefijos: LP/LPA=Licitación Pública, CP=Concurso Público, AS=Adjudicación Simplificada,
    # DIRECTA=Contratación Directa, SIE=Subasta Inversa Electrónica, CDP=Comparación de Precios,
    # SCI=Selección de Consultores Individuales.
    for l in lines:
        match = re.search(r'(LP|LPA|CP|AS|DIRECTA|SIE|CDP|SCI)([\s\-])([A-Z0-9\-/]*\d[A-Z0-9\-/]*)', l)
        if match:
            prefijo, separador, resto = match.group(1), match.group(2), match.group(3)
            if separador == ' ':
                licitacion['codigo_tipo_procedimiento'] = prefijo
                licitacion['id'] = resto.rstrip('.,;: ')
            else:
                licitacion['id'] = (prefijo + separador + resto).rstrip('.,;: ')
            break
    
    if 'id' not in licitacion:
        avisos.append("No se encontró ID de licitación")
    
    # Buscar CUI
    for l in lines:
        match = re.search(r'CUI\s+N°?\s+(\d+)', l, re.IGNORECASE)
        if match:
            licitacion['cui'] = match.group(1)
            break
    
    # Buscar dirección de la entidad contratante (suele venir al final del texto)
    for i, l in enumerate(lines):
        if l.strip().lower() in ('dirección', 'direccion') and i + 1 < len(lines):
            licitacion['direccion_entidad'] = lines[i + 1].strip()
            break
    
    if 'direccion_entidad' not in licitacion:
        avisos.append("No se encontró dirección de la entidad — puedes completarla manualmente si la tienes.")
    
    # Buscar teléfono de la entidad contratante (línea "Teléfono" seguida del número)
    for i, l in enumerate(lines):
        if l.strip().lower() in ('teléfono', 'telefono') and i + 1 < len(lines):
            telefono_raw = lines[i + 1].strip()
            # SEACE a veces pone "0" cuando no hay teléfono registrado
            if telefono_raw and telefono_raw != '0':
                licitacion['telefono_entidad'] = telefono_raw
            break
    
    # Fallback de región: si no se encontró junto a la entidad, tomar la última palabra
    # de la dirección (suele ser la provincia/región, ej: "...CHALLHUAHUACHO COTABAMBAS")
    if licitacion.get('region', '') == '' and licitacion.get('direccion_entidad'):
        partes_direccion = licitacion['direccion_entidad'].split()
        if partes_direccion:
            licitacion['region'] = partes_direccion[-1].strip()
            avisos.append(f"Región inferida de la dirección ('{licitacion['region']}') — verifica que sea correcta.")
    
    if licitacion.get('region', '') == '':
        avisos.append("No se pudo determinar la región — complétala manualmente.")
    
    # Buscar fechas
    for i, l in enumerate(lines):
        l_upper = l.upper()
        if l_upper == 'PUBLICADO' and i + 1 < len(lines):
            try:
                fecha = datetime.strptime(lines[i + 1].strip(), '%d/%m/%Y')
                licitacion['publicado'] = fecha.strftime('%Y-%m-%d')
            except:
                pass
        
        if 'ADJUDICACI' in l_upper and i + 1 < len(lines):
            try:
                fecha = datetime.strptime(lines[i + 1].strip(), '%d/%m/%Y')
                licitacion['adjudicacion'] = fecha.strftime('%Y-%m-%d')
            except:
                pass
        
        if l_upper == 'INICIO CONTRATO' and i + 1 < len(lines):
            try:
                fecha = datetime.strptime(lines[i + 1].strip(), '%d/%m/%Y')
                licitacion['inicio_contrato'] = fecha.strftime('%Y-%m-%d')
            except:
                pass
        
        if l_upper == 'FIN CONTRATO' and i + 1 < len(lines):
            try:
                fecha = datetime.strptime(lines[i + 1].strip(), '%d/%m/%Y')
                licitacion['fin_contrato'] = fecha.strftime('%Y-%m-%d')
            except:
                pass
    
    # Buscar duración en días. El texto de SEACE puede presentar "Duración" y "N días"
    # en la misma línea O en líneas separadas, así que busco en el texto completo con DOTALL
    match_duracion = re.search(r'Duración[^\d]*?(\d+)\s+días?', texto, re.IGNORECASE | re.DOTALL)
    if match_duracion:
        licitacion['duracion_dias'] = int(match_duracion.group(1))
    
    # Buscar ganador. Estrategia principal: anclar en el texto literal "Ganador adjudicado"
    # y tomar la línea siguiente — funciona sin importar el sufijo legal (S.A., S.A.C., S.R.L.,
    # E.I.R.L., Cooperativa, etc.) o si es una persona natural.
    for i, l in enumerate(lines):
        if l.strip().lower() == 'ganador adjudicado' and i + 1 < len(lines):
            licitacion['ganador'] = lines[i + 1].strip().replace('★', '').replace('Ganador', '').strip()
            break
    
    # Respaldo: si no se encontró por el ancla literal, buscar sufijos legales conocidos
    # cerca de la palabra "Ganador" o el símbolo ★ (cubre variantes de formato del texto)
    if 'ganador' not in licitacion:
        sufijos_legales = ['S.R.L.', 'S.A.C.', 'S.A.A.', 'S.A.', 'E.I.R.L.', 'SAC', 'SRL', 'SA', 'EIRL']
        for i, l in enumerate(lines):
            if ('Ganador' in l or '★' in l) and i + 1 < len(lines):
                for j in range(i, min(i + 5, len(lines))):
                    if any(lines[j].strip().endswith(suf) for suf in sufijos_legales):
                        licitacion['ganador'] = lines[j].strip().replace('★', '').strip()
                        break
                if 'ganador' in licitacion:
                    break
    
    if 'ganador' not in licitacion:
        licitacion['ganador'] = ''
        avisos.append("No se identificó el ganador automáticamente — verifícalo manualmente abajo.")
    
    # Buscar monto adjudicado
    for i, l in enumerate(lines):
        if 'Monto adjudicado' in l and i + 1 < len(lines):
            match = re.search(r'S/\s*([\d,\.]+)|Soles\s+([\d,\.]+)', lines[i + 1])
            if match:
                monto_str = match.group(1) or match.group(2)
                # Normalizar: SEACE a veces usa "257,441,00" (coma como miles Y como decimal)
                # Si hay 2+ comas, la última es el decimal; las anteriores son separador de miles
                if monto_str.count(',') >= 2:
                    partes_monto = monto_str.split(',')
                    monto_str = ''.join(partes_monto[:-1]) + '.' + partes_monto[-1]
                else:
                    monto_str = monto_str.replace(',', '')
                licitacion['monto_adjudicado'] = monto_str
                break
    
    # Moneda
    licitacion['moneda'] = 'PEN' if 'PEN' in texto else ('USD' if 'USD' in texto else 'PEN')
    
    # ---- Extraer lista de POSTORES (todas las empresas participantes con sus stats) ----
    # Soporta DOS formatos de copiado:
    #   A) Markdown: [NOMBRE EMPRESA](url) ★ Ganador
    #   B) Texto plano: NOMBRE EMPRESA (sin corchetes, según copie el navegador)
    # Solo buscar postores DESPUÉS del marcador "Postores (N)" para no confundir
    # los enlaces "[Ver documento](url)" de la sección de Documentos con postores reales.
    # IMPORTANTE: en licitaciones recién publicadas sin ofertas aún, SEACE puede mostrar
    # una sección "🔮 Empresas que probablemente postulen" (PREDICCIÓN especulativa, no
    # postores reales) con el mismo formato estructural. Si no existe el marcador real
    # "Postores (N)", NO se extrae nada de esa sección de predicción.
    idx_inicio_postores = None
    idx_fin_postores = len(lines)
    for i, l in enumerate(lines):
        if re.match(r'Postores\s*\(\d+\)', l):
            idx_inicio_postores = i + 1
        elif idx_inicio_postores is not None and (
            '🔮' in l or 'probablemente postulen' in l.lower() or l.strip().lower() == 'predicción'
        ):
            idx_fin_postores = i
            break
    
    hay_seccion_prediccion = any(
        '🔮' in l or 'probablemente postulen' in l.lower() for l in lines
    )
    
    postores = []
    if idx_inicio_postores is not None:
        i = idx_inicio_postores
        while i < idx_fin_postores:
            l = lines[i]
            
            match_md = re.match(r'\[([^\]]+)\]\(([^)]+)\)(.*)$', l)
            es_postor_md = match_md and match_md.group(1).strip().lower() not in ('ver documento', 'descargar')
            
            # Texto plano: esta línea es nombre de empresa si la SIGUIENTE línea es "N licitaciones"
            es_postor_plano = (not es_postor_md) and (i + 1 < idx_fin_postores) and \
                               re.match(r'\d+\s+licitaciones', lines[i + 1]) and \
                               l.lower() not in ('ver documento', 'descargar') and \
                               not re.match(r'^\d+\s+(ganadas|licitaciones)|^Tasa:|^Monto adjudicado:', l)
            
            if es_postor_md:
                nombre = match_md.group(1).strip()
                url = match_md.group(2).strip()
                resto_linea = match_md.group(3).strip()
                es_ganador = '★' in resto_linea or 'Ganador' in resto_linea
                postor = {'empresa': nombre, 'es_ganador': es_ganador, 'url_perfil': url,
                          'licitaciones_previas': 0, 'ganadas_previas': 0, 'tasa_exito': 0.0, 'monto_historico': 0}
                j = i + 1
            elif es_postor_plano:
                nombre = l.replace('★', '').replace('Ganador', '').strip()
                es_ganador = '★' in l or 'Ganador' in l
                postor = {'empresa': nombre, 'es_ganador': es_ganador, 'url_perfil': '',
                          'licitaciones_previas': 0, 'ganadas_previas': 0, 'tasa_exito': 0.0, 'monto_historico': 0}
                j = i + 1
            else:
                i += 1
                continue
            
            while j < idx_fin_postores and j < i + 5:
                lj = lines[j]
                m_lic = re.match(r'(\d+)\s+licitaciones', lj)
                m_gan = re.match(r'(\d+)\s+ganadas', lj)
                m_tasa = re.match(r'Tasa:\s*([\d,\.]+)%', lj)
                m_monto = re.match(r'Monto adjudicado:\s*S/\s*([\d\s,\.]+)', lj)
                
                if m_lic:
                    postor['licitaciones_previas'] = int(m_lic.group(1))
                elif m_gan:
                    postor['ganadas_previas'] = int(m_gan.group(1))
                elif m_tasa:
                    postor['tasa_exito'] = float(m_tasa.group(1).replace(',', '.'))
                elif m_monto:
                    try:
                        postor['monto_historico'] = float(m_monto.group(1).replace(' ', '').replace(',', '.'))
                    except ValueError:
                        pass
                else:
                    break
                j += 1
            
            postores.append(postor)
            i = j
    
    licitacion['postores'] = postores
    if postores:
        licitacion['num_postores_detectados'] = len(postores)
    elif hay_seccion_prediccion and idx_inicio_postores is None:
        avisos.append("⚠️ Esta licitación aún no tiene postores reales (proceso recién publicado). SEACE muestra una sección de empresas que 'probablemente postularán' (predicción especulativa), que NO se guardó como postores reales para no contaminar el ranking de competencia. Si quieres agregarlas manualmente cuando se confirmen, edítalas después en la base de datos.")
    elif 'empresas_participantes' in licitacion and licitacion['empresas_participantes'] > 0:
        avisos.append(f"⚠️ El texto indica {licitacion['empresas_participantes']} empresas participantes pero no se encontró la lista de postores. El texto pegado probablemente se cortó antes de llegar a la sección 'Postores'. Vuelve a copiar usando Ctrl+A (Cmd+A) sobre toda la página para no perder esa sección.")
    else:
        avisos.append("No se detectó lista de postores — el ranking de competencia no incluirá esta licitación.")
    
    # Campos por defecto
    licitacion['tdr_disponible'] = True
    licitacion['descripcion'] = licitacion.get('descripcion', '')
    
    return licitacion, avisos

def cargar_licitaciones():
    """Carga el archivo único de licitaciones como DataFrame sin cachear, así siempre ve los últimos cambios guardados."""
    data = cargar_licitaciones_raw()
    
    if data:
        df_combinado = pd.DataFrame(list(data.values()))
    else:
        df_combinado = pd.DataFrame()
    
    # Asegurar que existan todas las columnas
    for col in COLUMNAS_LICITACION_TODAS:
        if col not in df_combinado.columns:
            df_combinado[col] = None
    
    # Convertir montos a numéricos
    for col in ['monto_base', 'monto_adjudicado']:
        if col in df_combinado.columns:
            df_combinado[col] = pd.to_numeric(df_combinado[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    return df_combinado

# Cargar datos de AMBOS tipos de procesos
df_menores = cargar_procesos()
df_licitaciones = cargar_licitaciones()
renovaciones_editadas = cargar_renovaciones_editadas()

if df_menores.empty and df_licitaciones.empty:
    st.error("❌ No hay datos disponibles. Agrega procesos para comenzar.")
    st.stop()

# ==================== HEADER ====================
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    st.markdown('<div class="header-main">📊 QUBITS KAM INTELLIGENCE v2</div>', unsafe_allow_html=True)
    st.markdown('<div class="subheader-main">Plataforma de Inteligencia Comercial - Menores a 8 UIT</div>', unsafe_allow_html=True)

st.markdown("---")

# ==================== SIDEBAR ====================
with st.sidebar:
    st.title("🔧 NAVEGACIÓN")
    
    # Diagnóstico de Google Sheets
    if _gsheets_activo():
        st.caption("☁️ Google Sheets activo")
    else:
        st.caption("💾 Modo local (JSON)")
    
    # Selector de tipo de proceso
    tipo_proceso = st.radio("Tipo de Proceso:", ["📊 Menores (≤8 UIT)", "📑 Licitaciones (>8 UIT)"], 
                            label_visibility="collapsed")
    
    # Asignar datos según tipo de proceso
    if "Menores" in tipo_proceso:
        df = df_menores
        modulos_disponibles = [
            "📊 Dashboard",
            "🗄️ Base de Datos",
            "📅 Calendario de Renovaciones",
            "👥 CRM y Seguimiento",
            "💼 Análisis de Competencia",
            "📝 Generador de Documentos",
            "💼 Generador de Propuestas",
            "📊 Exportar Datos",
            "🎯 Oportunidades",
            "⚙️ Configuración"
        ]
    else:  # Licitaciones
        df = df_licitaciones
        modulos_disponibles = [
            "📊 Dashboard de Licitaciones",
            "🗄️ Base de Datos de Licitaciones",
            "👥 CRM y Seguimiento",
            "📅 Calendario de Renovaciones",
            "🎯 Oportunidades",
            "💼 Competencia en Licitaciones",
            "📝 Generador de Documentos",
            "💼 Generador de Propuestas",
            "🤖 Inteligencia Artificial",
            "📊 Exportar Licitaciones",
            "⚙️ Configuración"
        ]
    
    st.markdown("---")
    
    seccion = st.radio("Selecciona módulo:", modulos_disponibles,
                        label_visibility="collapsed")
    
    st.markdown("---")
    
    # Widgets laterales - KPIs Rápidos
    st.markdown("### 📈 KPIs Rápidos")
    
    if "Menores" in tipo_proceso:
        # KPIs para Menores (≤8 UIT)
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Procesos", len(df), "SEACE")
        with col2:
            st.metric("Mercado", f"S/ {df['montoAdjudicado'].sum()/1000000:.2f}M", "adjudicado")
        
        col1, col2 = st.columns(2)
        with col1:
            if len(df) > 0:
                tasa = (len(df[df['resultadoAdjudicacion'] == 'Adjudicado']) / len(df) * 100)
                st.metric("Éxito", f"{tasa:.1f}%", "adjudicados")
        with col2:
            st.metric("Cuota Q3", "S/ 500K", "meta")
    else:
        # KPIs para Licitaciones (>8 UIT)
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Licitaciones", len(df), "procesos")
        with col2:
            st.metric("Mercado Base", f"S/ {df['monto_base'].sum()/1000000:.2f}M", "en disputa")
        
        col1, col2 = st.columns(2)
        with col1:
            adjudicadas = len(df[df['estado'] == 'Contrato Firmado'])
            st.metric("Adjudicadas", adjudicadas, "cerradas")
        with col2:
            if len(df) > 0:
                st.metric("Entidades", df['entidad'].nunique(), "activas")

# ==================== SECCIÓN 1: DASHBOARD ====================
if "Menores" in tipo_proceso and seccion == "📊 Dashboard":
    st.markdown("### 📊 Dashboard Ejecutivo")
    
    col_refresh, col_spacer = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 Refrescar", key="refresh_dashboard"):
            st.rerun()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="metric-card"><h3>{len(df)}</h3>Procesos Totales</div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-card"><h3>S/ {df["montoAdjudicado"].sum()/1000000:.2f}M</h3>Mercado Total</div>', unsafe_allow_html=True)
    with col3:
        tasa = len(df[df['resultadoAdjudicacion'] == 'Adjudicado']) / len(df) * 100
        st.markdown(f'<div class="metric-card"><h3>{tasa:.1f}%</h3>Tasa de Éxito</div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="metric-card"><h3>{df["entidad"].nunique()}</h3>Clientes Únicos</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Estado
        estado_counts = df['estado'].value_counts()
        fig = px.pie(values=estado_counts.values, names=estado_counts.index,
                     title="📊 Distribución por Estado",
                     color_discrete_sequence=['#5B6FFF', '#FF6B6B', '#FFC107'])
        st.plotly_chart(fig, use_container_width=True)
        
        estado_click = st.selectbox("🔍 Ver procesos de un estado:", ["—"] + estado_counts.index.tolist(), key="estado_drill")
        if estado_click != "—":
            st.dataframe(
                df[df['estado'] == estado_click][['id', 'entidad', 'region', 'subcategoria', 'resultadoAdjudicacion']],
                use_container_width=True
            )
    
    with col2:
        # Resultado
        resultado_counts = df['resultadoAdjudicacion'].value_counts()
        fig = px.bar(x=resultado_counts.index, y=resultado_counts.values,
                     title="✅ Resultados de Adjudicación",
                     labels={'x': 'Resultado', 'y': 'Procesos'},
                     color_discrete_sequence=['#5B6FFF', '#FF6B6B', '#FFC107'])
        st.plotly_chart(fig, use_container_width=True)
        
        resultado_click = st.selectbox("🔍 Ver procesos de un resultado:", ["—"] + resultado_counts.index.tolist(), key="resultado_drill")
        if resultado_click != "—":
            st.dataframe(
                df[df['resultadoAdjudicacion'] == resultado_click][['id', 'entidad', 'region', 'subcategoria', 'montoAdjudicado']],
                use_container_width=True
            )
    
    col1, col2 = st.columns(2)
    
    with col1:
        regiones = df['region'].value_counts().head(10)
        fig = px.bar(x=regiones.values, y=regiones.index, orientation='h',
                      title="🌍 Top Regiones",
                      labels={'x': 'Procesos', 'y': 'Región'},
                      color_discrete_sequence=['#5B6FFF'])
        fig.update_layout(yaxis={'categoryorder': 'total ascending'})
        st.plotly_chart(fig, use_container_width=True)
        
        region_click = st.selectbox("🔍 Ver procesos de una región:", ["—"] + regiones.index.tolist(), key="region_drill")
        if region_click != "—":
            st.dataframe(
                df[df['region'] == region_click][['id', 'entidad', 'subcategoria', 'estado', 'resultadoAdjudicacion', 'montoAdjudicado']],
                use_container_width=True
            )
    
    with col2:
        subcats = df['subcategoria'].value_counts().head(10)
        fig = px.bar(x=subcats.values, y=subcats.index, orientation='h',
                      title="🎯 Top Servicios Demandados",
                      labels={'x': 'Procesos', 'y': 'Servicio'},
                      color_discrete_sequence=['#7B8FFF'])
        fig.update_layout(yaxis={'categoryorder': 'total ascending'})
        st.plotly_chart(fig, use_container_width=True)
        
        servicio_click = st.selectbox("🔍 Ver procesos de un servicio:", ["—"] + subcats.index.tolist(), key="servicio_drill")
        if servicio_click != "—":
            st.dataframe(
                df[df['subcategoria'] == servicio_click][['id', 'entidad', 'region', 'estado', 'resultadoAdjudicacion', 'montoAdjudicado']],
                use_container_width=True
            )

# ==================== SECCIÓN 1B: BASE DE DATOS ====================
elif "Menores" in tipo_proceso and seccion == "🗄️ Base de Datos":
    st.markdown("### 🗄️ Base de Datos — Agregar Procesos Nuevos")
    st.caption("Los 73 procesos históricos no se modifican. Lo que agregues aquí se guarda en `procesos_nuevos.json` y se combina automáticamente con el resto de la plataforma.")
    
    nuevos_raw = cargar_procesos_raw()
    
    tab1, tab2, tab3, tab4 = st.tabs(["📄 Pegar texto SEACE", "➕ Formulario guiado", "📋 Pegar JSON", "🗂️ Todos los Procesos"])
    
    # ---------- TAB 1: PEGAR TEXTO SEACE ----------
    with tab1:
        st.markdown("#### Pega el texto tal cual lo copias de la ficha del proceso en SEACE")
        st.caption("Copia todo el bloque de texto de la página del proceso (desde el ID hasta el área usuaria) y pégalo aquí. La plataforma extrae automáticamente los datos.")
        
        texto_seace = st.text_area(
            "Texto copiado de SEACE:",
            height=280,
            placeholder="CM-322-2026-DEC/2026\nSERVICIO DE CORREO ELECTRONICO EN LA NUBE...\n● Culminado\nUNIVERSIDAD ... · HUANCAVELICA / TAYACAJA / AHUAYCHA\n...",
            key="texto_seace_input"
        )
        
        if st.button("🔍 Extraer datos del texto"):
            if not texto_seace.strip():
                st.warning("Pega el texto del proceso antes de continuar.")
            else:
                proceso_parseado, avisos = parsear_texto_seace(texto_seace)
                if proceso_parseado is None:
                    st.error("No se pudo procesar el texto.")
                else:
                    # Validar que el ID no exista YA
                    id_extraido = proceso_parseado.get('id', '').strip()
                    ids_existentes = df['id'].tolist()
                    
                    if id_extraido in ids_existentes:
                        st.error(f"❌ **El ID '{id_extraido}' ya está registrado.** Ve a la pestaña '🗂️ Todos los Procesos' para editarlo o eliminarlo.")
                    else:
                        st.session_state['proceso_parseado'] = proceso_parseado
                        st.session_state['avisos_parseo'] = avisos
                        st.success(f"✅ Datos extraídos correctamente. ID: {id_extraido}")
        
        if 'proceso_parseado' in st.session_state:
            proceso_p = st.session_state['proceso_parseado']
            avisos_p = st.session_state.get('avisos_parseo', [])
            
            if avisos_p:
                st.warning("⚠️ Revisa estos puntos antes de guardar:\n\n" + "\n".join(f"- {a}" for a in avisos_p))
            
            st.markdown("##### Datos extraídos — corrige lo que necesites antes de guardar")
            
            col1, col2 = st.columns(2)
            with col1:
                p_id = st.text_input("ID*:", value=proceso_p.get('id', ''), key="p_id")
                p_entidad = st.text_input("Entidad*:", value=proceso_p.get('entidad', ''), key="p_entidad")
                p_region = st.text_input("Región*:", value=proceso_p.get('region', ''), key="p_region")
                p_localidad = st.text_input("Localidad:", value=proceso_p.get('localidad', ''), key="p_localidad")
            with col2:
                subcats_conocidas = sorted(set(df['subcategoria'].dropna().unique().tolist() + [proceso_p.get('subcategoria', '')]))
                subcat_default = proceso_p.get('subcategoria', '') or subcats_conocidas[0]
                p_subcategoria = st.selectbox("Subcategoría / Servicio*:", subcats_conocidas,
                                              index=subcats_conocidas.index(subcat_default) if subcat_default in subcats_conocidas else 0,
                                              key="p_subcat")
                p_estado = st.selectbox("Estado*:", ["Culminado", "En Evaluación", "Vigente", "DESIERTO"],
                                        index=["Culminado", "En Evaluación", "Vigente", "DESIERTO"].index(proceso_p.get('estado')) if proceso_p.get('estado') in ["Culminado", "En Evaluación", "Vigente", "DESIERTO"] else 0,
                                        key="p_estado")
                p_resultado = st.selectbox("Resultado*:", ["Adjudicado", "DESIERTO", "En Evaluación", "Sin definir"],
                                           index=["Adjudicado", "DESIERTO", "En Evaluación", "Sin definir"].index(proceso_p.get('resultadoAdjudicacion')) if proceso_p.get('resultadoAdjudicacion') in ["Adjudicado", "DESIERTO", "En Evaluación", "Sin definir"] else 0,
                                           key="p_resultado")
                p_prioridad = st.selectbox("Prioridad:", ["CRÍTICA", "ALTA", "MEDIA", "BAJA"], key="p_prioridad")
            
            p_descripcion = st.text_area("Descripción:", value=proceso_p.get('descripcion', ''), key="p_desc")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                p_proveedor = st.text_input("Proveedor*:", value=proceso_p.get('proveedor', 'SIN DEFINIR'), key="p_proveedor")
            with col2:
                p_monto = st.number_input("Monto adjudicado* (S/):", min_value=0.0, step=100.0, value=float(proceso_p.get('montoAdjudicado', 0)), key="p_monto")
            with col3:
                p_ruc = st.text_input("RUC proveedor (opcional):", key="p_ruc")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                p_publicado = st.text_input("Publicado (YYYY-MM-DD):", value=proceso_p.get('publicado', ''), key="p_publicado")
            with col2:
                p_iniciocotz = st.text_input("Inicio cotización (YYYY-MM-DD):", value=proceso_p.get('inicioCotz', ''), key="p_iniciocotz")
            with col3:
                p_fincotz = st.text_input("Fin cotización (YYYY-MM-DD):", value=proceso_p.get('finCotz', ''), key="p_fincotz")
            
            p_areausuaria = st.text_input("Área usuaria:", value=proceso_p.get('areaUsuaria', ''), key="p_area")
            p_cubo = st.text_input("Cubo de contratación:", value=proceso_p.get('cubo', ''), key="p_cubo")
            
            if st.button("💾 Confirmar y guardar proceso", key="guardar_parseado"):
                proceso_final = {
                    'id': p_id.strip(), 'entidad': p_entidad.strip(), 'region': p_region.strip(),
                    'localidad': p_localidad.strip(), 'subcategoria': p_subcategoria,
                    'categoria': 'TI', 'estado': p_estado, 'resultadoAdjudicacion': p_resultado,
                    'prioridad': p_prioridad, 'descripcion': p_descripcion.strip(),
                    'proveedor': p_proveedor.strip(), 'montoAdjudicado': p_monto, 'ruc': p_ruc.strip(),
                    'publicado': p_publicado.strip(), 'inicioCotz': p_iniciocotz.strip(), 'finCotz': p_fincotz.strip(),
                    'areaUsuaria': p_areausuaria.strip(), 'cubo': p_cubo.strip(), 'tdrDisponible': True
                }
                
                errores = validar_proceso(proceso_final)
                ids_existentes = df['id'].tolist()
                nuevos_raw = cargar_procesos_raw()
                
                if proceso_final['id'] in nuevos_raw and proceso_final['id'] != proceso_p.get('id'):
                    errores.append(f"❌ El ID '{proceso_final['id']}' ya está registrado en otro proceso. No puedes usar IDs duplicados.")
                elif proceso_final['id'] not in nuevos_raw and proceso_final['id'] in ids_existentes:
                    errores.append(f"❌ El ID '{proceso_final['id']}' ya está registrado. Usa otro ID diferente.")
                
                if errores:
                    for e in errores:
                        st.error(e)
                else:
                    agregar_proceso_nuevo(proceso_final)
                    st.success(f"✅ Proceso {proceso_final['id']} guardado correctamente.")
                    del st.session_state['proceso_parseado']
                    st.session_state.pop('avisos_parseo', None)
                    time.sleep(0.3)
                    st.rerun()
    
    # ---------- TAB 2: FORMULARIO GUIADO ----------
    with tab2:
        st.markdown("#### Completa los datos del proceso SEACE")
        st.caption("Campos con * son obligatorios para que el proceso aparezca correctamente en toda la plataforma.")
        
        col1, col2 = st.columns(2)
        with col1:
            f_id = st.text_input("ID del proceso* (ej: CM-100-2026-XXX):", key="form_id")
            f_entidad = st.text_input("Entidad*:", key="form_entidad")
            f_region = st.text_input("Región*:", key="form_region")
            f_localidad = st.text_input("Localidad:", key="form_localidad")
        with col2:
            f_subcategoria = st.text_input("Subcategoría / Servicio* (ej: Backup, Correo):", key="form_subcat")
            f_estado = st.selectbox("Estado*:", ["Culminado", "En Evaluación", "Vigente"], key="form_estado")
            f_resultado = st.selectbox("Resultado*:", ["Adjudicado", "DESIERTO", "En Evaluación", "Sin definir"], key="form_resultado")
            f_prioridad = st.selectbox("Prioridad:", ["CRÍTICA", "ALTA", "MEDIA", "BAJA"], key="form_prioridad")
        
        f_descripcion = st.text_area("Descripción:", key="form_desc")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            f_proveedor = st.text_input("Proveedor* (SIN DEFINIR si aún no hay):", value="SIN DEFINIR", key="form_proveedor")
        with col2:
            f_monto = st.number_input("Monto adjudicado* (S/):", min_value=0.0, step=100.0, key="form_monto")
        with col3:
            f_ruc = st.text_input("RUC proveedor (opcional):", key="form_ruc")
        
        col1, col2 = st.columns(2)
        with col1:
            f_publicado = st.date_input("Fecha de publicación:", value=datetime.now().date(), key="form_publicado")
        with col2:
            f_fincotz = st.date_input("Fin de cotización:", value=datetime.now().date(), key="form_fincotz")
        
        f_areausuaria = st.text_input("Área usuaria:", key="form_area")
        f_oportunidad = st.text_area("Nota / oportunidad detectada:", key="form_oportunidad")
        
        if st.button("💾 Guardar proceso"):
            nuevo_proceso = {
                'id': f_id.strip(),
                'entidad': f_entidad.strip(),
                'region': f_region.strip(),
                'localidad': f_localidad.strip(),
                'subcategoria': f_subcategoria.strip(),
                'categoria': 'TI',
                'estado': f_estado,
                'resultadoAdjudicacion': f_resultado,
                'prioridad': f_prioridad,
                'descripcion': f_descripcion.strip(),
                'proveedor': f_proveedor.strip(),
                'montoAdjudicado': f_monto,
                'ruc': f_ruc.strip(),
                'publicado': f_publicado.strftime('%Y-%m-%d'),
                'finCotz': f_fincotz.strftime('%Y-%m-%d'),
                'areaUsuaria': f_areausuaria.strip(),
                'oportunidad': f_oportunidad.strip(),
                'tdrDisponible': False
            }
            
            errores = validar_proceso(nuevo_proceso)
            ids_existentes = df['id'].tolist()
            
            if nuevo_proceso['id'] in ids_existentes:
                errores.append(f"❌ El ID '{nuevo_proceso['id']}' ya está registrado. Usa otro ID diferente.")
            
            if errores:
                for e in errores:
                    st.error(e)
            else:
                agregar_proceso_nuevo(nuevo_proceso)
                st.success(f"✅ Proceso {nuevo_proceso['id']} guardado correctamente.")
                time.sleep(0.3)
                st.rerun()
    
    # ---------- TAB 3: PEGAR JSON ----------
    with tab3:
        st.markdown("#### Pega el JSON de uno o varios procesos")
        st.caption("Acepta un objeto único o una lista de objetos. Campos obligatorios: " + ", ".join(COLUMNAS_OBLIGATORIAS))
        
        ejemplo = '''{
  "id": "CM-200-2026-XXX",
  "entidad": "NOMBRE DE LA ENTIDAD",
  "region": "LIMA",
  "subcategoria": "Backup",
  "estado": "En Evaluación",
  "resultadoAdjudicacion": "En Evaluación",
  "proveedor": "SIN DEFINIR",
  "montoAdjudicado": 0
}'''
        
        json_input = st.text_area("JSON del proceso:", value="", height=250, placeholder=ejemplo)
        
        if st.button("🔍 Validar y guardar JSON"):
            if not json_input.strip():
                st.warning("Pega un JSON antes de continuar.")
            else:
                try:
                    parsed = json.loads(json_input)
                    procesos_a_validar = parsed if isinstance(parsed, list) else [parsed]
                    
                    todos_ok = True
                    ids_existentes = df['id'].tolist()
                    for p in procesos_a_validar:
                        errores = validar_proceso(p)
                        
                        if p.get('id') in ids_existentes:
                            errores.append(f"❌ El ID ya está registrado.")
                        
                        if errores:
                            todos_ok = False
                            st.error(f"Proceso '{p.get('id', '(sin id)')}': " + " | ".join(errores))
                    
                    if todos_ok:
                        for p in procesos_a_validar:
                            agregar_proceso_nuevo(p)
                        st.success(f"✅ {len(procesos_a_validar)} proceso(s) guardado(s) correctamente.")
                        time.sleep(0.3)
                        st.rerun()
                
                except json.JSONDecodeError as e:
                    st.error(f"JSON inválido: {e}")
    
    # ---------- TAB 4: TODOS LOS PROCESOS (editar/eliminar) ----------
    with tab4:
        st.markdown("#### Todos los Procesos — Editar o Eliminar")
        st.caption("Base de datos única: puedes editar o eliminar cualquier proceso registrado.")
        
        if not nuevos_raw:
            st.info("Aún no hay procesos registrados. Usa el formulario o pega un JSON en las otras pestañas.")
        else:
            st.metric("Total de procesos", len(nuevos_raw))
            
            opciones_proc = [f"{pid} — {p.get('entidad', 'Sin entidad')}" for pid, p in nuevos_raw.items()]
            proc_seleccionado_label = st.selectbox("Busca el proceso a editar:", ["—"] + opciones_proc, key="selector_proc_editar")
            
            if proc_seleccionado_label != "—":
                pid = proc_seleccionado_label.split(" — ")[0]
                proceso = nuevos_raw[pid]
                
                st.markdown("##### ✏️ Editar proceso")
                
                col1, col2 = st.columns(2)
                with col1:
                    e_id = st.text_input("ID:", value=proceso.get('id', pid), key=f"edit_id_{pid}")
                    e_entidad = st.text_input("Entidad:", value=proceso.get('entidad', ''), key=f"edit_entidad_{pid}")
                    e_region = st.text_input("Región:", value=proceso.get('region', ''), key=f"edit_region_{pid}")
                    e_subcat = st.text_input("Subcategoría:", value=proceso.get('subcategoria', ''), key=f"edit_subcat_{pid}")
                with col2:
                    e_estado = st.text_input("Estado:", value=proceso.get('estado', ''), key=f"edit_estado_{pid}")
                    e_resultado = st.text_input("Resultado:", value=proceso.get('resultadoAdjudicacion', ''), key=f"edit_resultado_{pid}")
                    e_monto = st.number_input("Monto:", value=float(proceso.get('montoAdjudicado', 0) or 0), key=f"edit_monto_{pid}")
                
                e_proveedor = st.text_input("Proveedor:", value=proceso.get('proveedor', ''), key=f"edit_proveedor_{pid}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("💾 Guardar cambios", key=f"save_{pid}"):
                        proceso_actualizado = dict(proceso)
                        proceso_actualizado.update({
                            'id': e_id.strip(), 'entidad': e_entidad, 'region': e_region, 'subcategoria': e_subcat,
                            'estado': e_estado, 'resultadoAdjudicacion': e_resultado,
                            'montoAdjudicado': e_monto, 'proveedor': e_proveedor
                        })
                        errores = validar_proceso(proceso_actualizado)
                        
                        if e_id.strip() != pid:
                            ids_otros = [i for i in nuevos_raw.keys() if i != pid]
                            if e_id.strip() in ids_otros:
                                errores.append(f"❌ El nuevo ID '{e_id.strip()}' ya existe en otro proceso. Elige un ID distinto.")
                        
                        if errores:
                            for e in errores:
                                st.error(e)
                        else:
                            if e_id.strip() != pid:
                                eliminar_proceso_nuevo(pid)
                            agregar_proceso_nuevo(proceso_actualizado)
                            st.success("✅ Actualizado")
                            time.sleep(0.3)
                            st.rerun()
                with col2:
                    if st.button("🗑️ Eliminar proceso", key=f"del_{pid}"):
                        eliminar_proceso_nuevo(pid)
                        time.sleep(0.3)
                        st.rerun()

# ==================== SECCIÓN 2: CALENDARIO DE RENOVACIONES ====================
elif "Menores" in tipo_proceso and seccion == "📅 Calendario de Renovaciones":
    st.markdown("### 📅 Calendario de Renovaciones")
    st.caption("⚠️ = fecha estimada automáticamente (12 meses por defecto) · ✅ = fecha confirmada por ti")
    
    adjudicados = df[df['resultadoAdjudicacion'] == 'Adjudicado'].copy()
    
    # Calcular próximas renovaciones: usa corrección manual si existe, si no, estima
    renovaciones = []
    for idx, row in adjudicados.iterrows():
        pid = row['id']
        
        if pid in renovaciones_editadas:
            # Fecha CONFIRMADA por el usuario
            info = renovaciones_editadas[pid]
            proxima_renov = pd.Timestamp(info['fecha_renovacion_confirmada'])
            confianza = 'CONFIRMADA'
            plazo_meses = info['plazo_meses']
            fecha_inicio = info['fecha_inicio_real']
        else:
            # Fecha ESTIMADA automáticamente
            fecha_adj = pd.Timestamp(row['finCotz']) + timedelta(days=7)
            proxima_renov = fecha_adj + pd.DateOffset(months=12)
            confianza = 'ESTIMADA'
            plazo_meses = 12
            fecha_inicio = fecha_adj.strftime('%Y-%m-%d')
        
        dias_para_renovar = (proxima_renov - pd.Timestamp.now()).days
        
        renovaciones.append({
            'id': pid,
            'entidad': row['entidad'],
            'servicio': row['subcategoria'],
            'fecha_inicio': fecha_inicio,
            'plazo_meses': plazo_meses,
            'proxima_renovacion': proxima_renov.strftime('%Y-%m-%d'),
            'dias_para_renovar': dias_para_renovar,
            'monto_estimado': row['montoAdjudicado'],
            'region': row['region'],
            'confianza': confianza,
            'estado_urgencia': 'URGENTE' if dias_para_renovar < 30 else ('PRÓXIMA' if dias_para_renovar < 90 else 'NORMAL')
        })
    
    renov_df = pd.DataFrame(renovaciones).sort_values('dias_para_renovar')
    
    # Resumen de confianza de datos
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("✅ Fechas confirmadas", len(renov_df[renov_df['confianza'] == 'CONFIRMADA']))
    with col2:
        st.metric("⚠️ Fechas estimadas", len(renov_df[renov_df['confianza'] == 'ESTIMADA']))
    with col3:
        pct = len(renov_df[renov_df['confianza'] == 'CONFIRMADA']) / len(renov_df) * 100 if len(renov_df) else 0
        st.metric("Cobertura confirmada", f"{pct:.0f}%")
    
    st.markdown("---")
    
    tab_calendario, tab_editar = st.tabs(["📅 Ver calendario", "✏️ Confirmar fecha real de un proceso"])
    
    with tab_calendario:
        # Filtros
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            urgencia_filter = st.multiselect("Urgencia:", renov_df['estado_urgencia'].unique(),
                                            default=renov_df['estado_urgencia'].unique())
        with col2:
            meses = st.slider("Próximos meses:", 1, 12, 6)
        with col3:
            region_filter = st.multiselect("Región:", renov_df['region'].unique(),
                                          default=renov_df['region'].unique()[:5])
        with col4:
            confianza_filter = st.multiselect("Confianza:", ['CONFIRMADA', 'ESTIMADA'],
                                              default=['CONFIRMADA', 'ESTIMADA'])
        
        # Filtrar
        renov_filtered = renov_df[
            (renov_df['estado_urgencia'].isin(urgencia_filter)) &
            (renov_df['dias_para_renovar'] <= meses * 30) &
            (renov_df['region'].isin(region_filter)) &
            (renov_df['confianza'].isin(confianza_filter))
        ]
        
        # Mostrar por urgencia
        for urgencia in ['URGENTE', 'PRÓXIMA', 'NORMAL']:
            urgentes = renov_filtered[renov_filtered['estado_urgencia'] == urgencia]
            if not urgentes.empty:
                etiqueta = {'URGENTE': '🔴 RENOVACIONES URGENTES (< 30 días)',
                           'PRÓXIMA': '🟡 RENOVACIONES PRÓXIMAS (30-90 días)',
                           'NORMAL': '🔵 RENOVACIONES EN SEGUIMIENTO (> 90 días)'}[urgencia]
                
                st.markdown(f"#### {etiqueta} - {len(urgentes)} procesos")
                
                mostrar = urgentes if urgencia != 'NORMAL' else urgentes.head(8)
                for idx, row in mostrar.iterrows():
                    icono_confianza = "✅" if row['confianza'] == 'CONFIRMADA' else "⚠️"
                    titulo = f"{icono_confianza} **{row['entidad']}** — {row['servicio']} — vence {row['proxima_renovacion']} ({row['dias_para_renovar']} días)"
                    with st.expander(titulo):
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Plazo contractual", f"{row['plazo_meses']} meses")
                        with col2:
                            st.metric("Monto", f"S/ {row['monto_estimado']:,.0f}")
                        with col3:
                            st.metric("Región", row['region'])
                        
                        st.markdown(f"**ID Proceso:** {row['id']}")
                        st.markdown(f"**Confianza de fecha:** {row['confianza']}")
                        
                        proceso_completo = df[df['id'] == row['id']]
                        if not proceso_completo.empty:
                            st.dataframe(
                                proceso_completo[['id', 'descripcion', 'cubo', 'proveedor', 'areaUsuaria']],
                                use_container_width=True
                            )
        
        if renov_filtered.empty:
            st.info("No hay renovaciones que coincidan con los filtros seleccionados.")
    
    with tab_editar:
        st.markdown("#### Confirmar la fecha real de inicio de contrato")
        st.caption("Revisa el TDR o la orden de servicio en SEACE y registra el dato real aquí. Esto reemplaza la estimación automática de 12 meses.")
        
        proceso_a_editar = st.selectbox(
            "Proceso (solo adjudicados):",
            adjudicados['id'].tolist(),
            format_func=lambda x: f"{x} — {adjudicados[adjudicados['id']==x]['entidad'].values[0][:40]}"
        )
        
        if proceso_a_editar:
            ya_editado = proceso_a_editar in renovaciones_editadas
            if ya_editado:
                info_actual = renovaciones_editadas[proceso_a_editar]
                st.success(f"✅ Ya confirmaste este proceso el {info_actual['editado_el']}: inicio {info_actual['fecha_inicio_real']}, plazo {info_actual['plazo_meses']} meses → renueva {info_actual['fecha_renovacion_confirmada']}")
            
            col1, col2 = st.columns(2)
            with col1:
                fecha_inicio_input = st.date_input(
                    "Fecha real de inicio de contrato:",
                    value=pd.Timestamp(ya_editado and renovaciones_editadas[proceso_a_editar]['fecha_inicio_real'] or datetime.now().date())
                )
            with col2:
                plazo_input = st.number_input(
                    "Plazo contractual (meses):",
                    min_value=1, max_value=36,
                    value=int(renovaciones_editadas[proceso_a_editar]['plazo_meses']) if ya_editado else 12
                )
            
            nota_input = st.text_input("Nota (opcional, ej: 'confirmado en orden de servicio N° 123'):",
                                       value=renovaciones_editadas[proceso_a_editar].get('nota', '') if ya_editado else '')
            
            if st.button("💾 Confirmar fecha de renovación"):
                nueva_fecha = guardar_renovacion_editada(proceso_a_editar, fecha_inicio_input, plazo_input, nota_input)
                st.success(f"✅ Guardado. Próxima renovación: {nueva_fecha.strftime('%Y-%m-%d')}")
                st.rerun()

# ==================== SECCIÓN 3: CRM ====================
elif "Menores" in tipo_proceso and seccion == "👥 CRM y Seguimiento":
    st.markdown("### 👥 CRM - Gestión de Clientes")
    
    contactos_data = cargar_contactos()
    historial_data = cargar_historial()
    
    # Selector de cliente compartido por las 3 pestañas
    clientes_df = df.groupby('entidad').agg({
        'id': 'count',
        'region': 'first',
        'montoAdjudicado': 'sum',
        'resultadoAdjudicacion': lambda x: (x == 'Adjudicado').sum()
    }).rename(columns={
        'id': 'procesos',
        'montoAdjudicado': 'monto_total',
        'resultadoAdjudicacion': 'adjudicados'
    }).sort_values('monto_total', ascending=False)
    
    if 'crm_cliente_activo' not in st.session_state:
        st.session_state.crm_cliente_activo = clientes_df.index[0]
    
    tab1, tab2, tab3 = st.tabs(["📋 Clientes", "📞 Contactos", "📈 Historial"])
    
    with tab1:
        st.markdown("#### Base de Clientes")
        st.dataframe(clientes_df, use_container_width=True)
        
        cliente_select = st.selectbox(
            "Selecciona cliente para ver detalle completo:",
            clientes_df.index,
            index=list(clientes_df.index).index(st.session_state.crm_cliente_activo) if st.session_state.crm_cliente_activo in clientes_df.index else 0,
            key="cliente_select_tab1"
        )
        st.session_state.crm_cliente_activo = cliente_select
        
        cliente_data = df[df['entidad'] == cliente_select]
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Procesos", len(cliente_data))
        with col2:
            st.metric("Adjudicados", len(cliente_data[cliente_data['resultadoAdjudicacion'] == 'Adjudicado']))
        with col3:
            st.metric("Monto Total", f"S/ {cliente_data['montoAdjudicado'].sum()/1000:.0f}K")
        with col4:
            n_contactos = len(contactos_data.get(cliente_select, []))
            st.metric("Contactos guardados", n_contactos)
        
        st.markdown("##### 📊 Historial de Procesos SEACE")
        st.dataframe(cliente_data[['id', 'subcategoria', 'estado', 'resultadoAdjudicacion', 'montoAdjudicado']],
                    use_container_width=True)
        
        st.caption(f"💡 Para ver/agregar contactos o notas de **{cliente_select}**, ve a las pestañas 'Contactos' o 'Historial' — ya quedó seleccionado.")
    
    with tab2:
        st.markdown("#### 📞 Gestión de Contactos")
        
        cliente = st.selectbox("Cliente:", df['entidad'].unique(),
                               index=list(df['entidad'].unique()).index(st.session_state.crm_cliente_activo) if st.session_state.crm_cliente_activo in df['entidad'].unique() else 0,
                               key="cliente_select_tab2")
        st.session_state.crm_cliente_activo = cliente
        
        # Mostrar contactos existentes
        contactos_cliente = contactos_data.get(cliente, [])
        if contactos_cliente:
            st.markdown(f"##### Contactos guardados de {cliente}")
            for i, c in enumerate(contactos_cliente):
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(f"**{c['nombre']}** — {c['cargo']} · 📧 {c['email']} · 📱 {c.get('telefono', 'N/D')}")
                with col2:
                    if st.button("🗑️", key=f"del_contacto_{cliente}_{i}"):
                        eliminar_contacto(cliente, i)
                        st.rerun()
        else:
            st.info(f"Aún no hay contactos guardados para {cliente}.")
        
        st.markdown("---")
        st.markdown("##### ➕ Agregar nuevo contacto")
        
        col1, col2 = st.columns(2)
        with col1:
            nombre_contacto = st.text_input("Nombre del contacto:", key="nombre_contacto_input")
        with col2:
            cargo = st.text_input("Cargo:", key="cargo_input")
        
        col1, col2 = st.columns(2)
        with col1:
            email = st.text_input("Email:", key="email_input")
        with col2:
            telefono = st.text_input("Teléfono:", key="telefono_input")
        
        if st.button("💾 Guardar Contacto"):
            if nombre_contacto and email:
                guardar_contacto(cliente, nombre_contacto, cargo, email, telefono)
                st.success(f"✅ Contacto guardado para {cliente}")
                st.rerun()
            else:
                st.warning("Nombre y email son obligatorios.")
    
    with tab3:
        st.markdown("#### 📈 Historial de Seguimiento")
        
        cliente = st.selectbox("Cliente:", df['entidad'].unique(),
                               index=list(df['entidad'].unique()).index(st.session_state.crm_cliente_activo) if st.session_state.crm_cliente_activo in df['entidad'].unique() else 0,
                               key="cliente_select_tab3")
        st.session_state.crm_cliente_activo = cliente
        
        # Mostrar historial existente
        historial_cliente = historial_data.get(cliente, [])
        if historial_cliente:
            st.markdown(f"##### Historial de {cliente}")
            for h in reversed(historial_cliente):
                st.markdown(f"""
                <div class='alert-info'>
                📅 <b>{h['fecha']}</b><br>
                {h['nota']}<br>
                {'<b>Próxima acción:</b> ' + h['proxima_accion'] if h.get('proxima_accion') else ''}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info(f"Aún no hay historial registrado para {cliente}.")
        
        st.markdown("---")
        st.markdown("##### ➕ Nueva entrada")
        
        ultima_interaccion = st.text_area("Nota de la interacción:", key="nota_hist_input")
        proxima_accion = st.text_input("Próxima acción planificada:", key="accion_hist_input")
        
        if st.button("📝 Guardar Historial"):
            if ultima_interaccion:
                guardar_historial(cliente, ultima_interaccion, proxima_accion)
                st.success("✅ Historial guardado")
                st.rerun()
            else:
                st.warning("Escribe una nota antes de guardar.")

# ==================== SECCIÓN 3B: ANÁLISIS DE COMPETENCIA ====================
elif "Menores" in tipo_proceso and seccion == "💼 Análisis de Competencia":
    st.markdown("### 💼 Análisis de Competencia — Proveedores Ganadores")
    
    adjudicados_comp = df[(df['resultadoAdjudicacion'] == 'Adjudicado') & (df['proveedor'] != 'SIN DEFINIR') & (df['proveedor'] != 'DESIERTO')]
    
    competencia = adjudicados_comp.groupby('proveedor').agg(
        procesos=('id', 'count'),
        monto=('montoAdjudicado', 'sum')
    ).reset_index().sort_values('monto', ascending=False)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Proveedores identificados", len(competencia))
    with col2:
        st.metric("Monto total en competencia", f"S/ {competencia['monto'].sum():,.0f}")
    with col3:
        st.metric("Procesos con proveedor conocido", int(competencia['procesos'].sum()))
    
    st.markdown("---")
    
    fig = px.bar(competencia, x='proveedor', y='monto',
                 title="Montos Adjudicados por Proveedor",
                 labels={'monto': 'Monto (S/)', 'proveedor': 'Proveedor'},
                 color_discrete_sequence=['#5B6FFF'])
    fig.update_layout(xaxis_tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("#### 🏆 Ranking de Competidores")
    
    for idx, row in competencia.iterrows():
        with st.expander(f"**{row['proveedor']}** — {int(row['procesos'])} procesos ganados — S/ {row['monto']:,.0f}"):
            procesos_proveedor = df[df['proveedor'] == row['proveedor']]
            st.dataframe(
                procesos_proveedor[['id', 'entidad', 'region', 'subcategoria', 'montoAdjudicado']],
                use_container_width=True
            )
            ruc = procesos_proveedor['ruc'].dropna().unique()
            if len(ruc) > 0:
                st.caption(f"RUC: {ruc[0]}")

# ==================== SECCIÓN 4: GENERADOR DE DOCUMENTOS ====================
elif "Menores" in tipo_proceso and seccion == "📝 Generador de Documentos":
    st.markdown("### 📝 Generador de Documentos")
    
    tipo_doc = st.selectbox("Tipo de documento:", [
        "Carta de Presentación",
        "Propuesta Comercial",
        "Checklist de Postulación",
        "Análisis FODA",
        "Reporte Ejecutivo"
    ])
    
    cliente = st.selectbox("Cliente:", df['entidad'].unique())
    
    if tipo_doc == "Carta de Presentación":
        st.markdown("#### 📄 Carta de Presentación")
        
        asunto = st.text_input("Asunto:", "Presentación de Soluciones Tecnológicas")
        
        contenido = f"""
QUBITS SAC
RUC: [COMPLETAR]
Dirección: [COMPLETAR]

{datetime.now().strftime('%d de %B de %Y')}

Señor(a) [NOMBRE FUNCIONARIO]
[CARGO]
{cliente}
[DOMICILIO]

**Asunto: {asunto}**

Estimado(a) [NOMBRE FUNCIONARIO],

En atención a su interés en soluciones de infraestructura y servicios en la nube, nos complace presentar 
a QUBITS SAC, empresa peruana especializada en tecnología y consultoría TI para el sector público y privado.

**Nuestra Propuesta de Valor:**

✓ Infraestructura en nube escalable y segura
✓ Soluciones de almacenamiento y respaldo
✓ Servicios de comunicaciones unificadas
✓ Consultoría técnica especializada
✓ Soporte técnico 24/7

Quedamos atenta(o) a sus consultas y disponibles para agendar una reunión de presentación.

Cordialmente,

Alexander Cerna
Key Account Manager Senior
QUBITS SAC
📧 acerna@qubits.pe
📱 +51 [COMPLETAR]
        """
        
        st.text_area("Contenido del documento:", value=contenido, height=400, disabled=True)
        
        if st.button("📥 Descargar Carta"):
            st.success("✅ Documento preparado para descargar")
    
    elif tipo_doc == "Propuesta Comercial":
        st.markdown("#### 💼 Propuesta Comercial")
        
        servicios = st.multiselect("Servicios incluidos:", 
                                  df['subcategoria'].unique())
        
        monto = st.number_input("Monto estimado (S/):", min_value=5000, step=1000)
        
        plazo = st.number_input("Plazo de implementación (días):", min_value=7, step=1)
        
        contenido = f"""
PROPUESTA COMERCIAL
QUBITS SAC

Cliente: {cliente}
Fecha: {datetime.now().strftime('%d/%m/%Y')}

SERVICIOS PROPUESTOS:
{chr(10).join([f"• {s}" for s in servicios])}

INVERSIÓN TOTAL: S/ {monto:,.2f}
PLAZO DE IMPLEMENTACIÓN: {plazo} días

CONDICIONES:
• Pago: 50% a la firma, 50% en implementación
• Garantía: 12 meses
• SLA: 99.5% disponibilidad
• Soporte técnico: 24/7

Esta propuesta tiene validez de 30 días.
        """
        
        st.text_area("Propuesta:", value=contenido, height=400, disabled=True)
        
        if st.button("📥 Descargar Propuesta"):
            st.success("✅ Propuesta comercial lista")

# ==================== SECCIÓN 5: GENERADOR DE PROPUESTAS TÉCNICAS ====================
elif "Menores" in tipo_proceso and seccion == "💼 Generador de Propuestas":
    st.markdown("### 💼 Generador de Propuestas Técnicas")
    
    proceso_id = st.selectbox("Selecciona proceso SEACE:", df['id'].unique())
    
    if proceso_id:
        proceso = df[df['id'] == proceso_id].iloc[0]
        
        st.markdown(f"#### 📋 Proceso: {proceso_id}")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Entidad", proceso['entidad'][:30])
        with col2:
            st.metric("Servicio", proceso['subcategoria'])
        with col3:
            st.metric("Región", proceso['region'])
        
        st.markdown("---")
        
        # Generar propuesta automática
        propuesta = f"""
═══════════════════════════════════════════════════════════════
PROPUESTA TÉCNICA
QUBITS SAC
═══════════════════════════════════════════════════════════════

CLIENTE: {proceso['entidad']}
PROCESO SEACE: {proceso['id']}
FECHA: {datetime.now().strftime('%d/%m/%Y')}
REGIÓN: {proceso['region']}

───────────────────────────────────────────────────────────────
1. ENTENDIMIENTO DE REQUERIMIENTOS
───────────────────────────────────────────────────────────────

Servicio Solicitado: {proceso['subcategoria']}
Descripción: {proceso['descripcion']}
Cubo de Contratación: {proceso['cubo']}

El {proceso['entidad']} requiere una solución de {proceso['subcategoria'].lower()} 
que garantice disponibilidad, seguridad y escalabilidad.

───────────────────────────────────────────────────────────────
2. SOLUCIÓN PROPUESTA
───────────────────────────────────────────────────────────────

ARQUITECTURA TÉCNICA:
✓ Infraestructura cloud de alta disponibilidad
✓ Respaldo automático y redundancia geográfica
✓ Encriptación end-to-end de datos
✓ Cumplimiento normativo (ISO/IEC 27001, RM 004-2016-PCM)
✓ Monitoreo 24/7 y alertas automáticas

CARACTERÍSTICAS PRINCIPALES:
• Disponibilidad: 99.9% SLA
• RTO (Recovery Time Objective): < 4 horas
• RPO (Recovery Point Objective): < 1 hora
• Ancho de banda escalable
• Integración con sistemas legacy

───────────────────────────────────────────────────────────────
3. EQUIPO TÉCNICO
───────────────────────────────────────────────────────────────

Líder Técnico: Alexander Cerna (Ingeniero Electrónico)
Especialidad: Infraestructura en Nube y Networking
Años de experiencia: 8+ en sector público

───────────────────────────────────────────────────────────────
4. CRONOGRAMA DE IMPLEMENTACIÓN
───────────────────────────────────────────────────────────────

Semana 1: Análisis detallado y planificación
Semana 2: Aprovisionamiento de infraestructura
Semana 3: Configuración y testing
Semana 4: Migración y validación
Semana 5: Capacitación y soporte

───────────────────────────────────────────────────────────────
5. CONDICIONES COMERCIALES
───────────────────────────────────────────────────────────────

Monto: S/ {proceso['montoAdjudicado']:,.2f}
Plazo: 30 días calendario
Forma de pago: Conforme OSCE
Garantía: 12 meses post-implementación

───────────────────────────────────────────────────────────────
6. CUMPLIMIENTO NORMATIVO
───────────────────────────────────────────────────────────────

✓ Ley N.° 30225 - Ley de Contrataciones del Estado
✓ RM 004-2016-PCM - NTP-ISO/IEC 27001
✓ DL 1412 - Ley de Gobierno Digital
✓ Ley 29733 - Protección de Datos Personales

═══════════════════════════════════════════════════════════════
        """
        
        st.text_area("PROPUESTA TÉCNICA GENERADA:", value=propuesta, height=600, disabled=True)
        
        if st.button("📥 Descargar Propuesta Técnica"):
            st.success("✅ Propuesta técnica lista para descargar")

# ==================== SECCIÓN 6: EXPORTAR DATOS ====================
elif "Menores" in tipo_proceso and seccion == "📊 Exportar Datos":
    st.markdown("### 📊 Exportar Datos y Reportes")
    
    opcion_export = st.selectbox("Tipo de exportación:", [
        "Todos los procesos (Excel)",
        "Clientes recurrentes (Excel)",
        "Procesos vencidos (Excel)",
        "Reporte ejecutivo (Excel)",
        "Base de contactos (Excel)"
    ])
    
    if opcion_export == "Todos los procesos (Excel)":
        st.markdown("#### 📋 Exportar todos los procesos")
        
        export_cols = ['id', 'entidad', 'region', 'subcategoria', 'estado', 
                      'resultadoAdjudicacion', 'montoAdjudicado', 'prioridad']
        
        if st.button("📥 Descargar Excel - Todos los Procesos"):
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df[export_cols].to_excel(writer, sheet_name='Procesos', index=False)
            
            st.download_button(
                label="⬇️ Descargar procesos_73.xlsx",
                data=buffer.getvalue(),
                file_name="procesos_73.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success("✅ Archivo listo para descargar")
    
    elif opcion_export == "Clientes recurrentes (Excel)":
        st.markdown("#### 👥 Exportar clientes recurrentes")
        
        clientes_df = df.groupby('entidad').agg({
            'id': 'count',
            'region': 'first',
            'montoAdjudicado': 'sum',
            'resultadoAdjudicacion': lambda x: (x == 'Adjudicado').sum()
        }).rename(columns={
            'id': 'procesos',
            'montoAdjudicado': 'monto_total',
            'resultadoAdjudicacion': 'adjudicados'
        }).sort_values('monto_total', ascending=False)
        
        if st.button("📥 Descargar Excel - Clientes"):
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                clientes_df.to_excel(writer, sheet_name='Clientes')
            
            st.download_button(
                label="⬇️ Descargar clientes_recurrentes.xlsx",
                data=buffer.getvalue(),
                file_name="clientes_recurrentes.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success("✅ Archivo listo")

# ==================== SECCIÓN 7: OPORTUNIDADES ====================
elif "Menores" in tipo_proceso and seccion == "🎯 Oportunidades":
    st.markdown("### 🎯 Oportunidades Críticas y Estratégicas")
    
    # Procesos vencidos
    evaluacion = df[(df['estado'] == 'En Evaluación') | (df['estado'] == 'Vigente')]
    evaluacion_vencida = evaluacion[evaluacion['diasVencidos'] > 30]
    
    st.markdown(f"#### 🔴 PROCESOS VENCIDOS ({len(evaluacion_vencida)})")
    
    for idx, row in evaluacion_vencida.sort_values('diasVencidos', ascending=False).head(10).iterrows():
        st.markdown(f"""
        <div class='alert-critical'>
        <b>{row['entidad']}</b> | {row['region']}<br>
        Proceso: {row['id']} | Servicio: {row['subcategoria']}<br>
        ⏰ VENCIDO: {int(row['diasVencidos'])} DÍAS | Prioridad: <b>{row['prioridad']}</b>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Clientes con múltiples desiertos
    st.markdown("#### 🔴 CLIENTES CON MÚLTIPLES DESIERTOS")
    
    desiertos_por_cliente = df[df['resultadoAdjudicacion'] == 'DESIERTO'].groupby('entidad').size()
    clientes_desiertos = desiertos_por_cliente[desiertos_por_cliente > 1].sort_values(ascending=False)
    
    for cliente, count in clientes_desiertos.items():
        st.markdown(f"""
        <div class='alert-warning'>
        <b>{cliente}</b><br>
        🔴 {count} procesos desiertos | Oportunidad de reintento
        </div>
        """, unsafe_allow_html=True)

# ==================== MÓDULOS DE LICITACIONES ====================
# Si el usuario seleccionó Licitaciones, mostrar los módulos correspondientes

if "Licitaciones" in tipo_proceso:
    if seccion == "📊 Dashboard de Licitaciones":
        st.markdown("### 📊 Dashboard de Licitaciones")
        
        col_refresh, col_spacer = st.columns([1, 5])
        with col_refresh:
            if st.button("🔄 Refrescar", key="refresh_licitaciones"):
                st.rerun()
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f'<div class="metric-card"><h3>{len(df)}</h3>Licitaciones Totales</div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div class="metric-card"><h3>S/ {df["monto_base"].sum()/1000000:.2f}M</h3>Mercado Base</div>', unsafe_allow_html=True)
        with col3:
            adjudicadas = len(df[df['estado'] == 'Contrato Firmado'])
            st.markdown(f'<div class="metric-card"><h3>{adjudicadas}</h3>Adjudicadas</div>', unsafe_allow_html=True)
        with col4:
            st.markdown(f'<div class="metric-card"><h3>{df["entidad"].nunique()}</h3>Entidades</div>', unsafe_allow_html=True)
        
        st.markdown("---")
        
        if len(df) > 0:
            # Fila de análisis comercial
            col1, col2, col3 = st.columns(3)
            with col1:
                monto_adj_total = df['monto_adjudicado'].sum()
                ahorro_pct = ((df['monto_base'].sum() - monto_adj_total) / df['monto_base'].sum() * 100) if df['monto_base'].sum() > 0 else 0
                st.markdown(f'<div class="metric-card"><h3>S/ {monto_adj_total/1000000:.2f}M</h3>Monto Adjudicado</div>', unsafe_allow_html=True)
            with col2:
                st.markdown(f'<div class="metric-card"><h3>{ahorro_pct:.1f}%</h3>Diferencia Base vs Adjudicado</div>', unsafe_allow_html=True)
            with col3:
                promedio_postores = df['empresas_participantes'].dropna().mean() if df['empresas_participantes'].notna().any() else 0
                st.markdown(f'<div class="metric-card"><h3>{promedio_postores:.1f}</h3>Postores Promedio</div>', unsafe_allow_html=True)
            
            st.markdown("---")
            
            col1, col2 = st.columns(2)
            
            with col1:
                tipo_lic_counts = df['tipo_licitacion'].value_counts()
                fig = px.pie(values=tipo_lic_counts.values, names=tipo_lic_counts.index,
                           title="Distribución por Tipo de Licitación",
                           color_discrete_sequence=['#5B6FFF', '#7B8FFF', '#9BA9FF', '#BBBBFF'])
                st.plotly_chart(fig, use_container_width=True)
                
                tipo_click = st.selectbox("🔍 Ver licitaciones de un tipo:", ["—"] + tipo_lic_counts.index.tolist(), key="tipo_lic_drill")
                if tipo_click != "—":
                    st.dataframe(
                        df[df['tipo_licitacion'] == tipo_click][['id', 'entidad', 'region', 'estado', 'monto_base', 'ganador']],
                        use_container_width=True
                    )
            
            with col2:
                estado_counts = df['estado'].value_counts()
                fig = px.bar(x=estado_counts.index, y=estado_counts.values,
                           title="Licitaciones por Estado",
                           labels={'x': 'Estado', 'y': 'Cantidad'},
                           color_discrete_sequence=['#5B6FFF'])
                st.plotly_chart(fig, use_container_width=True)
                
                estado_click = st.selectbox("🔍 Ver licitaciones de un estado:", ["—"] + estado_counts.index.tolist(), key="estado_lic_drill")
                if estado_click != "—":
                    st.dataframe(
                        df[df['estado'] == estado_click][['id', 'entidad', 'region', 'tipo_licitacion', 'monto_base', 'ganador']],
                        use_container_width=True
                    )
            
            st.markdown("---")
            
            col1, col2 = st.columns(2)
            with col1:
                region_counts = df.groupby('region')['monto_base'].sum().sort_values(ascending=False).head(10)
                if not region_counts.empty:
                    fig = px.bar(x=region_counts.values, y=region_counts.index, orientation='h',
                               title="🌍 Mercado Base por Región (Top 10)",
                               labels={'x': 'Monto Base (S/)', 'y': 'Región'},
                               color_discrete_sequence=['#5B6FFF'])
                    fig.update_layout(yaxis={'categoryorder': 'total ascending'})
                    st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                entidad_counts = df.groupby('entidad')['monto_base'].sum().sort_values(ascending=False).head(10)
                if not entidad_counts.empty:
                    fig = px.bar(x=entidad_counts.values, y=entidad_counts.index, orientation='h',
                               title="🏛 Top Entidades por Monto en Licitación",
                               labels={'x': 'Monto Base (S/)', 'y': 'Entidad'},
                               color_discrete_sequence=['#7B8FFF'])
                    fig.update_layout(yaxis={'categoryorder': 'total ascending'})
                    st.plotly_chart(fig, use_container_width=True)
            
            st.markdown("---")
            st.markdown("#### 📋 Todas las Licitaciones")
            cols_mostrar = ['id', 'titulo', 'entidad', 'region', 'tipo_licitacion', 'estado', 'monto_base', 'monto_adjudicado', 'ganador', 'empresas_participantes']
            cols_disponibles = [c for c in cols_mostrar if c in df.columns]
            st.dataframe(df[cols_disponibles], use_container_width=True)
        else:
            st.info("No hay licitaciones aún. Agrega procesos desde Base de Datos.")
    
    elif seccion == "🗄️ Base de Datos de Licitaciones":
        st.markdown("### 🗄️ Base de Datos — Licitaciones")
        st.caption("Una sola base de datos editable: agrega licitaciones nuevas y edita cualquiera de las ya registradas.")
        
        nuevas_raw = cargar_licitaciones_raw()
        
        tab1, tab2, tab3, tab4 = st.tabs(["📄 Pegar texto SEACE", "➕ Formulario guiado", "📋 Pegar JSON", "🗂️ Todas las Licitaciones"])
        
        # TAB 1: Parser de texto SEACE
        with tab1:
            st.markdown("#### Pega el texto tal cual lo copias de SEACE")
            st.caption("💡 Tip: en la página de licitacionesperu.pe, haz clic en cualquier parte del texto y usa Ctrl+A (Cmd+A en Mac) para seleccionar TODO el contenido de la página, luego Ctrl+C. Si seleccionas manualmente con el mouse arrastrando, es fácil que se corte antes de llegar a la lista de Postores.")
            
            texto_lic = st.text_area("Texto copiado de SEACE:", height=280, 
                                     placeholder="Compra de Pantallas...\nS/ 291,401.17\nContrato firmado...",
                                     key="texto_licitacion_input")
            
            if texto_lic.strip():
                num_lineas = len(texto_lic.strip().split('\n'))
                tiene_marcador_postores = bool(re.search(r'Postores\s*\(\d+\)', texto_lic))
                menciona_participantes = bool(re.search(r'\d+\s+empresas?\s+particip', texto_lic))
                
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.caption(f"📏 {num_lineas} líneas pegadas")
                with col_info2:
                    if menciona_participantes and not tiene_marcador_postores:
                        st.caption("⚠️ Posible texto incompleto — sigue leyendo abajo")
                    elif tiene_marcador_postores:
                        st.caption("✅ Incluye sección de Postores")
                
                if menciona_participantes and not tiene_marcador_postores:
                    st.warning("⚠️ **El texto menciona '# empresas participaron' pero no encuentro la sección 'Postores (N)'.** Es muy probable que el pegado se haya cortado antes de llegar a la lista de postores. Vuelve a la página de SEACE, haz clic dentro del texto y presiona Ctrl+A (Cmd+A en Mac) para seleccionar TODO el contenido, luego Ctrl+C y pega de nuevo aquí — así no se corta nada.")
            
            if st.button("🔍 Extraer datos del texto", key="extraer_lic"):
                if not texto_lic.strip():
                    st.warning("Pega el texto de la licitación primero.")
                else:
                    lic_parseada, avisos = parsear_texto_licitacion(texto_lic)
                    if lic_parseada is None:
                        st.error("No se pudo procesar el texto.")
                    else:
                        id_extraido = lic_parseada.get('id', '').strip()
                        ids_existentes = df['id'].tolist()
                        
                        if id_extraido in ids_existentes:
                            st.error(f"❌ **La licitación '{id_extraido}' ya está registrada.** Ve a '🗂️ Todas las Licitaciones' para editarla o eliminarla.")
                        else:
                            st.session_state['licitacion_parseada'] = lic_parseada
                            st.session_state['avisos_lic'] = avisos
                            st.session_state['contador_extraccion_lic'] = st.session_state.get('contador_extraccion_lic', 0) + 1
                            st.success(f"✅ Datos extraídos. ID: {id_extraido}")
            
            if 'licitacion_parseada' in st.session_state:
                lic_p = st.session_state['licitacion_parseada']
                avisos_p = st.session_state.get('avisos_lic', [])
                
                if avisos_p:
                    st.warning("⚠️ Revisa estos puntos:\n\n" + "\n".join(f"- {a}" for a in avisos_p))
                
                st.markdown("##### Datos extraídos — corrige antes de guardar")
                
                # IMPORTANTE: el sufijo único (basado en el ID recién extraído + un contador de
                # extracciones) evita que Streamlit reutilice los valores de session_state de una
                # licitación previa cuando extraes una nueva — sin esto, el formulario se queda
                # "pegado" mostrando (y guardando) los datos de la primera licitación una y otra vez.
                sufijo_form = re.sub(r'[^A-Za-z0-9]', '_', lic_p.get('id', 'nuevo')) + f"_{st.session_state.get('contador_extraccion_lic', 0)}"
                
                col1, col2 = st.columns(2)
                with col1:
                    lic_id = st.text_input("ID*:", value=lic_p.get('id', ''), key=f"lic_id_{sufijo_form}")
                    if lic_p.get('codigo_tipo_procedimiento'):
                        lic_codigo_tipo = st.text_input("Código de tipo de procedimiento:", value=lic_p.get('codigo_tipo_procedimiento', ''), key=f"lic_codigo_tipo_{sufijo_form}",
                                                        help="Ej: CP = Concurso Público. Se extrajo por separado del ID porque venían unidos por un espacio en el texto original.")
                    else:
                        lic_codigo_tipo = ''
                    lic_titulo = st.text_input("Título*:", value=lic_p.get('titulo', ''), key=f"lic_titulo_{sufijo_form}")
                    lic_entidad = st.text_input("Entidad*:", value=lic_p.get('entidad', ''), key=f"lic_entidad_{sufijo_form}")
                    lic_region = st.text_input("Región*:", value=lic_p.get('region', ''), key=f"lic_region_{sufijo_form}")
                    lic_tipo_contratacion = st.selectbox("Tipo de Contratación:", TIPOS_CONTRATACION,
                                                         index=TIPOS_CONTRATACION.index(lic_p.get('tipo_contratacion', '')) if lic_p.get('tipo_contratacion', '') in TIPOS_CONTRATACION else len(TIPOS_CONTRATACION) - 1,
                                                         key=f"lic_tipo_contratacion_{sufijo_form}")
                with col2:
                    lic_tipo = st.text_input("Tipo de Licitación*:", value=lic_p.get('tipo_licitacion', ''), key=f"lic_tipo_{sufijo_form}")
                    estado_detectado = lic_p.get('estado', '')
                    lic_estado = st.selectbox("Estado*:", ESTADOS_LICITACION,
                                             index=ESTADOS_LICITACION.index(estado_detectado) if estado_detectado in ESTADOS_LICITACION else ESTADOS_LICITACION.index("En Evaluación"),
                                             key=f"lic_estado_{sufijo_form}")
                    lic_monto_base = st.number_input("Monto Base* (S/):", min_value=0.0, step=1000.0,
                                                     value=float(str(lic_p.get('monto_base', 0)).replace(',', '')),
                                                     key=f"lic_monto_base_{sufijo_form}")
                    lic_ganador = st.text_input("Ganador (déjalo vacío si aún no hay):", value=lic_p.get('ganador', ''), key=f"lic_ganador_{sufijo_form}")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    lic_publicado = st.text_input("Publicado (YYYY-MM-DD):", value=lic_p.get('publicado', ''), key=f"lic_publicado_{sufijo_form}")
                with col2:
                    lic_adj = st.text_input("Adjudicación (YYYY-MM-DD):", value=lic_p.get('adjudicacion', ''), key=f"lic_adj_{sufijo_form}")
                with col3:
                    lic_monto_adj = st.number_input("Monto Adjudicado (S/):", min_value=0.0, step=1000.0,
                                                    value=float(str(lic_p.get('monto_adjudicado', 0)).replace(',', '')),
                                                    key=f"lic_monto_adj_{sufijo_form}")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    lic_inicio = st.text_input("Inicio Contrato (YYYY-MM-DD):", value=lic_p.get('inicio_contrato', '') or '', key=f"lic_inicio_{sufijo_form}")
                with col2:
                    lic_fin = st.text_input("Fin Contrato (YYYY-MM-DD):", value=lic_p.get('fin_contrato', '') or '', key=f"lic_fin_{sufijo_form}")
                with col3:
                    lic_duracion = st.number_input("Duración (días):", min_value=0, step=1,
                                                   value=int(lic_p.get('duracion_dias', 0) or 0),
                                                   key=f"lic_duracion_{sufijo_form}")
                
                lic_direccion = st.text_input("Dirección de la entidad:", value=lic_p.get('direccion_entidad', ''), key=f"lic_direccion_{sufijo_form}")
                lic_telefono = st.text_input("Teléfono de la entidad:", value=lic_p.get('telefono_entidad', ''), key=f"lic_telefono_{sufijo_form}")
                
                postores_detectados = lic_p.get('postores', [])
                if postores_detectados:
                    st.markdown(f"##### 👥 Postores detectados ({len(postores_detectados)})")
                    df_postores_preview = pd.DataFrame(postores_detectados)
                    cols_preview = [c for c in ['empresa', 'es_ganador', 'licitaciones_previas', 'ganadas_previas', 'tasa_exito'] if c in df_postores_preview.columns]
                    st.dataframe(df_postores_preview[cols_preview], use_container_width=True)
                else:
                    st.caption("ℹ️ No se detectó lista de postores en el texto pegado. La licitación se guardará solo con el ganador.")
                
                if st.button("💾 Guardar licitación", key=f"guardar_lic_{sufijo_form}"):
                    lic_final = {
                        'id': lic_id.strip(), 'titulo': lic_titulo.strip(), 'entidad': lic_entidad.strip(),
                        'region': lic_region.strip(), 'tipo_licitacion': lic_tipo.strip(),
                        'tipo_contratacion': lic_tipo_contratacion,
                        'estado': lic_estado, 'monto_base': lic_monto_base, 'ganador': lic_ganador.strip(),
                        'monto_adjudicado': lic_monto_adj, 'empresas_participantes': lic_p.get('empresas_participantes'),
                        'publicado': lic_publicado.strip(), 'adjudicacion': lic_adj.strip(),
                        'inicio_contrato': lic_inicio.strip(), 'fin_contrato': lic_fin.strip(),
                        'duracion_dias': lic_duracion, 'moneda': lic_p.get('moneda', 'PEN'),
                        'cui': lic_p.get('cui'), 'tdr_disponible': True, 'postores': postores_detectados,
                        'direccion_entidad': lic_direccion.strip(), 'telefono_entidad': lic_telefono.strip(),
                        'codigo_tipo_procedimiento': lic_codigo_tipo.strip()
                    }
                    
                    errores = validar_licitacion(lic_final)
                    ids_existentes = df['id'].tolist()
                    
                    if lic_final['id'] in nuevas_raw and lic_final['id'] != lic_p.get('id'):
                        errores.append(f"❌ El ID ya está registrado en otra licitación.")
                    elif lic_final['id'] not in nuevas_raw and lic_final['id'] in ids_existentes:
                        errores.append(f"❌ El ID ya está registrado.")
                    
                    if errores:
                        for e in errores:
                            st.error(e)
                    else:
                        agregar_licitacion_nueva(lic_final)
                        st.success(f"✅ Licitación {lic_final['id']} guardada.")
                        del st.session_state['licitacion_parseada']
                        st.session_state.pop('avisos_lic', None)
                        st.session_state.pop('texto_licitacion_input', None)
                        time.sleep(0.3)
                        st.rerun()
        
        with tab2:
            st.markdown("#### Ingresa los datos manualmente")
            col1, col2 = st.columns(2)
            with col1:
                f_id = st.text_input("ID*:", key="f_lic_id")
                f_titulo = st.text_input("Título*:", key="f_lic_titulo")
                f_entidad = st.text_input("Entidad*:", key="f_lic_entidad")
            with col2:
                f_region = st.text_input("Región*:", key="f_lic_region")
                f_tipo = st.text_input("Tipo*:", key="f_lic_tipo")
                f_estado = st.selectbox("Estado*:", ["Contrato Firmado", "En Evaluación"], key="f_lic_estado")
            
            f_monto_base = st.number_input("Monto Base* (S/):", min_value=0.0, key="f_lic_monto_base")
            f_ganador = st.text_input("Ganador*:", key="f_lic_ganador")
            f_monto_adj = st.number_input("Monto Adjudicado (S/):", min_value=0.0, key="f_lic_monto_adj")
            
            if st.button("💾 Guardar licitación", key="save_form_lic"):
                lic_nueva = {
                    'id': f_id.strip(), 'titulo': f_titulo.strip(), 'entidad': f_entidad.strip(),
                    'region': f_region.strip(), 'tipo_licitacion': f_tipo.strip(),
                    'estado': f_estado, 'monto_base': f_monto_base, 'ganador': f_ganador.strip(),
                    'monto_adjudicado': f_monto_adj, 'moneda': 'PEN', 'tdr_disponible': True
                }
                
                errores = validar_licitacion(lic_nueva)
                if errores:
                    for e in errores:
                        st.error(e)
                else:
                    agregar_licitacion_nueva(lic_nueva)
                    st.success(f"✅ Guardada: {lic_nueva['id']}")
                    time.sleep(0.3)
                    st.rerun()
        
        with tab3:
            st.markdown("#### Pega JSON de licitaciones")
            json_input = st.text_area("JSON:", height=250, key="json_lic_input")
            
            if st.button("🔍 Validar y guardar JSON", key="save_json_lic"):
                if not json_input.strip():
                    st.warning("Pega JSON primero.")
                else:
                    try:
                        parsed = json.loads(json_input)
                        lics_validar = parsed if isinstance(parsed, list) else [parsed]
                        todos_ok = True
                        ids_existentes = df['id'].tolist()
                        
                        for lic in lics_validar:
                            errores = validar_licitacion(lic)
                            if lic.get('id') in ids_existentes:
                                errores.append(f"ID ya está registrado")
                            if errores:
                                todos_ok = False
                                st.error(f"Licitación '{lic.get('id')}': " + " | ".join(errores))
                        
                        if todos_ok:
                            for lic in lics_validar:
                                agregar_licitacion_nueva(lic)
                            st.success(f"✅ {len(lics_validar)} licitación(es) guardada(s).")
                            time.sleep(0.3)
                            st.rerun()
                    except json.JSONDecodeError as e:
                        st.error(f"JSON inválido: {e}")
        
        with tab4:
            st.markdown("#### Todas las Licitaciones — Editar o Eliminar")
            st.caption("Base de datos única: puedes editar o eliminar cualquier licitación registrada, sin distinción de cómo se agregó.")
            
            if len(df) == 0:
                st.info("No hay licitaciones registradas aún.")
            else:
                st.metric("Total licitaciones", len(df))
                
                opciones_lic = [f"{row['id']} — {row['entidad']}" for _, row in df.iterrows()]
                lic_seleccionada_label = st.selectbox("Busca la licitación a editar:", ["—"] + opciones_lic, key="selector_lic_editar")
                
                if lic_seleccionada_label != "—":
                    lid_sel = lic_seleccionada_label.split(" — ")[0]
                    lic_row = df[df['id'] == lid_sel].iloc[0]
                    lic = lic_row.to_dict()
                    
                    st.markdown("##### ✏️ Editar licitación")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        e_id = st.text_input("ID:", value=lic.get('id', lid_sel), key=f"edit_lic_id_{lid_sel}")
                        e_titulo = st.text_input("Título:", value=lic.get('titulo', '') or '', key=f"edit_lic_titulo_{lid_sel}")
                        e_entidad = st.text_input("Entidad:", value=lic.get('entidad', '') or '', key=f"edit_lic_entidad_{lid_sel}")
                        e_region = st.text_input("Región:", value=lic.get('region', '') or '', key=f"edit_lic_region_{lid_sel}")
                        e_tipo_contratacion = st.text_input("Tipo Contratación:", value=lic.get('tipo_contratacion', '') or '', key=f"edit_lic_tipocontr_{lid_sel}",
                                                            help="Categorías oficiales OSCE: Bien, Servicio, Obra, Consultoría, Consultoría de Obras")
                    with col2:
                        e_tipo = st.text_input("Tipo Licitación:", value=lic.get('tipo_licitacion', '') or '', key=f"edit_lic_tipo_{lid_sel}")
                        e_estado = st.selectbox("Estado:", ESTADOS_LICITACION,
                                                index=ESTADOS_LICITACION.index(lic.get('estado')) if lic.get('estado') in ESTADOS_LICITACION else 0,
                                                key=f"edit_lic_estado_{lid_sel}")
                        e_monto_base = st.number_input("Monto Base (S/):", min_value=0.0, step=1000.0,
                                                       value=float(lic.get('monto_base', 0) or 0), key=f"edit_lic_montobase_{lid_sel}")
                        e_ganador = st.text_input("Ganador (vacío si aún no hay):", value=lic.get('ganador', '') or '', key=f"edit_lic_ganador_{lid_sel}")
                        e_monto_adj = st.number_input("Monto Adjudicado (S/):", min_value=0.0, step=1000.0,
                                                      value=float(lic.get('monto_adjudicado', 0) or 0), key=f"edit_lic_montoadj_{lid_sel}")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        e_publicado = st.text_input("Publicado (YYYY-MM-DD):", value=lic.get('publicado', '') or '', key=f"edit_lic_publicado_{lid_sel}")
                    with col2:
                        e_adjudicacion = st.text_input("Adjudicación (YYYY-MM-DD):", value=lic.get('adjudicacion', '') or '', key=f"edit_lic_adj_{lid_sel}")
                    with col3:
                        e_cui = st.text_input("CUI:", value=lic.get('cui', '') or '', key=f"edit_lic_cui_{lid_sel}")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        e_inicio_contrato = st.text_input("Inicio Contrato (YYYY-MM-DD):", value=lic.get('inicio_contrato', '') or '', key=f"edit_lic_inicio_{lid_sel}")
                    with col2:
                        e_fin_contrato = st.text_input("Fin Contrato (YYYY-MM-DD):", value=lic.get('fin_contrato', '') or '', key=f"edit_lic_fin_{lid_sel}")
                    with col3:
                        e_duracion_dias = st.number_input("Duración (días):", min_value=0, step=1,
                                                          value=int(lic.get('duracion_dias', 0) or 0), key=f"edit_lic_duracion_{lid_sel}")
                    
                    e_direccion = st.text_input("Dirección de la entidad:", value=lic.get('direccion_entidad', '') or '', key=f"edit_lic_direccion_{lid_sel}")
                    e_telefono = st.text_input("Teléfono de la entidad:", value=lic.get('telefono_entidad', '') or '', key=f"edit_lic_telefono_{lid_sel}")
                    
                    postores_existentes = lic.get('postores', [])
                    if isinstance(postores_existentes, list) and postores_existentes:
                        st.caption(f"👥 {len(postores_existentes)} postores registrados (no editables aquí — vuelve a pegar el texto SEACE si necesitas actualizarlos)")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("💾 Guardar cambios", key=f"save_lic_{lid_sel}"):
                            lic_actualizada = dict(lic)
                            lic_actualizada.update({
                                'id': e_id.strip(), 'titulo': e_titulo.strip(), 'entidad': e_entidad.strip(),
                                'region': e_region.strip(), 'tipo_contratacion': e_tipo_contratacion.strip(),
                                'tipo_licitacion': e_tipo.strip(), 'estado': e_estado,
                                'monto_base': e_monto_base, 'ganador': e_ganador.strip(),
                                'monto_adjudicado': e_monto_adj, 'publicado': e_publicado.strip(),
                                'adjudicacion': e_adjudicacion.strip(), 'cui': e_cui.strip(),
                                'inicio_contrato': e_inicio_contrato.strip(), 'fin_contrato': e_fin_contrato.strip(),
                                'duracion_dias': e_duracion_dias,
                                'direccion_entidad': e_direccion.strip(), 'telefono_entidad': e_telefono.strip()
                            })
                            
                            errores = validar_licitacion(lic_actualizada)
                            
                            if e_id.strip() != lid_sel:
                                ids_existentes_otros = [i for i in df['id'].tolist() if i != lid_sel]
                                if e_id.strip() in ids_existentes_otros:
                                    errores.append(f"❌ El nuevo ID '{e_id.strip()}' ya existe en otra licitación. Elige un ID distinto.")
                            
                            if errores:
                                for e in errores:
                                    st.error(e)
                            else:
                                if e_id.strip() != lid_sel:
                                    eliminar_licitacion_nueva(lid_sel)
                                agregar_licitacion_nueva(lic_actualizada)
                                st.success("✅ Licitación actualizada")
                                time.sleep(0.3)
                                st.rerun()
                    with col2:
                        if st.button("🗑️ Eliminar licitación", key=f"del_lic_{lid_sel}"):
                            eliminar_licitacion_nueva(lid_sel)
                            time.sleep(0.3)
                            st.rerun()
                    
                    st.caption(f"Última modificación: {lic.get('_agregada_el', 'N/D')}")
    
    elif seccion == "👥 CRM y Seguimiento":
        st.markdown("### 👥 CRM - Gestión de Entidades Públicas")
        st.caption("Administra contactos, notas de interacciones y seguimiento de procesos por entidad licitante.")
        
        contactos_data = cargar_contactos()
        historial_data = cargar_historial()
        
        # Resumen por entidad
        entidades_df = df.groupby('entidad').agg({
            'id': 'count',
            'region': 'first',
            'monto_adjudicado': 'sum',
            'estado': lambda x: (x == 'Contrato Firmado').sum()
        }).rename(columns={
            'id': 'licitaciones',
            'monto_adjudicado': 'monto_total_adjudicado',
            'estado': 'contratos_firmados'
        }).sort_values('monto_total_adjudicado', ascending=False)
        
        if 'crm_entidad_activa' not in st.session_state:
            st.session_state.crm_entidad_activa = entidades_df.index[0] if len(entidades_df) > 0 else ''
        
        tab1, tab2, tab3 = st.tabs(["📋 Entidades", "📞 Contactos", "📈 Historial"])
        
        with tab1:
            st.markdown("#### Base de Entidades Licitantes")
            if len(entidades_df) > 0:
                st.dataframe(entidades_df, use_container_width=True)
                
                entidad_select = st.selectbox(
                    "Selecciona entidad para ver detalle completo:",
                    entidades_df.index,
                    index=list(entidades_df.index).index(st.session_state.crm_entidad_activa) if st.session_state.crm_entidad_activa in entidades_df.index else 0,
                    key="entidad_select_tab1"
                )
                st.session_state.crm_entidad_activa = entidad_select
                
                entidad_data = df[df['entidad'] == entidad_select]
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Licitaciones", len(entidad_data))
                with col2:
                    st.metric("Contratos Firmados", len(entidad_data[entidad_data['estado'] == 'Contrato Firmado']))
                with col3:
                    st.metric("Monto Total", f"S/ {entidad_data['monto_adjudicado'].sum()/1e6:.2f}M")
                with col4:
                    n_contactos = len(contactos_data.get(entidad_select, []))
                    st.metric("Contactos", n_contactos)
                
                st.markdown("##### 📊 Historial de Licitaciones")
                cols_mostrar = ['id', 'tipo_licitacion', 'estado', 'monto_adjudicado']
                cols_disp = [c for c in cols_mostrar if c in entidad_data.columns]
                st.dataframe(entidad_data[cols_disp], use_container_width=True)
                
                st.caption(f"💡 Para ver/agregar contactos de **{entidad_select}**, ve a la pestaña 'Contactos' — ya quedó seleccionada.")
            else:
                st.info("Aún no hay entidades registradas.")
        
        with tab2:
            st.markdown("#### 📞 Gestión de Contactos")
            
            if len(df) > 0:
                entidades_list = sorted(df['entidad'].unique().tolist())
                entidad = st.selectbox("Entidad:", entidades_list,
                                       index=entidades_list.index(st.session_state.crm_entidad_activa) if st.session_state.crm_entidad_activa in entidades_list else 0,
                                       key="entidad_select_tab2")
                st.session_state.crm_entidad_activa = entidad
                
                contactos_entidad = contactos_data.get(entidad, [])
                if contactos_entidad:
                    st.markdown(f"##### Contactos de {entidad}")
                    for i, c in enumerate(contactos_entidad):
                        col1, col2 = st.columns([5, 1])
                        with col1:
                            st.markdown(f"**{c['nombre']}** — {c['cargo']} · 📧 {c['email']} · 📱 {c.get('telefono', 'N/D')}")
                        with col2:
                            if st.button("🗑️", key=f"del_contacto_lic_{entidad}_{i}"):
                                eliminar_contacto(entidad, i)
                                st.rerun()
                else:
                    st.info(f"Aún no hay contactos para {entidad}.")
                
                st.markdown("---")
                st.markdown("##### ➕ Agregar nuevo contacto")
                
                col1, col2 = st.columns(2)
                with col1:
                    nombre = st.text_input("Nombre:", key="lic_nombre_contacto")
                with col2:
                    cargo = st.text_input("Cargo:", key="lic_cargo_contacto")
                
                col1, col2 = st.columns(2)
                with col1:
                    email = st.text_input("Email:", key="lic_email_contacto")
                with col2:
                    telefono = st.text_input("Teléfono:", key="lic_telefono_contacto")
                
                if st.button("💾 Guardar Contacto", key="lic_guardar_contacto"):
                    if nombre and email:
                        guardar_contacto(entidad, nombre, cargo, email, telefono)
                        st.success(f"✅ Contacto guardado para {entidad}")
                        st.rerun()
                    else:
                        st.warning("Nombre y email son obligatorios.")
            else:
                st.info("Agrega licitaciones primero para ver entidades.")
        
        with tab3:
            st.markdown("#### 📈 Historial de Seguimiento")
            
            if len(df) > 0:
                entidades_list = sorted(df['entidad'].unique().tolist())
                entidad = st.selectbox("Entidad:", entidades_list,
                                       index=entidades_list.index(st.session_state.crm_entidad_activa) if st.session_state.crm_entidad_activa in entidades_list else 0,
                                       key="entidad_select_tab3")
                st.session_state.crm_entidad_activa = entidad
                
                historial_entidad = historial_data.get(entidad, [])
                if historial_entidad:
                    st.markdown(f"##### Historial de {entidad}")
                    for h in reversed(historial_entidad):
                        st.markdown(f"""
                        <div style='border-left: 4px solid #1f77b4; padding: 10px; margin: 10px 0;'>
                        📅 <b>{h['fecha']}</b><br>
                        {h['nota']}<br>
                        {'<b>Próxima acción:</b> ' + h['proxima_accion'] if h.get('proxima_accion') else ''}
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info(f"Aún no hay historial para {entidad}.")
                
                st.markdown("---")
                st.markdown("##### ➕ Nueva entrada de seguimiento")
                
                nota = st.text_area("Nota de interacción:", key="lic_nota_hist")
                proxima = st.text_input("Próxima acción planificada:", key="lic_accion_hist")
                
                if st.button("📝 Guardar Historial", key="lic_guardar_hist"):
                    if nota:
                        guardar_historial(entidad, nota, proxima)
                        st.success("✅ Historial guardado")
                        st.rerun()
                    else:
                        st.warning("Escribe una nota antes de guardar.")
            else:
                st.info("Agrega licitaciones primero para ver entidades.")
    
    elif seccion == "📅 Calendario de Renovaciones":
        st.markdown("### 📅 Calendario de Renovaciones — Alertas de Vencimiento")
        st.caption("Monitorea fechas de fin de contrato para anticipar nuevas oportunidades de negocio.")
        
        # Filtrar solo licitaciones con estado "Contrato Firmado" y fecha de fin conocida
        renovaciones = df[(df['estado'] == 'Contrato Firmado') & (df['fin_contrato'].notna() & (df['fin_contrato'] != ''))].copy()
        
        if len(renovaciones) > 0:
            # Convertir a datetime para cálculos
            renovaciones['fin_dt'] = pd.to_datetime(renovaciones['fin_contrato'], errors='coerce')
            renovaciones = renovaciones.dropna(subset=['fin_dt'])
            
            # Calcular días para vencer
            from datetime import datetime as dt
            hoy = dt.now()
            renovaciones['dias_para_vencer'] = (renovaciones['fin_dt'] - pd.Timestamp(hoy)).dt.days
            renovaciones = renovaciones.sort_values('dias_para_vencer')
            
            # Crear categorías de urgencia
            def categoria_urgencia(dias):
                if dias < 0:
                    return "⚫ Vencido"
                elif dias <= 30:
                    return "🔴 URGENTE (≤30 días)"
                elif dias <= 90:
                    return "🟠 Próximo (≤90 días)"
                elif dias <= 180:
                    return "🟡 Vigilancia (≤180 días)"
                else:
                    return "🟢 Futuro (>180 días)"
            
            renovaciones['urgencia'] = renovaciones['dias_para_vencer'].apply(categoria_urgencia)
            
            # KPIs de urgencia
            col1, col2, col3, col4, col5 = st.columns(5)
            vencidos = len(renovaciones[renovaciones['urgencia'] == "⚫ Vencido"])
            urgentes = len(renovaciones[renovaciones['urgencia'] == "🔴 URGENTE (≤30 días)"])
            proximos = len(renovaciones[renovaciones['urgencia'] == "🟠 Próximo (≤90 días)"])
            vigilancia = len(renovaciones[renovaciones['urgencia'] == "🟡 Vigilancia (≤180 días)"])
            futuro = len(renovaciones[renovaciones['urgencia'] == "🟢 Futuro (>180 días)"])
            
            with col1:
                st.metric("⚫ Vencidos", vencidos)
            with col2:
                st.metric("🔴 Urgentes", urgentes)
            with col3:
                st.metric("🟠 Próximos", proximos)
            with col4:
                st.metric("🟡 Vigilancia", vigilancia)
            with col5:
                st.metric("🟢 Futuro", futuro)
            
            st.markdown("---")
            
            # Selector de filtro por urgencia
            filtro_urgencia = st.selectbox("Filtrar por urgencia:", 
                                           ["Todas"] + sorted(renovaciones['urgencia'].unique().tolist()),
                                           key="filtro_urgencia_renovaciones")
            
            if filtro_urgencia == "Todas":
                renovaciones_filtradas = renovaciones
            else:
                renovaciones_filtradas = renovaciones[renovaciones['urgencia'] == filtro_urgencia]
            
            # Tabla de renovaciones
            st.markdown(f"#### Renovaciones ({len(renovaciones_filtradas)})")
            cols_mostrar = ['entidad', 'id', 'titulo', 'fin_dt', 'dias_para_vencer', 'urgencia']
            cols_disp = [c for c in cols_mostrar if c in renovaciones_filtradas.columns]
            
            # Renombrar para mejor presentación
            renovaciones_display = renovaciones_filtradas[cols_disp].copy()
            renovaciones_display.columns = ['Entidad', 'ID Licitación', 'Título', 'Vencimiento', 'Días para vencer', 'Urgencia']
            renovaciones_display['Vencimiento'] = renovaciones_display['Vencimiento'].dt.strftime('%Y-%m-%d')
            
            st.dataframe(renovaciones_display, use_container_width=True, hide_index=True)
            
            # Expandir para ver detalles por entidad
            st.markdown("---")
            st.markdown("#### 📋 Resumen por Entidad")
            
            resumen_entidad = renovaciones_filtradas.groupby('entidad').agg({
                'id': 'count',
                'dias_para_vencer': 'min'
            }).rename(columns={'id': 'licitaciones', 'dias_para_vencer': 'próximo_vencimiento_en_días'}).sort_values('próximo_vencimiento_en_días')
            
            st.dataframe(resumen_entidad, use_container_width=True)
            
        else:
            st.info("Aún no hay contratos firmados con fecha de fin registrada. Agrega licitaciones con estado 'Contrato Firmado' e ingresa las fechas de inicio y fin.")
    
    elif seccion == "🎯 Oportunidades":
        st.markdown("### 🎯 Oportunidades Estratégicas — Licitaciones")
        st.caption("Inteligencia comercial basada en tu base de datos: contratos por renovar, procesos activos y entidades clave.")

        if len(df) == 0:
            st.info("Aún no hay licitaciones registradas.")
        else:
            from datetime import datetime as dt
            hoy = pd.Timestamp(dt.now())

            con_fecha = df[(df['fin_contrato'].notna()) & (df['fin_contrato'] != '')].copy()
            if len(con_fecha) > 0:
                con_fecha['fin_dt'] = pd.to_datetime(con_fecha['fin_contrato'], errors='coerce')
                con_fecha = con_fecha.dropna(subset=['fin_dt'])
                con_fecha['dias'] = (con_fecha['fin_dt'] - hoy).dt.days

            activos     = df[df['estado'].isin(['Abierto para participar', 'Publicado'])]
            evaluacion  = df[df['estado'] == 'En Evaluación']
            por_vencer  = con_fecha[con_fecha['dias'].between(0, 90)] if len(con_fecha) > 0 else pd.DataFrame()
            ya_vencidos = con_fecha[con_fecha['dias'] < 0] if len(con_fecha) > 0 else pd.DataFrame()

            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("🟢 Participar ahora", len(activos))
            with col2:
                st.metric("🟡 En evaluación", len(evaluacion))
            with col3:
                st.metric("🔴 Por vencer ≤90d", len(por_vencer))
            with col4:
                st.metric("⚫ Contratos vencidos", len(ya_vencidos))
            with col5:
                monto_renovacion = por_vencer['monto_adjudicado'].sum() if len(por_vencer) > 0 else 0
                st.metric("💰 Monto en juego", f"S/ {monto_renovacion/1e6:.2f}M")

            st.markdown("---")

            st.markdown(f"#### 🟢 PARTICIPAR AHORA — Procesos Activos ({len(activos)})")
            if len(activos) > 0:
                for _, row in activos.sort_values('monto_base', ascending=False).iterrows():
                    pub = row.get('publicado', '') or ''
                    with st.container(border=True):
                        col_a, col_b = st.columns([4, 1])
                        with col_a:
                            st.markdown(f"**{row['entidad']}**")
                            st.caption(row.get('titulo', '')[:100])
                            st.caption(f"📋 {row['id']} · 🏛 {row.get('region','')} · 📅 Publicado: {pub} · Estado: **{row['estado']}**")
                        with col_b:
                            monto = row.get('monto_base', 0) or 0
                            st.metric("Monto Base", f"S/ {monto:,.0f}")
            else:
                st.info("No hay procesos activos registrados.")

            st.markdown("---")

            st.markdown(f"#### 🔴 RENOVACIONES PRÓXIMAS — Vencen en ≤90 días ({len(por_vencer)})")
            if len(por_vencer) > 0:
                for _, row in por_vencer.sort_values('dias').iterrows():
                    dias    = int(row['dias'])
                    adj     = row.get('monto_adjudicado', 0) or 0
                    ganador = row.get('ganador', 'N/D') or 'N/D'
                    urgencia = "🔴" if dias <= 30 else "🟠"
                    with st.container(border=True):
                        col_a, col_b = st.columns([4, 1])
                        with col_a:
                            st.markdown(f"**{row['entidad']}**")
                            st.caption(row.get('titulo', '')[:100])
                            st.caption(f"📋 {row['id']} · 🏆 Ganador actual: **{ganador[:60]}** · Vence: {row['fin_contrato']}")
                        with col_b:
                            st.metric(f"{urgencia} Días restantes", dias)
                            st.caption(f"S/ {adj:,.0f}")
            else:
                st.info("No hay contratos con vencimiento en los próximos 90 días.")

            st.markdown("---")

            st.markdown(f"#### ⚫ VENCIDOS — Nueva licitación probable ({len(ya_vencidos)})")
            if len(ya_vencidos) > 0:
                for _, row in ya_vencidos.sort_values('dias', ascending=False).iterrows():
                    dias_v  = abs(int(row['dias']))
                    ganador = row.get('ganador', 'N/D') or 'N/D'
                    adj     = row.get('monto_adjudicado', 0) or 0
                    with st.container(border=True):
                        col_a, col_b = st.columns([4, 1])
                        with col_a:
                            st.markdown(f"**{row['entidad']}**")
                            st.caption(row.get('titulo', '')[:100])
                            st.caption(f"📋 {row['id']} · Venció: {row['fin_contrato']} · 🏆 Ganador anterior: **{ganador[:60]}**")
                        with col_b:
                            st.metric("⏳ Hace", f"{dias_v}d")
                            st.caption(f"S/ {adj:,.0f}")
            else:
                st.info("No hay contratos vencidos registrados.")

            st.markdown("---")

            st.markdown("#### 🏛 ENTIDADES RECURRENTES — Mayor potencial de cuenta")
            resumen = df.groupby('entidad').agg(
                licitaciones    = ('id', 'count'),
                monto_total     = ('monto_adjudicado', 'sum'),
                contratos_firma = ('estado', lambda x: (x == 'Contrato Firmado').sum()),
                region          = ('region', 'first')
            ).sort_values('licitaciones', ascending=False)
            recurrentes = resumen[resumen['licitaciones'] > 1].copy()
            if len(recurrentes) > 0:
                recurrentes.columns = ['Licitaciones', 'Monto Total (S/)', 'Contratos Firmados', 'Región']
                recurrentes['Monto Total (S/)'] = recurrentes['Monto Total (S/)'].apply(lambda x: f"S/ {x:,.0f}")
                st.dataframe(recurrentes, use_container_width=True)
            else:
                st.info("Aún no hay entidades con más de una licitación registrada.")

    elif seccion == "💼 Competencia en Licitaciones":
        st.markdown("### 💼 Competencia en Licitaciones — Ranking de Postores")
        st.caption("Tabla consolidada de empresas que postulan a las licitaciones que registraste, con historial y tasa de éxito reportada por SEACE.")
        
        if len(df) == 0 or 'postores' not in df.columns or df['postores'].apply(lambda x: isinstance(x, list) and len(x) > 0).sum() == 0:
            st.info("Aún no hay licitaciones con lista de postores registrada. Usa la pestaña '📄 Pegar texto SEACE' en Base de Datos — el parser extrae automáticamente la lista completa de postores cuando está presente en el texto copiado.")
        else:
            filas_postores = []
            for _, row in df.iterrows():
                postores_lic = row.get('postores', [])
                if isinstance(postores_lic, list):
                    for p in postores_lic:
                        filas_postores.append({
                            'empresa': p.get('empresa', 'Desconocido'),
                            'licitacion_id': row['id'],
                            'entidad': row['entidad'],
                            'es_ganador': p.get('es_ganador', False),
                            'licitaciones_previas': p.get('licitaciones_previas', 0),
                            'ganadas_previas': p.get('ganadas_previas', 0),
                            'tasa_exito': p.get('tasa_exito', 0.0),
                            'monto_historico': p.get('monto_historico', 0)
                        })
            
            df_postores = pd.DataFrame(filas_postores)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Empresas únicas detectadas", df_postores['empresa'].nunique())
            with col2:
                st.metric("Licitaciones con postores", df_postores['licitacion_id'].nunique())
            with col3:
                ganadores_unicos = df_postores[df_postores['es_ganador']]['empresa'].nunique()
                st.metric("Empresas que ganaron al menos una vez", ganadores_unicos)
            
            st.markdown("---")
            
            ranking = df_postores.groupby('empresa').agg(
                veces_postulo_en_tu_base=('licitacion_id', 'count'),
                veces_gano_en_tu_base=('es_ganador', 'sum'),
                tasa_exito_historica_seace=('tasa_exito', 'max'),
                licitaciones_previas_seace=('licitaciones_previas', 'max')
            ).reset_index().sort_values('veces_gano_en_tu_base', ascending=False)
            
            st.markdown("#### 🏆 Ranking de Competidores")
            fig = px.bar(ranking.head(15), x='empresa', y='veces_postulo_en_tu_base',
                        title="Frecuencia de Participación (en licitaciones que registraste)",
                        labels={'veces_postulo_en_tu_base': 'Veces que postuló', 'empresa': 'Empresa'},
                        color_discrete_sequence=['#5B6FFF'])
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            
            for _, row in ranking.iterrows():
                titulo_exp = f"**{row['empresa']}** — postuló {int(row['veces_postulo_en_tu_base'])}x, ganó {int(row['veces_gano_en_tu_base'])}x en tu base"
                with st.expander(titulo_exp):
                    st.caption(f"📊 Tasa de éxito histórica reportada por SEACE: {row['tasa_exito_historica_seace']:.1f}% (sobre {int(row['licitaciones_previas_seace'])} licitaciones totales en su historial)")
                    detalle_empresa = df_postores[df_postores['empresa'] == row['empresa']][['licitacion_id', 'entidad', 'es_ganador']]
                    st.dataframe(detalle_empresa, use_container_width=True)
    
    elif seccion == "📝 Generador de Documentos":
        st.markdown("### 📝 Generador de Documentos — Licitaciones Públicas")
        st.caption("Genera documentos formales adaptados a la normativa OSCE y Ley 30225 para procesos >8 UIT.")

        if len(df) == 0:
            st.info("Agrega licitaciones primero para usar el generador.")
        else:
            tipo_doc = st.selectbox("Tipo de documento:", [
                "Carta de Presentación (entidad pública)",
                "Carta de Consulta / Observación a Bases",
                "Solicitud de Ampliación de Plazo",
                "Checklist de Postulación",
                "Resumen Ejecutivo de Licitación",
            ])

            licitacion_id = st.selectbox("Licitación de referencia:", df['id'].unique())
            lic_row = df[df['id'] == licitacion_id].iloc[0]
            entidad   = lic_row.get('entidad', '[ENTIDAD]')
            titulo    = lic_row.get('titulo', '[OBJETO DE CONTRATACIÓN]')
            region    = lic_row.get('region', '[REGIÓN]')
            tipo_lic  = lic_row.get('tipo_licitacion', '[TIPO DE PROCEDIMIENTO]')
            monto_b   = lic_row.get('monto_base', 0) or 0
            publicado = lic_row.get('publicado', '[FECHA]')
            ganador   = lic_row.get('ganador', '') or ''
            duracion  = lic_row.get('duracion_dias', '') or ''
            fin_c     = lic_row.get('fin_contrato', '') or ''
            fecha_hoy = datetime.now().strftime('%d de %B de %Y')

            st.markdown("---")

            if tipo_doc == "Carta de Presentación (entidad pública)":
                nombre_funcionario = st.text_input("Nombre del funcionario:", "[NOMBRE DEL FUNCIONARIO]")
                cargo_funcionario  = st.text_input("Cargo:", "[CARGO DEL FUNCIONARIO]")
                asunto_extra       = st.text_input("Complemento del asunto:", "para la atención del proceso de selección")

                contenido = f"""QUBITS SAC
RUC: 20603475604
Av. [COMPLETAR DIRECCIÓN] — Lima, Perú

Lima, {fecha_hoy}

Señor(a)
{nombre_funcionario}
{cargo_funcionario}
{entidad}
{region}

Asunto: Presentación de Propuesta Técnica y Comercial — {licitacion_id}

Estimado(a) {nombre_funcionario},

En atención al proceso de selección denominado "{titulo}" (Ref. {licitacion_id}), convocado mediante {tipo_lic} por {entidad}, y {asunto_extra}, Qubits SAC, empresa peruana especializada en infraestructura tecnológica y servicios en la nube, se permite presentar ante su Despacho su propuesta técnica y comercial.

Qubits SAC cuenta con amplia experiencia en el diseño, implementación y soporte de soluciones de tecnología de la información para entidades del sector público, en estricto cumplimiento de la normativa vigente, incluyendo el Decreto Legislativo N.° 1412 — Ley de Gobierno Digital, la Resolución Ministerial N.° 004-2016-PCM, y las Bases Estándar emitidas por el Organismo Supervisor de las Contrataciones del Estado (OSCE).

En tal sentido, adjuntamos a la presente nuestra propuesta técnica y económica, la cual ha sido elaborada en función de los requerimientos establecidos en los Términos de Referencia del proceso antes indicado.

Quedamos a su entera disposición para cualquier consulta o coordinación adicional que su Despacho estime conveniente.

Atentamente,

_______________________________
[NOMBRE DEL REPRESENTANTE]
[CARGO]
Qubits SAC
RUC: 20603475604
[TELÉFONO] | [EMAIL]
Lima, Perú"""

            elif tipo_doc == "Carta de Consulta / Observación a Bases":
                num_consulta  = st.text_input("N.° de consulta/observación:", "001")
                punto_bases   = st.text_input("Punto de las Bases que se consulta:", "Capítulo III, Numeral 3.1")
                texto_consulta = st.text_area("Texto de la consulta u observación:", "Sírvase precisar si el requisito de...")

                contenido = f"""QUBITS SAC
RUC: 20603475604
Lima, {fecha_hoy}

Señores
Comité de Selección
{entidad}
{region}

Asunto: Presentación de Consultas y Observaciones a las Bases — {licitacion_id}
Referencia: {tipo_lic} — "{titulo}"

De nuestra consideración:

Por medio de la presente, Qubits SAC, identificada con RUC N.° 20603475604, en calidad de participante en el proceso de selección de la referencia, y de conformidad con lo dispuesto en el artículo 52° del Reglamento de la Ley de Contrataciones del Estado (D.S. N.° 344-2018-EF), presenta la siguiente consulta/observación:

CONSULTA / OBSERVACIÓN N.° {num_consulta}

Punto de las Bases: {punto_bases}

Texto:
{texto_consulta}

Sustento:
En concordancia con lo establecido en las Bases Estándar aprobadas por OSCE y los principios de transparencia y trato igualitario que rigen las contrataciones públicas, solicitamos respetuosamente que el Comité de Selección absuelva la presente consulta en el plazo previsto en el cronograma del proceso.

Atentamente,

_______________________________
[NOMBRE DEL REPRESENTANTE LEGAL]
[CARGO]
Qubits SAC — RUC: 20603475604"""

            elif tipo_doc == "Solicitud de Ampliación de Plazo":
                motivo = st.text_area("Motivo de la solicitud:", "Debido a la complejidad técnica de los requerimientos establecidos en los Términos de Referencia...")
                dias_solicitados = st.number_input("Días adicionales solicitados:", min_value=1, value=5)

                contenido = f"""QUBITS SAC
RUC: 20603475604
Lima, {fecha_hoy}

Señores
Comité de Selección
{entidad}
{region}

Asunto: Solicitud de Ampliación de Plazo para Presentación de Propuestas — {licitacion_id}

De nuestra consideración:

Qubits SAC, con RUC N.° 20603475604, participante en el proceso de selección "{titulo}" ({licitacion_id}), convocado por {entidad}, respetuosamente solicita la ampliación del plazo de presentación de propuestas por {dias_solicitados} días hábiles adicionales, por las siguientes razones:

{motivo}

En tal sentido, y de conformidad con el principio de eficiencia que rige las contrataciones del Estado, solicitamos que el Comité de Selección evalúe la viabilidad de la presente solicitud, a fin de garantizar la participación de postores calificados y condiciones de competencia adecuadas para el proceso.

Atentamente,

_______________________________
[NOMBRE DEL REPRESENTANTE LEGAL]
Qubits SAC — RUC: 20603475604"""

            elif tipo_doc == "Checklist de Postulación":
                contenido = f"""CHECKLIST DE POSTULACIÓN
═══════════════════════════════════════════════════════════
Proceso  : {licitacion_id}
Entidad  : {entidad}
Objeto   : {titulo}
Tipo     : {tipo_lic}
Monto Base: S/ {monto_b:,.2f}
Publicado: {publicado}
Fecha    : {fecha_hoy}
═══════════════════════════════════════════════════════════

DOCUMENTOS DE HABILITACIÓN
[ ] Copia de RUC vigente y en estado activo
[ ] Copia del DNI del Representante Legal
[ ] Copia de la Vigencia de Poder (no mayor a 30 días)
[ ] Declaración Jurada de datos del postor (Anexo N.° 1)
[ ] Declaración Jurada de cumplimiento de requisitos (Anexo N.° 2)
[ ] Declaración Jurada de ausencia de impedimentos (Anexo N.° 3)
[ ] Declaración Jurada de plazo de entrega (Anexo N.° 4)

DOCUMENTOS TÉCNICOS
[ ] Propuesta Técnica desarrollada conforme a los TdR
[ ] Metodología de implementación / Plan de trabajo
[ ] Cronograma de actividades
[ ] Currículum del equipo técnico propuesto
[ ] Certificaciones técnicas del equipo (según TdR)
[ ] Fichas técnicas de equipos / soluciones ofertadas
[ ] Experiencia del postor — contratos o conformidades previas
[ ] Carta de autorización del fabricante / marca (si aplica)

DOCUMENTOS ECONÓMICOS
[ ] Propuesta Económica en sobre cerrado (monto en letras y números)
[ ] Estructura de costos (si lo solicitan las Bases)
[ ] Garantía de seriedad de oferta (si aplica según monto)

VERIFICACIONES FINALES
[ ] Verificar cronograma en SEACE antes de presentar
[ ] Foliar y rubricar todos los documentos
[ ] Presentar en el orden indicado en las Bases
[ ] Verificar que el monto ofertado no excede el valor referencial
[ ] Confirmar lugar, fecha y hora exacta de presentación

NOTAS:
• Revisar las Bases Integradas (después de absolución de consultas)
• Verificar requisitos de calificación y factores de evaluación
• Confirmar si se requiere presentación física o por SEACE electrónico
═══════════════════════════════════════════════════════════"""

            elif tipo_doc == "Resumen Ejecutivo de Licitación":
                postores_n  = lic_row.get('empresas_participantes', 0) or 0
                monto_adj   = lic_row.get('monto_adjudicado', 0) or 0
                adj_fecha   = lic_row.get('adjudicacion', '') or ''
                inicio_c    = lic_row.get('inicio_contrato', '') or ''
                tipo_contr  = lic_row.get('tipo_contratacion', '') or ''

                contenido = f"""RESUMEN EJECUTIVO DE LICITACIÓN
═══════════════════════════════════════════════════════════
Generado por Qubits KAM Intelligence v2
Fecha de reporte: {fecha_hoy}
═══════════════════════════════════════════════════════════

IDENTIFICACIÓN DEL PROCESO
───────────────────────────────────────────────────────────
ID SEACE       : {licitacion_id}
Tipo           : {tipo_lic}
Objeto contrat.: {tipo_contr}
Entidad        : {entidad}
Región         : {region}
Publicación    : {publicado}

OBJETO DE CONTRATACIÓN
───────────────────────────────────────────────────────────
{titulo}

DATOS ECONÓMICOS
───────────────────────────────────────────────────────────
Valor Referencial / Monto Base : S/ {monto_b:,.2f}
Monto Adjudicado               : S/ {monto_adj:,.2f}
{'Ahorro sobre referencial      : S/ ' + f'{(monto_b - monto_adj):,.2f} ({((monto_b-monto_adj)/monto_b*100):.1f}%)' if monto_b > 0 and monto_adj > 0 else ''}

RESULTADO
───────────────────────────────────────────────────────────
Estado         : {lic_row.get('estado', '')}
Ganador        : {ganador if ganador else 'Por definir'}
Fecha adjudic. : {adj_fecha}
Postores       : {postores_n}

CONTRATO
───────────────────────────────────────────────────────────
Inicio         : {inicio_c}
Fin            : {fin_c}
Duración       : {str(duracion) + ' días' if duracion else 'Por definir'}

ANÁLISIS COMPETITIVO
───────────────────────────────────────────────────────────
{'Ganador identificado: ' + ganador if ganador else 'Sin ganador registrado aún'}
Postores participantes: {postores_n}
{'Posición de Qubits: [COMPLETAR según resultado]' if ganador else 'Proceso aún activo — oportunidad abierta'}

NORMATIVA APLICABLE
───────────────────────────────────────────────────────────
• Ley N.° 30225 — Ley de Contrataciones del Estado
• D.S. N.° 344-2018-EF — Reglamento de la Ley
• D. Leg. N.° 1412 — Ley de Gobierno Digital
• RM N.° 004-2016-PCM — NTP-ISO/IEC 27001 (si aplica TI)
═══════════════════════════════════════════════════════════"""

            # Mostrar y descargar
            st.markdown("#### 📄 Documento generado")
            st.text_area("", contenido, height=450, label_visibility="collapsed")
            st.download_button(
                label="📥 Descargar como .txt",
                data=contenido.encode('utf-8'),
                file_name=f"{tipo_doc.split('(')[0].strip().replace(' ','_')}_{licitacion_id}_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain"
            )

    elif seccion == "💼 Generador de Propuestas":
        st.markdown("### 💼 Generador de Propuestas Técnicas — Licitaciones")
        st.caption("Genera propuestas técnicas estructuradas según la normativa OSCE para procesos >8 UIT.")

        if len(df) == 0:
            st.info("Agrega licitaciones primero para usar el generador.")
        else:
            licitacion_id = st.selectbox("Selecciona la licitación:", df['id'].unique(), key="gen_prop_lic_id")
            lic_row = df[df['id'] == licitacion_id].iloc[0]

            entidad   = lic_row.get('entidad', '[ENTIDAD]')
            titulo    = lic_row.get('titulo', '[OBJETO]')
            region    = lic_row.get('region', '[REGIÓN]')
            tipo_lic  = lic_row.get('tipo_licitacion', '[TIPO]')
            tipo_contr= lic_row.get('tipo_contratacion', 'Servicio')
            monto_b   = lic_row.get('monto_base', 0) or 0
            duracion_raw = lic_row.get('duracion_dias', 0)
            try:
                duracion = int(duracion_raw) if duracion_raw and str(duracion_raw) not in ('', 'nan', 'None', '0') else 0
            except (ValueError, TypeError):
                duracion = 0
            publicado = lic_row.get('publicado', '') or ''
            fecha_hoy = datetime.now().strftime('%d de %B de %Y')

            st.markdown("---")
            st.markdown("#### ⚙️ Personaliza la propuesta")

            col1, col2 = st.columns(2)
            with col1:
                nombre_rep    = st.text_input("Representante Legal de Qubits:", "[NOMBRE REPRESENTANTE LEGAL]")
                cargo_rep     = st.text_input("Cargo:", "Gerente General")
                descripcion_sol = st.text_area("Descripción breve de la solución propuesta:",
                    f"Solución integral de {tipo_contr.lower()} en la nube con alta disponibilidad, seguridad y cumplimiento normativo.", height=80)
            with col2:
                plazo_impl    = st.text_input("Plazo de implementación:", f"{duracion} días" if duracion else "30 días calendario")
                sla_disp      = st.text_input("SLA de disponibilidad ofrecido:", "99.9%")
                experiencia   = st.text_area("Experiencia relevante (contratos similares):",
                    "Implementación de servicios cloud para [ENTIDAD] — [AÑO]\nImplementación de [SOLUCIÓN] para [ENTIDAD 2] — [AÑO]", height=80)

            if st.button("🚀 Generar Propuesta Técnica", type="primary"):
                propuesta = f"""═══════════════════════════════════════════════════════════════
PROPUESTA TÉCNICA Y ECONÓMICA
QUBITS SAC — RUC: 20603475604
═══════════════════════════════════════════════════════════════

PROCESO  : {licitacion_id}
TIPO     : {tipo_lic}
ENTIDAD  : {entidad}
REGIÓN   : {region}
FECHA    : {fecha_hoy}
VIGENCIA : 60 días calendario desde la presentación

═══════════════════════════════════════════════════════════════
1. CARTA DE PRESENTACIÓN
═══════════════════════════════════════════════════════════════

Lima, {fecha_hoy}

Señores
Comité de Selección — {tipo_lic}
{entidad}
{region}

Estimados señores:

Qubits SAC, empresa peruana especializada en infraestructura tecnológica y servicios en la nube, se complace en presentar su Propuesta Técnica y Económica para el proceso "{titulo}" (Ref. {licitacion_id}), en estricta observancia de las Bases del proceso y la normativa vigente en materia de contrataciones del Estado.

═══════════════════════════════════════════════════════════════
2. DATOS GENERALES DE LA EMPRESA
═══════════════════════════════════════════════════════════════

Razón Social  : Qubits SAC
RUC           : 20603475604
Domicilio     : [COMPLETAR DIRECCIÓN]
Representante : {nombre_rep}
Cargo         : {cargo_rep}
Teléfono      : [COMPLETAR]
Email         : [COMPLETAR]
Web           : [COMPLETAR]

═══════════════════════════════════════════════════════════════
3. ENTENDIMIENTO DE LOS REQUERIMIENTOS
═══════════════════════════════════════════════════════════════

3.1 OBJETO DE CONTRATACIÓN
{titulo}

3.2 ANÁLISIS DEL REQUERIMIENTO
{entidad}, con sede en {region}, requiere una solución de {tipo_contr.lower()} que cumpla con los estándares técnicos y normativos establecidos en los Términos de Referencia del proceso {licitacion_id}.

Valor Referencial : S/ {monto_b:,.2f}
Plazo de ejecución: {str(duracion) + ' días' if duracion else '[SEGÚN TDR]'}
Publicación SEACE : {publicado}

3.3 COMPRENSIÓN DE LAS NECESIDADES
Qubits SAC ha analizado en detalle los Términos de Referencia y comprende que {entidad} requiere:
• [COMPLETAR SEGÚN TDR — Requisito 1]
• [COMPLETAR SEGÚN TDR — Requisito 2]
• [COMPLETAR SEGÚN TDR — Requisito 3]

═══════════════════════════════════════════════════════════════
4. PROPUESTA TÉCNICA
═══════════════════════════════════════════════════════════════

4.1 DESCRIPCIÓN DE LA SOLUCIÓN
{descripcion_sol}

4.2 ARQUITECTURA TÉCNICA
✓ Infraestructura cloud de alta disponibilidad ({sla_disp} SLA)
✓ Respaldo automático y redundancia geográfica
✓ Cifrado de datos en tránsito y en reposo (AES-256)
✓ Cumplimiento normativo: ISO/IEC 27001, RM 004-2016-PCM, D. Leg. 1412
✓ Monitoreo proactivo 24/7 con alertas automáticas
✓ Panel de administración centralizado
✓ Soporte técnico especializado en español

4.3 ESPECIFICACIONES TÉCNICAS
[COMPLETAR CON ESPECIFICACIONES SEGÚN TDR]
• Capacidad / dimensionamiento: [COMPLETAR]
• Tecnología / plataforma: [COMPLETAR]
• Integraciones requeridas: [COMPLETAR]
• Requisitos de seguridad: [COMPLETAR]

4.4 NIVELES DE SERVICIO (SLA)
• Disponibilidad garantizada : {sla_disp}
• Tiempo de respuesta incidentes críticos : < 2 horas
• Tiempo de respuesta incidentes mayores  : < 4 horas
• Tiempo de respuesta incidentes menores  : < 8 horas
• RTO (Recovery Time Objective)           : < 4 horas
• RPO (Recovery Point Objective)          : < 1 hora

═══════════════════════════════════════════════════════════════
5. METODOLOGÍA DE IMPLEMENTACIÓN Y CRONOGRAMA
═══════════════════════════════════════════════════════════════

5.1 METODOLOGÍA
Qubits SAC aplicará una metodología estructurada en fases:

FASE 1 — INICIO Y PLANIFICACIÓN (Semana 1)
• Firma del contrato y designación de equipo
• Reunión de kick-off con {entidad}
• Levantamiento de información detallado
• Aprobación del Plan de Implementación

FASE 2 — IMPLEMENTACIÓN (Semanas 2-{max(3, duracion//7 - 1) if duracion > 0 else "N"})
• Configuración del entorno
• Migración / integración de datos
• Pruebas de funcionamiento
• Capacitación al personal de {entidad}

FASE 3 — PUESTA EN PRODUCCIÓN (Semana final)
• Go-live supervisado
• Pruebas de aceptación (UAT)
• Entrega de documentación técnica
• Inicio del período de soporte

Plazo total de implementación: {plazo_impl}

═══════════════════════════════════════════════════════════════
6. EQUIPO TÉCNICO PROPUESTO
═══════════════════════════════════════════════════════════════

• [NOMBRE] — Jefe de Proyecto | [CERTIFICACIÓN] | [AÑOS] años de exp.
• [NOMBRE] — Arquitecto de Soluciones | [CERTIFICACIÓN] | [AÑOS] años de exp.
• [NOMBRE] — Ingeniero de Implementación | [CERTIFICACIÓN] | [AÑOS] años de exp.
• [NOMBRE] — Especialista en Seguridad | [CERTIFICACIÓN] | [AÑOS] años de exp.

═══════════════════════════════════════════════════════════════
7. EXPERIENCIA Y REFERENCIAS
═══════════════════════════════════════════════════════════════

{experiencia}

[ADJUNTAR: Órdenes de servicio, contratos o conformidades que acrediten la experiencia]

═══════════════════════════════════════════════════════════════
8. CUMPLIMIENTO NORMATIVO
═══════════════════════════════════════════════════════════════

Qubits SAC declara que la presente propuesta cumple con:

✓ Ley N.° 30225 — Ley de Contrataciones del Estado y modificatorias
✓ D.S. N.° 344-2018-EF — Reglamento de la Ley de Contrataciones
✓ Decreto Legislativo N.° 1412 — Ley de Gobierno Digital
✓ D.S. N.° 029-2021-PCM — Reglamento del D. Leg. de Gobierno Digital
✓ RM N.° 004-2016-PCM — NTP-ISO/IEC 27001 (para soluciones TI)
✓ Ley N.° 29733 — Protección de Datos Personales (si aplica)
✓ Bases Estándar OSCE vigentes para {tipo_contr}s

═══════════════════════════════════════════════════════════════
9. PROPUESTA ECONÓMICA
═══════════════════════════════════════════════════════════════

Monto Ofertado (sin IGV) : S/ [COMPLETAR]
IGV (18%)                : S/ [COMPLETAR]
PRECIO TOTAL             : S/ [COMPLETAR]

En letras: [COMPLETAR EN LETRAS]

Nota: El precio incluye todos los costos de implementación, licencias,
soporte y cualquier otro costo asociado al cumplimiento de los TdR.
El precio es fijo y no está sujeto a reajuste.

═══════════════════════════════════════════════════════════════
10. DECLARACIÓN JURADA
═══════════════════════════════════════════════════════════════

Yo, {nombre_rep}, identificado con DNI N.° [COMPLETAR], en calidad de
{cargo_rep} de Qubits SAC (RUC: 20603475604), declaro bajo juramento que:

1. La información consignada en la presente propuesta es veraz.
2. La empresa no se encuentra impedida de contratar con el Estado.
3. La empresa no tiene sanción vigente impuesta por el Tribunal de OSCE.
4. Me comprometo a cumplir con todas las condiciones establecidas en las
   Bases del proceso {licitacion_id}.

Firma: _______________________________
{nombre_rep}
{cargo_rep} — Qubits SAC
Lima, {fecha_hoy}
═══════════════════════════════════════════════════════════════
Documento generado por Qubits KAM Intelligence v2
Revisar y completar los campos marcados con [COMPLETAR] antes de presentar.
═══════════════════════════════════════════════════════════════"""

                st.session_state['propuesta_generada'] = propuesta
                st.session_state['propuesta_lid']      = licitacion_id

            if 'propuesta_generada' in st.session_state and st.session_state.get('propuesta_lid') == licitacion_id:
                st.markdown("#### 📄 Propuesta Técnica generada")
                st.text_area("", st.session_state['propuesta_generada'], height=500, label_visibility="collapsed")
                st.download_button(
                    label="📥 Descargar Propuesta (.txt)",
                    data=st.session_state['propuesta_generada'].encode('utf-8'),
                    file_name=f"Propuesta_Tecnica_{licitacion_id}_{datetime.now().strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    key="dl_propuesta"
                )
                st.info("💡 Tip: Descarga el .txt y ábrelo en Word para darle formato final antes de presentar.")

    elif seccion == "🤖 Inteligencia Artificial":
        st.markdown("### 🤖 Inteligencia Artificial — Análisis de Oportunidades")
        st.caption("Motor de IA basado en Claude (Anthropic) para análisis estratégico de licitaciones públicas peruanas.")

        # ── Configuración de API Key ──────────────────────────────────────────
        with st.expander("⚙️ Configuración de API Key", expanded='api_key_lic' not in st.session_state):
            api_key_input = st.text_input(
                "Anthropic API Key:",
                type="password",
                value=st.session_state.get('api_key_lic', ''),
                help="Obtén tu API Key en https://console.anthropic.com — se guarda solo en esta sesión, nunca en disco."
            )
            if st.button("💾 Guardar API Key", key="save_api_key_lic"):
                if api_key_input.startswith("sk-ant-"):
                    st.session_state['api_key_lic'] = api_key_input
                    st.success("✅ API Key guardada para esta sesión.")
                    st.rerun()
                else:
                    st.error("❌ La API Key debe comenzar con 'sk-ant-'")

        if 'api_key_lic' not in st.session_state:
            st.info("👆 Ingresa tu Anthropic API Key para activar el módulo de IA.")
            st.stop()

        API_KEY = st.session_state['api_key_lic']

        # ── Función de llamada a Claude ───────────────────────────────────────
        def consultar_claude(sistema, usuario, max_tokens=2000):
            import urllib.request
            import urllib.error
            payload = json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": max_tokens,
                "system": sistema,
                "messages": [{"role": "user", "content": usuario}]
            }).encode('utf-8')
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    return data['content'][0]['text'], None
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8')
                return None, f"Error HTTP {e.code}: {body}"
            except Exception as e:
                return None, str(e)

        SISTEMA_KAM = """Eres un experto en contrataciones públicas peruanas y estrategia comercial B2B para el sector TI. 
Conoces en profundidad la Ley N.° 30225, el D.S. N.° 344-2018-EF, las directivas de OSCE, y el ecosistema 
de licitaciones en Perú (SEACE). Trabajas para Qubits SAC, empresa de infraestructura tecnológica y 
servicios en la nube. Eres el asesor estratégico de Alex Cerna, Key Account Manager Senior.
Responde siempre en español formal peruano, con análisis concreto y accionable. 
Usa datos exactos cuando los tengas. Sé directo y práctico."""

        if len(df) == 0:
            st.info("Agrega licitaciones primero para usar el análisis de IA.")
        else:
            tab1, tab2, tab3, tab4 = st.tabs([
                "🎯 Análisis de Licitación",
                "📊 Inteligencia de Mercado",
                "🏆 Estrategia Competitiva",
                "💬 Consulta Libre"
            ])

            # ── TAB 1: Análisis individual de licitación ──────────────────────
            with tab1:
                st.markdown("#### 🎯 Análisis Estratégico de Licitación")
                st.caption("IA analiza una licitación específica y te dice si debes postular, cómo ganarla y qué riesgos enfrentas.")

                lic_sel = st.selectbox("Selecciona la licitación a analizar:", df['id'].unique(), key="ia_lic_sel")
                lic_row = df[df['id'] == lic_sel].iloc[0]

                # Construir contexto rico para Claude
                postores = lic_row.get('postores', [])
                if not isinstance(postores, list): postores = []
                postores_txt = "\n".join([
                    f"  - {p.get('empresa','?')} | {p.get('licitaciones_previas',0)} licitaciones previas | {p.get('ganadas_previas',0)} ganadas | Tasa: {p.get('tasa_exito',0):.1f}% {'★ GANADOR' if p.get('es_ganador') else ''}"
                    for p in postores
                ]) if postores else "  Sin postores registrados"

                contexto_lic = f"""
LICITACIÓN A ANALIZAR:
- ID SEACE: {lic_sel}
- Título: {lic_row.get('titulo', '')}
- Entidad: {lic_row.get('entidad', '')}
- Región: {lic_row.get('region', '')}
- Tipo: {lic_row.get('tipo_licitacion', '')} | {lic_row.get('tipo_contratacion', '')}
- Estado: {lic_row.get('estado', '')}
- Monto Base: S/ {lic_row.get('monto_base', 0):,.2f}
- Monto Adjudicado: S/ {lic_row.get('monto_adjudicado', 0):,.2f}
- Ganador: {lic_row.get('ganador', 'Sin definir')}
- Publicado: {lic_row.get('publicado', '')}
- Inicio Contrato: {lic_row.get('inicio_contrato', '')}
- Fin Contrato: {lic_row.get('fin_contrato', '')}
- Duración: {lic_row.get('duracion_dias', '')} días
- N.° Postores: {len(postores)}

POSTORES REGISTRADOS:
{postores_txt}

EMPRESA QUE ANALIZA: Qubits SAC (infraestructura TI y nube, sector público peruano)
"""
                tipo_analisis = st.selectbox("Tipo de análisis:", [
                    "Análisis completo (Go/No-Go + estrategia + riesgos)",
                    "Probabilidad de ganar y factores clave",
                    "Análisis de la competencia en este proceso",
                    "Estrategia de precio y posicionamiento",
                    "Riesgos técnicos y contractuales",
                    "Próximos pasos accionables"
                ], key="ia_tipo_analisis")

                prompts_analisis = {
                    "Análisis completo (Go/No-Go + estrategia + riesgos)": f"""Analiza esta licitación para Qubits SAC y proporciona:
1. DECISIÓN GO/NO-GO con justificación (3-5 razones concretas)
2. PROBABILIDAD DE GANAR (%) con sustento basado en los datos
3. ESTRATEGIA RECOMENDADA para ganar este proceso
4. TOP 3 RIESGOS a mitigar
5. PRÓXIMOS PASOS inmediatos (esta semana)
{contexto_lic}""",
                    "Probabilidad de ganar y factores clave": f"""Calcula la probabilidad de que Qubits SAC gane esta licitación.
Considera: competencia registrada, monto base, tipo de proceso, estado actual.
Da un porcentaje justificado y los 3 factores que más influyen.
{contexto_lic}""",
                    "Análisis de la competencia en este proceso": f"""Analiza el perfil competitivo de los postores registrados en esta licitación.
Identifica: quién es el rival más peligroso, sus fortalezas/debilidades, y cómo diferenciarse.
{contexto_lic}""",
                    "Estrategia de precio y posicionamiento": f"""Recomienda la estrategia de precio y posicionamiento para que Qubits SAC gane.
Considera el valor referencial, el monto adjudicado (si hay), y el perfil de la entidad.
{contexto_lic}""",
                    "Riesgos técnicos y contractuales": f"""Identifica los principales riesgos técnicos y contractuales de este proceso para Qubits SAC.
Considera el tipo de servicio, la entidad, la duración y el marco normativo OSCE.
{contexto_lic}""",
                    "Próximos pasos accionables": f"""Dame un plan de acción inmediato para esta licitación.
¿Qué debe hacer Qubits SAC esta semana, este mes, antes de presentar propuesta?
{contexto_lic}"""
                }

                if st.button("🚀 Analizar con IA", key="btn_analisis_lic", type="primary"):
                    with st.spinner("🤖 Claude está analizando la licitación..."):
                        respuesta, error = consultar_claude(SISTEMA_KAM, prompts_analisis[tipo_analisis])
                    if error:
                        st.error(f"❌ Error: {error}")
                    else:
                        st.session_state[f'ia_resp_lic_{lic_sel}'] = respuesta

                if f'ia_resp_lic_{lic_sel}' in st.session_state:
                    st.markdown("---")
                    st.markdown("#### 📋 Análisis de Claude")
                    st.markdown(st.session_state[f'ia_resp_lic_{lic_sel}'])
                    st.download_button(
                        "📥 Descargar análisis",
                        data=st.session_state[f'ia_resp_lic_{lic_sel}'].encode('utf-8'),
                        file_name=f"Analisis_IA_{lic_sel}_{datetime.now().strftime('%Y%m%d')}.txt",
                        mime="text/plain", key=f"dl_ia_{lic_sel}"
                    )

            # ── TAB 2: Inteligencia de mercado ────────────────────────────────
            with tab2:
                st.markdown("#### 📊 Inteligencia de Mercado")
                st.caption("IA analiza toda tu base de licitaciones y extrae patrones, tendencias y oportunidades del mercado.")

                # Preparar resumen ejecutivo del portafolio
                total        = len(df)
                firmados     = len(df[df['estado'] == 'Contrato Firmado'])
                monto_base   = df['monto_base'].sum()
                monto_adj    = df['monto_adjudicado'].sum()
                top_entidades = df.groupby('entidad')['id'].count().sort_values(ascending=False).head(5)
                top_tipos     = df['tipo_licitacion'].value_counts().head(5)
                regiones      = df['region'].value_counts().head(5)
                activos       = len(df[df['estado'].isin(['Publicado', 'Abierto para participar'])])

                resumen_portafolio = f"""
PORTAFOLIO DE LICITACIONES DE QUBITS SAC:
- Total licitaciones registradas: {total}
- Contratos Firmados: {firmados} ({firmados/total*100:.1f}% tasa de cierre)
- Procesos activos (participar ahora): {activos}
- Monto Base Total: S/ {monto_base:,.0f}
- Monto Adjudicado Total: S/ {monto_adj:,.0f}

TOP 5 ENTIDADES (por frecuencia):
{chr(10).join([f'  - {e}: {n} licitaciones' for e, n in top_entidades.items()])}

TIPOS DE PROCESO MÁS FRECUENTES:
{chr(10).join([f'  - {t}: {n}' for t, n in top_tipos.items()])}

DISTRIBUCIÓN REGIONAL:
{chr(10).join([f'  - {r}: {n}' for r, n in regiones.items()])}
"""

                tipo_intel = st.selectbox("¿Qué quieres analizar?", [
                    "Oportunidades de mayor potencial en mi base",
                    "Patrones de adjudicación y factores de éxito",
                    "Entidades con mayor potencial de cuenta",
                    "Tendencias del mercado TI en licitaciones públicas peruanas",
                    "Recomendaciones para aumentar la tasa de cierre",
                ], key="ia_tipo_intel")

                prompts_intel = {
                    "Oportunidades de mayor potencial en mi base": f"Analiza este portafolio e identifica las 3-5 oportunidades de mayor potencial para Qubits SAC. Justifica con datos.\n{resumen_portafolio}",
                    "Patrones de adjudicación y factores de éxito": f"¿Qué patrones ves en este portafolio? ¿Qué factores explican los contratos firmados? ¿Qué debería replicar Qubits?\n{resumen_portafolio}",
                    "Entidades con mayor potencial de cuenta": f"De las entidades en este portafolio, ¿cuáles tienen mayor potencial para convertirse en cuentas clave de largo plazo? ¿Por qué?\n{resumen_portafolio}",
                    "Tendencias del mercado TI en licitaciones públicas peruanas": f"Basándote en este portafolio y tu conocimiento del mercado público peruano de TI, ¿qué tendencias ves? ¿Qué servicios tienen más demanda?\n{resumen_portafolio}",
                    "Recomendaciones para aumentar la tasa de cierre": f"La tasa de cierre actual es {firmados/total*100:.1f}%. ¿Cómo podría Qubits SAC mejorarla? Dame recomendaciones concretas basadas en el portafolio.\n{resumen_portafolio}",
                }

                if st.button("🚀 Analizar Mercado con IA", key="btn_intel_mercado", type="primary"):
                    with st.spinner("🤖 Claude está analizando tu portafolio..."):
                        respuesta, error = consultar_claude(SISTEMA_KAM, prompts_intel[tipo_intel])
                    if error:
                        st.error(f"❌ Error: {error}")
                    else:
                        st.session_state['ia_resp_intel'] = respuesta
                        st.session_state['ia_resp_intel_tipo'] = tipo_intel

                if 'ia_resp_intel' in st.session_state:
                    st.markdown("---")
                    st.markdown(f"#### 📋 {st.session_state.get('ia_resp_intel_tipo', 'Análisis')}")
                    st.markdown(st.session_state['ia_resp_intel'])
                    st.download_button(
                        "📥 Descargar análisis",
                        data=st.session_state['ia_resp_intel'].encode('utf-8'),
                        file_name=f"Intel_Mercado_{datetime.now().strftime('%Y%m%d')}.txt",
                        mime="text/plain", key="dl_intel"
                    )

            # ── TAB 3: Estrategia Competitiva ─────────────────────────────────
            with tab3:
                st.markdown("#### 🏆 Estrategia Competitiva")
                st.caption("IA analiza a tus competidores recurrentes y te ayuda a diferenciarte.")

                # Consolidar competidores
                filas_comp = []
                for _, row in df.iterrows():
                    postores_lic = row.get('postores', [])
                    if isinstance(postores_lic, list):
                        for p in postores_lic:
                            filas_comp.append({
                                'empresa': p.get('empresa', ''),
                                'es_ganador': p.get('es_ganador', False),
                                'tasa_seace': p.get('tasa_exito', 0),
                                'lics_previas': p.get('licitaciones_previas', 0),
                            })

                if not filas_comp:
                    st.info("Aún no hay postores registrados. Agrega licitaciones con texto completo de SEACE para que el parser extraiga los postores.")
                else:
                    df_comp = pd.DataFrame(filas_comp)
                    ranking_comp = df_comp.groupby('empresa').agg(
                        apariciones=('empresa', 'count'),
                        victorias=('es_ganador', 'sum'),
                        tasa_seace=('tasa_seace', 'max'),
                        lics_totales=('lics_previas', 'max')
                    ).sort_values('apariciones', ascending=False).head(10)

                    st.markdown("##### Top competidores en tu base:")
                    st.dataframe(ranking_comp, use_container_width=True)

                    competidor_sel = st.selectbox(
                        "Selecciona un competidor para análisis profundo:",
                        ranking_comp.index.tolist(), key="ia_comp_sel"
                    )
                    comp_data = ranking_comp.loc[competidor_sel]
                    contexto_comp = f"""
COMPETIDOR A ANALIZAR: {competidor_sel}
- Apariciones en procesos de Qubits: {int(comp_data['apariciones'])}
- Victorias en procesos de Qubits: {int(comp_data['victorias'])}
- Licitaciones totales en SEACE: {int(comp_data['lics_totales'])}
- Tasa de éxito histórica SEACE: {comp_data['tasa_seace']:.1f}%

EMPRESA QUE COMPITE: Qubits SAC (infraestructura TI y nube)
MERCADO: Licitaciones públicas peruanas >8 UIT (Ley 30225)
"""
                    tipo_comp = st.selectbox("Tipo de análisis competitivo:", [
                        "Perfil completo del competidor y cómo superarlo",
                        "Fortalezas y debilidades del competidor",
                        "Estrategia para ganarle en licitaciones directas",
                        "Sectores donde Qubits tiene ventaja sobre este competidor",
                    ], key="ia_tipo_comp")

                    prompts_comp = {
                        "Perfil completo del competidor y cómo superarlo": f"Analiza a {competidor_sel} como competidor de Qubits SAC en licitaciones públicas TI. Da un perfil completo y estrategia para superarlo.\n{contexto_comp}",
                        "Fortalezas y debilidades del competidor": f"¿Cuáles son las fortalezas y debilidades de {competidor_sel} frente a Qubits SAC en licitaciones públicas TI peruanas?\n{contexto_comp}",
                        "Estrategia para ganarle en licitaciones directas": f"Cuando Qubits SAC y {competidor_sel} compiten en el mismo proceso, ¿qué estrategia debería usar Qubits para ganar?\n{contexto_comp}",
                        "Sectores donde Qubits tiene ventaja sobre este competidor": f"¿En qué tipos de licitaciones, entidades o regiones tiene Qubits SAC ventaja competitiva sobre {competidor_sel}?\n{contexto_comp}",
                    }

                    if st.button("🚀 Analizar Competidor con IA", key="btn_comp_ia", type="primary"):
                        with st.spinner(f"🤖 Analizando a {competidor_sel}..."):
                            respuesta, error = consultar_claude(SISTEMA_KAM, prompts_comp[tipo_comp])
                        if error:
                            st.error(f"❌ Error: {error}")
                        else:
                            st.session_state['ia_resp_comp'] = respuesta

                    if 'ia_resp_comp' in st.session_state:
                        st.markdown("---")
                        st.markdown("#### 📋 Análisis Competitivo")
                        st.markdown(st.session_state['ia_resp_comp'])
                        st.download_button(
                            "📥 Descargar análisis",
                            data=st.session_state['ia_resp_comp'].encode('utf-8'),
                            file_name=f"Competencia_{competidor_sel[:20]}_{datetime.now().strftime('%Y%m%d')}.txt",
                            mime="text/plain", key="dl_comp"
                        )

            # ── TAB 4: Consulta Libre ─────────────────────────────────────────
            with tab4:
                st.markdown("#### 💬 Consulta Libre al Asesor IA")
                st.caption("Hazle cualquier pregunta sobre licitaciones, normativa OSCE, estrategia comercial o tu portafolio.")

                # Historial de conversación
                if 'ia_historial' not in st.session_state:
                    st.session_state['ia_historial'] = []

                # Mostrar historial
                for msg in st.session_state['ia_historial']:
                    with st.chat_message(msg['rol']):
                        st.markdown(msg['texto'])

                # Input de usuario
                pregunta = st.chat_input("Escribe tu consulta aquí...", key="ia_chat_input")

                if pregunta:
                    st.session_state['ia_historial'].append({'rol': 'user', 'texto': pregunta})
                    with st.chat_message("user"):
                        st.markdown(pregunta)

                    # Construir historial para la API
                    mensajes_api = [
                        {"role": "user" if m['rol'] == 'user' else "assistant", "content": m['texto']}
                        for m in st.session_state['ia_historial']
                    ]

                    with st.chat_message("assistant"):
                        with st.spinner("🤖 Pensando..."):
                            import urllib.request, urllib.error
                            payload = json.dumps({
                                "model": "claude-sonnet-4-6",
                                "max_tokens": 2000,
                                "system": SISTEMA_KAM,
                                "messages": mensajes_api
                            }).encode('utf-8')
                            req = urllib.request.Request(
                                "https://api.anthropic.com/v1/messages",
                                data=payload,
                                headers={
                                    "x-api-key": API_KEY,
                                    "anthropic-version": "2023-06-01",
                                    "content-type": "application/json"
                                },
                                method="POST"
                            )
                            try:
                                with urllib.request.urlopen(req, timeout=60) as resp:
                                    data_resp = json.loads(resp.read().decode('utf-8'))
                                    respuesta_chat = data_resp['content'][0]['text']
                                    st.markdown(respuesta_chat)
                                    st.session_state['ia_historial'].append({'rol': 'assistant', 'texto': respuesta_chat})
                            except Exception as e:
                                st.error(f"❌ Error: {str(e)}")

                if st.session_state.get('ia_historial'):
                    if st.button("🗑️ Limpiar conversación", key="limpiar_chat_ia"):
                        st.session_state['ia_historial'] = []
                        st.rerun()

    elif seccion == "📊 Exportar Licitaciones":
        st.markdown("### 📊 Exportar Licitaciones")
        
        if len(df) == 0:
            st.info("No hay licitaciones para exportar.")
        else:
            cols_exportar = [c for c in COLUMNAS_LICITACION_TODAS if c in df.columns and c != 'postores']
            df_exportar = df[cols_exportar]
            
            st.markdown(f"#### Vista previa ({len(df_exportar)} licitaciones)")
            st.dataframe(df_exportar, use_container_width=True)
            
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_exportar.to_excel(writer, index=False, sheet_name='Licitaciones')
            output.seek(0)
            
            st.download_button(
                label="📥 Descargar Excel",
                data=output,
                file_name=f"licitaciones_qubits_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    elif seccion == "⚙️ Configuración":
        st.markdown("### ⚙️ Configuración del Sistema — Licitaciones")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 📊 Estadísticas")
            if len(df_licitaciones) > 0:
                st.info(f"""
**Total Licitaciones:** {len(df_licitaciones)}
**Entidades Únicas:** {df_licitaciones['entidad'].nunique()}
**Regiones:** {df_licitaciones['region'].nunique()}
**Tipos de Licitación:** {df_licitaciones['tipo_licitacion'].nunique()}
**Monto Base Total:** S/ {df_licitaciones['monto_base'].sum():,.2f}
**Monto Adjudicado Total:** S/ {df_licitaciones['monto_adjudicado'].sum():,.2f}
                """)
            else:
                st.info("Aún no hay licitaciones registradas.")

        with col2:
            st.markdown("#### 📈 Desempeño")
            if len(df_licitaciones) > 0:
                firmados   = len(df_licitaciones[df_licitaciones['estado'] == 'Contrato Firmado'])
                desiertos  = len(df_licitaciones[df_licitaciones['estado'].str.startswith('Desierto', na=False)])
                evaluacion = len(df_licitaciones[df_licitaciones['estado'] == 'En Evaluación'])
                tasa       = (firmados / len(df_licitaciones) * 100)
                st.info(f"""
**Tasa de Cierre:** {tasa:.1f}%
**Contratos Firmados:** {firmados}
**En Evaluación:** {evaluacion}
**Desiertos:** {desiertos}
**Avance vs Cuota Q3:** {(df_licitaciones['monto_adjudicado'].sum() / 500000 * 100):.1f}%
                """)
            else:
                st.info("Sin datos de desempeño aún.")

        st.markdown("---")
        st.markdown("#### 🔧 Información Técnica")
        st.text(f"Última actualización: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        st.text("Versión: 2.0 · Módulo: Licitaciones (>8 UIT)")
        st.text("Estado: ✅ Operativo con todos los módulos")

# ==================== SECCIÓN 8: CONFIGURACIÓN ====================

elif seccion == "⚙️ Configuración" and "Menores" in tipo_proceso:
    st.markdown("### ⚙️ Configuración del Sistema")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### 📊 Estadísticas")
        st.info(f"""
        **Total Procesos:** {len(df)}
        **Clientes Únicos:** {df['entidad'].nunique()}
        **Regiones:** {df['region'].nunique()}
        **Servicios:** {df['subcategoria'].nunique()}
        **Mercado Total:** S/ {df['montoAdjudicado'].sum():,.2f}
        """)
    
    with col2:
        st.markdown("#### 📈 Desempeño")
        tasa_exito = len(df[df['resultadoAdjudicacion'] == 'Adjudicado']) / len(df) * 100
        st.info(f"""
        **Tasa de Éxito:** {tasa_exito:.1f}%
        **Adjudicados:** {len(df[df['resultadoAdjudicacion'] == 'Adjudicado'])}
        **Desiertos:** {len(df[df['resultadoAdjudicacion'] == 'DESIERTO'])}
        **Avance vs Cuota:** {(df['montoAdjudicado'].sum()/500000*100):.1f}%
        """)
    
    st.markdown("---")
    st.markdown("#### 🔧 Información Técnica")
    st.text(f"Última actualización: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    st.text(f"Versión: 2.0 (completa)")
    st.text("Estado: ✅ Operativo con todos los módulos")


st.markdown("---")
st.markdown("<center><small>QUBITS KAM Intelligence v2 | Inteligencia Comercial para Sector Público y Privado</small></center>", unsafe_allow_html=True)
