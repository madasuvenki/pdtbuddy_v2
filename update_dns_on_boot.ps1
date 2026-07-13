# update_dns_on_boot.ps1
# ─────────────────────────────────────────────────────────────────
# Runs at every boot via Task Scheduler (SYSTEM account).
# Checks if the current IP matches the DNS A-record for pdt-buddy.
# If they differ, updates the DNS record automatically.
#
# INSTALL (run once as Administrator):
#   schtasks /create /tn "PDTBuddy_DNS_BootFix" ^
#     /tr "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\PDTBuddy\update_dns_on_boot.ps1" ^
#     /sc onstart /ru SYSTEM /delay 0000:30 /f
# ─────────────────────────────────────────────────────────────────

$DNS_HOSTNAME   = "pdt-buddy"
$DNS_ZONE       = "qualcomm.com"
$DNS_SERVER     = "hyd-e-dcr-dns-01.qualcomm.com"   # Qualcomm DNS server
$EXPECTED_IP    = "10.142.213.5"                     # The registered DNS IP
$LOG_FILE       = "C:\PDTBuddy\dns_boot_fix_log.txt"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Out-File -FilePath $LOG_FILE -Append -Encoding UTF8
}

# ── Ensure log directory exists ───────────────────────────────────
New-Item -ItemType Directory -Force -Path (Split-Path $LOG_FILE) | Out-Null
Write-Log "=== PDTBuddy DNS Boot Fix started ==="

# ── Step 1: Get current machine IP (10.x.x.x) ────────────────────
$currentIP = (
    Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -like "10.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1
).IPAddress

if (-not $currentIP) {
    Write-Log "ERROR: No 10.x.x.x IP found on this machine. Aborting."
    exit 1
}

Write-Log "Current machine IP : $currentIP"
Write-Log "DNS registered IP  : $EXPECTED_IP"

# ── Step 2: Check if IP matches DNS record ────────────────────────
if ($currentIP -eq $EXPECTED_IP) {
    Write-Log "IP matches DNS record. No update needed. All good."
    exit 0
}

# ── Step 3: IP has changed — update DNS record ────────────────────
Write-Log "WARNING: IP mismatch! DNS still points to $EXPECTED_IP but machine is now $currentIP"
Write-Log "Attempting DNS record update on $DNS_SERVER ..."

try {
    # Remove old A record and add new one
    # Requires DNS admin rights — run as SYSTEM or domain admin
    $fqdn = "$DNS_HOSTNAME.$DNS_ZONE"

    # Try using dnscmd (available on Windows Server / with RSAT DNS tools)
    $result = & dnscmd $DNS_SERVER /RecordDelete $DNS_ZONE $DNS_HOSTNAME A /f 2>&1
    Write-Log "dnscmd delete result: $result"

    $result2 = & dnscmd $DNS_SERVER /RecordAdd $DNS_ZONE $DNS_HOSTNAME A $currentIP 2>&1
    Write-Log "dnscmd add result: $result2"

    Write-Log "DNS record updated: $fqdn -> $currentIP"

} catch {
    Write-Log "ERROR: DNS update via dnscmd failed: $_"
    Write-Log "FALLBACK: Updating local hosts file as workaround..."

    # Fallback: update local hosts file so THIS machine resolves correctly
    $hostsFile = "C:\Windows\System32\drivers\etc\hosts"
    $fqdn      = "$DNS_HOSTNAME.$DNS_ZONE"
    $content   = Get-Content $hostsFile -ErrorAction SilentlyContinue
    $filtered  = $content | Where-Object { $_ -notmatch [regex]::Escape($fqdn) }
    $filtered + "$currentIP    $fqdn" | Set-Content $hostsFile -Encoding ASCII
    Write-Log "hosts file updated: $currentIP -> $fqdn"
}

Write-Log "=== Done ==="
