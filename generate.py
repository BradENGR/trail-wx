import sys
import re
import json
import yaml
import requests
from html import escape
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

EASTERN = ZoneInfo("America/New_York")

UA = "trail-wx/1.0 (https://github.com/BradENGR/trail-wx/issues)"
LAPSE_RATE = 3.5  # °F per 1000 ft


# ── NWS API helpers ────────────────────────────────────────────────────────────

def nws_get(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    return r.json()


def load_location_cache():
    path = Path("locations_cache.json")
    if not path.exists():
        raise FileNotFoundError("locations_cache.json not found — run cache.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def get_grid_temp_values(office, grid_x, grid_y):
    try:
        data = nws_get(
            f"https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}"
        )["properties"]
        return (data.get("temperature") or {}).get("values", [])
    except Exception as e:
        print(f"  WARN grid temp: {e}", file=sys.stderr)
        return []


def get_forecast(forecast_url):
    try:
        return nws_get(forecast_url)["properties"]["periods"]
    except Exception as e:
        print(f"  WARN forecast: {e}", file=sys.stderr)
        return None


def get_alerts(lat, lon):
    try:
        return nws_get(f"https://api.weather.gov/alerts/active?point={lat},{lon}")["features"]
    except Exception as e:
        print(f"  WARN alerts: {e}", file=sys.stderr)
        return []


def get_observations(station_id):
    try:
        return nws_get(
            f"https://api.weather.gov/stations/{station_id}/observations/latest"
        )["properties"]
    except Exception as e:
        print(f"  WARN observations: {e}", file=sys.stderr)
        return None


def get_current_grid_temp_c(temp_values, now_utc):
    """Return grid temperature (°C) for the interval containing now_utc, or None."""
    for entry in temp_values:
        try:
            valid_str, dur_str = entry["validTime"].split("/")
            start = datetime.fromisoformat(valid_str)
            m = re.match(r'PT(\d+(?:\.\d+)?)H', dur_str)
            hours = float(m.group(1)) if m else 1.0
            if start <= now_utc < start + timedelta(hours=hours):
                return entry.get("value")
        except Exception:
            continue
    return None


# ── Temperature helpers ────────────────────────────────────────────────────────

def compute_temp_offset(grid_elev_ft, location_elev_ft):
    return -((location_elev_ft - grid_elev_ft) / 1000.0) * LAPSE_RATE


def adjust_temps_in_text(text, offset):
    # Optionally capture NWS leadup phrase ("high near", "low around", etc.)
    # then the temperature integer 30-100, excluding unit-suffixed numbers.
    pattern = (
        r'((?:(?:high|low|temperatures?)\s+(?:near|around|of)|(?:near|around))\s+)?'
        r'(\b(?:[3-9]\d|100)\b)'
        r'(?!\s*(?:mph|percent|%|kt|knots|inches?|mm|cm|mb|hpa))'
    )

    def replacer(m):
        prefix = m.group(1) or ""
        val = int(m.group(2))
        adj = round(val + offset)
        if adj == val:
            return f'<strong>{prefix}{val}</strong>'
        return f'<strong>{prefix}{adj}</strong><span class="orig">({val})</span>'

    return re.sub(pattern, replacer, text, flags=re.IGNORECASE)


def adjust_period_temp(temp, temp_unit, offset):
    """Returns (adjusted_str, original) or (raw_str, None)."""
    if temp is None:
        return "—", None
    if temp_unit == "F" and 30 <= temp <= 100:
        adj = round(temp + offset)
        return str(adj), temp
    return str(temp), None


# ── Unit conversions ───────────────────────────────────────────────────────────

def c_to_f(c):
    return c * 9 / 5 + 32 if c is not None else None


def mps_to_mph(mps):
    return round(mps * 2.237) if mps is not None else None


def deg_to_compass(deg):
    if deg is None:
        return ""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[round(deg / 22.5) % 16]


def safe_url(url):
    if url and re.match(r'^https?://', str(url)):
        return url
    return "#"


def alert_web_url(params):
    """Build a human-readable NWS text-product URL from AWIPSidentifier, or return None."""
    awips = (params.get("AWIPSidentifier") or [""])[0]
    if len(awips) == 6:
        product, office = awips[:3].upper(), awips[3:].upper()
        return f"https://forecast.weather.gov/product.php?site=NWS&issuedby={office}&product={product}&format=CI&version=1&glossary=0"
    return None


def alert_split(text, max_chars=80):
    """Return (snip_escaped, rest_html) split at first sentence or word boundary.

    snip: escaped text up to the cut point (no ellipsis — caller adds it via CSS)
    rest_html: escaped remainder with paragraph breaks as <br><br>, or "" if fits in snip
    """
    if not text:
        return "", ""
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n") if p.strip()]
    collapsed = " ".join(paragraphs)
    if not collapsed:
        return "", ""

    dot = collapsed.find(". ")
    if 0 < dot <= max_chars:
        cut = dot + 1
    elif len(collapsed) <= max_chars:
        return escape(collapsed), ""
    else:
        space = collapsed[:max_chars].rfind(" ")
        cut = space if space > 0 else max_chars

    snip = escape(collapsed[:cut])

    # Rebuild rest with paragraph breaks intact
    rest_parts = []
    pos = 0
    for para in paragraphs:
        end = pos + len(para)
        if cut <= pos:
            rest_parts.append(escape(para))
        elif cut < end:
            remainder = para[cut - pos:].lstrip()
            if remainder:
                rest_parts.append(escape(remainder))
        pos = end + 1  # +1 for the joining space between paragraphs in collapsed
    rest_html = "<br><br>".join(rest_parts)
    return snip, rest_html


def valid_radar_id(radar_id):
    return bool(radar_id and re.match(r'^K[A-Z]{3}$', str(radar_id)))


# ── HTML rendering ─────────────────────────────────────────────────────────────

CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;font-size:14px;line-height:1.45;max-width:500px;margin:0 auto;padding:10px}
a{color:inherit}
h1{font-size:17px;margin-bottom:2px}
.meta{font-size:11px;color:#888;margin-bottom:10px}
.alerts{margin-bottom:10px}
.alert{border:1px solid;border-radius:3px;padding:5px 8px;margin-bottom:4px}
.alert a{font-weight:700;text-decoration:none;display:block}
.alert-det{margin-top:3px}
.alert-snip{font-size:11px;margin-top:3px;opacity:0.75}
.alert-det summary{font-size:11px;opacity:0.75;cursor:pointer;list-style:none;line-height:1.5}
.alert-det summary::-webkit-details-marker{display:none}
.alert-det summary::before{content:"▸ ";font-size:9px}
.alert-det[open] summary::before{content:"▾ "}
.alert-rest{display:none}
.alert-det[open] .alert-ell{display:none}
.alert-det[open] .alert-rest{display:inline}
.obs{border-left:3px solid #4a8;padding:5px 8px;margin-bottom:10px;font-size:13px}
.obs-row{display:flex;flex-wrap:wrap;gap:12px}
.periods{margin-bottom:10px}
.period{border-radius:3px;padding:6px 8px;margin-bottom:4px}
.period-head{display:flex;justify-content:space-between;align-items:baseline}
.period-name{font-weight:700;font-size:13px}
.period-temp{font-weight:700}
.period-text{font-size:12px;margin-top:3px}
.orig{font-size:10px;margin-left:2px}
.more-periods{margin-bottom:4px}
.more-periods summary{font-size:12px;opacity:0.7;cursor:pointer;list-style:none;padding:3px 0}
.more-periods summary::-webkit-details-marker{display:none}
.more-periods summary::before{content:"▸ ";font-size:9px}
.more-periods[open] summary::before{content:"▾ "}
.radar-det{margin-bottom:8px}
.radar-det summary{font-size:12px;cursor:pointer;list-style:none;margin-bottom:6px}
.radar-det summary::-webkit-details-marker{display:none}
.radar-det summary::before{content:"▸ ";font-size:9px}
.radar-det[open] summary::before{content:"▾ "}
.radar img{max-width:100%;border-radius:3px;display:block}
.radar-link{font-size:11px;margin-top:2px}
.gen{font-size:10px;margin-top:8px}
@media(prefers-color-scheme:light){
  body{background:#fff;color:#111}
  .alert{background:#fff8e1;border-color:#e6a817}
  .period{background:#f6f6f6}
  .orig{color:#888}
}
@media(prefers-color-scheme:dark){
  body{background:#111;color:#ddd}
  a{color:#7ab8f5}
  .alert{background:#2d1a00;border-color:#c87}
  .period{background:#1e1e1e}
  .orig{color:#777}
  .obs{border-color:#4a8}
}
"""

INDEX_CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;font-size:14px;line-height:1.6;max-width:400px;margin:0 auto;padding:16px}
h1{font-size:18px;margin-bottom:12px}
ul{list-style:none}
li{padding:6px 0;border-bottom:1px solid}
a{text-decoration:none;font-weight:600}
a:hover{text-decoration:underline}
.elev{font-size:11px;margin-left:6px}
.gen{font-size:10px;margin-top:14px}
@media(prefers-color-scheme:light){
  body{background:#fff;color:#111}
  li{border-color:#e8e8e8}
  a{color:#1a6ab0}
  .elev{color:#888}
}
@media(prefers-color-scheme:dark){
  body{background:#111;color:#ddd}
  li{border-color:#333}
  a{color:#7ab8f5}
  .elev{color:#777}
}
"""


def render_location(loc, periods, alerts, obs, temp_offset, grid_elev_ft, current_temp_f, radar_id, generated_at):
    name = escape(loc["name"])
    elev_ft = loc["elevation_ft"]
    if not valid_radar_id(radar_id):
        radar_id = ""
    ts = generated_at.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M %Z")

    # ── alerts ──
    alerts_html = ""
    if alerts:
        items = []
        for a in alerts:
            p = a.get("properties", {})
            params = p.get("parameters", {})
            event = escape(p.get("event", "Alert"))
            url = escape(safe_url(alert_web_url(params)))
            desc = p.get("description") or ""
            snip, rest = alert_split(desc)
            if snip and rest:
                detail_html = (
                    f'<details class="alert-det">'
                    f'<summary>{snip}'
                    f'<span class="alert-ell"> …</span>'
                    f'<span class="alert-rest"> {rest}</span>'
                    f'</summary>'
                    f'</details>'
                )
            elif snip:
                detail_html = f'<div class="alert-snip">{snip}</div>'
            else:
                detail_html = ""
            items.append(f'<div class="alert"><a href="{url}">{event}</a>{detail_html}</div>')
        alerts_html = f'<div class="alerts">{"".join(items)}</div>\n'

    # ── observations ──
    obs_html = ""
    parts = []
    parts.append(
        f'<span>Temp: <b>{current_temp_f}°F</b></span>'
        if current_temp_f is not None else
        '<span>Temp: <b>—</b></span>'
    )
    if obs:
        wind_mph = mps_to_mph((obs.get("windSpeed") or {}).get("value"))
        wind_dir = deg_to_compass((obs.get("windDirection") or {}).get("value"))
        if wind_mph is not None:
            parts.append(f'<span>Wind: <b>{wind_dir} {wind_mph} mph</b></span>')
        sky = escape(obs.get("textDescription", "").strip())
        if sky:
            parts.append(f'<span>Sky: <b>{sky}</b></span>')
    if parts:
        obs_html = f'<div class="obs"><div class="obs-row">{"".join(parts)}</div></div>\n'

    # ── forecast periods ──
    if periods:
        all_items = []
        for p in periods:
            pname = escape(p.get("name", ""))
            temp = p.get("temperature")
            unit = p.get("temperatureUnit", "F")
            detail = escape(p.get("detailedForecast") or p.get("shortForecast", ""))
            adj_temp, orig_temp = adjust_period_temp(temp, unit, temp_offset)
            if orig_temp is not None and str(orig_temp) != adj_temp:
                temp_html = f'<strong>{adj_temp}</strong><span class="orig">({orig_temp})</span>°F'
            elif orig_temp is not None:
                temp_html = f'<strong>{adj_temp}</strong>°F'
            else:
                temp_html = f"{adj_temp}°{escape(unit)}"
            adj_detail = adjust_temps_in_text(detail, temp_offset)
            all_items.append(
                f'<div class="period">'
                f'<div class="period-head">'
                f'<span class="period-name">{pname}</span>'
                f'<span class="period-temp">{temp_html}</span>'
                f'</div>'
                f'<div class="period-text">{adj_detail}</div>'
                f'</div>'
            )
        shown = "".join(all_items[:2])
        extra = all_items[2:]
        if extra:
            n = len(extra)
            more = (
                f'<details class="more-periods">'
                f'<summary>{n} more period{"s" if n != 1 else ""}</summary>'
                f'{"".join(extra)}'
                f'</details>'
            )
        else:
            more = ""
        periods_html = f'<div class="periods">{shown}{more}</div>\n'
    else:
        periods_html = '<div class="periods"><p>Forecast data unavailable.</p></div>\n'

    # ── radar ──
    radar_html = ""
    if radar_id:
        img_url = f"https://radar.weather.gov/ridge/standard/{radar_id}_0.gif"
        stn_url = f"https://radar.weather.gov/station/{radar_id}"
        radar_html = (
            f'<details class="radar-det">'
            f'<summary>Radar: {radar_id}</summary>'
            f'<div class="radar">'
            f'<img src="{img_url}" alt="Radar {radar_id}" loading="lazy">'
            f'<div class="radar-link"><a href="{stn_url}">Full radar: {radar_id}</a></div>'
            f'</div>'
            f'</details>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Trail Weather</title>
<style>{CSS}</style>
</head>
<body>
<h1>{name}</h1>
<div class="meta">{elev_ft:,} ft &bull; Grid: {round(grid_elev_ft):,} ft &bull; Adj: {round(temp_offset):+d}&deg;F &bull; <a href="index.html">All locations</a></div>
{alerts_html}{obs_html}{periods_html}{radar_html}<div class="gen">Updated: {ts}</div>
</body>
</html>"""


def render_index(locations, generated_at):
    ts = generated_at.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M %Z")
    items = "".join(
        f'<li><a href="{loc["id"]}.html">{escape(loc["name"])}</a>'
        f'<span class="elev">{loc["elevation_ft"]:,} ft</span></li>'
        for loc in locations
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trail Weather</title>
<style>{INDEX_CSS}</style>
</head>
<body>
<h1>Trail Weather</h1>
<ul>{items}</ul>
<div class="gen">Updated: {ts}</div>
</body>
</html>"""


# ── Build worker ──────────────────────────────────────────────────────────────

def build_location(loc, cached, generated_at, out_dir):
    lat, lon    = loc["lat"], loc["lon"]
    office      = cached["office"]
    grid_x      = cached["grid_x"]
    grid_y      = cached["grid_y"]
    grid_elev_ft = cached["grid_elevation_ft"]
    radar_id    = cached.get("radar_id", "")
    station_id  = cached.get("station_id")

    temp_offset = compute_temp_offset(grid_elev_ft, loc["elevation_ft"])

    grid_temp_values = get_grid_temp_values(office, grid_x, grid_y)
    grid_temp_c  = get_current_grid_temp_c(grid_temp_values, generated_at)
    current_temp_f = round(c_to_f(grid_temp_c) + temp_offset) if grid_temp_c is not None else None

    forecast_url = f"https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}/forecast"
    periods = get_forecast(forecast_url)
    alerts  = get_alerts(lat, lon)
    obs     = get_observations(station_id) if station_id else None

    page_html = render_location(
        loc, periods, alerts, obs,
        temp_offset, grid_elev_ft, current_temp_f, radar_id, generated_at,
    )
    out_path = out_dir / f"{loc['id']}.html"
    out_path.write_text(page_html, encoding="utf-8")
    print(f"  -> {out_path}")
    return loc


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    with open("locations.yaml") as f:
        config = yaml.safe_load(f)
    locations = config["locations"]

    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)

    generated_at = datetime.now(timezone.utc)

    try:
        cache = load_location_cache()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate and collect work items sequentially before launching threads
    work_items = []
    errors = 0
    for loc in locations:
        loc_id = loc.get("id", "")
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', str(loc_id)):
            print(f"SKIP invalid id {loc_id!r}", file=sys.stderr)
            errors += 1
            continue
        cached = cache.get(loc_id)
        if not cached:
            print(f"ERROR no cache entry for {loc_id!r} — run cache.py first", file=sys.stderr)
            errors += 1
            continue
        print(f"Building {loc['name']}...")
        work_items.append((loc, cached))

    # Fetch live data for all locations in parallel
    built_locations = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(build_location, loc, cached, generated_at, out_dir)
            for loc, cached in work_items
        ]
        for future in futures:
            try:
                built_locations.append(future.result())
            except Exception as e:
                print(f"ERROR: {e}", file=sys.stderr)
                errors += 1

    index_html = render_index(built_locations, generated_at)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    print("  -> docs/index.html")

    if errors:
        print(f"\n{errors} location(s) failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
