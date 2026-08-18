"""Check what Axiom /jobs API returns for ALANA.LA.1.0 jobs from last 1 day."""
import sys, json
from datetime import datetime, timedelta
sys.path.insert(0, '.')
from src.axiom_client import _paginate

# Last 1 day
from_dt = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
print(f'Querying Axiom /jobs for ALANA.LA.1.0 since {from_dt}')
print()

# Query jobs with softwareProduct=ALANA.LA.1.0
base = f'/axiom/v1/public/jobs?taxonomyPath=/PDT&softwareProduct=ALANA.LA.1.0&submittedFrom={from_dt}&expand=chipIdSerialNumbers&state=Running'
count = 0
for job in _paginate(base, page_size=10):
    print(f'jobId={job.get("jobId")} | state={job.get("state")} | submitter={job.get("submitter")}')
    print(f'  softwareProduct={job.get("softwareProduct")}')
    print(f'  chipIdSerialNumbers={job.get("chipIdSerialNumbers")}')
    print(f'  build={job.get("build")}')
    count += 1
    if count >= 5:
        break

if count == 0:
    print('No running jobs found. Trying all states...')
    base2 = f'/axiom/v1/public/jobs?taxonomyPath=/PDT&softwareProduct=ALANA.LA.1.0&submittedFrom={from_dt}&expand=chipIdSerialNumbers'
    count2 = 0
    for job in _paginate(base2, page_size=5):
        print(f'jobId={job.get("jobId")} | state={job.get("state")}')
        print(f'  chipIdSerialNumbers={job.get("chipIdSerialNumbers")}')
        count2 += 1
        if count2 >= 3:
            break