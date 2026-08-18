"""Check Axiom job results for ALANA.LA.1.0 to find device hostname/MCN."""
import sys
sys.path.insert(0, '.')
from src.axiom_client import axiom_get

# Use job 38556994 from previous query
job_id = 38556994

print(f'=== Job {job_id} info ===')
info = axiom_get(f'/axiom/v1/public/jobs/{job_id}/info')
print('  state:', info.get('state'))
print('  softwareProduct:', info.get('softwareProduct'))
print('  taxonomyPath:', info.get('taxonomyPath'))
print()

print(f'=== Job {job_id} results (first 3) ===')
results = axiom_get(f'/axiom/v1/public/jobs/{job_id}/results?pageNumber=0&pageSize=3')
for r in (results.get('data') or [])[:3]:
    print('  testCaseTestResourceName:', r.get('testCaseTestResourceName'))
    print('  testCaseHostName:', r.get('testCaseHostName'))
    print('  testCaseName:', r.get('testCaseName'))
    print()

print(f'=== Job {job_id} playlists ===')
playlists = axiom_get(f'/axiom/v1/public/jobs/{job_id}/data/playlists?pageNumber=0&pageSize=3')
for p in (playlists.get('data') or [])[:3]:
    print('  playlistName:', p.get('name'))
    for track in (p.get('playlistStatusOfEachTrack') or [])[:2]:
        tr = track.get('testResource') or {}
        print('    track resource:', tr.get('name'), '| chipset:', tr.get('chipset'))
        print('    hostName:', track.get('hostName'))