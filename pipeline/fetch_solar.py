#!/usr/bin/env python3
"""Fetch solar production potential by state from NREL PVWatts v8 -> data/solar_states.json.
For a standard 6 kW residential rooftop system (fixed mount, 20deg tilt, south-facing).
DEMO_KEY works for this volume; set NREL_API_KEY secret for reliability.
Get a free key at https://developer.nrel.gov/signup/  (api.data.gov).
Runs in GitHub Actions (unrestricted network).
"""
import urllib.request, urllib.parse, json, os, sys, time

API_KEY = os.environ.get("NREL_API_KEY", "DEMO_KEY").strip()
DEST = sys.argv[1] if len(sys.argv) > 1 else "data/solar_states.json"
SYSTEM_KW = 6
RATE = 0.17  # national avg residential $/kWh (2025-2026); used only for an illustrative estimate

# Representative city per state (lat, lon)
LOCATIONS = [
    ("Arizona", 33.45, -112.07), ("Nevada", 36.17, -115.14), ("New Mexico", 35.08, -106.65),
    ("California", 34.05, -118.24), ("Texas", 30.27, -97.74), ("Florida", 25.76, -80.19),
    ("Colorado", 39.74, -104.99), ("Georgia", 33.75, -84.39), ("North Carolina", 35.78, -78.64),
    ("New York", 40.71, -74.01), ("Illinois", 41.88, -87.63), ("Massachusetts", 42.36, -71.06),
    ("Ohio", 39.96, -82.99), ("Michigan", 42.33, -83.05), ("Oregon", 45.51, -122.68),
    ("Washington", 47.61, -122.33),
]
BASE = "https://developer.nrel.gov/api/pvwatts/v8.json"

def query(lat, lon):
    params = urllib.parse.urlencode({
        "api_key": API_KEY, "lat": lat, "lon": lon, "system_capacity": SYSTEM_KW,
        "azimuth": 180, "tilt": 20, "array_type": 1, "module_type": 0, "losses": 14,
    })
    data = json.loads(urllib.request.urlopen(urllib.request.Request(f"{BASE}?{params}", headers={"User-Agent": "solar-data/1.0"}), timeout=30).read())
    o = data.get("outputs", {})
    return o.get("ac_annual"), o.get("solrad_annual")

out = []
for state, lat, lon in LOCATIONS:
    try:
        ac, sun = query(lat, lon)
        if ac is None:
            print(f"  {state}: no data", file=sys.stderr); continue
        out.append({
            "state": state,
            "sun_hours": round(sun, 1) if sun else None,     # avg peak sun hours/day
            "annual_kwh": round(ac),                          # kWh/yr for 6 kW system
            "est_savings": round(ac * RATE),                  # $/yr illustrative
        })
        print(f"  {state}: {round(sun,1) if sun else '?'} sun-hrs, {round(ac)} kWh/yr, ~${round(ac*RATE)}/yr")
        time.sleep(1.2)
    except Exception as e:
        print(f"  ERROR {state}: {e}", file=sys.stderr)

if not out:
    print("No data fetched — leaving existing file untouched"); sys.exit(0)

out.sort(key=lambda x: x["annual_kwh"], reverse=True)
result = {
    "title": "Solar Production Potential by State",
    "system": f"{SYSTEM_KW} kW residential rooftop, south-facing, 20° tilt",
    "rate_assumption": RATE,
    "source": "NREL PVWatts v8 (nrel.gov)",
    "states": out,
}
os.makedirs(os.path.dirname(DEST), exist_ok=True)
with open(DEST, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=2)
print(f"\nWrote {len(out)} states to {DEST}")
