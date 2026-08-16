# Kachelmannwetter for Home Assistant

[![Validate](https://github.com/globus243/kachelmannwetter-ha/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/globus243/kachelmannwetter-ha/actions/workflows/validate.yml)

Custom integration for the [Kachelmannwetter / Meteologix Public API v2](https://api.kachelmannwetter.com/v02/_doc.html).

Uses the paid Meteologix API and offers multi-model forecasts out to
14 days, real station observations including soil temperatures at four depths,
a full astronomy set, and forecast aggregates that are actually usable in
automations.

## Coverage

Forecast data is worldwide. What differs by region is the observation network:
Meteologix runs about 1,000 stations of its own, "primarily Central Europe",
plus access to global WMO stations. In DACH the nearest station is usually a
few kilometres away and reports soil temperature, leaf wetness and soil water
potential; elsewhere it can be further out and report less.

Station sensors are created from the fields the chosen station actually
delivers, so a thinner station just yields fewer entities. Developed and
tested against a Central European location.

## Requirements

- A Meteologix subscription with API access. The private tier is sold as
  **Private / Private Smart Home** (private use only), commercial use needs one
  of the Business packages. Current packages and limits are in the
  [portal](https://accounts.meteologix.com/products)
- An API key from the [account portal](https://accounts.meteologix.com/subscriptions)
  ("manage API keys").
- The target location must be registered as an **API location** first ("manage
  API locations"); after that a radius of roughly 5 km around that point works.
  Without a registered location the API answers 403.

## Installation

**HACS:** add this repository as a custom repository (type "Integration") and
install it.

**Manual:** copy `custom_components/kachelmannwetter` into
`/config/custom_components/` and restart Home Assistant.

Then: Settings → Devices & Services → Add Integration → "Kachelmannwetter".
The flow checks key and location and offers the weather stations nearby.

## Entities

- **`weather.kachelmannwetter`** — current conditions from the analysis;
  hourly forecast stitched from 1h (24 h, SuperHD) + 3h (to 120 h) + 6h
  (to 240 h, ECMWF); daily forecast from the 14-day multi-model trend.
- **Current (analysis):** temperature, dew point, humidity, pressure,
  wind/gusts/direction, cloud coverage, precipitation 1 h, sunshine, snow,
  WMO code, weather symbol, day/night.
- **Weather station (optional):** everything the station reports, among it
  wet-bulb temperature, temperature at 5 cm, soil temperatures, QFE/QFF
  pressure, pressure tendency. Knots are converted to m/s. Values reported only
  periodically (prec6h/24h, min/max 1 h) keep their last reading — across
  restarts too (RestoreSensor); the `measured_at` attribute shows when it was
  taken.
- **Forecast aggregates (next 24 h):** precipitation total, global radiation,
  maximum gust, Tmin/Tmax.
- **3-day ECMWF:** precipitation probability today/tomorrow/day after (with
  intra-day segments as an attribute), Tmax/Tmin/precipitation/sunshine hours
  for today and tomorrow. The API drops "today" from the response in the late
  afternoon — these sensors hold the last seen value until midnight and restore
  it after a restart.
- **14-day trend:** full day list with uncertainty ranges as an attribute, plus
  the number of days above 30 °C.
- **Astronomy:** sunrise/sunset, solar noon, all three twilights (civil,
  nautical, astronomical, each dawn and dusk), moonrise/moonset. These eleven
  timestamps behave like `sun.sun` and always show the **next** event: once the
  current one has passed, a timer moves the sensor on to tomorrow's. Plus moon
  phase/illumination and day length, next full/new moon, and the complete
  14-day list as an attribute.
- **Diagnostic:** remaining API requests, 1:1 from the `X-RateLimit-Remaining`
  header. Careful: since 2026-08-12 the server side no longer counts down
  reliably and sticks around 699 — the sensor deliberately still shows what the
  API reports rather than doing its own arithmetic.

## API budget

The default intervals come to about **402 requests/day** plus 16 for the two
full refreshes — roughly 60 % of the budget. The rest is reserve for restarts
(8 requests each) and manual refreshes.

| Endpoint | Interval | Requests/day |
|---|---|---|
| current | 10 min | 144 |
| station | 10 min | 144 |
| advanced/1h | 30 min | 48 |
| advanced/3h | 1 h | 24 |
| 3day (ECMWF) | 1 h | 24 |
| advanced/6h | 3 h | 8 |
| trend14days | 3 h | 8 |
| astronomy | 12 h | 2 |

On top of that the integration reloads **all endpoints together at 00:05 and
12:05 local time**: at midnight so the day-scoped sensors (today/tomorrow,
astronomy) switch to the new date immediately instead of waiting for their
interval.

## Disclaimer

Independent, unofficial project, not affiliated with or endorsed by Meteologix
AG / Kachelmann Group.

Bring your own subscription and API key — none is shipped here, and the API
terms forbid passing a key on. The integration fetches data only for the Home
Assistant instance it runs on and does not redistribute it. Weather data by
Kachelmannwetter / Meteologix AG; per their terms, severe weather data is a
supporting planning aid and does not replace official warnings.

## License

[MIT](LICENSE) © Tim Hartmann
