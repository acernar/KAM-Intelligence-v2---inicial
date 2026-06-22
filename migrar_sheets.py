import json
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1CsnfzVC_Bk9CTK2BHJCoBU1gouIEAnXApC_Ji0DoSeI"
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

print("Conectando a Google Sheets...")
creds = Credentials.from_service_account_file('gsheets_credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
sh = client.open_by_key(SPREADSHEET_ID)
print("✅ Conectado")

def subir_dict(nombre_hoja, datos):
    try:
        ws = sh.worksheet(nombre_hoja)
    except:
        ws = sh.add_worksheet(title=nombre_hoja, rows=1000, cols=50)
    ws.clear()
    if not datos:
        print(f"⚠️  {nombre_hoja}: vacío")
        return
    todas_columnas = set()
    for v in datos.values():
        if isinstance(v, dict):
            todas_columnas.update(v.keys())
    columnas = sorted(list(todas_columnas))
    filas = [columnas]
    for registro in datos.values():
        fila = []
        for col in columnas:
            val = registro.get(col, '')
            if isinstance(val, (list, dict)):
                val = json.dumps(val, ensure_ascii=False)
            elif val is None:
                val = ''
            fila.append(str(val))
        filas.append(fila)
    ws.clear()
    ws.update(filas, value_input_option='RAW')
    print(f"✅ {nombre_hoja}: {len(datos)} registros subidos")

with open('licitaciones.json', 'r', encoding='utf-8') as f:
    licitaciones = json.load(f)
subir_dict('licitaciones', licitaciones)

with open('procesos.json', 'r', encoding='utf-8') as f:
    procesos = json.load(f)
subir_dict('procesos', procesos)

for hoja in ['contactos', 'historial']:
    try:
        sh.worksheet(hoja)
        print(f"✅ {hoja}: ya existe")
    except:
        sh.add_worksheet(title=hoja, rows=1000, cols=20)
        print(f"✅ {hoja}: creada")

print("\n🎉 Migración completa. Abre tu Google Sheet para verificar.")
