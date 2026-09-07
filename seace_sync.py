#!/usr/bin/env python3
"""Sincroniza oportunidades TI desde la API OCDS oficial de OECE."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import smtplib
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

API_BASE = "https://contratacionesabiertas.oece.gob.pe/api/v1"
SPREADSHEET_ID = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"
HOJA_LICITACIONES = "licitaciones"
HOJA_SYNC_LOG = "sync_log"
UIT_POR_ANIO = {2025: 5350, 2026: 5500}


def _cargar_env_local(ruta=".env"):
    """Carga secretos locales ignorados por Git, sin reemplazar variables ya definidas."""
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            clave, valor = clave.strip(), valor.strip().strip('"').strip("'")
            if clave and valor:
                os.environ.setdefault(clave, valor)


_cargar_env_local()
GMAIL_FROM = os.environ.get("GMAIL_FROM", "")
GMAIL_TO = os.environ.get("GMAIL_TO", "acernar@gmail.com,alexander.cerna@qubitssales.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")

# Una consulta por grupo reduce llamadas; el filtro de puntuación decide la relevancia final.
TERMINOS_BUSQUEDA = [
    "nube", "cloud", "correo electronico", "google workspace", "microsoft 365",
    "ciberseguridad", "firewall", "backup", "software", "hosting", "servidor",
    "almacenamiento", "base de datos", "videoconferencia", "inteligencia artificial",
    "mesa de ayuda", "saas", "devops", "switch", "router", "wifi", "access point",
    "rack", "cableado estructurado", "fibra optica", "ups", "videovigilancia",
    "sd-wan", "gpon", "pantalla interactiva", "pizarra interactiva",
    "computadora", "laptop", "impresora", "escáner", "equipamiento informatico",
    "licenciamiento", "seguridad informatica", "soporte tecnico", "data center",
    "analitica de datos", "business intelligence", "transformacion digital",
    "desarrollo de software", "digitalizacion", "firma digital", "telefonia ip",
    "elaboracion de expediente tecnico", "supervision de expediente tecnico",
    "elaboracion de software", "construccion de videovigilancia",
]

REGLAS_TI = {
    "Correo/Colaboración": {
        3: ["google workspace", "microsoft 365", "office 365", "exchange online", "correo electronico en la nube"],
        2: ["correo electronico", "correo institucional", "correo corporativo", "colaboracion", "mensajeria electronica", "smtp"],
    },
    "Nube": {
        3: ["amazon web services", "google cloud", "oracle cloud", "azure", "multinube", "cloud computing"],
        2: ["infraestructura en nube", "infraestructura cloud", "nube publica", "nube privada", "servicio en la nube"],
        1: ["cloud", "nube", "iac"],
    },
    "Seguridad Web": {
        3: ["cloudflare", "firewall de aplicaciones", "waf", "ciberseguridad", "seguridad perimetral", "antiddos", "antispam", "gestion unificada de amenazas"],
        2: ["seguridad informatica", "seguridad de la informacion", "proteccion de correo", "endpoint", "firewall", "utm"],
        1: ["vpn", "antivirus", "zero trust"],
    },
    "Backup": {
        3: ["backup en la nube", "respaldo en la nube", "disaster recovery", "recuperacion ante desastres"],
        2: ["copias de respaldo", "contingencia", "backup", "respaldo de datos"],
    },
    "Software": {
        3: ["software como servicio", "saas", "licencia de software", "suscripcion de software", "atlassian"],
        2: ["licenciamiento", "licencias de software", "plataforma digital", "sistema de informacion", "mesa de ayuda"],
        1: ["software", "aplicacion web", "sistema web", "helpdesk", "erp", "crm"],
    },
    "Infraestructura": {
        3: ["gabinete de comunicaciones", "gabinete de datos", "rack de comunicaciones", "rack de servidores"],
        2: ["servidor", "almacenamiento", "storage", "base de datos", "datacenter", "centro de datos", "virtualizacion", "gabinete rack"],
        1: ["infraestructura tecnologica", "conectividad", "fibra optica", "internet dedicado", "red lan", "red wan", "rack"],
    },
    "Redes y Conectividad": {
        3: ["switch core", "switch de distribucion", "router de borde", "controlador wifi", "balanceador de carga", "load balancer", "software defined wan"],
        2: ["switch administrable", "switch de red", "router", "access point", "punto de acceso", "red inalambrica", "sd-wan"],
        1: ["switch", "wifi", "wireless", "nms", "monitoreo de red"],
    },
    "Cableado Estructurado": {
        3: ["cableado estructurado", "certificacion de cableado", "fusion de fibra optica"],
        2: ["patch panel", "panel de parcheo", "organizador de cables", "cable de fibra optica"],
        1: ["patch cord", "transceiver", "fibra optica"],
    },
    "Energía TI": {
        3: ["sistema de alimentacion ininterrumpida", "unidad de distribucion de energia"],
        2: ["ups para data center", "ups para centro de datos", "pdu para rack", "pdu inteligente"],
        1: ["ups", "pdu"],
    },
    "Videovigilancia": {
        3: ["sistema de videovigilancia", "sistema de video vigilancia", "circuito cerrado de television", "construccion de videovigilancia", "implementacion de videovigilancia", "instalacion de sistema de videovigilancia"],
        2: ["videovigilancia", "video vigilancia", "camara ip", "cctv", "ampliacion de videovigilancia", "mejoramiento de videovigilancia", "mantenimiento de videovigilancia"],
    },
    "GPON": {
        3: ["red gpon", "sistema gpon", "gigabit passive optical network"],
        2: ["terminal de linea optica", "terminal de red optica", "olt", "ont", "onu", "splitter optico"],
        1: ["gpon", "transceiver optico", "patch cord optico"],
    },
    "Pantallas Interactivas": {
        3: ["pantalla interactiva", "pantallas interactivas", "panel interactivo", "paneles interactivos", "pizarra digital interactiva", "pizarras digitales interactivas", "monitor interactivo"],
        2: ["pizarra interactiva", "pizarras interactivas", "display interactivo", "pantalla tactil educativa", "panel tactil"],
        1: ["pantalla tactil", "pantallas tactiles", "smart board"],
    },
    "Cómputo y Periféricos": {
        3: ["equipamiento informatico", "equipos de computo", "computadora de escritorio", "estacion de trabajo"],
        2: ["computadora portatil", "laptop", "desktop", "workstation", "tablet", "monitor profesional"],
        1: ["computadora", "monitor", "teclado", "mouse", "dock station", "periferico"],
    },
    "Impresión y Digitalización": {
        3: ["servicio de impresion gestionada", "alquiler de impresoras", "digitalizacion de documentos", "gestion documental"],
        2: ["impresora multifuncional", "equipo multifuncional", "escaner de documentos", "scanner de documentos"],
        1: ["impresora", "escaner", "scanner", "plotter"],
    },
    "Datos y Analítica": {
        3: ["business intelligence", "inteligencia de negocios", "data warehouse", "lago de datos", "gobierno de datos"],
        2: ["analitica de datos", "plataforma de datos", "tablero de control", "power bi", "tableau"],
        1: ["big data", "dashboard", "etl", "data lake"],
    },
    "Desarrollo y Transformación Digital": {
        3: ["transformacion digital", "desarrollo de software", "elaboracion de software", "fabrica de software", "automatizacion de procesos", "implementacion de software"],
        2: ["desarrollo de sistema", "elaboracion de sistema", "desarrollo de aplicativo", "modernizacion de aplicaciones", "mantenimiento de software", "mejora de software", "servicios web"],
        1: ["aplicacion movil", "portal web", "rpa", "low code", "no code", "puesta en marcha de software"],
    },
    "Soporte y Servicios Gestionados": {
        3: ["servicio gestionado de ti", "operacion de infraestructura tecnologica", "outsourcing de ti"],
        2: ["soporte tecnico informatico", "mantenimiento de equipos de computo", "mesa de servicios", "service desk"],
        1: ["soporte tecnico", "mantenimiento preventivo", "help desk", "itil"],
    },
    "Telecomunicaciones y Voz": {
        3: ["telefonia ip", "central telefonica ip", "comunicaciones unificadas", "enlace de datos"],
        2: ["internet dedicado", "enlace dedicado", "sip trunk", "contact center", "call center"],
        1: ["voip", "anexo ip", "telefono ip", "mpls"],
    },
    "Identidad y Firma Digital": {
        3: ["gestion de identidades", "firma digital", "certificado digital", "autenticacion multifactor"],
        2: ["control de acceso logico", "directorio activo", "single sign on", "biometria"],
        1: ["iam", "mfa", "sso", "token digital"],
    },
    "Audiovisual y Salas": {
        3: ["sistema audiovisual", "sala de reuniones inteligente", "sala de videoconferencia"],
        2: ["proyector multimedia", "muro de video", "video wall", "sistema de audio"],
        1: ["proyector", "microfono", "parlante", "camara de conferencia"],
    },
    "Capacitación TI": {
        3: ["capacitacion en tecnologias de informacion", "entrenamiento en ciberseguridad", "capacitacion cloud"],
        2: ["curso de tecnologia", "certificacion tecnologica", "transferencia de conocimiento"],
        1: ["capacitacion informatica", "taller tecnologico"],
    },
    "Expedientes Técnicos y Supervisión": {
        3: ["elaboracion de expediente tecnico", "elaboracion del expediente tecnico", "supervision de elaboracion de expediente tecnico", "supervision de la elaboracion del expediente tecnico", "consultoria para expediente tecnico", "consultoria para la elaboracion del expediente tecnico", "expediente de saldo de obra"],
        2: ["revision de expediente tecnico", "evaluacion de expediente tecnico", "actualizacion de expediente tecnico", "supervision de expediente tecnico", "saldo de obra"],
        1: ["expediente tecnico"],
    },
    "Videoconferencia": {
        3: ["videoconferencia", "video conferencia", "zoom", "google meet"],
        2: ["sala de video", "teams"],
    },
    "IA": {
        3: ["inteligencia artificial", "machine learning", "aprendizaje automatico", "ia generativa"],
        2: ["analitica de datos", "procesamiento de lenguaje natural"],
    },
    "DevOps": {
        3: ["devops", "repositorio de codigo", "controlador de versiones"],
        2: ["integracion continua", "despliegue continuo", "gitlab", "github"],
    },
}

EXCLUSIONES = [
    # Excluir únicamente logística y mensajería física; correo electrónico,
    # colaboración y mensajería digital sí son oportunidades comerciales.
    "correo fisico y mensajeria nacional", "servicio de mensajeria fisica local",
    "servicio de mensajeria fisica nacional", "mensajeria fisica", "servicio courier", "courier",
    "servicio postal", "distribucion fisica de documentos",
    "reparacion de analizador", "analizador de presion", "equipo medico",
    "transporte de carga", "servicio de alimentacion", "obra de construccion",
]
# Alias legible para integraciones y pruebas que referencien esta lista por su
# nombre funcional. Se mantiene una sola fuente de verdad.
TERMINOS_EXCLUIR = EXCLUSIONES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).lower().split())


def _contiene_frase(texto: str, frase: str) -> bool:
    """Busca términos completos para evitar falsos positivos por subcadenas (p. ej. ``ont`` en ``consultoría``)."""
    texto = normalizar(texto)
    termino = normalizar(frase)
    if not termino:
        return False
    return re.search(rf"(?<!\w){re.escape(termino)}(?!\w)", texto) is not None


def _es_excluido(texto: str) -> bool:
    """Indica si el texto contiene una exclusión comercial como término completo."""
    return any(_contiene_frase(texto, termino) for termino in TERMINOS_EXCLUIR)


def evaluar_relevancia(titulo: str, descripcion: str = "") -> tuple[int, str, list[str]]:
    texto = normalizar(f"{titulo} {descripcion}")
    if _es_excluido(texto):
        return 0, "No TI", ["exclusión comercial"]
    puntos_por_categoria = {}
    coincidencias_por_categoria = {}
    for categoria, reglas in REGLAS_TI.items():
        puntos = 0
        coincidencias = []
        for peso, frases in reglas.items():
            halladas = [frase for frase in frases if _contiene_frase(texto, frase)]
            if halladas:
                puntos += peso  # una suma por nivel evita inflar sinónimos repetidos
                coincidencias.extend(halladas)
        puntos_por_categoria[categoria] = puntos
        coincidencias_por_categoria[categoria] = coincidencias
    categoria = max(puntos_por_categoria, key=puntos_por_categoria.get)
    puntos = puntos_por_categoria[categoria]
    # Bonificación por coincidencia en dos líneas complementarias.
    lineas_positivas = sum(1 for valor in puntos_por_categoria.values() if valor > 0)
    if puntos and lineas_positivas >= 2:
        puntos += 1
    return puntos, categoria, coincidencias_por_categoria[categoria]


OECE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Referer": "https://contratacionesabiertas.oece.gob.pe/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def _get_json(url: str, params=None, max_intentos: int = 4) -> dict:
    """Consulta OECE con reintentos para bloqueos y errores temporales."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    ultimo_error = None
    for intento in range(1, max_intentos + 1):
        request = urllib.request.Request(url, headers=OECE_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            ultimo_error = exc
            reintentable = exc.code in (403, 408, 425, 429, 500, 502, 503, 504)
            if not reintentable or intento == max_intentos:
                raise RuntimeError(
                    f"OECE rechazó la consulta después de {intento} intentos "
                    f"(HTTP {exc.code}). URL: {url.split('?')[0]}"
                ) from exc
            espera = min(20, 2 ** intento)
            log.warning("OECE HTTP %s; reintento %d/%d en %ss", exc.code, intento, max_intentos, espera)
            time.sleep(espera)
        except (urllib.error.URLError, TimeoutError) as exc:
            ultimo_error = exc
            if intento == max_intentos:
                raise RuntimeError(f"OECE no respondió después de {intento} intentos") from exc
            espera = min(20, 2 ** intento)
            log.warning("OECE no respondió; reintento %d/%d en %ss", intento, max_intentos, espera)
            time.sleep(espera)
    raise RuntimeError("No se pudo consultar OECE") from ultimo_error


def _fecha_corta(valor) -> str:
    return str(valor or "")[:10]


def _estado_licitacion(release: dict) -> str:
    tender = release.get("tender") or {}
    if release.get("contracts"):
        return "Contrato Firmado"
    if release.get("awards"):
        return "Adjudicado — pendiente de firma"
    estado = normalizar(tender.get("statusDetails") or tender.get("status"))
    if "cancel" in estado:
        return "Cancelado"
    if "desiert" in estado or "unsuccessful" in estado:
        return "Desierto — ninguna propuesta cumplió los requisitos"
    cierre = _fecha_corta((tender.get("tenderPeriod") or {}).get("endDate"))
    if cierre and cierre >= date.today().isoformat():
        return "Abierto para participar"
    return "Publicado"


def evaluar_politica_nube(titulo: str, descripcion: str = "", documentos=None) -> dict:
    """Aplica la política Qubits: GCP se descarta; otras nubes se evalúan."""
    documentos = documentos or []
    texto_documentos = " ".join(
        f"{doc.get('title', '')} {doc.get('description', '')}" for doc in documentos if isinstance(doc, dict)
    )
    texto = normalizar(f"{titulo} {descripcion} {texto_documentos}")
    workspace = any(_contiene_frase(texto, frase) for frase in (
        "google workspace", "gmail empresarial", "correo google", "correo electronico google"
    ))
    proveedores = {
        "Google Cloud (GCP)": (
            "google cloud platform", "nube google", "infraestructura google cloud", "servicios gcp",
            "compute engine", "cloud storage", "bigquery", "vertex ai", "google kubernetes engine",
            "gke", "cloud sql", "apigee",
        ),
        "Amazon Web Services (AWS)": ("amazon web services", "nube aws", "servicios aws", "aws cloud"),
        "Microsoft Azure": ("microsoft azure", "nube azure", "azure cloud"),
        "Oracle Cloud": ("oracle cloud", "oci cloud", "oracle cloud infrastructure"),
        "Huawei Cloud": ("huawei cloud", "nube huawei"),
        "IBM Cloud": ("ibm cloud", "nube ibm"),
        "Nube privada/multinube": ("nube privada", "multinube", "multi cloud", "multicloud"),
    }
    detectados = [nombre for nombre, frases in proveedores.items() if any(_contiene_frase(texto, frase) for frase in frases)]
    es_google_cloud = "Google Cloud (GCP)" in detectados
    # "Google Workspace" por sí solo pertenece a colaboración, no a infraestructura GCP.
    if workspace and es_google_cloud and not any(_contiene_frase(texto, frase) for frase in proveedores["Google Cloud (GCP)"][:-1]):
        es_google_cloud = False
        detectados = [p for p in detectados if p != "Google Cloud (GCP)"]
    menciona_nube = any(_contiene_frase(texto, frase) for frase in (
        "nube", "cloud", "iaas", "paas", "infraestructura como servicio", "servicio de computo",
        "google workspace", "microsoft 365", "office 365", "exchange online",
        "correo electronico en la nube", "mensajeria electronica",
    )) or bool(detectados)
    if not menciona_nube:
        decision = "NO APLICA"
        motivo = "Este proceso no corresponde a nube, cloud ni servicios equivalentes; la política de GCP no aplica."
    elif es_google_cloud:
        decision = "DESCARTAR"
        motivo = "La documentación disponible identifica infraestructura Google Cloud/GCP, excluida por política comercial Qubits."
    elif detectados:
        decision = "EVALUAR"
        motivo = f"Proveedor de nube detectado: {', '.join(detectados)}. No corresponde a GCP."
    elif menciona_nube:
        decision = "REVISAR BASES"
        motivo = "El proceso menciona nube, pero el proveedor no se identifica en el título, descripción o metadatos de las bases."
    else:
        decision = "EVALUAR"
        motivo = "No se detectó una contratación de infraestructura Google Cloud/GCP."
    return {
        "proveedor_nube_detectado": ", ".join(detectados) if detectados else ("Google Workspace" if workspace else "No identificado"),
        "decision_comercial": decision,
        "motivo_decision": motivo,
        "lectura_bases": (
            "Metadatos OCDS revisados" if documentos else "Sin bases accesibles en OCDS; revisión pendiente"
        ) if menciona_nube else "No aplica: proceso no relacionado con nube/cloud",
    }


def enriquecer_decision_con_bases(oportunidad: dict, max_mb: int = 25, max_paginas: int = 250) -> dict:
    """Lee el PDF de bases de procesos de nube nuevos y confirma la política GCP."""
    texto_inicial = normalizar(
        f"{oportunidad.get('titulo', '')} {oportunidad.get('descripcion', '')} "
        f"{oportunidad.get('subcategoria_ti', oportunidad.get('subcategoria', ''))}"
    )
    if not any(_contiene_frase(texto_inicial, frase) for frase in (
        "nube", "cloud", "iaas", "paas", "google workspace", "microsoft 365",
        "office 365", "exchange online", "correo electronico en la nube", "mensajeria electronica"
    )):
        return oportunidad
    documentos = oportunidad.get("documentos_bases") or []
    if isinstance(documentos, str):
        try:
            documentos = json.loads(documentos)
        except json.JSONDecodeError:
            documentos = []
    candidatos = [doc for doc in documentos if isinstance(doc, dict) and str(doc.get("formato", "")).lower() == "pdf" and doc.get("url")]
    candidatos.sort(key=lambda doc: (
        "bases integradas" in normalizar(doc.get("titulo", "")),
        "bases" in normalizar(doc.get("titulo", "")),
        doc.get("fecha", ""),
    ), reverse=True)
    if not candidatos:
        oportunidad["lectura_bases"] = "REVISIÓN PENDIENTE: OECE no publicó bases PDF accesibles"
        if oportunidad.get("decision_comercial") == "EVALUAR" and oportunidad.get("proveedor_nube_detectado") == "No identificado":
            oportunidad["decision_comercial"] = "REVISAR BASES"
        return oportunidad
    documento = candidatos[0]
    try:
        from pypdf import PdfReader
        request = urllib.request.Request(documento["url"], headers=OECE_HEADERS)
        with urllib.request.urlopen(request, timeout=120) as response:
            contenido = response.read(max_mb * 1024 * 1024 + 1)
        if len(contenido) > max_mb * 1024 * 1024:
            raise ValueError(f"PDF supera {max_mb} MB")
        lector = PdfReader(BytesIO(contenido))
        texto_bases = " ".join((pagina.extract_text() or "") for pagina in lector.pages[:max_paginas])
        if not texto_bases.strip():
            raise ValueError("PDF sin texto extraíble")
        politica = evaluar_politica_nube(
            oportunidad.get("titulo", oportunidad.get("descripcion", "")), texto_bases
        )
        oportunidad.update(politica)
        oportunidad["lectura_bases"] = f"LEÍDO: {documento.get('titulo', 'Bases')} ({min(len(lector.pages), max_paginas)} páginas revisadas)"
        oportunidad["base_revisada_url"] = documento["url"]
    except Exception as exc:
        oportunidad["lectura_bases"] = f"REVISIÓN PENDIENTE: no se pudo leer {documento.get('titulo', 'el PDF')} ({exc})"
        if oportunidad.get("proveedor_nube_detectado") == "No identificado":
            oportunidad["decision_comercial"] = "REVISAR BASES"
            oportunidad["motivo_decision"] = "El proceso es de nube y las bases no pudieron leerse automáticamente."
    return oportunidad


def convertir_record(record: dict) -> dict | None:
    release = record.get("compiledRelease") or {}
    tender = release.get("tender") or {}
    titulo = tender.get("description") or tender.get("title") or ""
    descripcion = tender.get("description") or ""
    score, subcategoria, coincidencias = evaluar_relevancia(titulo, descripcion)
    if score < 3:
        return None

    valor = tender.get("value") or ((release.get("planning") or {}).get("budget") or {}).get("amount") or {}
    monto = float(valor.get("amount_PEN") or valor.get("amount") or 0)
    moneda = valor.get("currency") or "PEN"
    parties = release.get("parties") or []
    comprador = next((p for p in parties if "buyer" in (p.get("roles") or [])), {})
    direccion = comprador.get("address") or {}
    awards = release.get("awards") or []
    award = awards[0] if awards else {}
    suppliers = award.get("suppliers") or []
    award_value = award.get("value") or {}
    contracts = release.get("contracts") or []
    contract = contracts[0] if contracts else {}
    period = tender.get("tenderPeriod") or {}
    contract_period = contract.get("period") or {}
    documentos = tender.get("documents") or []
    nomenclatura = str(tender.get("title") or record.get("ocid") or release.get("ocid"))

    documentos_normalizados = [{
        "id": doc.get("id", ""), "titulo": doc.get("title", ""),
        "descripcion": doc.get("description", ""), "url": doc.get("url", ""),
        "formato": doc.get("format", ""), "fecha": _fecha_corta(doc.get("datePublished") or doc.get("dateModified")),
    } for doc in documentos if isinstance(doc, dict)]
    politica_nube = evaluar_politica_nube(titulo, descripcion, documentos)

    resultado = {
        "id": nomenclatura,
        "titulo": titulo,
        "entidad": (release.get("buyer") or tender.get("procuringEntity") or {}).get("name", ""),
        "region": direccion.get("department") or direccion.get("region") or "",
        "tipo_licitacion": tender.get("procurementMethodDetails") or tender.get("procurementMethod") or "",
        "tipo_contratacion": {"goods": "Bien", "services": "Servicio", "works": "Obra"}.get(tender.get("mainProcurementCategory"), "Servicio"),
        "estado": _estado_licitacion(release),
        "monto_base": monto,
        "ganador": suppliers[0].get("name", "") if suppliers else "",
        "ruc_ganador": str(suppliers[0].get("id", "")).replace("PE-RUC-", "") if suppliers else "",
        "monto_adjudicado": float(award_value.get("amount_PEN") or award_value.get("amount") or 0),
        "publicado": _fecha_corta(tender.get("datePublished") or release.get("date")),
        "adjudicacion": _fecha_corta(award.get("date")),
        "inicio_contrato": _fecha_corta(contract_period.get("startDate")),
        "fin_contrato": _fecha_corta(contract_period.get("endDate")),
        "duracion_dias": contract_period.get("durationInDays") or "",
        "moneda": moneda,
        "empresas_participantes": tender.get("numberOfTenderers") or len(tender.get("tenderers") or []),
        "descripcion": descripcion,
        "tdr_disponible": bool(documentos),
        "documentos_bases": documentos_normalizados,
        "ocid": record.get("ocid") or release.get("ocid") or "",
        "fecha_cierre": _fecha_corta(period.get("endDate")),
        "fuente": "OECE-OCDS-OFICIAL",
        "fuente_url": f"https://contratacionesabiertas.oece.gob.pe/proceso/{record.get('ocid') or release.get('ocid') or ''}",
        "api_url": f"{API_BASE}/record/{record.get('ocid') or release.get('ocid') or ''}",
        "subcategoria_ti": subcategoria,
        "score_ti": score,
        "coincidencias_ti": coincidencias,
        "_agregada_el": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "_sync_automatico": "SI",
    }
    resultado.update(politica_nube)
    return resultado


def limite_8_uit(fecha_publicacion: str = "") -> float:
    try:
        anio = int(str(fecha_publicacion)[:4])
    except (TypeError, ValueError):
        anio = date.today().year
    valor_uit = UIT_POR_ANIO.get(anio, UIT_POR_ANIO[max(UIT_POR_ANIO)])
    return float(valor_uit * 8)


def convertir_a_proceso_menor(licitacion: dict) -> dict:
    estado_lic = licitacion.get("estado", "")
    adjudicado = estado_lic in ("Contrato Firmado", "Adjudicado — pendiente de firma")
    desierto = estado_lic.startswith("Desierto")
    if adjudicado:
        estado, resultado = "Culminado", "Adjudicado"
    elif desierto:
        estado, resultado = "Culminado", "DESIERTO"
    elif estado_lic == "Abierto para participar":
        estado, resultado = "Vigente", "Sin definir"
    else:
        estado, resultado = "En Evaluación", "En Evaluación"
    return {
        "id": licitacion.get("id", ""),
        "entidad": licitacion.get("entidad", ""),
        "region": licitacion.get("region", ""),
        "localidad": "",
        "descripcion": licitacion.get("titulo") or licitacion.get("descripcion", ""),
        "categoria": "TI",
        "subcategoria": licitacion.get("subcategoria_ti", "Software"),
        "estado": estado,
        "resultadoAdjudicacion": resultado,
        "proveedor": licitacion.get("ganador", ""),
        "ruc": licitacion.get("ruc_ganador", ""),
        "montoAdjudicado": licitacion.get("monto_adjudicado", 0),
        "montoReferencial": licitacion.get("monto_base", 0),
        "moneda": licitacion.get("moneda", "PEN"),
        "publicado": licitacion.get("publicado", ""),
        "inicioCotz": licitacion.get("publicado", ""),
        "finCotz": licitacion.get("fecha_cierre", ""),
        "fechaAdjudicacionEstimada": licitacion.get("adjudicacion", ""),
        "inicioContrato": licitacion.get("inicio_contrato", ""),
        "finContrato": licitacion.get("fin_contrato", ""),
        "plazo": "",
        "areaUsuaria": "",
        "cubo": "",
        "prioridad": "ALTA" if licitacion.get("score_ti", 0) >= 5 else "MEDIA",
        "oportunidad": f"Oportunidad {licitacion.get('subcategoria_ti', 'TI')} detectada automáticamente en OECE",
        "tdrDisponible": licitacion.get("tdr_disponible", False),
        "tipo": licitacion.get("tipo_licitacion", ""),
        "ocid": licitacion.get("ocid", ""),
        "fuente": licitacion.get("fuente", "OECE-OCDS-OFICIAL"),
        "fuente_url": licitacion.get("fuente_url", ""),
        "score_ti": licitacion.get("score_ti", 0),
        "documentos_bases": licitacion.get("documentos_bases", []),
        "proveedor_nube_detectado": licitacion.get("proveedor_nube_detectado", "No identificado"),
        "decision_comercial": licitacion.get("decision_comercial", "EVALUAR"),
        "motivo_decision": licitacion.get("motivo_decision", ""),
        "lectura_bases": licitacion.get("lectura_bases", ""),
        "_agregado_el": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "_sync_automatico": "SI",
    }


def separar_por_cuantia(oportunidades: list[dict]) -> tuple[list[dict], list[dict]]:
    menores, licitaciones = [], []
    for oportunidad in oportunidades:
        monto = float(oportunidad.get("monto_base") or 0)
        limite = limite_8_uit(oportunidad.get("publicado", ""))
        if monto > 0 and monto < limite:
            menores.append(convertir_a_proceso_menor(oportunidad))
        else:
            # Un monto 0 significa "aún no publicado": se conserva para revisión.
            oportunidad["limite_8uit"] = limite
            licitaciones.append(oportunidad)
    return menores, licitaciones


def descargar_oportunidades_oece(fecha_desde: date, fecha_hasta: date | None = None,
                                 max_paginas: int = 2, page_size: int = 100):
    oportunidades, stats = descargar_licitaciones_oece(
        fecha_desde, fecha_hasta, max_paginas=max_paginas, page_size=page_size
    )
    menores, licitaciones = separar_por_cuantia(oportunidades)
    stats["menores"] = len(menores)
    stats["licitaciones"] = len(licitaciones)
    return menores, licitaciones, stats


def descargar_licitaciones_oece(fecha_desde: date, fecha_hasta: date | None = None,
                                max_paginas: int = 2, page_size: int = 100) -> tuple[list[dict], dict]:
    """Busca por términos QUBITS, obtiene el record completo y devuelve licitaciones relevantes."""
    fecha_hasta = fecha_hasta or date.today()
    candidatos = {}
    consultas = 0

    for year in range(fecha_desde.year, fecha_hasta.year + 1):
        for termino in TERMINOS_BUSQUEDA:
            for pagina in range(1, max_paginas + 1):
                consultas += 1
                data = _get_json(f"{API_BASE}/search", {
                    "year": year, "search": termino, "page": pagina,
                    "paginateBy": page_size, "format": "json",
                })
                resultados = data.get("results") or []
                for resultado in resultados:
                    release = resultado.get("compiledRelease") or {}
                    tender = release.get("tender") or {}
                    publicada_resumen = _fecha_corta(tender.get("datePublished") or release.get("date"))
                    if publicada_resumen and not (fecha_desde.isoformat() <= publicada_resumen <= fecha_hasta.isoformat()):
                        continue
                    score, _, _ = evaluar_relevancia(tender.get("description", ""), tender.get("title", ""))
                    ocid = release.get("ocid")
                    if ocid and score >= 3:
                        candidatos[ocid] = resultado
                paginacion = data.get("pagination") or {}
                if not resultados or not paginacion.get("has_next"):
                    break

    def cargar_detalle(ocid):
        paquete = _get_json(f"{API_BASE}/record/{urllib.parse.quote(ocid, safe='')}")
        records = paquete.get("records") or []
        if not records:
            return None
        licitacion = convertir_record(records[0])
        if not licitacion:
            return None
        publicada = licitacion.get("publicado")
        if publicada and not (fecha_desde.isoformat() <= publicada <= fecha_hasta.isoformat()):
            return None
        return licitacion

    licitaciones = []
    errores_detalle = 0
    # Los detalles son consultas independientes; ejecutarlas en paralelo reduce mucho la espera.
    with ThreadPoolExecutor(max_workers=8) as executor:
        futuros = {executor.submit(cargar_detalle, ocid): ocid for ocid in candidatos}
        for futuro in as_completed(futuros):
            ocid = futuros[futuro]
            try:
                licitacion = futuro.result()
                if licitacion:
                    licitaciones.append(licitacion)
            except Exception as exc:
                errores_detalle += 1
                log.warning("No se pudo obtener %s: %s", ocid, exc)

    licitaciones.sort(key=lambda item: (item.get("publicado", ""), item.get("score_ti", 0)), reverse=True)
    return licitaciones, {
        "consultas": consultas,
        "candidatos": len(candidatos),
        "relevantes": len(licitaciones),
        "errores_detalle": errores_detalle,
        "desde": fecha_desde.isoformat(),
        "hasta": fecha_hasta.isoformat(),
    }


def conectar_sheets():
    import gspread
    from google.oauth2.service_account import Credentials
    creds_dict = None
    if os.environ.get("GSHEETS_CREDENTIALS_JSON"):
        creds_dict = json.loads(os.environ["GSHEETS_CREDENTIALS_JSON"])
    elif os.path.exists("gsheets_credentials.json"):
        with open("gsheets_credentials.json", encoding="utf-8") as archivo:
            creds_dict = json.load(archivo)
    if not creds_dict:
        raise ValueError("No hay credenciales de Google Sheets")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def obtener_ids_existentes(sh, hoja=HOJA_LICITACIONES) -> set[str]:
    return {str(row.get("id", "")).strip() for row in sh.worksheet(hoja).get_all_records() if row.get("id")}


def deduplicar_por_id(registros: list[dict]) -> list[dict]:
    """Conserva un solo registro por ID también dentro del lote descargado."""
    unicos = {}
    for registro in registros:
        identificador = str(registro.get("id", "")).strip()
        if identificador and identificador not in unicos:
            unicos[identificador] = registro
    return list(unicos.values())


def guardar_en_sheets(sh, nuevas: list[dict], hoja=HOJA_LICITACIONES):
    if not nuevas:
        return
    ws = sh.worksheet(hoja)
    registros = ws.get_all_records()
    columnas = list(registros[0].keys()) if registros else list(nuevas[0].keys())
    columnas_nuevas = sorted({campo for item in nuevas for campo in item if campo not in columnas})
    if columnas_nuevas:
        columnas.extend(columnas_nuevas)
        ws.update(values=[columnas], range_name="A1", value_input_option="RAW")
    if not registros:
        ws.append_row(columnas)
    filas = []
    for lic in nuevas:
        fila = []
        for columna in columnas:
            valor = lic.get(columna, "")
            if isinstance(valor, (list, dict)):
                valor = json.dumps(valor, ensure_ascii=False)
            fila.append(str(valor))
        filas.append(fila)
    ws.append_rows(filas, value_input_option="RAW")


def enriquecer_existentes_sheets(sh, hoja: str, enriquecidos: list[dict]) -> int:
    """Completa solo celdas OCDS de filas existentes, sin reescribir la hoja."""
    if not enriquecidos:
        return 0
    from gspread.utils import rowcol_to_a1
    ws = sh.worksheet(hoja)
    registros = ws.get_all_records()
    por_id = {str(item.get("id", "")): item for item in enriquecidos if item.get("id")}
    columnas = list(ws.row_values(1))
    campos_trazabilidad = [
        "fuente", "fuente_url", "ocid", "score_ti", "_sync_automatico",
        "subcategoria_ti", "fecha_cierre", "montoReferencial",
        "fechaAdjudicacionEstimada", "inicioContrato", "finContrato",
    ]
    nuevas_columnas = [campo for campo in campos_trazabilidad if campo not in columnas]
    if nuevas_columnas:
        columnas.extend(nuevas_columnas)
        ws.update(values=[columnas], range_name=f"A1:{rowcol_to_a1(1, len(columnas))}", value_input_option="RAW")
    cambios = []
    filas_actualizadas = 0
    for numero_fila, registro in enumerate(registros, start=2):
        extra = por_id.get(str(registro.get("id", "")))
        if not extra:
            continue
        filas_actualizadas += 1
        for campo in campos_trazabilidad:
            if campo not in extra:
                continue
            valor = extra.get(campo, "")
            if isinstance(valor, (list, dict)):
                valor = json.dumps(valor, ensure_ascii=False)
            numero_columna = columnas.index(campo) + 1
            celda = rowcol_to_a1(numero_fila, numero_columna)
            cambios.append({"range": celda, "values": [[str(valor) if valor is not None else ""]]})
    if cambios:
        ws.batch_update(cambios, value_input_option="RAW")
    return filas_actualizadas


def guardar_local(nuevas: list[dict], ruta="licitaciones.json") -> int:
    try:
        with open(ruta, encoding="utf-8") as archivo:
            data = json.load(archivo)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    agregadas = 0
    for lic in nuevas:
        if lic["id"] not in data:
            agregadas += 1
        data[lic["id"]] = lic
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(data, archivo, ensure_ascii=False, indent=2)
    return agregadas


def enviar_email(nuevas: list[dict], menores_nuevos=None, dry_run=False,
                 destinatario: str | None = None, remitente: str | None = None,
                 app_password: str | None = None):
    """Notifica cada lote nuevo, incluyendo menores y el descarte comercial por GCP."""
    menores_nuevos = menores_nuevos or []
    oportunidades = []
    for lic in nuevas:
        oportunidades.append({
            "tipo": "Licitación >8 UIT", "id": lic.get("id", ""), "entidad": lic.get("entidad", ""),
            "titulo": lic.get("titulo", ""), "monto": lic.get("monto_base", 0),
            "categoria": lic.get("subcategoria_ti", ""), "cierre": lic.get("fecha_cierre", ""),
            "decision": lic.get("decision_comercial", "EVALUAR"),
            "proveedor_nube": lic.get("proveedor_nube_detectado", "No identificado"),
            "motivo": lic.get("motivo_decision", ""), "url": lic.get("fuente_url", ""),
        })
    for proc in menores_nuevos:
        oportunidades.append({
            "tipo": "Menor ≤8 UIT", "id": proc.get("id", ""), "entidad": proc.get("entidad", ""),
            "titulo": proc.get("descripcion", ""), "monto": proc.get("montoReferencial", 0),
            "categoria": proc.get("subcategoria", ""), "cierre": proc.get("finCotz", ""),
            "decision": proc.get("decision_comercial", "EVALUAR"),
            "proveedor_nube": proc.get("proveedor_nube_detectado", "No identificado"),
            "motivo": proc.get("motivo_decision", ""), "url": proc.get("fuente_url", ""),
        })
    destinatario = destinatario or GMAIL_TO
    remitente = remitente or GMAIL_FROM
    app_password = app_password or GMAIL_APP_PASS
    if dry_run or not oportunidades or not all([remitente, destinatario, app_password]):
        return
    filas = ""
    for oportunidad in oportunidades[:100]:
        color = "#b91c1c" if oportunidad["decision"] == "DESCARTAR" else "#b45309" if oportunidad["decision"] == "REVISAR BASES" else "#047857"
        enlace = f'<a href="{html.escape(str(oportunidad["url"]))}">Abrir OECE</a>' if oportunidad["url"] else "—"
        filas += (
            f"<tr><td>{html.escape(oportunidad['tipo'])}</td><td>{html.escape(str(oportunidad['entidad']))}</td>"
            f"<td><b>{html.escape(str(oportunidad['id']))}</b><br>{html.escape(str(oportunidad['titulo']))}</td>"
            f"<td>S/ {float(oportunidad['monto'] or 0):,.0f}</td><td>{html.escape(str(oportunidad['cierre']))}</td>"
            f"<td>{html.escape(str(oportunidad['proveedor_nube']))}</td>"
            f"<td style='color:{color};font-weight:700'>{html.escape(oportunidad['decision'])}<br>"
            f"<span style='font-weight:400'>{html.escape(str(oportunidad['motivo']))}</span></td><td>{enlace}</td></tr>"
        )
    descartadas = sum(1 for item in oportunidades if item["decision"] == "DESCARTAR")
    contenido = f"""
    <div style="font-family:Arial,sans-serif;max-width:1100px;margin:auto">
      <h2 style="color:#534AB7">KAM Intelligence · {len(oportunidades)} procesos nuevos</h2>
      <p><b>{len(menores_nuevos)}</b> menores de 8 UIT · <b>{len(nuevas)}</b> licitaciones ·
         <b style="color:#b91c1c">{descartadas}</b> descartados por Google Cloud/GCP.</p>
      <table style="border-collapse:collapse;width:100%" border="1" cellpadding="7">
        <thead><tr><th>Tipo</th><th>Entidad</th><th>Proceso</th><th>Monto referencial</th><th>Cierre</th>
        <th>Nube detectada</th><th>Decisión preliminar</th><th>Fuente</th></tr></thead>
        <tbody>{filas}</tbody>
      </table>
      <p style="font-size:12px;color:#64748b">La decisión es preliminar y debe confirmarse con las bases integradas y el RNP.</p>
    </div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"KAM Intelligence · {len(oportunidades)} procesos nuevos · {descartadas} descartados GCP"
    msg["From"], msg["To"] = remitente, destinatario
    msg.attach(MIMEText(contenido, "html", "utf-8"))
    receptores = [correo.strip() for correo in destinatario.split(",") if correo.strip()]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(remitente, app_password)
        server.sendmail(remitente, receptores, msg.as_string())


def enviar_alerta_eventos(eventos: list[dict], destinatario: str | None = None,
                          remitente: str | None = None, app_password: str | None = None,
                          dry_run: bool = False) -> bool:
    """Envía un resumen de fechas críticas del calendario mediante Gmail."""
    destinatario = destinatario or GMAIL_TO
    remitente = remitente or GMAIL_FROM
    app_password = app_password or GMAIL_APP_PASS
    if dry_run or not eventos or not all([destinatario, remitente, app_password]):
        return False
    eventos = sorted(eventos, key=lambda item: (item.get("fecha", ""), item.get("entidad", "")))
    filas = ""
    for evento in eventos[:100]:
        dias = evento.get("dias", "")
        color = "#b91c1c" if isinstance(dias, int) and dias <= 7 else "#b45309" if isinstance(dias, int) and dias <= 30 else "#334155"
        filas += (
            f"<tr><td>{html.escape(str(evento.get('tipo','')))}</td>"
            f"<td>{html.escape(str(evento.get('entidad','')))}</td>"
            f"<td>{html.escape(str(evento.get('proceso','')))}</td>"
            f"<td>{html.escape(str(evento.get('fecha','')))}</td>"
            f"<td style='color:{color};font-weight:600'>{html.escape(str(dias))}</td></tr>"
        )
    cuerpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:900px;margin:auto">
      <h2 style="color:#534AB7">KAM Intelligence · Alertas del calendario</h2>
      <p>{len(eventos)} fechas requieren seguimiento.</p>
      <table style="border-collapse:collapse;width:100%" border="1" cellpadding="7">
        <thead><tr><th>Alerta</th><th>Entidad</th><th>Proceso</th><th>Fecha</th><th>Días</th></tr></thead>
        <tbody>{filas}</tbody>
      </table>
      <p style="color:#64748b;font-size:12px">Fuente: KAM Intelligence · OECE OCDS y contratos registrados.</p>
    </div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"KAM Intelligence · {len(eventos)} alertas de fechas"
    msg["From"], msg["To"] = remitente, destinatario
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))
    receptores = [correo.strip() for correo in destinatario.split(",") if correo.strip()]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(remitente, app_password)
        server.sendmail(remitente, receptores, msg.as_string())
    return True


def obtener_eventos_calendario_sheets(sh, dias_cierres=30, dias_contratos=180) -> list[dict]:
    """Construye alertas de cierres de oferta y fin de contrato desde Google Sheets."""
    hoy = date.today()
    eventos, vistos = [], set()

    def agregar(tipo, row, fecha_texto, limite):
        fecha_texto = str(fecha_texto or "")[:10]
        try:
            fecha_evento = datetime.strptime(fecha_texto, "%Y-%m-%d").date()
        except ValueError:
            return
        dias = (fecha_evento - hoy).days
        if dias < 0 or dias > limite:
            return
        clave = (tipo, str(row.get("id", "")), fecha_texto)
        if clave in vistos:
            return
        vistos.add(clave)
        eventos.append({
            "tipo": tipo, "entidad": row.get("entidad", ""),
            "proceso": row.get("id", ""), "fecha": fecha_texto, "dias": dias,
        })

    for row in sh.worksheet("procesos").get_all_records():
        agregar("Cierre de proceso menor", row, row.get("finCotz"), dias_cierres)
        agregar("Fin de contrato menor", row, row.get("finContrato"), dias_contratos)
    for row in sh.worksheet("licitaciones").get_all_records():
        agregar("Cierre de licitación", row, row.get("fecha_cierre"), dias_cierres)
        agregar("Fin de contrato", row, row.get("fin_contrato"), dias_contratos)
    return eventos


def main():
    parser = argparse.ArgumentParser(description="Sincroniza oportunidades TI oficiales de OECE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--local", action="store_true", help="Guarda en licitaciones.json en vez de Google Sheets")
    parser.add_argument("--fecha", type=str, default=None)
    parser.add_argument("--dias", type=int, default=7)
    parser.add_argument("--max-paginas", type=int, default=2)
    parser.add_argument("--sin-alertas-calendario", action="store_true",
                        help="No envía el resumen de calendario; útil para sincronizaciones frecuentes")
    args = parser.parse_args()
    hasta = datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha else date.today()
    desde = hasta - timedelta(days=args.dias)
    log.info("Buscando oportunidades TI oficiales: %s a %s", desde, hasta)
    menores, candidatas, stats = descargar_oportunidades_oece(desde, hasta, args.max_paginas)
    log.info("Resultado: %s", stats)

    if args.local:
        def ids_local(ruta):
            try:
                with open(ruta, encoding="utf-8") as archivo:
                    return set(json.load(archivo))
            except (FileNotFoundError, json.JSONDecodeError):
                return set()
        nuevas = deduplicar_por_id(
            [item for item in candidatas if item["id"] not in ids_local("licitaciones.json")]
        )
        menores_nuevos = deduplicar_por_id(
            [item for item in menores if item["id"] not in ids_local("procesos.json")]
        )
        nuevas = [enriquecer_decision_con_bases(item) for item in nuevas]
        menores_nuevos = [enriquecer_decision_con_bases(item) for item in menores_nuevos]
        if not args.dry_run:
            guardar_local(nuevas, "licitaciones.json")
            guardar_local(menores_nuevos, "procesos.json")
    else:
        sh = conectar_sheets()
        ids_licitaciones = obtener_ids_existentes(sh, "licitaciones")
        ids_procesos = obtener_ids_existentes(sh, "procesos")
        nuevas = deduplicar_por_id([item for item in candidatas if item["id"] not in ids_licitaciones])
        menores_nuevos = deduplicar_por_id([item for item in menores if item["id"] not in ids_procesos])
        nuevas = [enriquecer_decision_con_bases(item) for item in nuevas]
        menores_nuevos = [enriquecer_decision_con_bases(item) for item in menores_nuevos]
        if not args.dry_run:
            guardar_en_sheets(sh, nuevas, "licitaciones")
            guardar_en_sheets(sh, menores_nuevos, "procesos")

    log.info("Nuevas sin duplicados: %d menores y %d licitaciones", len(menores_nuevos), len(nuevas))
    for lic in nuevas[:10]:
        log.info("%s | %s | %s | score %s", lic["id"], lic["entidad"], lic["subcategoria_ti"], lic["score_ti"])
    enviar_email(nuevas, menores_nuevos, dry_run=args.dry_run)
    if not args.local and not args.sin_alertas_calendario:
        eventos = obtener_eventos_calendario_sheets(sh)
        log.info("Alertas de calendario detectadas: %d", len(eventos))
        enviar_alerta_eventos(eventos, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
