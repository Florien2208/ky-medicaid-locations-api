#!/usr/bin/env python3
"""
Centene - Pull Kentucky Medicaid via Location address-state=KY
We found 200 KY locations. Now trace them to InsurancePlans.
"""

import requests
import json
import os
import base64
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE = "https://prod.api.centene.com/fhir/providerdirectory"
AUTH = "Basic " + base64.b64encode(f"{os.getenv('CENTENE_USER')}:{os.getenv('CENTENE_PASS')}".encode()).decode()
HEADERS = {"Authorization": AUTH, "Accept": "application/fhir+json"}


def get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        print(f"  HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print(f"  Error: {e}")
    return None


def all_pages(url, params):
    entries, page = [], 1
    cur_url, cur_params = url, params
    while cur_url:
        print(f"  Page {page}...")
        body = get(cur_url, cur_params)
        if not body:
            break
        batch = body.get("entry", [])
        entries.extend(batch)
        print(f"  -> {len(batch)} entries (total so far: {len(entries)}, server total: {body.get('total')})")
        cur_url, cur_params, page = None, None, page + 1
        for link in body.get("link", []):
            if link.get("relation") == "next":
                cur_url = link["url"]
                break
    return entries


print("=" * 60)
print("Centene Kentucky Medicaid - via Location KY")
print("=" * 60)

# ── Step 1: Get all KY locations ──────────────────────────────
print("\n[1] Fetching all KY Locations...")
ky_locations = all_pages(f"{BASE}/Location", {"address-state": "KY", "_count": 50})
print(f"\nTotal KY locations: {len(ky_locations)}")

# Show unique sources
sources = set(e.get("resource", {}).get("meta", {}).get("source", "?") for e in ky_locations)
print(f"Sources: {sources}")

# ── Step 2: Try InsurancePlan with _source=Fidelis and _source=MCS ──
print("\n[2] InsurancePlan by source (Fidelis = KY Medicaid brand)...")
for src in ["Fidelis", "MCS", "FIDELIS", "fidelis"]:
    body = get(f"{BASE}/InsurancePlan", {"_source": src, "_count": 10})
    if body:
        total = body.get("total", 0)
        entries = body.get("entry", [])
        print(f"  _source={src}: total={total}, entries={len(entries)}")
        if entries:
            for e in entries[:5]:
                res = e.get("resource", {})
                print(f"    [{res.get('status')}] name={res.get('name','N/A')} | "
                      f"type={[t.get('coding',[{}])[0].get('display','?') for t in res.get('type',[])]}")

# ── Step 3: InsurancePlan filtered by Medicaid + Fidelis source ──
print("\n[3] InsurancePlan Medicaid + Fidelis source...")
body = get(f"{BASE}/InsurancePlan", {"_source": "Fidelis", "plan-type": "Medicaid"})
if body:
    total = body.get("total", 0)
    entries = body.get("entry", [])
    print(f"  total={total}, entries={len(entries)}")

# ── Step 4: Use _revinclude to get InsurancePlans from KY locations ──
print("\n[4] InsurancePlan with coverage-area pointing to KY locations...")
# Take first 5 KY location IDs and query InsurancePlan by coverage-area
ky_loc_ids = [e.get("resource", {}).get("id") for e in ky_locations[:5] if e.get("resource", {}).get("id")]
for loc_id in ky_loc_ids:
    body = get(f"{BASE}/InsurancePlan", {"coverage-area": f"Location/{loc_id}"})
    if body:
        total = body.get("total", 0)
        entries = body.get("entry", [])
        if total or entries:
            print(f"  coverage-area=Location/{loc_id}: total={total}, entries={len(entries)}")
            for e in entries[:2]:
                res = e.get("resource", {})
                print(f"    {res.get('name','N/A')} | {[t.get('coding',[{}])[0].get('display') for t in res.get('type',[])]}")

# ── Step 5: Pull Practitioner in KY ──────────────────────────
print("\n[5] Practitioners in KY (via _source=Fidelis or MCS)...")
for src in ["Fidelis", "MCS"]:
    body = get(f"{BASE}/Practitioner", {"_source": src, "_count": 5})
    if body:
        total = body.get("total", 0)
        entries = body.get("entry", [])
        print(f"  Practitioner _source={src}: total={total}, entries={len(entries)}")
        if entries:
            for e in entries[:3]:
                res = e.get("resource", {})
                names = res.get("name", [{}])
                print(f"    {names[0].get('text', 'N/A')} | id={res.get('id')}")

# ── Step 6: Save KY locations to file ────────────────────────
print("\n[6] Saving KY locations to centene_ky_locations.json...")
out = {
    "run_at": datetime.now().isoformat(),
    "total_ky_locations": len(ky_locations),
    "sources": list(sources),
    "entries": ky_locations
}
with open("centene_ky_locations.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"  Saved {len(ky_locations)} KY locations.")
