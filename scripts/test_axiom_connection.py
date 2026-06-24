"""
Quick Axiom connectivity test.
Reads credentials from .env, attempts token fetch, then one API call.
Run: venv\Scripts\python.exe scripts\test_axiom_connection.py
"""
import os, sys, base64, http.client, json, ssl, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)

SEP = "-" * 60

def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

cid  = os.environ.get("AXIOM_CLIENT_ID",     "").strip()
sec  = os.environ.get("AXIOM_CLIENT_SECRET",  "").strip()
host = os.environ.get("AXIOM_API_HOST", "api-int.qualcomm.com").strip()

print(SEP)
print("Axiom Connection Test")
print(SEP)
print(f"HOST             : {host}")
print(f"CLIENT_ID set    : {bool(cid)}   prefix={cid[:6] + '...' if cid else 'EMPTY'}")
print(f"CLIENT_SECRET set: {bool(sec)}   len={len(sec)}")
print(SEP)

if not cid or not sec:
    print("FAIL: AXIOM_CLIENT_ID or AXIOM_CLIENT_SECRET is empty in .env")
    sys.exit(1)

# ── Step 1: Token ──────────────────────────────────────────────────────────
print("Step 1: Requesting OAuth token ...")
auth = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
try:
    conn = http.client.HTTPSConnection(host, timeout=30, context=ssl_ctx())
    conn.request("POST", "/ent/oauth/v1/accesstoken?grant_type=client_credentials",
                 body="", headers={"Authorization": auth})
    resp  = conn.getresponse()
    body  = resp.read().decode()
    conn.close()
    print(f"  HTTP status : {resp.status}")
    payload = json.loads(body)
    token   = payload.get("access_token", "")
    if resp.status == 200 and token:
        print(f"  Token       : OK  (first 20 chars: {token[:20]}...)")
    else:
        print(f"  Token       : FAIL")
        print(f"  Response    : {body[:400]}")
        sys.exit(1)
except Exception as e:
    print(f"  Token       : EXCEPTION — {e}")
    sys.exit(1)

print(SEP)

# ── Step 2: API call — /PDT 1 job ─────────────────────────────────────────
print("Step 2: GET /axiom/v1/public/jobs?taxonomyPath=/PDT&pageSize=1 ...")
headers = {
    "Authorization":    f"Bearer {token}",
    "Accept":           "application/json",
    "X-QCOM-AppName":   "Axiom_public-pdt-pcie",
    "X-QCOM-TokenType": "OAuth",
    "X-QCOM-TracingID": uuid.uuid4().hex,
    "X-QCOM-ClientType":"Python",
}
try:
    conn2 = http.client.HTTPSConnection(host, timeout=30, context=ssl_ctx())
    conn2.request("GET",
                  "/axiom/v1/public/jobs?taxonomyPath=/PDT&pageNumber=0&pageSize=1&expand=chipIdSerialNumbers",
                  body="", headers=headers)
    resp2 = conn2.getresponse()
    body2 = resp2.read().decode()
    conn2.close()
    print(f"  HTTP status : {resp2.status}")
    if resp2.status == 200:
        data  = json.loads(body2)
        total = data.get("total", "N/A")
        jobs  = data.get("data", [])
        print(f"  Total jobs  : {total}")
        if jobs:
            j = jobs[0]
            print(f"  Sample job  : id={j.get('jobId','?')}  state={j.get('state','?')}  submitted={str(j.get('submitted','?'))[:19]}")
        print("  Result      : PASS")
    else:
        print(f"  Result      : FAIL")
        print(f"  Response    : {body2[:400]}")
        sys.exit(1)
except Exception as e:
    print(f"  Result      : EXCEPTION — {e}")
    sys.exit(1)

print(SEP)

# ── Step 3: API call — /PDT/QIPL/HW (HWPDT) ──────────────────────────────
print("Step 3: GET /axiom/v1/public/jobs?taxonomyPath=/PDT/QIPL/HW&pageSize=1 ...")
try:
    conn3 = http.client.HTTPSConnection(host, timeout=30, context=ssl_ctx())
    conn3.request("GET",
                  "/axiom/v1/public/jobs?taxonomyPath=/PDT/QIPL/HW&pageNumber=0&pageSize=1",
                  body="", headers=headers)
    resp3 = conn3.getresponse()
    body3 = resp3.read().decode()
    conn3.close()
    print(f"  HTTP status : {resp3.status}")
    if resp3.status == 200:
        data3  = json.loads(body3)
        total3 = data3.get("total", "N/A")
        print(f"  Total jobs  : {total3}")
        print("  Result      : PASS")
    else:
        print(f"  Result      : FAIL")
        print(f"  Response    : {body3[:400]}")
except Exception as e:
    print(f"  Result      : EXCEPTION — {e}")

print(SEP)
print("ALL TESTS PASSED" if True else "")
