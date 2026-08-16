"""Constants for the Kachelmannwetter integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "kachelmannwetter"

CONF_STATION_ID = "station_id"
CONF_STATION_NAME = "station_name"
STATION_NONE = "none"

# Request the 3-day forecast explicitly with ECMWF. For standard/advanced the
# 3h and 6h steps already default to ECMWF, 1h to SuperHD. `model=` is
# undocumented on these endpoints — it works, but can go away any time.
DEFAULT_MODEL_3DAY = "ECMWF"

ATTRIBUTION = "Data provided by Kachelmannwetter / Meteologix AG"

# Update intervals, sized for the 700 requests/day of the private plan.
# Total: ~402/day + 16 for the two full refreshes = ~60 % of the budget; the
# remainder is headroom for restarts (8 requests each) and manual refreshes.
#
#   current  10 min -> 144    fc1h   30 min ->  48    day3   1h ->  24
#   station  10 min -> 144    fc3h    1h    ->  24    trend  3h ->   8
#                             fc6h    3h    ->   8    astro 12h ->   2
#
# The analysis behind /current is computed in 10-minute steps. On the private
# plan a station only reports hourly (the 10-minute resolution is locked), so
# polling more often buys no extra detail — only lower latency until the new
# hourly value arrives. 5 minutes would fit the budget (~80 %) and gain
# nothing.
UPDATE_CURRENT = timedelta(minutes=10)
UPDATE_STATION = timedelta(minutes=10)
UPDATE_FC1H = timedelta(minutes=30)
UPDATE_FC3H = timedelta(hours=1)
UPDATE_FC6H = timedelta(hours=3)
UPDATE_3DAY = timedelta(hours=1)
UPDATE_TREND = timedelta(hours=3)
UPDATE_ASTRO = timedelta(hours=12)

# Full refresh of every endpoint (local time). Midnight makes the day-scoped
# sensors (today/tomorrow, astronomy) switch to the new date immediately
# instead of waiting for their next interval; noon picks up the 12z model runs.
FULL_REFRESH_HOURS = [0, 12]
FULL_REFRESH_MINUTE = 5

KNOTS_TO_MS = 0.514444  # station values arrive in knots

# Kachelmann weather symbol (base form) -> (day condition, night condition)
CONDITION_MAP: dict[str, tuple[str, str]] = {
    "sunshine": ("sunny", "clear-night"),
    "partlycloudy": ("partlycloudy", "partlycloudy"),
    "cloudy": ("cloudy", "cloudy"),
    "overcast": ("cloudy", "cloudy"),
    "fog": ("fog", "fog"),
    "raindrizzle": ("rainy", "rainy"),
    "rain": ("rainy", "rainy"),
    "rainheavy": ("pouring", "pouring"),
    "showers": ("rainy", "rainy"),
    "showersheavy": ("pouring", "pouring"),
    "thunderstorm": ("lightning-rainy", "lightning-rainy"),
    "severethunderstorm": ("lightning-rainy", "lightning-rainy"),
    "snow": ("snowy", "snowy"),
    "snowheavy": ("snowy", "snowy"),
    "snowshowers": ("snowy", "snowy"),
    "snowshowersheavy": ("snowy", "snowy"),
    "snowrain": ("snowy-rainy", "snowy-rainy"),
    "snowrainshowers": ("snowy-rainy", "snowy-rainy"),
    "freezingrain": ("snowy-rainy", "snowy-rainy"),
    "wind": ("windy", "windy"),
}


def symbol_to_condition(symbol: str | None, is_day: bool = True) -> str | None:
    """Translate a Kachelmann weather symbol into a HA weather condition.

    Besides the documented base forms the API also returns variants with a
    "_night" suffix, with digits (partlycloudy2) and compound symbols such as
    "showers_moderate" or "showers_rain_light". Anything that is not an exact
    match in CONDITION_MAP falls through to the keyword heuristic below.
    """
    if not symbol:
        return None
    sym = symbol.lower()
    night = "_night" in sym or not is_day
    base = sym.replace("_night", "").rstrip("0123456789")

    if base in CONDITION_MAP:
        day_cond, night_cond = CONDITION_MAP[base]
        return night_cond if night else day_cond

    if "thunderstorm" in base:
        return "lightning-rainy"
    if "freezing" in base or ("snow" in base and ("rain" in base or "sleet" in base)):
        return "snowy-rainy"
    if "snow" in base:
        return "snowy"
    if "shower" in base or "rain" in base or "drizzle" in base:
        return "pouring" if "heavy" in base else "rainy"
    if "fog" in base or "mist" in base:
        return "fog"
    if "wind" in base:
        return "windy"
    if "overcast" in base:
        return "cloudy"
    if "partlycloudy" in base or "fair" in base:
        return "partlycloudy"
    if "cloud" in base:
        return "cloudy"
    if "sun" in base or "clear" in base:
        return "clear-night" if night else "sunny"
    return None
