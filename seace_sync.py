#!/usr/bin/env python3
"""
seace_sync.py — KAM Intelligence v2 · Qubits SAC
Sincroniza licitaciones TI nuevas desde la API OCDS de OECE/SEACE
a Google Sheets y envía reporte diario por Gmail.

Ejecución:
    python3 seace_sync.py                  # manual
    python3 seace_sync.py --dry-run        # sin guardar ni enviar email
    python3 seace_sync.py --fecha 2026-07-02  # fecha específica

GitHub Actions: corre automáticamente cada día a 7am Lima (UTC-5 = 12:00 UTC)
"""

import os
import sys
import json
import re
import time
import argparse
import logging
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
import gspread
from google.oauth2.service_account import Credentials

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
SPREADSHEET_ID   = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"
HOJA_LICITACIONES = "licitaciones"
HOJA_SYNC_LOG    = "sync_log"          # hoja de log de sincronización (se crea sola)

# Email
GMAIL_FROM       = os.environ.get("GMAIL_FROM", "")       # tu@gmail.com
GMAIL_TO         = os.environ.get("GMAIL_TO", "")         # destinatario (puede ser el mismo)
GMAIL_APP_PASS   = os.environ.get("GMAIL_APP_PASS", "")   # App Password de Gmail

# API OCDS de OECE — endpoint de búsqueda
OCDS_BASE        = "https://contratacionesabiertas.osce.gob.pe/api"
OCDS_RELEASES    = f"{OCDS_BASE}/releases/"

# Palabras clave TI relevantes para Qubits SAC
KEYWORDS_TI = [
    "nube", "cloud", "correo electrónico", "microsoft", "google workspace",
    "oracle", "ciberseguridad", "kaspersky", "software", "licencias",
    "internet", "hosting", "seguridad informática", "backup", "respaldo",
    "servidor", "storage", "almacenamiento", "antivirus", "firewall",
    "conectividad", "vpn", "soporte técnico", "mesa de ayuda", "helpdesk",
    "infraestructura", "datacenter", "virtualización", "office 365",
    "microsoft 365", "aws", "azure", "huawei cloud", "veeam", "check point",
    "palo alto", "fortinet", "cisco", "redes", "switching", "routing",
    "correo corporativo", "videoconferencia", "zoom", "teams", "colaboración",
    "erp", "crm", "plataforma digital", "transformación digital",
    "soporte ti", "mantenimiento de equipos", "servicio de ti",
    "desarrollo de software", "sistema de información", "base de datos",
]

