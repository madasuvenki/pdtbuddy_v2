"""Check what Axiom API returns for ALANA.LA.1.0 devices."""
import sys, json
sys.path.insert(0, '.')
from src.axiom_client import _paginate
from src.utils import get_mysql_connection_db

conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor(dictionary=True)

# Get taxonomy paths
cur.execute("""
    SELECT DISTINCT taxonomy_path
    FROM pdt_stats_dashboard.axiom_job_summary
    WHERE state IN ('Running','JobSetup')
      AND software_product = 'ALANA.LA.1.0'
      AND taxonomy_path IS NOT NULL
    LIMIT 5
""")
paths = [r['taxonomy_path'] for r in cur.fetchall()]

# Get sample chip_ids
cur.execute("""
    SELECT chip_ids
    FROM pdt_stats_dashboard.axiom_job_summary
    WHERE state IN ('Running','JobSetup')
      AND software_product = 'ALANA.LA.1.0'
      AND chip_ids IS NOT NULL
    LIMIT 1
""")
row = cur.fetchone()
sample_chips = json.loads(row['chip_ids'])[:5] if row else []
cur.close(); conn.close()

print('=== job_summary data ===')
print('Taxonomy paths:', paths)
print('Sample chip_ids:', sample_chips)
print()

if not paths:
    print('No taxonomy paths found for ALANA.LA.1.0')
    sys.exit(0)

# Query Axiom for first taxonomy path
tax = paths[0]
print(f'=== Axiom API: GET /axiom/v1/public/resources?taxonomyPath={tax}&type=Device ===')
base = f'/axiom/v1/public/resources?taxonomyPath={tax}&type=Device'
count = 0
for dev in _paginate(base, page_size=10):
    props = dev.get('properties') or {}
    deps  = dev.get('dependencies') or {}
    print(f'  id={dev.get("id")} | hostname={dev.get("hostname")} | taxonomyPath={dev.get("taxonomyPath")}')
    print(f'    serialNumber={props.get("serialNumber")}')
    print(f'    adbId={props.get("adbId")}')
    print(f'    macAddress={props.get("macAddress")}')
    print(f'    edlId={props.get("edlId")}')
    print(f'    hwId={props.get("hwId")}')
    print(f'    deviceMcn={props.get("deviceMcn")}')
    print(f'    chipset={deps.get("chipset")}')
    count += 1
    if count >= 5:
        break

print()
print('=== Comparison ===')
print('job_summary chip_ids:', sample_chips)
print('Check which Axiom field above matches these chip_ids')