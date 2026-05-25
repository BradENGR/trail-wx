"""
Unit tests for generate.py pure functions and settings-driven rendering.
Run with: py -3.14 -m pytest tests/
"""
import pytest
from datetime import datetime, timezone
from generate import (
    c_to_f, mps_to_mph, deg_to_compass,
    safe_url, valid_radar_id,
    compute_temp_offset, adjust_period_temp, adjust_temps_in_text,
    alert_web_url, alert_split, get_current_grid_temp_c,
    render_location,
)


# ── c_to_f ────────────────────────────────────────────────────────────────────

def test_c_to_f_none():
    assert c_to_f(None) is None

def test_c_to_f_freezing():
    assert c_to_f(0) == 32

def test_c_to_f_boiling():
    assert c_to_f(100) == 212

def test_c_to_f_negative():
    assert c_to_f(-40) == -40  # -40 is the crossover point


# ── mps_to_mph ────────────────────────────────────────────────────────────────

def test_mps_to_mph_none():
    assert mps_to_mph(None) is None

def test_mps_to_mph_zero():
    assert mps_to_mph(0) == 0

def test_mps_to_mph_rounds():
    assert mps_to_mph(10) == 22   # 10 * 2.237 = 22.37 → 22
    assert mps_to_mph(20) == 45   # 20 * 2.237 = 44.74 → 45


# ── deg_to_compass ────────────────────────────────────────────────────────────

def test_deg_to_compass_none():
    assert deg_to_compass(None) == ""

def test_deg_to_compass_cardinals():
    assert deg_to_compass(0)   == "N"
    assert deg_to_compass(90)  == "E"
    assert deg_to_compass(180) == "S"
    assert deg_to_compass(270) == "W"
    assert deg_to_compass(360) == "N"

def test_deg_to_compass_intercardinals():
    assert deg_to_compass(45)  == "NE"
    assert deg_to_compass(135) == "SE"
    assert deg_to_compass(225) == "SW"
    assert deg_to_compass(315) == "NW"

def test_deg_to_compass_fine():
    assert deg_to_compass(112) == "ESE"   # round(112/22.5)=5 → ESE
    assert deg_to_compass(247) == "WSW"   # round(247/22.5)=11 → WSW


# ── safe_url ──────────────────────────────────────────────────────────────────

def test_safe_url_none():
    assert safe_url(None) == "#"

def test_safe_url_empty():
    assert safe_url("") == "#"

def test_safe_url_http():
    assert safe_url("http://weather.gov/alerts") == "http://weather.gov/alerts"

def test_safe_url_https():
    assert safe_url("https://weather.gov/alerts") == "https://weather.gov/alerts"

def test_safe_url_rejects_other_schemes():
    assert safe_url("ftp://example.com") == "#"
    assert safe_url("javascript:alert(1)") == "#"
    assert safe_url("data:text/html,hi") == "#"


# ── valid_radar_id ────────────────────────────────────────────────────────────

def test_valid_radar_id_valid():
    assert valid_radar_id("KMRX") is True
    assert valid_radar_id("KGSP") is True
    assert valid_radar_id("KATL") is True

def test_valid_radar_id_none_or_empty():
    assert valid_radar_id(None) is False
    assert valid_radar_id("") is False

def test_valid_radar_id_wrong_prefix():
    assert valid_radar_id("AMRX") is False

def test_valid_radar_id_wrong_length():
    assert valid_radar_id("KMRXY") is False
    assert valid_radar_id("KMR") is False

def test_valid_radar_id_lowercase():
    assert valid_radar_id("kmrx") is False

def test_valid_radar_id_digits():
    assert valid_radar_id("K1MR") is False


# ── compute_temp_offset ───────────────────────────────────────────────────────

def test_compute_temp_offset_same_elevation():
    assert compute_temp_offset(5000, 5000) == 0

def test_compute_temp_offset_location_higher():
    # Location 1000 ft above grid → colder → negative offset
    assert compute_temp_offset(4000, 5000) == pytest.approx(-3.5)

def test_compute_temp_offset_location_lower():
    # Location 1000 ft below grid → warmer → positive offset
    assert compute_temp_offset(5000, 4000) == pytest.approx(3.5)

def test_compute_temp_offset_large_difference():
    # 2000 ft difference → 7.0°F
    assert compute_temp_offset(3000, 5000) == pytest.approx(-7.0)


# ── adjust_period_temp ────────────────────────────────────────────────────────

def test_adjust_period_temp_none():
    assert adjust_period_temp(None, "F", 0) == ("—", None)

def test_adjust_period_temp_no_offset():
    adj, orig = adjust_period_temp(62, "F", 0)
    assert adj == "62"
    assert orig == 62

