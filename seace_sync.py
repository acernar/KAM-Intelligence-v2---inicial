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
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

API_BASE = "https://contratacionesabiertas.oece.gob.pe/api/v1"
SPREADSHEET_ID = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"
HOJA_LICITACIONES = "licitaciones"
HOJA_SYNC_LOG = "sync_log"
UIT_POR_ANIO = {2025: 5350, 2026: 5500}

GMAIL_FROM = os.environ.get("GMAIL_FROM", "")
GMAIL_TO = os.environ.get("GMAIL_TO", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")

# Una consulta por grupo reduce llamadas; el filtro de puntuación decide la relevancia final.
TERMINOS_BUSQUEDA = [
    "nube", "cloud", "correo electronico", "google workspace", "microsoft 365",
    "ciberseguridad", "firewall", "backup", "software", "hosting", "servidor",
    "almacenamiento", "base de datos", "videoconferencia", "inteligencia artificial",
    "mesa de ayuda", "saas", "devops",
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
        3: ["cloudflare", "firewall de aplicaciones", "waf", "ciberseguridad", "seguridad perimetral", "antiddos", "antispam"],
        2: ["seguridad informatica", "seguridad de la informacion", "proteccion de correo", "endpoint", "firewall"],
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
        2: ["servidor", "almacenamiento", "storage", "base de datos", "datacenter", "centro de datos", "virtualizacion"],
        1: ["infraestructura tecnologica", "conectividad", "fibra optica", "internet dedicado", "red lan", "red wan"],
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
    "correo y mensajeria a nivel nacional", "servicio de mensajeria local",
    "servicio de mensajeria nacional", "mensajeria fisica", "servicio courier",
    "reparacion de analizador", "analizador de presion", "equipo medico",
    "transporte de carga", "servicio de alimentacion", "obra de construccion",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).lower().split())


def evaluar_relevancia(titulo: str, descripcion: str = "") -> tuple[int, str, list[str]]:
    texto = normalizar(f"{titulo} {descripcion}")
    if any(frase in texto for frase in EXCLUSIONES):
        return 0, "No TI", ["exclusión comercial"]
    puntos_por_categoria = {}
    coincidencias_por_categoria = {}
    for categoria, reglas in REGLAS_TI.items():
        puntos = 0
        coincidencias = []
        for peso, frases in reglas.items():
            halladas = [frase for frase in frases if frase in texto]
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


def _get_json(url: str, params=None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QUBITS-KAM-Intelligence/3.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


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

    return {
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
        "ocid": record.get("ocid") or release.get("ocid") or "",
        "fecha_cierre": _fecha_corta(period.get("endDate")),
        "fuente": "OECE-OCDS-OFICIAL",
        "fuente_url": f"{API_BASE}/record/{record.get('ocid') or release.get('ocid') or ''}",
        "subcategoria_ti": subcategoria,
        "score_ti": score,
        "coincidencias_ti": coincidencias,
        "_agregada_el": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "_sync_automatico": "SI",
    }


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


def enviar_email(nuevas: list[dict], dry_run=False):
    if dry_run or not nuevas or not all([GMAIL_FROM, GMAIL_TO, GMAIL_APP_PASS]):
        return
    filas = "".join(
        f"<tr><td>{lic['entidad']}</td><td>{lic['titulo']}</td><td>S/ {lic['monto_base']:,.0f}</td><td>{lic['subcategoria_ti']}</td></tr>"
        for lic in nuevas[:30]
    )
    contenido = f"<h2>{len(nuevas)} oportunidades TI nuevas</h2><table>{filas}</table>"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"KAM Intelligence · {len(nuevas)} oportunidades TI OECE"
    msg["From"], msg["To"] = GMAIL_FROM, GMAIL_TO
    msg.attach(MIMEText(contenido, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_FROM, GMAIL_APP_PASS)
        server.sendmail(GMAIL_FROM, GMAIL_TO, msg.as_string())


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
        nuevas = [item for item in candidatas if item["id"] not in ids_local("licitaciones.json")]
        menores_nuevos = [item for item in menores if item["id"] not in ids_local("procesos.json")]
        if not args.dry_run:
            guardar_local(nuevas, "licitaciones.json")
            guardar_local(menores_nuevos, "procesos.json")
    else:
        sh = conectar_sheets()
        ids_licitaciones = obtener_ids_existentes(sh, "licitaciones")
        ids_procesos = obtener_ids_existentes(sh, "procesos")
        nuevas = [item for item in candidatas if item["id"] not in ids_licitaciones]
        menores_nuevos = [item for item in menores if item["id"] not in ids_procesos]
        if not args.dry_run:
            guardar_en_sheets(sh, nuevas, "licitaciones")
            guardar_en_sheets(sh, menores_nuevos, "procesos")

    log.info("Nuevas sin duplicados: %d menores y %d licitaciones", len(menores_nuevos), len(nuevas))
    for lic in nuevas[:10]:
        log.info("%s | %s | %s | score %s", lic["id"], lic["entidad"], lic["subcategoria_ti"], lic["score_ti"])
    enviar_email(nuevas, dry_run=args.dry_run)
    if not args.local:
        eventos = obtener_eventos_calendario_sheets(sh)
        log.info("Alertas de calendario detectadas: %d", len(eventos))
        enviar_alerta_eventos(eventos, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
