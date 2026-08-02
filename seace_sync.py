#!/usr/bin/env python3
"""
seace_sync.py — KAM Intelligence v2 · Qubits SAC
Descarga licitaciones TI nuevas desde licitacionesperu.pe (vía todolicitaciones.pe)
las filtra por palabras clave TI, las guarda en Google Sheets y envía reporte Gmail.

Ejecución:
    python3 seace_sync.py                  # manual
    python3 seace_sync.py --dry-run        # sin guardar ni enviar email
    python3 seace_sync.py --dias 3         # últimos 3 días
"""

import os, sys, json, re, time, argparse, logging, smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
import gspread
from google.oauth2.service_account import Credentials

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
SPREADSHEET_ID    = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"
HOJA_LICITACIONES = "licitaciones"
HOJA_SYNC_LOG     = "sync_log"

GMAIL_FROM     = os.environ.get("GMAIL_FROM", "")
GMAIL_TO       = os.environ.get("GMAIL_TO", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")

# Palabras clave TI para Qubits SAC
KEYWORDS_TI = [
    "nube","cloud","correo electronico","correo electrónico","microsoft","google workspace",
    "oracle","ciberseguridad","kaspersky","software","licencias","internet","hosting",
    "seguridad informatica","seguridad informática","backup","respaldo","servidor",
    "storage","almacenamiento","antivirus","firewall","conectividad","vpn",
    "soporte tecnico","soporte técnico","mesa de ayuda","helpdesk","infraestructura",
    "datacenter","virtualizacion","virtualización","office 365","microsoft 365",
    "aws","azure","huawei","veeam","check point","palo alto","fortinet","cisco",
    "correo corporativo","videoconferencia","zoom","teams","colaboracion","colaboración",
    "erp","crm","plataforma digital","transformacion digital","transformación digital",
    "servicio de ti","soporte ti","mantenimiento de equipos informaticos",
    "sistema de informacion","sistema de información","base de datos","red lan","red wan",
    "switching","routing","fibra optica","fibra óptica","correo institucional",
]

MONTO_MIN = 33200  # >8 UIT en soles 2026

# API de todolicitaciones.pe — búsqueda de servicios TI
TL_SEARCH_URL = "https://www.todolicitaciones.pe/busqueda/licitaciones"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def conectar_sheets():
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    creds_dict = None
    creds_json = os.environ.get("GSHEETS_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
    elif os.path.exists("gsheets_credentials.json"):
        with open("gsheets_credentials.json") as f:
            creds_dict = json.load(f)
    if not creds_dict:
        raise ValueError("No hay credenciales de Google Sheets.")
    creds  = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)

def obtener_ids_existentes(sh):
    try:
        ws = sh.worksheet(HOJA_LICITACIONES)
        return {str(r.get("id","")).strip() for r in ws.get_all_records() if r.get("id")}
    except Exception as e:
        log.warning(f"No se pudo leer Sheets: {e}")
        return set()

def guardar_en_sheets(sh, nuevas):
    if not nuevas:
        return
    ws = sh.worksheet(HOJA_LICITACIONES)
    registros = ws.get_all_records()
    columnas = list(registros[0].keys()) if registros else [
        "id","titulo","entidad","region","tipo_licitacion","tipo_contratacion",
        "estado","monto_base","ganador","monto_adjudicado","publicado","adjudicacion",
        "inicio_contrato","fin_contrato","duracion_dias","moneda","empresas_participantes",
        "fuente","_agregada_el","_sync_automatico"
    ]
    if not registros:
        ws.append_row(columnas)
    filas = [[str(lic.get(c,"")) for c in columnas] for lic in nuevas]
    ws.append_rows(filas, value_input_option="RAW")
    log.info(f"✅ {len(nuevas)} licitaciones guardadas en Google Sheets")

def registrar_log(sh, fecha, total, nuevas_n, error=""):
    try:
        try:
            ws = sh.worksheet(HOJA_SYNC_LOG)
        except:
            ws = sh.add_worksheet(title=HOJA_SYNC_LOG, rows=1000, cols=10)
            ws.append_row(["fecha","hora","total_consultadas","nuevas","error"])
        ws.append_row([fecha, datetime.now().strftime("%H:%M:%S"), total, nuevas_n, error])
    except Exception as e:
        log.warning(f"Error registrando log: {e}")

# ── SCRAPING LICITACIONESPERU.PE ───────────────────────────────────────────────
def normalizar(texto):
    """Normaliza texto para comparación sin tildes ni mayúsculas."""
    t = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        t = t.replace(a, b)
    return t

def es_ti(titulo, descripcion=""):
    texto = normalizar(f"{titulo} {descripcion}")
    return any(normalizar(kw) in texto for kw in KEYWORDS_TI)

def buscar_licitaciones_ti(fecha_desde_str, fecha_hasta_str, max_paginas=5):
    """
    Busca licitaciones en licitacionesperu.pe usando la API interna de búsqueda.
    Retorna lista de dicts con datos básicos de cada licitación.
    """
    resultados = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html",
        "Referer": "https://licitacionesperu.pe/",
    }

    # Búsqueda por palabras clave relevantes para TI
    terminos_busqueda = [
        "nube", "cloud", "correo electronico", "microsoft", "software",
        "ciberseguridad", "licencias", "servidor", "internet", "storage",
        "oracle", "backup", "soporte tecnico", "infraestructura ti"
    ]

    ids_vistos = set()

    for termino in terminos_busqueda[:6]:  # máximo 6 términos para no abusar
        for pagina in range(max_paginas):
            try:
                # API interna de licitacionesperu.pe
                url = f"https://licitacionesperu.pe/api/procesos"
                params = {
                    "q": termino,
                    "page": pagina + 1,
                    "per_page": 20,
                    "fecha_desde": fecha_desde_str,
                    "fecha_hasta": fecha_hasta_str,
                    "categoria": "servicios",
                }
                resp = requests.get(url, params=params, headers=headers, timeout=15)

                if resp.status_code == 404 or resp.status_code == 405:
                    # Intentar endpoint alternativo
                    break

                if resp.status_code != 200:
                    log.warning(f"HTTP {resp.status_code} para '{termino}' p{pagina+1}")
                    break

                data = resp.json()
                items = data.get("data", data.get("results", data.get("items", [])))

                if not items:
                    break

                for item in items:
                    id_proc = str(item.get("id", item.get("codigo", item.get("ocid", ""))))
                    if not id_proc or id_proc in ids_vistos:
                        continue
                    titulo = item.get("titulo", item.get("title", item.get("descripcion", "")))
                    if not es_ti(titulo):
                        continue
                    ids_vistos.add(id_proc)
                    resultados.append({
                        "id": id_proc,
                        "titulo": titulo[:200],
                        "entidad": item.get("entidad", item.get("entidadContratante", "")),
                        "region": item.get("region", item.get("departamento", "")),
                        "tipo_licitacion": item.get("tipoProcedimiento", item.get("tipo", "")),
                        "tipo_contratacion": item.get("tipoContratacion", item.get("categoria", "Servicio")),
                        "estado": item.get("estado", "Publicado"),
                        "monto_base": item.get("montoBase", item.get("valorReferencial", 0)) or 0,
                        "ganador": item.get("ganador", item.get("adjudicado", "")),
                        "monto_adjudicado": item.get("montoAdjudicado", 0) or 0,
                        "publicado": item.get("fechaPublicacion", item.get("fecha", ""))[:10] if item.get("fechaPublicacion", item.get("fecha")) else "",
                        "adjudicacion": "",
                        "inicio_contrato": "",
                        "fin_contrato": "",
                        "duracion_dias": "",
                        "moneda": "PEN",
                        "empresas_participantes": item.get("nPostores", 0) or 0,
                        "fuente": "LICITACIONESPERU-AUTO",
                        "_agregada_el": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "_sync_automatico": "SI",
                    })

                time.sleep(0.3)

            except requests.exceptions.ConnectionError as e:
                log.warning(f"No se pudo conectar para '{termino}': {e}")
                break
            except Exception as e:
                log.warning(f"Error buscando '{termino}' p{pagina+1}: {e}")
                break

    log.info(f"Licitaciones TI encontradas en búsqueda: {len(resultados)}")
    return resultados