# Montos mínimos para filtrar (>8 UIT = S/ 33,200 en 2026 para licitaciones)
MONTO_MIN_LICITACIONES = 33200

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def conectar_sheets():
    """Conecta a Google Sheets usando credenciales del entorno o archivo local."""
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds_dict = None

    # Opción 1: Variable de entorno (GitHub Actions)
    creds_json = os.environ.get("GSHEETS_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)

    # Opción 2: Archivo local (tu Mac)
    elif os.path.exists("gsheets_credentials.json"):
        with open("gsheets_credentials.json", "r") as f:
            creds_dict = json.load(f)

    if not creds_dict:
        raise ValueError("No se encontraron credenciales de Google Sheets. "
                         "Define GSHEETS_CREDENTIALS_JSON o coloca gsheets_credentials.json en la carpeta.")

    creds  = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sh     = client.open_by_key(SPREADSHEET_ID)
    return sh

def obtener_ids_existentes(sh):
    """Retorna un set con todos los IDs de licitaciones ya registradas en Sheets."""
    try:
        ws = sh.worksheet(HOJA_LICITACIONES)
        registros = ws.get_all_records()
        return {str(r.get("id", "")).strip() for r in registros if r.get("id")}
    except Exception as e:
        log.warning(f"No se pudo leer la hoja de licitaciones: {e}")
        return set()

def guardar_licitaciones_sheets(sh, nuevas):
    """Agrega las licitaciones nuevas al final de la hoja de Google Sheets."""
    if not nuevas:
        return
    try:
        ws = sh.worksheet(HOJA_LICITACIONES)
        registros_actuales = ws.get_all_records()

        # Obtener columnas actuales
        if registros_actuales:
            columnas = list(registros_actuales[0].keys())
        else:
            columnas = [
                "id", "titulo", "entidad", "region", "tipo_licitacion",
                "tipo_contratacion", "estado", "monto_base", "ganador",
                "monto_adjudicado", "publicado", "adjudicacion",
                "inicio_contrato", "fin_contrato", "duracion_dias",
                "moneda", "empresas_participantes", "fuente",
                "_agregada_el", "_sync_automatico"
            ]
            if not registros_actuales:
                ws.append_row(columnas)

        filas = []
        for lic in nuevas:
            fila = [str(lic.get(col, "")) for col in columnas]
            filas.append(fila)

        ws.append_rows(filas, value_input_option="RAW")
        log.info(f"✅ {len(nuevas)} licitaciones guardadas en Google Sheets")
    except Exception as e:
        log.error(f"Error al guardar en Sheets: {e}")
        raise

def registrar_log_sync(sh, fecha, total_consultadas, total_nuevas, errores=""):
    """Registra el resultado de la sincronización en la hoja sync_log."""
    try:
        try:
            ws = sh.worksheet(HOJA_SYNC_LOG)
        except Exception:
            ws = sh.add_worksheet(title=HOJA_SYNC_LOG, rows=1000, cols=10)
            ws.append_row(["fecha", "hora", "total_consultadas", "total_nuevas", "errores"])

        ws.append_row([
            fecha,
            datetime.now().strftime("%H:%M:%S"),
            total_consultadas,
            total_nuevas,
            errores
        ])
    except Exception as e:
        log.warning(f"No se pudo registrar el log de sync: {e}")

# ── API OECE/SEACE ─────────────────────────────────────────────────────────────
def es_relevante_ti(titulo, descripcion=""):
    """Verifica si una licitación es relevante para Qubits (TI/cloud/seguridad)."""
    texto = f"{titulo} {descripcion}".lower()
    texto = re.sub(r'[áàä]', 'a', texto)
    texto = re.sub(r'[éèë]', 'e', texto)
    texto = re.sub(r'[íìï]', 'i', texto)
    texto = re.sub(r'[óòö]', 'o', texto)
    texto = re.sub(r'[úùü]', 'u', texto)
    for kw in KEYWORDS_TI:
        kw_norm = kw.lower()
        kw_norm = re.sub(r'[áàä]', 'a', kw_norm)
        kw_norm = re.sub(r'[éèë]', 'e', kw_norm)
        kw_norm = re.sub(r'[íìï]', 'i', kw_norm)
        kw_norm = re.sub(r'[óòö]', 'o', kw_norm)
        kw_norm = re.sub(r'[úùü]', 'u', kw_norm)
        if kw_norm in texto:
            return True
    return False

def parsear_release_ocds(release):
    """Convierte un release OCDS al formato de licitación de KAM Intelligence."""
    try:
        tender   = release.get("tender", {})
        parties  = {p.get("id"): p for p in release.get("parties", [])}
        buyer_id = release.get("buyer", {}).get("id", "")
        buyer    = parties.get(buyer_id, {})
        awards   = release.get("awards", [])
        contracts = release.get("contracts", [])

        # ID
        ocid = release.get("ocid", "")
        id_seace = ocid.replace("ocds-dgv273-seacev3-", "").replace("ocds-", "")

        # Título
        titulo = tender.get("title", release.get("description", ""))

        # Entidad
        entidad = buyer.get("name", "")

        # Región — extraída de la dirección del comprador
        address = buyer.get("address", {})
        region  = address.get("region", address.get("locality", ""))

        # Tipo de licitación
        metodo = tender.get("procurementMethodDetails", "")
        tipo_licitacion = metodo

        # Tipo de contratación (Bienes/Servicios/Obras)
        categoria = tender.get("mainProcurementCategory", "")
        mapa_cat = {"goods": "Bien", "services": "Servicio", "works": "Obra"}
        tipo_contratacion = mapa_cat.get(categoria, categoria)

        # Estado
        estado_raw = tender.get("status", "")
        mapa_estado = {
            "active": "Abierto para participar",
            "cancelled": "Cancelado",
            "complete": "Contrato Firmado",
            "unsuccessful": "Desierto — ninguna propuesta cumplió los requisitos",
            "planned": "Publicado",
        }
        estado = mapa_estado.get(estado_raw, estado_raw)

        # Monto base
        valor = tender.get("value", {})
        monto_base = valor.get("amount", 0) or 0
        moneda = valor.get("currency", "PEN")

        # Fechas
        publicado = release.get("date", "")[:10] if release.get("date") else ""
        tender_period = tender.get("tenderPeriod", {})
        adj_date = ""

        # Ganador y monto adjudicado
        ganador = ""
        monto_adjudicado = 0
        for award in awards:
            if award.get("status") == "active":
                suppliers = award.get("suppliers", [])
                if suppliers:
                    ganador = suppliers[0].get("name", "")
                monto_adjudicado = award.get("value", {}).get("amount", 0) or 0
                adj_date = award.get("date", "")[:10] if award.get("date") else ""
                break

        # Contrato
        inicio_contrato = ""
        fin_contrato    = ""
        duracion_dias   = ""
        for contrato in contracts:
            period = contrato.get("period", {})
            inicio_contrato = (period.get("startDate", "")[:10]
                               if period.get("startDate") else "")
            fin_contrato    = (period.get("endDate", "")[:10]
                               if period.get("endDate") else "")
            if inicio_contrato and fin_contrato:
                try:
                    d1 = datetime.strptime(inicio_contrato, "%Y-%m-%d")
                    d2 = datetime.strptime(fin_contrato, "%Y-%m-%d")
                    duracion_dias = (d2 - d1).days
                except Exception:
                    pass
            break

        # N° postores
        n_tenderers = len(tender.get("tenderers", []))

        return {
            "id":                  id_seace,
            "titulo":              titulo[:200],
            "entidad":             entidad,
            "region":              region,
            "tipo_licitacion":     tipo_licitacion,
            "tipo_contratacion":   tipo_contratacion,
            "estado":              estado,
            "monto_base":          monto_base,
            "ganador":             ganador,
            "monto_adjudicado":    monto_adjudicado,
            "publicado":           publicado,
            "adjudicacion":        adj_date,
            "inicio_contrato":     inicio_contrato,
            "fin_contrato":        fin_contrato,
            "duracion_dias":       duracion_dias,
            "moneda":              moneda,
            "empresas_participantes": n_tenderers,
            "fuente":              "SEACE-OCDS-AUTO",
            "_agregada_el":        datetime.now().strftime("%Y-%m-%d %H:%M"),
            "_sync_automatico":    "SI",
        }
    except Exception as e:
        log.warning(f"Error parseando release: {e}")
        return None

def consultar_api_ocds(fecha_desde, fecha_hasta, max_paginas=10):
    """
    Consulta la API OCDS de OECE para obtener releases publicados entre
    fecha_desde y fecha_hasta. Retorna lista de releases.
    """
    releases_encontrados = []
    pagina = 1
    offset = 0
    limit  = 100

    while pagina <= max_paginas:
        params = {
            "limit":        limit,
            "offset":       offset,
            "date_after":   fecha_desde,
            "date_before":  fecha_hasta,
        }
        try:
            resp = requests.get(
                OCDS_RELEASES,
                params=params,
                timeout=30,
                headers={"Accept": "application/json"}
            )
            if resp.status_code != 200:
                log.warning(f"API respondió {resp.status_code} en página {pagina}")
                break

            data = resp.json()
            releases = data.get("results", data.get("releases", []))

            if not releases:
                log.info(f"Sin más resultados en página {pagina}")
                break

            releases_encontrados.extend(releases)
            log.info(f"Página {pagina}: {len(releases)} releases obtenidos")

            # Paginación
            siguiente = data.get("next")
            if not siguiente:
                break

            offset  += limit
            pagina  += 1
            time.sleep(0.5)  # Respetar rate limit

        except requests.exceptions.ConnectionError:
            log.warning("No se pudo conectar a la API OCDS. "
                        "Verificar conectividad o que el endpoint esté disponible.")
            break
        except Exception as e:
            log.error(f"Error consultando API OCDS: {e}")
            break

    return releases_encontrados

# ── EMAIL ─────────────────────────────────────────────────────────────────────
def generar_html_reporte(nuevas, fecha, total_consultadas):
    """Genera el HTML del reporte diario para Gmail."""
    n = len(nuevas)
    color_header = "#534AB7" if n > 0 else "#888780"

    filas_html = ""
    for lic in nuevas[:50]:  # máximo 50 en el email
        monto = lic.get("monto_base", 0)
        monto_str = f"S/ {float(monto):,.0f}" if monto else "Por definir"
        estado_color = {
            "Abierto para participar": "#0F6E56",
            "Publicado":               "#185FA5",
            "Contrato Firmado":        "#534AB7",
        }.get(lic.get("estado", ""), "#888780")

        filas_html += f"""
        <tr style="border-bottom:1px solid #E2E8F0">
          <td style="padding:10px 8px;font-size:12px;color:#1C2E4A;font-weight:500">
            {lic.get('entidad','')[:50]}
          </td>
          <td style="padding:10px 8px;font-size:11px;color:#64748B">
            {lic.get('titulo','')[:80]}
          </td>
          <td style="padding:10px 8px;font-size:11px;color:#64748B">
            {lic.get('tipo_licitacion','')[:30]}
          </td>
          <td style="padding:10px 8px;font-size:12px;font-weight:500;color:#534AB7;text-align:right">
            {monto_str}
          </td>
          <td style="padding:10px 8px;font-size:11px;color:{estado_color};text-align:center">
            {lic.get('estado','')}
          </td>
          <td style="padding:10px 8px;font-size:11px;color:#64748B;text-align:center">
            {lic.get('region','')}
          </td>
        </tr>"""

    if n > 50:
        filas_html += f"""
        <tr><td colspan="6" style="padding:10px;text-align:center;color:#888;font-size:11px">
          ... y {n - 50} licitaciones más disponibles en KAM Intelligence
        </td></tr>"""

    if n == 0:
        filas_html = """
        <tr><td colspan="6" style="padding:20px;text-align:center;color:#888;font-size:13px">
          No se encontraron licitaciones TI nuevas hoy.
        </td></tr>"""

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Calibri,Arial,sans-serif;background:#F8F9FC;margin:0;padding:20px">
      <div style="max-width:900px;margin:0 auto;background:white;border-radius:10px;
                  box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden">

        <!-- Header -->
        <div style="background:{color_header};padding:24px 28px">
          <div style="font-size:11px;color:rgba(255,255,255,0.7);letter-spacing:2px;
                      text-transform:uppercase;margin-bottom:6px">
            QUBITS SAC · KAM Intelligence v2
          </div>
          <div style="font-size:22px;color:white;font-weight:600">
            Reporte Diario SEACE
          </div>
          <div style="font-size:13px;color:rgba(255,255,255,0.8);margin-top:4px">
            {fecha} · {n} licitaciones TI nuevas encontradas de {total_consultadas} consultadas
          </div>
        </div>

        <!-- KPIs -->
        <div style="display:flex;gap:0;border-bottom:1px solid #E2E8F0">
          <div style="flex:1;padding:16px 20px;border-right:1px solid #E2E8F0">
            <div style="font-size:24px;font-weight:600;color:#534AB7">{n}</div>
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px">
              Nuevas licitaciones TI
            </div>
          </div>
          <div style="flex:1;padding:16px 20px;border-right:1px solid #E2E8F0">
            <div style="font-size:24px;font-weight:600;color:#0F6E56">{total_consultadas}</div>
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px">
              Total consultadas en SEACE
            </div>
          </div>
          <div style="flex:1;padding:16px 20px">
            <div style="font-size:24px;font-weight:600;color:#BA7517">
              S/ {sum(float(l.get('monto_base',0) or 0) for l in nuevas)/1e6:.1f}M
            </div>
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px">
              Mercado potencial hoy
            </div>
          </div>
        </div>

        <!-- Tabla -->
        <div style="padding:20px 0">
          <div style="padding:0 20px 10px;font-size:13px;font-weight:600;color:#1C2E4A">
            Licitaciones TI detectadas hoy
          </div>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-collapse:collapse;font-size:12px">
            <thead>
              <tr style="background:#F1EFF8">
                <th style="padding:8px;text-align:left;color:#534AB7;font-size:10px;
                           text-transform:uppercase;letter-spacing:0.5px">Entidad</th>
                <th style="padding:8px;text-align:left;color:#534AB7;font-size:10px;
                           text-transform:uppercase">Título</th>
                <th style="padding:8px;text-align:left;color:#534AB7;font-size:10px;
                           text-transform:uppercase">Tipo</th>
                <th style="padding:8px;text-align:right;color:#534AB7;font-size:10px;
                           text-transform:uppercase">Monto</th>
                <th style="padding:8px;text-align:center;color:#534AB7;font-size:10px;
                           text-transform:uppercase">Estado</th>
                <th style="padding:8px;text-align:center;color:#534AB7;font-size:10px;
                           text-transform:uppercase">Región</th>
              </tr>
            </thead>
            <tbody>{filas_html}</tbody>
          </table>
        </div>

        <!-- Footer -->
        <div style="padding:16px 20px;background:#F8F9FC;border-top:1px solid #E2E8F0;
                    font-size:11px;color:#888;text-align:center">
          Generado automáticamente por KAM Intelligence v2 · Qubits SAC ·
          Las licitaciones nuevas ya están disponibles en tu app.
        </div>
      </div>
    </body>
    </html>
    """

def enviar_email(nuevas, fecha, total_consultadas, dry_run=False):
    """Envía el reporte diario por Gmail."""
    if not GMAIL_FROM or not GMAIL_TO or not GMAIL_APP_PASS:
        log.warning("Credenciales de Gmail no configuradas. No se envía email.")
        return

    if dry_run:
        log.info("DRY RUN: email no enviado")
        return

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text       import MIMEText

    n = len(nuevas)
    asunto = (f"🔔 KAM Intelligence · {n} licitaciones TI nuevas hoy ({fecha})"
              if n > 0
              else f"KAM Intelligence · Sin licitaciones TI nuevas hoy ({fecha})")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg.attach(MIMEText(generar_html_reporte(nuevas, fecha, total_consultadas), "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASS)
            server.sendmail(GMAIL_FROM, GMAIL_TO, msg.as_string())
        log.info(f"✅ Email enviado a {GMAIL_TO}: '{asunto}'")
    except Exception as e:
        log.error(f"Error enviando email: {e}")

# ── FLUJO PRINCIPAL ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="KAM Intelligence — Sync SEACE")
    parser.add_argument("--dry-run",  action="store_true",
                        help="No guardar en Sheets ni enviar email")
    parser.add_argument("--fecha",    type=str, default=None,
                        help="Fecha específica YYYY-MM-DD (default: ayer)")
    parser.add_argument("--dias",     type=int, default=1,
                        help="Número de días hacia atrás a consultar (default: 1)")
    args = parser.parse_args()

    # Fechas
    if args.fecha:
        fecha_hasta = datetime.strptime(args.fecha, "%Y-%m-%d")
    else:
        fecha_hasta = datetime.now() - timedelta(hours=1)  # 1h de margen

    fecha_desde = fecha_hasta - timedelta(days=args.dias)
    fecha_str   = fecha_hasta.strftime("%Y-%m-%d")
    fecha_desde_str = fecha_desde.strftime("%Y-%m-%dT00:00:00")
    fecha_hasta_str = fecha_hasta.strftime("%Y-%m-%dT23:59:59")

    log.info(f"{'='*60}")
    log.info(f"KAM Intelligence — Sync SEACE · {fecha_str}")
    log.info(f"Rango: {fecha_desde_str} → {fecha_hasta_str}")
    log.info(f"Dry run: {args.dry_run}")
    log.info(f"{'='*60}")

    # 1. Conectar a Google Sheets
    log.info("Conectando a Google Sheets...")
    try:
        sh = conectar_sheets()
        ids_existentes = obtener_ids_existentes(sh)
        log.info(f"IDs existentes en Sheets: {len(ids_existentes)}")
    except Exception as e:
        log.error(f"Error conectando a Sheets: {e}")
        sys.exit(1)

    # 2. Consultar API OCDS
    log.info("Consultando API OCDS de OECE/SEACE...")
    releases = consultar_api_ocds(fecha_desde_str, fecha_hasta_str)
    total_consultadas = len(releases)
    log.info(f"Releases obtenidos de SEACE: {total_consultadas}")

    # 3. Filtrar por TI y por duplicados
    nuevas = []
    for release in releases:
        tender = release.get("tender", {})
        titulo = tender.get("title", release.get("description", ""))

        # Solo categoría Servicios (services)
        categoria = tender.get("mainProcurementCategory", "")
        if categoria not in ("services", "goods", ""):
            continue

        # Filtrar por palabras clave TI
        if not es_relevante_ti(titulo, tender.get("description", "")):
            continue

        # Parsear
        lic = parsear_release_ocds(release)
        if not lic:
            continue

        # Filtrar monto mínimo
        monto = lic.get("monto_base", 0) or 0
        if float(monto) < MONTO_MIN_LICITACIONES and float(monto) > 0:
            continue

        # Verificar duplicado
        if lic["id"] in ids_existentes:
            log.debug(f"Duplicado ignorado: {lic['id']}")
            continue

        nuevas.append(lic)
        ids_existentes.add(lic["id"])  # evitar duplicados dentro del mismo lote

    log.info(f"Licitaciones TI nuevas (sin duplicados): {len(nuevas)}")

    # 4. Guardar en Google Sheets
    if nuevas and not args.dry_run:
        guardar_licitaciones_sheets(sh, nuevas)
    elif args.dry_run and nuevas:
        log.info(f"DRY RUN — Se habrían guardado {len(nuevas)} licitaciones:")
        for lic in nuevas[:5]:
            log.info(f"  · {lic['id']} | {lic['entidad'][:40]} | {lic['titulo'][:50]}")
        if len(nuevas) > 5:
            log.info(f"  ... y {len(nuevas) - 5} más")

    # 5. Registrar log de sync
    if not args.dry_run:
        registrar_log_sync(sh, fecha_str, total_consultadas, len(nuevas))

    # 6. Enviar reporte por email
    enviar_email(nuevas, fecha_str, total_consultadas, dry_run=args.dry_run)

    log.info(f"{'='*60}")
    log.info(f"Sync completado · {len(nuevas)} nuevas licitaciones TI encontradas")
    log.info(f"{'='*60}")

    return len(nuevas)

if __name__ == "__main__":
    main()
