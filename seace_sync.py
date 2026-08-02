#!/usr/bin/env python3
"""
seace_sync.py — KAM Intelligence v2 · Qubits SAC
Descarga licitaciones TI nuevas desde licitacionesperu.pe via scraping HTML,
filtra por palabras clave TI, guarda en Google Sheets y envía reporte Gmail.
"""

import os, sys, json, re, time, argparse, logging, smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID    = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"
HOJA_LICITACIONES = "licitaciones"
HOJA_SYNC_LOG     = "sync_log"

GMAIL_FROM     = os.environ.get("GMAIL_FROM", "")
GMAIL_TO       = os.environ.get("GMAIL_TO", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")

KEYWORDS_TI = [
    "nube","cloud","correo electronico","correo electrónico","microsoft",
    "google workspace","oracle","ciberseguridad","kaspersky","software",
    "licencias","internet","hosting","seguridad informatica","seguridad informática",
    "backup","respaldo","servidor","storage","almacenamiento","antivirus",
    "firewall","conectividad","vpn","soporte tecnico","soporte técnico",
    "mesa de ayuda","helpdesk","infraestructura","datacenter","virtualizacion",
    "virtualización","office 365","microsoft 365","aws","azure","huawei",
    "veeam","check point","palo alto","fortinet","cisco","correo corporativo",
    "videoconferencia","zoom","teams","colaboracion","colaboración","erp","crm",
    "plataforma digital","transformacion digital","transformación digital",
    "soporte ti","mantenimiento informatico","sistema de informacion",
    "sistema de información","base de datos","red lan","red wan",
    "fibra optica","fibra óptica","correo institucional","licencia de software",
    "servicio de correo","servicio en la nube","infraestructura tecnologica",
    "infraestructura tecnológica","equipos informaticos","equipos informáticos",
]

MONTO_MIN = 33200

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-PE,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

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
        "inicio_contrato","fin_contrato","duracion_dias","moneda",
        "empresas_participantes","fuente","_agregada_el","_sync_automatico"
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
            ws = sh.add_worksheet(title=HOJA_SYNC_LOG, rows=1000, cols=6)
            ws.append_row(["fecha","hora","total_consultadas","nuevas","error"])
        ws.append_row([fecha, datetime.now().strftime("%H:%M:%S"),
                       total, nuevas_n, error])
    except Exception as e:
        log.warning(f"Error log: {e}")

# ── SCRAPING LICITACIONESPERU.PE ───────────────────────────────────────────────
def normalizar(texto):
    t = texto.lower()
    for a,b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        t = t.replace(a, b)
    return t

def es_ti(titulo, desc=""):
    texto = normalizar(f"{titulo} {desc}")
    return any(normalizar(kw) in texto for kw in KEYWORDS_TI)

def parsear_monto(texto):
    """Extrae monto numérico de strings como 'S/ 1,234,567.00'"""
    if not texto:
        return 0
    nums = re.sub(r'[^\d.]', '', texto.replace(',',''))
    try:
        return float(nums)
    except:
        return 0

def scrape_licitacionesperu(fecha_desde_str, max_paginas=10):
    """
    Scrapea licitacionesperu.pe buscando servicios TI publicados desde fecha_desde.
    Retorna lista de dicts con datos básicos.
    """
    resultados = []
    ids_vistos = set()

    # URLs de servicios en licitacionesperu.pe
    urls_base = [
        "https://licitacionesperu.pe/servicios/",
        "https://licitacionesperu.pe/licitaciones/?categoria=servicios",
    ]

    for url_base in urls_base[:1]:  # empezar con la primera
        for pagina in range(1, max_paginas + 1):
            url = f"{url_base}?page={pagina}" if pagina > 1 else url_base
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                if resp.status_code != 200:
                    log.warning(f"HTTP {resp.status_code} en {url}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # Buscar items de licitación — estructura típica de licitacionesperu.pe
                items = (soup.find_all("article") or
                         soup.find_all("div", class_=re.compile(r"licitacion|proceso|item|card", re.I)) or
                         soup.find_all("li", class_=re.compile(r"licitacion|proceso", re.I)))

                if not items:
                    # Intentar parsear tabla
                    items = soup.find_all("tr")[1:]  # skip header

                if not items:
                    log.info(f"Sin items en página {pagina} — fin de resultados")
                    break

                encontrados_pagina = 0
                for item in items:
                    texto_item = item.get_text(" ", strip=True)

                    # Extraer título
                    titulo_el = (item.find(["h2","h3","h4","a","strong"]) or
                                 item.find(class_=re.compile(r"titulo|title|nombre", re.I)))
                    titulo = titulo_el.get_text(strip=True) if titulo_el else texto_item[:100]

                    if not titulo or len(titulo) < 10:
                        continue

                    # Filtrar por TI
                    if not es_ti(titulo, texto_item):
                        continue

                    # Extraer ID/código
                    id_match = re.search(
                        r'((?:LP|LPA|CP|AS|DIRECTA|SIE|CDP|SCI)[A-Z0-9\-/]*\d[A-Z0-9\-/]*|'
                        r'ocds-dgv273-seacev3-\d+)',
                        texto_item
                    )
                    id_proc = id_match.group(0) if id_match else ""

                    # Si no hay ID del texto, buscar en href
                    if not id_proc:
                        enlace = item.find("a", href=True)
                        if enlace:
                            href = enlace.get("href","")
                            id_match2 = re.search(r'seacev3-(\d+)', href)
                            if id_match2:
                                id_proc = f"seacev3-{id_match2.group(1)}"

                    if not id_proc or id_proc in ids_vistos:
                        continue

                    # Verificar fecha (solo procesos recientes)
                    fecha_match = re.search(r'\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}', texto_item)
                    fecha_pub = ""
                    if fecha_match:
                        fecha_raw = fecha_match.group(0)
                        try:
                            if "/" in fecha_raw:
                                d,m,a = fecha_raw.split("/")
                                fecha_pub = f"{a}-{m}-{d}"
                            else:
                                fecha_pub = fecha_raw
                            # Filtrar por fecha
                            if fecha_pub < fecha_desde_str:
                                continue
                        except:
                            pass

                    # Extraer monto
                    monto_match = re.search(r'S/\s*([\d,]+\.?\d*)', texto_item)
                    monto = parsear_monto(monto_match.group(1)) if monto_match else 0

                    if monto > 0 and monto < MONTO_MIN:
                        continue  # menor a 8 UIT

                    # Extraer entidad y región
                    entidad = ""
                    region  = ""
                    # Buscar en elementos específicos
                    for el in item.find_all(["span","small","p","div"]):
                        txt = el.get_text(strip=True)
                        if re.search(r'(ministerio|gobierno|municipalidad|instituto|'
                                     r'universidad|hospital|autoridad|organismo|'
                                     r'superintendencia|fondo|programa)', txt, re.I):
                            if len(txt) > 8 and len(txt) < 120:
                                entidad = txt
                        depts = ['Lima','Arequipa','Cusco','Piura','La Libertad',
                                 'Puno','Junín','Ancash','Cajamarca','Lambayeque',
                                 'Loreto','Ica','Ucayali','Huánuco','Moquegua',
                                 'Tacna','Amazonas','Ayacucho','Huancavelica','Pasco',
                                 'Tumbes','Madre de Dios','San Martín','Callao']
                        for dept in depts:
                            if dept.lower() in txt.lower():
                                region = dept
                                break

                    ids_vistos.add(id_proc)
                    encontrados_pagina += 1
                    resultados.append({
                        "id":                  id_proc,
                        "titulo":              titulo[:200],
                        "entidad":             entidad[:150],
                        "region":              region,
                        "tipo_licitacion":     "Concurso Público",
                        "tipo_contratacion":   "Servicio",
                        "estado":              "Publicado",
                        "monto_base":          monto,
                        "ganador":             "",
                        "monto_adjudicado":    0,
                        "publicado":           fecha_pub,
                        "adjudicacion":        "",
                        "inicio_contrato":     "",
                        "fin_contrato":        "",
                        "duracion_dias":       "",
                        "moneda":              "PEN",
                        "empresas_participantes": 0,
                        "fuente":              "LICITACIONESPERU-SCRAPING",
                        "_agregada_el":        datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "_sync_automatico":    "SI",
                    })

                log.info(f"Página {pagina}: {encontrados_pagina} licitaciones TI encontradas")

                # Verificar si hay página siguiente
                next_btn = soup.find("a", string=re.compile(r'siguiente|next|›|»', re.I))
                if not next_btn and pagina > 1 and encontrados_pagina == 0:
                    break

                time.sleep(1)

            except Exception as e:
                log.warning(f"Error en página {pagina}: {e}")
                break

    log.info(f"Total licitaciones TI scrapeadas: {len(resultados)}")
    return resultados

# ── EMAIL ─────────────────────────────────────────────────────────────────────
def enviar_email(nuevas, fecha, total, dry_run=False):
    if not all([GMAIL_FROM, GMAIL_TO, GMAIL_APP_PASS]):
        log.warning("Gmail no configurado — secrets: GMAIL_FROM, GMAIL_TO, GMAIL_APP_PASS")
        return
    if dry_run:
        log.info("DRY RUN — email no enviado")
        return

    n = len(nuevas)
    asunto = (f"🔔 KAM Intelligence · {n} licitaciones TI nuevas · {fecha}"
              if n > 0 else f"KAM Intelligence · Sin licitaciones TI nuevas · {fecha}")

    filas = ""
    for lic in nuevas[:30]:
        monto = float(lic.get("monto_base",0) or 0)
        ms = f"S/ {monto:,.0f}" if monto > 0 else "Por definir"
        filas += f"""<tr style="border-bottom:1px solid #E2E8F0">
          <td style="padding:8px;font-size:12px;font-weight:500;color:#1C2E4A">{lic.get('entidad','')[:45]}</td>
          <td style="padding:8px;font-size:11px;color:#64748B">{lic.get('titulo','')[:70]}</td>
          <td style="padding:8px;font-size:11px;color:#64748B">{lic.get('region','')}</td>
          <td style="padding:8px;font-size:12px;font-weight:500;color:#534AB7;text-align:right">{ms}</td>
        </tr>"""

    if n > 30:
        filas += f'<tr><td colspan="4" style="padding:8px;text-align:center;color:#888;font-size:11px">... y {n-30} más en KAM Intelligence</td></tr>'
    if n == 0:
        filas = '<tr><td colspan="4" style="padding:20px;text-align:center;color:#888">Sin licitaciones TI nuevas hoy.</td></tr>'

    mt = sum(float(l.get("monto_base",0) or 0) for l in nuevas)
    html = f"""<!DOCTYPE html><html><body style="font-family:Calibri,Arial,sans-serif;background:#F8F9FC;margin:0;padding:20px">
    <div style="max-width:800px;margin:0 auto;background:white;border-radius:10px;
                box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden">
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
          <div style="font-size:10px;color:#888;text-transform:uppercase">Páginas consultadas</div>
        </div>
        <div style="flex:1;padding:14px 18px">
          <div style="font-size:22px;font-weight:600;color:#BA7517">S/ {mt/1e6:.1f}M</div>
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
      <div style="padding:14px 18px;background:#F8F9FC;border-top:1px solid #E2E8F0;
                  font-size:10px;color:#888;text-align:center">
        Generado por KAM Intelligence v2 · Qubits SAC · Las licitaciones ya están en Google Sheets.
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
        log.error(f"Error email: {e}")

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

    fecha_desde = fecha_hasta - timedelta(days=args.dias)
    fecha_str   = fecha_hasta.strftime("%Y-%m-%d")
    desde_str   = fecha_desde.strftime("%Y-%m-%d")

    log.info("=" * 60)
    log.info(f"KAM Intelligence — Sync SEACE · {fecha_str}")
    log.info(f"Rango: {desde_str} → {fecha_str} · Dry run: {args.dry_run}")
    log.info("=" * 60)

    log.info("Conectando a Google Sheets...")
    sh = conectar_sheets()
    ids_existentes = obtener_ids_existentes(sh)
    log.info(f"IDs existentes: {len(ids_existentes)}")

    log.info("Scrapeando licitacionesperu.pe...")
    candidatas = scrape_licitacionesperu(desde_str, max_paginas=8)
    total = len(candidatas)

    # Filtrar duplicados
    nuevas = []
    ids_lote = set()
    for lic in candidatas:
        lid = lic.get("id","")
        if not lid or lid in ids_existentes or lid in ids_lote:
            continue
        nuevas.append(lic)
        ids_lote.add(lid)

    log.info(f"Licitaciones TI nuevas (sin duplicados): {len(nuevas)}")

    if nuevas and not args.dry_run:
        guardar_en_sheets(sh, nuevas)
    elif args.dry_run and nuevas:
        log.info("DRY RUN — primeras 3:")
        for lic in nuevas[:3]:
            log.info(f"  · {lic['id']} | {lic['entidad'][:40]} | {lic['titulo'][:50]}")

    if not args.dry_run:
        registrar_log(sh, fecha_str, total, len(nuevas))
    enviar_email(nuevas, fecha_str, total, dry_run=args.dry_run)

    log.info("=" * 60)
    log.info(f"Sync completado · {len(nuevas)} nuevas licitaciones TI")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
