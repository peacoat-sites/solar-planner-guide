#!/usr/bin/env python3
"""Fetch solar production potential by state from NASA POWER -> data/solar_states.json.
Keyless, official NASA data. Uses annual average solar irradiance (peak sun hours/day),
then estimates production for a standard 6 kW residential system.
  Annual kWh = system_kW x peak_sun_hours x 365 x performance_ratio
Runs in GitHub Actions (unrestricted network).
"""
import urllib.request, urllib.parse, json, os, sys, time

DEST = sys.argv[1] if len(sys.argv) > 1 else "data/solar_states.json"
SYSTEM_KW = 6
PERF_RATIO = 0.80          # real-world system losses (inverter, wiring, soiling, temp)
RATE = 0.17                # national avg residential $/kWh (illustrative only)

LOCATIONS = [
    ("Arizona", 33.45, -112.07), ("Nevada", 36.17, -115.14), ("New Mexico", 35.08, -106.65),
    ("California", 34.05, -118.24), ("Texas", 30.27, -97.74), ("Florida", 25.76, -80.19),
    ("Colorado", 39.74, -104.99), ("Georgia", 33.75, -84.39), ("North Carolina", 35.78, -78.64),
    ("New York", 40.71, -74.01), ("Illinois", 41.88, -87.63), ("Massachusetts", 42.36, -71.06),
    ("Ohio", 39.96, -82.99), ("Michigan", 42.33, -83.05), ("Oregon", 45.51, -122.68),
    ("Washington", 47.61, -122.33),
]
BASE = "https://power.larc.nasa.gov/api/temporal/climatology/point"

def peak_sun_hours(lat, lon):
    params = urllib.parse.urlencode({
        "parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
        "longitude": lon, "latitude": lat, "format": "JSON",
    })
    data = json.loads(urllib.request.urlopen(urllib.request.Request(f"{BASE}?{params}", headers={"User-Agent": "solar-data/1.0"}), timeout=30).read())
    return data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"].get("ANN")

out = []
for state, lat, lon in LOCATIONS:
    try:
        sun = peak_sun_hours(lat, lon)
        if not sun:
            print(f"  {state}: no data", file=sys.stderr); continue
        annual_kwh = round(SYSTEM_KW * sun * 365 * PERF_RATIO)
        out.append({
            "state": state,
            "sun_hours": round(sun, 1),
            "annual_kwh": annual_kwh,
            "est_savings": round(annual_kwh * RATE),
        })
        print(f"  {state}: {round(sun,1)} sun-hrs, {annual_kwh} kWh/yr, ~${round(annual_kwh*RATE)}/yr")
        time.sleep(0.8)
    except Exception as e:
        print(f"  ERROR {state}: {e}", file=sys.stderr)

if not out:
    print("No data fetched — leaving existing file untouched"); sys.exit(0)

out.sort(key=lambda x: x["annual_kwh"], reverse=True)
result = {
    "title": "Solar Production Potential by State",
    "system": f"{SYSTEM_KW} kW residential rooftop (80% performance ratio)",
    "rate_assumption": RATE,
    "source": "NASA POWER (power.larc.nasa.gov)",
    "states": out,
}
_dir = os.path.dirname(DEST)
if _dir:
    os.makedirs(_dir, exist_ok=True)
with open(DEST, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=2)
print(f"\nWrote {len(out)} states to {DEST}")