def test_adjust_period_temp_with_offset():
    adj, orig = adjust_period_temp(62, "F", -3.0)
    assert adj == "59"
    assert orig == 62

def test_adjust_period_temp_celsius_passthrough():
    adj, orig = adjust_period_temp(20, "C", 5)
    assert adj == "20"
    assert orig is None  # Celsius temps are not adjusted

def test_adjust_period_temp_below_range():
    adj, orig = adjust_period_temp(25, "F", 0)  # below the 30–100 guard
    assert adj == "25"
    assert orig is None

def test_adjust_period_temp_above_range():
    adj, orig = adjust_period_temp(101, "F", 0)  # above the 30–100 guard
    assert adj == "101"
    assert orig is None


# ── adjust_temps_in_text ──────────────────────────────────────────────────────

def test_adjust_temps_in_text_zero_offset_wraps():
    result = adjust_temps_in_text("High near 62.", 0)
    assert "<strong>" in result
    assert "62" in result
    assert 'class="orig"' not in result

def test_adjust_temps_in_text_shows_adjusted_and_original():
    result = adjust_temps_in_text("High near 62.", -5)
    assert "57" in result   # adjusted value
    assert "62" in result   # original preserved in orig span
    assert 'class="orig"' in result

def test_adjust_temps_in_text_preserves_leadup_phrase():
    result = adjust_temps_in_text("temperatures falling to around 59", -3)
    assert "<strong>" in result
    assert "56" in result   # 59 - 3

def test_adjust_temps_in_text_excludes_mph():
    result = adjust_temps_in_text("Winds 45 mph.", 10)
    assert "<strong>55</strong>" not in result

def test_adjust_temps_in_text_excludes_percent():
    result = adjust_temps_in_text("Chance of precipitation is 90%.", 10)
    assert "<strong>100</strong>" not in result

def test_adjust_temps_in_text_excludes_below_range():
    result = adjust_temps_in_text("Temperature 25 degrees.", 0)
    assert "<strong>25</strong>" not in result

def test_adjust_temps_in_text_multiple_temps():
    result = adjust_temps_in_text("High near 65, low around 45.", -5)
    assert "60" in result   # 65 adjusted
    assert "40" in result   # 45 adjusted


# ── alert_web_url ─────────────────────────────────────────────────────────────

def test_alert_web_url_valid():
    url = alert_web_url({"AWIPSidentifier": ["SPSGSP"]})
    assert url is not None
    assert "issuedby=GSP" in url
    assert "product=SPS" in url
    assert url.startswith("https://forecast.weather.gov")

def test_alert_web_url_empty_params():
    assert alert_web_url({}) is None

def test_alert_web_url_short_awips():
    assert alert_web_url({"AWIPSidentifier": ["SPS"]}) is None

def test_alert_web_url_long_awips():
    assert alert_web_url({"AWIPSidentifier": ["SPSGSPXX"]}) is None

def test_alert_web_url_empty_list():
    assert alert_web_url({"AWIPSidentifier": []}) is None


# ── alert_split ───────────────────────────────────────────────────────────────

def test_alert_split_empty():
    assert alert_split("") == ("", "")

def test_alert_split_short_text_no_split():
    snip, rest = alert_split("Dense fog.")
    assert snip == "Dense fog."
    assert rest == ""

def test_alert_split_sentence_within_80():
    text = "Dense fog has developed. Visibilities are low."
    snip, rest = alert_split(text)
    assert snip == "Dense fog has developed."
    assert rest == "Visibilities are low."

def test_alert_split_long_cuts_at_word_boundary():
    # No early sentence; first sentence > 80 chars
    text = "Areas of dense fog have developed with visibilities as low as a quarter mile across the entire warned region today."
    snip, rest = alert_split(text)
    assert len(snip) <= 80
    assert not snip.endswith(" ")
    # snip and rest together reconstruct the content (HTML-escaped)
    assert snip
    assert rest

def test_alert_split_multi_paragraph_preserves_break():
    # Cut falls at sentence boundary — only 2nd paragraph ends up in rest,
    # so no <br><br> needed (single item). Verify <br><br> with 3 paragraphs below.
    text = "First sentence.\n\nSecond paragraph here."
    snip, rest = alert_split(text)
    assert snip == "First sentence."
    assert rest == "Second paragraph here."

def test_alert_split_multi_paragraph_separator():
    # Cut in first paragraph → partial para1 + full para2 in rest, separated by <br><br>
    para1 = "This is a long first paragraph that will exceed the eighty character limit easily."
    para2 = "Second paragraph."
    snip, rest = alert_split(para1 + "\n\n" + para2)
    assert len(snip) <= 80
    assert "<br><br>" in rest
    assert "Second paragraph." in rest

