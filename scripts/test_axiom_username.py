"""Test which username header Axiom accepts."""
import os, sys, base64, http.client, json, ssl, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)

cid  = os.environ.get('AXIOM_CLIENT_ID','').strip()
sec  = os.environ.get('AXIOM_CLIENT_SECRET','').strip()
host = os.environ.get('AXIOM_API_HOST','api-int.qualcomm.com').strip()

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Get token
auth = 'Basic ' + base64.b64encode((cid + ':' + sec).encode()).decode()
conn = http.client.HTTPSConnection(host, timeout=30, context=ctx)
conn.request('POST', '/ent/oauth/v1/accesstoken?grant_type=client_credentials',
             body='', headers={'Authorization': auth})
resp = conn.getresponse()
token = json.loads(resp.read().decode()).get('access_token', '')
conn.close()
print('Token OK:', bool(token))
print('-' * 70)

# Try different username combinations
test_cases = [
    # (X-QCOM-UserName, X-QCOM-AppName)
    ('pdt-pcie@qualcomm.com', 'Axiom_public-pdt-pcie'),
    ('pdt-pcie',              'Axiom_public-pdt-pcie'),
    ('',                      'Axiom_public-pdt-pcie'),
    ('pdt-pcie@qualcomm.com', 'PDTDashboard'),
    ('',                      'PDTDashboard'),
    ('pdt-pcie@qualcomm.com', 'pdt-pcie'),
]

for username, appname in test_cases:
    headers = {
        'Authorization':     'Bearer ' + token,
        'Accept':            'application/json',
        'X-QCOM-AppName':    appname,
        'X-QCOM-TokenType':  'OAuth',
        'X-QCOM-TracingID':  uuid.uuid4().hex,
        'X-QCOM-ClientType': 'Python',
    }
    if username:
        headers['X-QCOM-UserName'] = username

    conn2 = http.client.HTTPSConnection(host, timeout=30, context=ctx)
    conn2.request('GET',
                  '/axiom/v1/public/jobs?taxonomyPath=/PDT&pageNumber=0&pageSize=1',
                  body='', headers=headers)
    resp2 = conn2.getresponse()
    body2 = resp2.read().decode()
    conn2.close()

    try:
        msg = json.loads(body2).get('message', 'OK')[:80]
        total = json.loads(body2).get('total', '')
    except Exception:
        msg = body2[:80]
        total = ''

    status_str = 'PASS' if resp2.status == 200 else 'FAIL'
    print(f'[{status_str}] status={resp2.status}  AppName={appname!r:30s}  UserName={username!r:30s}')
    if resp2.status != 200:
        print(f'       msg: {msg}')
    else:
        print(f'       total_jobs={total}')
    print()