def buscar_via_todolicitaciones(fecha_desde_str, fecha_hasta_str):
    """
    Alternativa: busca en todolicitaciones.pe que tiene API más documentada.
    """
    resultados = []
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KAMIntelligence/2.0; +https://github.com/acernar)",
        "Accept": "application/json",
    }

    terminos = ["nube", "cloud", "software", "correo", "ciberseguridad", "oracle", "microsoft"]
    ids_vistos = set()

    for termino in terminos[:4]:
        try:
            # API de búsqueda de todolicitaciones.pe
            url = "https://www.todolicitaciones.pe/api/licitaciones/buscar"
            params = {
                "q": termino,
                "categoria": "servicios",
                "fecha_desde": fecha_desde_str,
                "fecha_hasta": fecha_hasta_str,
                "page": 1,
                "per_page": 20,
            }
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code not in (200, 201):
                log.warning(f"todolicitaciones HTTP {resp.status_code} para '{termino}'")
                continue

            data = resp.json()
            items = data.get("licitaciones", data.get("data", data.get("results", [])))

            for item in items:
                id_proc = str(item.get("id", item.get("codigo", "")))
                if not id_proc or id_proc in ids_vistos:
                    continue
                titulo = item.get("titulo", item.get("descripcion", ""))
                if not es_ti(titulo):
                    continue
                monto = float(item.get("monto", item.get("valor", 0)) or 0)
                if monto > 0 and monto < MONTO_MIN:
                    continue
                ids_vistos.add(id_proc)
                resultados.append({
                    "id": id_proc,
                    "titulo": titulo[:200],
                    "entidad": item.get("entidad", ""),
                    "region": item.get("region", item.get("departamento", "")),
                    "tipo_licitacion": item.get("tipo", ""),
                    "tipo_contratacion": "Servicio",
                    "estado": item.get("estado", "Publicado"),
                    "monto_base": monto,
                    "ganador": "",
                    "monto_adjudicado": 0,
                    "publicado": (item.get("fecha_publicacion", "")[:10]
                                  if item.get("fecha_publicacion") else ""),
                    "adjudicacion": "", "inicio_contrato": "",
                    "fin_contrato": "", "duracion_dias": "",
                    "moneda": "PEN",
                    "empresas_participantes": 0,
                    "fuente": "TODOLICITACIONES-AUTO",
                    "_agregada_el": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "_sync_automatico": "SI",
                })
            time.sleep(0.5)

        except Exception as e:
            log.warning(f"Error en todolicitaciones '{termino}': {e}")

    log.info(f"Licitaciones desde todolicitaciones.pe: {len(resultados)}")
    return resultados

