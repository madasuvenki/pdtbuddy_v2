"""Decode the JWT token to inspect its claims."""
import os, sys, base64, http.client, json, ssl
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)

cid  = os.environ.get('AXIOM_CLIENT_ID','').strip()
sec  = os.environ.get('AXIOM_CLIENT_SECRET','').strip()
host = os.environ.get('AXIOM_API_HOST','api-int.qualcomm.com').strip()

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

auth = 'Basic ' + base64.b64encode((cid + ':' + sec).encode()).decode()
conn = http.client.HTTPSConnection(host, timeout=30, context=ctx)
conn.request('POST', '/ent/oauth/v1/accesstoken?grant_type=client_credentials',
             body='', headers={'Authorization': auth})
resp = conn.getresponse()
raw = json.loads(resp.read().decode())
conn.close()

token = raw.get('access_token', '')
print('Full token response keys:', list(raw.keys()))
print()

# Decode JWT payload (no signature verification needed - just inspect claims)
parts = token.split('.')
if len(parts) >= 2:
    payload_b64 = parts[1]
    # Add padding
    payload_b64 += '=' * (4 - len(payload_b64) % 4)
    try:
        payload = json.loads(base64.b64decode(payload_b64).decode('utf-8', errors='replace'))
        print('JWT Claims:')
        for k, v in payload.items():
            print(f'  {k:20s} = {v}')
    except Exception as e:
        print('Could not decode JWT payload:', e)
        print('Raw b64:', payload_b64[:200])
else:
    print('Token does not look like a JWT:', token[:100])