def test_alert_split_cut_within_first_paragraph():
    # First para > 80 chars, second para ends up in rest after <br><br>
    para1 = "Word " * 20    # 100 chars — forces word-boundary cut
    para2 = "Second paragraph."
    text = para1.strip() + "\n\n" + para2
    snip, rest = alert_split(text)
    assert len(snip) <= 80
    assert "Second paragraph." in rest
    assert "<br><br>" in rest

def test_alert_split_escapes_html_in_snip():
    text = "Alert with <b>bold</b> & special chars in opening sentence."
    snip, rest = alert_split(text)
    assert "<b>" not in snip
    assert "&lt;" in snip or "&amp;" in snip


# ── get_current_grid_temp_c ───────────────────────────────────────────────────

def _entry(start_iso, hours, value):
    return {"validTime": f"{start_iso}/PT{hours}H", "value": value}

def test_get_current_grid_temp_c_empty():
    assert get_current_grid_temp_c([], datetime.now(timezone.utc)) is None

def test_get_current_grid_temp_c_hit():
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    assert get_current_grid_temp_c([_entry("2026-05-24T11:00:00+00:00", 2, 15.0)], now) == 15.0

def test_get_current_grid_temp_c_miss_before():
    now = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    assert get_current_grid_temp_c([_entry("2026-05-24T11:00:00+00:00", 2, 15.0)], now) is None

def test_get_current_grid_temp_c_miss_after():
    now = datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc)
    assert get_current_grid_temp_c([_entry("2026-05-24T11:00:00+00:00", 2, 15.0)], now) is None

def test_get_current_grid_temp_c_picks_correct_interval():
    now = datetime(2026, 5, 24, 13, 0, tzinfo=timezone.utc)
    entries = [
        _entry("2026-05-24T10:00:00+00:00", 2, 10.0),
        _entry("2026-05-24T12:00:00+00:00", 2, 20.0),
        _entry("2026-05-24T14:00:00+00:00", 2, 30.0),
    ]
    assert get_current_grid_temp_c(entries, now) == 20.0

def test_get_current_grid_temp_c_boundary_exclusive():
    # now == start of next interval; should NOT match previous
    now = datetime(2026, 5, 24, 13, 0, tzinfo=timezone.utc)
    assert get_current_grid_temp_c([_entry("2026-05-24T11:00:00+00:00", 2, 99.0)], now) is None


# ── render_location settings flags ───────────────────────────────────────────

def _period():
    return {
        "name": "Today",
        "temperature": 65,
        "temperatureUnit": "F",
        "detailedForecast": "Sunny. High near 65.",
        "shortForecast": "Sunny",
    }

def _render(settings, periods=None, radar_id="KMRX"):
    return render_location(
        loc={"name": "Test Peak", "elevation_ft": 5000},
        periods=periods or [_period() for _ in range(6)],
        alerts=[],
        obs=None,
        temp_offset=0,
        grid_elev_ft=5000,
        current_temp_f=65,
        radar_id=radar_id,
        generated_at=datetime.now(timezone.utc),
        settings=settings,
    )

def test_render_radar_excluded():
    html = _render({"include-radar": False})
    assert '<details class="radar-det">' not in html

def test_render_radar_included():
    html = _render({"include-radar": True})
    assert '<details class="radar-det">' in html

def test_render_radar_absent_when_no_id():
    html = _render({"include-radar": True}, radar_id="")
    assert '<details class="radar-det">' not in html

def test_render_observations_excluded():
    html = _render({"include-observations": False})
    assert 'class="obs"' not in html

def test_render_observations_included():
    html = _render({"include-observations": True})
    assert 'class="obs"' in html

def test_render_periods_shown_count():
    periods = [_period() for _ in range(6)]
    html = _render({"periods-shown": 2, "periods-more": 4}, periods=periods)
    assert "4 more periods" in html

def test_render_periods_more_zero_hides_toggle():
    periods = [_period() for _ in range(6)]
    html = _render({"periods-shown": 2, "periods-more": 0}, periods=periods)
    assert "more period" not in html

def test_render_periods_shown_caps_without_extra():
    # Fewer periods available than periods-shown — no toggle should appear
    periods = [_period(), _period()]
    html = _render({"periods-shown": 3, "periods-more": 5}, periods=periods)
    assert "more period" not in html

def test_render_defaults_produce_valid_html():
    html = _render({})
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html

def test_render_notice_present():
    html = _render({})
    assert 'class="notice"' in html
    assert "without guarantee of accuracy" in html
    assert "sole source" in html