# ── EMAIL ─────────────────────────────────────────────────────────────────────
def enviar_email(nuevas, fecha, total, dry_run=False):
    if not all([GMAIL_FROM, GMAIL_TO, GMAIL_APP_PASS]):
        log.warning("Gmail no configurado — revisa los secrets GMAIL_FROM, GMAIL_TO, GMAIL_APP_PASS")
        return
    if dry_run:
        log.info("DRY RUN — email no enviado")
        return

    n = len(nuevas)
    asunto = (f"🔔 KAM Intelligence · {n} licitaciones TI nuevas · {fecha}"
              if n > 0 else f"KAM Intelligence · Sin licitaciones TI nuevas · {fecha}")

    filas = ""
    for lic in nuevas[:30]:
        monto = float(lic.get("monto_base", 0) or 0)
        monto_s = f"S/ {monto:,.0f}" if monto > 0 else "Por definir"
        filas += f"""
        <tr style="border-bottom:1px solid #E2E8F0">
          <td style="padding:8px;font-size:12px;font-weight:500;color:#1C2E4A">{lic.get('entidad','')[:45]}</td>
          <td style="padding:8px;font-size:11px;color:#64748B">{lic.get('titulo','')[:70]}</td>
          <td style="padding:8px;font-size:11px;color:#64748B">{lic.get('region','')}</td>
          <td style="padding:8px;font-size:12px;font-weight:500;color:#534AB7;text-align:right">{monto_s}</td>
        </tr>"""

    if n > 30:
        filas += f'<tr><td colspan="4" style="padding:8px;text-align:center;color:#888;font-size:11px">... y {n-30} más en KAM Intelligence</td></tr>'
    if n == 0:
        filas = '<tr><td colspan="4" style="padding:20px;text-align:center;color:#888">Sin licitaciones TI nuevas hoy.</td></tr>'

    monto_total = sum(float(l.get("monto_base", 0) or 0) for l in nuevas)
    html = f"""<!DOCTYPE html><html><body style="font-family:Calibri,Arial,sans-serif;background:#F8F9FC;margin:0;padding:20px">
    <div style="max-width:800px;margin:0 auto;background:white;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden">
      <div style="background:#534AB7;padding:20px 24px">
        <div style="font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;text-transform:uppercase">QUBITS SAC · KAM Intelligence v2</div>
        <div style="font-size:20px;color:white;font-weight:600;margin-top:4px">Reporte Diario SEACE · {fecha}</div>
        <div style="font-size:12px;color:rgba(255,255,255,0.8);margin-top:2px">{n} licitaciones TI nuevas encontradas</div>
      </div>
      <div style="display:flex;border-bottom:1px solid #E2E8F0">
        <div style="flex:1;padding:14px 18px;border-right:1px solid #E2E8F0">
          <div style="font-size:22px;font-weight:600;color:#534AB7">{n}</div>
          <div style="font-size:10px;color:#888;text-transform:uppercase">Nuevas licitaciones TI</div>
        </div>
        <div style="flex:1;padding:14px 18px;border-right:1px solid #E2E8F0">
          <div style="font-size:22px;font-weight:600;color:#0F6E56">{total}</div>
          <div style="font-size:10px;color:#888;text-transform:uppercase">Total consultadas</div>
        </div>
        <div style="flex:1;padding:14px 18px">
          <div style="font-size:22px;font-weight:600;color:#BA7517">S/ {monto_total/1e6:.1f}M</div>
          <div style="font-size:10px;color:#888;text-transform:uppercase">Mercado potencial</div>
        </div>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
        <thead><tr style="background:#F1EFF8">
          <th style="padding:8px;text-align:left;font-size:10px;color:#534AB7;text-transform:uppercase">Entidad</th>
          <th style="padding:8px;text-align:left;font-size:10px;color:#534AB7;text-transform:uppercase">Título</th>
          <th style="padding:8px;text-align:left;font-size:10px;color:#534AB7;text-transform:uppercase">Región</th>
          <th style="padding:8px;text-align:right;font-size:10px;color:#534AB7;text-transform:uppercase">Monto</th>
        </tr></thead>
        <tbody>{filas}</tbody>
      </table>
      <div style="padding:14px 18px;background:#F8F9FC;border-top:1px solid #E2E8F0;font-size:10px;color:#888;text-align:center">
        Generado automáticamente por KAM Intelligence v2 · Qubits SAC · Las licitaciones ya están en Google Sheets.
      </div>
    </div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASS)
            server.sendmail(GMAIL_FROM, GMAIL_TO, msg.as_string())
        log.info(f"✅ Email enviado: {asunto}")
    except Exception as e:
        log.error(f"Error enviando email: {e}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fecha",   type=str, default=None)
    parser.add_argument("--dias",    type=int, default=1)
    args = parser.parse_args()

    if args.fecha:
        fecha_hasta = datetime.strptime(args.fecha, "%Y-%m-%d")
    else:
        fecha_hasta = datetime.now()

    fecha_desde  = fecha_hasta - timedelta(days=args.dias)
    fecha_str    = fecha_hasta.strftime("%Y-%m-%d")
    desde_str    = fecha_desde.strftime("%Y-%m-%d")

    log.info("=" * 60)
    log.info(f"KAM Intelligence — Sync SEACE · {fecha_str}")
    log.info(f"Rango: {desde_str} → {fecha_str} · Dry run: {args.dry_run}")
    log.info("=" * 60)

    # Conectar Sheets
    log.info("Conectando a Google Sheets...")
    sh = conectar_sheets()
    ids_existentes = obtener_ids_existentes(sh)
    log.info(f"IDs existentes: {len(ids_existentes)}")

    # Buscar licitaciones TI
    log.info("Buscando licitaciones TI nuevas...")
    candidatas = buscar_licitaciones_ti(desde_str, fecha_str)

    # Si el primer método falla, intentar con todolicitaciones
    if len(candidatas) == 0:
        log.info("Intentando vía todolicitaciones.pe...")
        candidatas = buscar_via_todolicitaciones(desde_str, fecha_str)

    total_consultadas = len(candidatas)

    # Filtrar duplicados y monto mínimo
    nuevas = []
    ids_lote = set()
    for lic in candidatas:
        lid = lic.get("id", "")
        if not lid or lid in ids_existentes or lid in ids_lote:
            continue
        monto = float(lic.get("monto_base", 0) or 0)
        if 0 < monto < MONTO_MIN:
            continue
        nuevas.append(lic)
        ids_lote.add(lid)

    log.info(f"Licitaciones TI nuevas (sin duplicados): {len(nuevas)}")

    # Guardar
    if nuevas and not args.dry_run:
        guardar_en_sheets(sh, nuevas)
    elif args.dry_run and nuevas:
        log.info("DRY RUN — muestra:")
        for lic in nuevas[:3]:
            log.info(f"  · {lic['id']} | {lic['entidad'][:40]} | {lic['titulo'][:50]}")

    # Log y email
    if not args.dry_run:
        registrar_log(sh, fecha_str, total_consultadas, len(nuevas))
    enviar_email(nuevas, fecha_str, total_consultadas, dry_run=args.dry_run)

    log.info("=" * 60)
    log.info(f"Sync completado · {len(nuevas)} nuevas licitaciones TI")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
