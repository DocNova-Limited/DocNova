"""Device compliance records for the owner dashboard.

What the manufacturer's certificates say is written down here. The certificate files themselves are uploaded
from the dashboard and kept on the private disk (never in Git), and can only be opened after signing in.
Nothing in this file is shown on the public website.
"""
import datetime, re
from shop import DATA

FOLDER = DATA / 'compliance'
MAX_BYTES = 12 * 1024 * 1024
CHECKED = '2026-10-07'  # the day the certificates and the MHRA register were last read

MANUFACTURER = {
    'name': 'Shenzhen Finicare Co., Ltd.',
    'address': '201 and 301 of Building A22, Building A22 and A23, No.4 Industrial Park, Tantou Community, Songgang Street, '
               "Bao'an District, Shenzhen 518105, China",
    'eu_rep': 'Riomavix S.L., Calle de Almansa 55, 1D, 28039 Madrid, Spain (SRN ES-AR-000001202)',
}

DEVICES = [
    {'name': 'DocNova Upper Arm Blood Pressure Monitor', 'model': 'FC-BP113', 'class': 'Class IIa', 'barcode': '6971522820329',
     'received': '100 received 22 June 2026', 'udi': 'Basic UDI-DI 697152282UABP1K2'},
    {'name': 'DocNova Infrared Thermometer', 'model': 'FC-IR100', 'class': 'Class IIa', 'barcode': '6971522820022',
     'received': '97 received 22 June 2026', 'udi': 'Basic UDI-DI 697152282IR001D3'},
]

DOCS = [
    {'key': 'eu-mdr-certificate', 'title': 'EU Quality Management System Certificate (MDR)',
     'issuer': 'TÜV SÜD Product Service GmbH (Notified Body 0123)', 'number': 'G10 004665 0011 Rev. 01',
     'covers': 'Class IIa blood pressure monitors and thermometers, Regulation (EU) 2017/745', 'from': '2025-12-29', 'until': '2028-10-16'},
    {'key': 'iso-13485-certificate', 'title': 'ISO 13485 quality system certificate',
     'issuer': 'TÜV SÜD Product Service GmbH', 'number': 'Q5 004665 0010 Rev. 02',
     'covers': 'Design, production and distribution of infrared thermometers and electronic blood pressure monitors', 'from': '2025-12-29', 'until': '2028-05-07'},
    {'key': 'eu-declaration-blood-pressure-monitor', 'title': 'EU Declaration of Conformity – blood pressure monitor',
     'issuer': 'Shenzhen Finicare Co., Ltd. (signed 30 December 2025)', 'number': 'refers to G10 004665 0011 Rev. 01',
     'covers': 'Lists model FC-BP113', 'from': '2025-12-30', 'until': '2028-10-16'},
    {'key': 'eu-declaration-infrared-thermometer', 'title': 'EU Declaration of Conformity – infrared thermometer',
     'issuer': 'Shenzhen Finicare Co., Ltd. (signed 30 December 2025)', 'number': 'refers to G10 004665 0011 Rev. 01',
     'covers': 'Lists model FC-IR100', 'from': '2025-12-30', 'until': '2028-10-16'},
    {'key': 'china-export-certificate-blood-pressure-monitor', 'title': 'Chinese export sales certificate – blood pressure monitor',
     'issuer': 'Guangdong medical products authority', 'number': '粤食药监械出 20251867 号',
     'covers': 'Lists model FC-BP113; registered to be made and sold in China', 'from': '2025-11-10', 'until': '2027-09-01'},
    {'key': 'china-export-certificate-infrared-thermometer', 'title': 'Chinese export sales certificate – infrared thermometer',
     'issuer': 'Guangdong medical products authority', 'number': '粤食药监械出 20251866 号',
     'covers': 'Lists model FC-IR100; registered to be made and sold in China', 'from': '2025-11-10', 'until': '2027-11-09'},
]

# Other private records kept with the certificates (no expiry date).
RECORDS = [
    {'key': 'supplier-record', 'title': 'Supplier record – Shenzhen Finicare',
     'what': 'Summary of the order, the UK check, copies of the certificates and box artwork, and the full Alibaba chat (167 pages)'},
    {'key': 'box-artwork-blood-pressure-monitor', 'title': 'Approved box artwork – blood pressure monitor', 'what': 'Shows the manufacturer, the CE 0123 mark and the barcode as printed'},
    {'key': 'box-artwork-infrared-thermometer', 'title': 'Approved box artwork – infrared thermometer', 'what': 'Shows the manufacturer, the CE 0123 mark and the barcode as printed'},
    {'key': 'mhra-register-entry', 'title': 'MHRA public register entry', 'what': 'Copy of the register page for Shenzhen Finicare, reference 23366, as seen on 7 October 2026'},
]

REGISTER = {
    'source': 'MHRA Public Access Registration Database (pard.mhra.gov.uk)', 'reference': '23366', 'registered': '2022-03-02',
    'uk_responsible_person': 'Wellkang Ltd, 16 Castle St., Dover, Kent, CT16 1PW',
    'devices': ['Upper Arm Electronic Blood Pressure Monitor – Class IIa – Registered', 'Infrared Thermometer (ear/skin) – Class IIa – Registered'],
    'note': 'The register lists device types, not model numbers. Registration is not an approval and must not be used in advertising.',
}

RULES = ('Great Britain accepts CE-marked devices that meet the EU Medical Devices Regulation until 30 June 2030 (GOV.UK guidance, read 7 October 2026). '
         'There is no separate UK certificate for these products.')

def _days(iso):
    return (datetime.date.fromisoformat(iso) - datetime.date.today()).days

def path(key):
    if not any(d['key'] == key for d in DOCS + RECORDS):
        return None
    return FOLDER / (key + '.pdf')

def listing():
    docs = []
    for d in DOCS:
        p = path(d['key'])
        docs.append({**d, 'days_left': _days(d['until']), 'has_file': p.is_file(), 'bytes': p.stat().st_size if p.is_file() else 0})
    records = [{**r, 'has_file': path(r['key']).is_file()} for r in RECORDS]
    return {'records': records, 'manufacturer': MANUFACTURER, 'devices': DEVICES, 'docs': docs, 'register': REGISTER, 'rules': RULES, 'checked': CHECKED}

def save(key, raw):
    p = path(key)
    if not p:
        raise ValueError('Unknown document.')
    if not raw.startswith(b'%PDF-') or len(raw) > MAX_BYTES:
        raise ValueError('Please choose the PDF file for this certificate (up to 12 MB).')
    FOLDER.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_bytes(raw)
    tmp.replace(p)
    return {'ok': True, 'bytes': len(raw)}

def filename(key):
    return 'DocNova-device-' + re.sub(r'[^a-z0-9-]', '', key) + '.pdf'
