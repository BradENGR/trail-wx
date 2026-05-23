"""
Fetch and store static NWS metadata for each location in locations.yaml.
Run this once after adding or changing locations:

    python cache.py

Writes locations_cache.json. Commit that file alongside locations.yaml.
The main generator reads from the cache and skips these API calls on every build.
"""
import json
import sys
import yaml
import requests
from pathlib import Path

UA = "trail-wx/1.0 (https://github.com/BradENGR/trail-wx/issues)"


def nws_get(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_static(loc):
    lat, lon = loc["lat"], loc["lon"]

    props = nws_get(f"https://api.weather.gov/points/{lat},{lon}")["properties"]
    office  = props["gridId"]
    grid_x  = props["gridX"]
    grid_y  = props["gridY"]
    radar_id = props.get("radarStation", "")

    grid_props = nws_get(
        f"https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}"
    )["properties"]
    elev_m = (grid_props.get("elevation") or {}).get("value") or 0

    stations = nws_get(props["observationStations"])["features"]
    station_id = stations[0]["properties"]["stationIdentifier"] if stations else None

    return {
        "office":           office,
        "grid_x":           grid_x,
        "grid_y":           grid_y,
        "grid_elevation_ft": round(elev_m * 3.28084, 1),
        "radar_id":         radar_id,
        "station_id":       station_id,
    }


def main():
    with open("locations.yaml") as f:
        locations = yaml.safe_load(f)["locations"]

    cache_path = Path("locations_cache.json")
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    errors = 0
    for loc in locations:
        loc_id = loc.get("id", "")
        print(f"Fetching {loc['name']}...")
        try:
            entry = fetch_static(loc)
            cache[loc_id] = entry
            print(f"  office={entry['office']} "
                  f"grid=({entry['grid_x']},{entry['grid_y']}) "
                  f"elev={entry['grid_elevation_ft']} ft "
                  f"radar={entry['radar_id']} "
                  f"station={entry['station_id']}")
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            errors += 1

    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    print(f"\nWritten: {cache_path}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
