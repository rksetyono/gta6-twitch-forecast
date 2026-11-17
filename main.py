"""Forecast GTA VI's launch-day Twitch viewers, hour by hour.

make.fun competition gta6-twitch-ccu-forecast-01, protocol agent-fun/program/v1.
Python 3.13, standard library only, no network.

The program reads every `<id>.in` case in the input directory and writes one
`<id>.out` forecast per case: for hours 2..24 of the release day, five
quantiles [0.05, 0.25, 0.5, 0.75, 0.95] of the Twitch category's concurrent
viewers in that hour, anchored on hour 1's print.

Model (fit fresh on every run, from the case's own `comparables`):
  1. Build each comparable launch's release-day curve: its hourly viewers
     from its `day_start` to 24 hours later.
  2. Keep only rolling-launch analogues: drop early-access-contaminated
     launches (their release day was already half over at hour 1) and weak
     analogues, drop launches whose hour-1 print is under 5% of their own
     peak (those unlocked simultaneously later in the UTC day, so hour 1 was
     still pre-launch), and require at least 20 of the 24 release-day hours.
  3. Per forecast hour h, regress log(viewers) on log(hour-1 print) across
     the kept curves:  log v_h = a_h + b_h * log(p1).  The slope runs from
     ~1.0 in the small hours to ~0.5 at the evening peak: bigger launches
     rise less in relative terms, which is the whole signal (the correlation
     between log(p1) and log(peak/p1) is about -0.94 across the comparables).
     The slope is clamped to [0, 1.05].
  4. Quantiles are log-normal around the regression prediction, with spread
     0.9x the per-hour residual standard deviation (floored at 0.15 log
     units) — the residual sd itself is averaged over hours h-1, h, h+1,
     because a single hour's sd is estimated from only ~30 curves and is
     noisy while true uncertainty evolves smoothly across the day — widened
     a little when hour 1's print lies outside the range of hour-1 prints
     the regression was fit on (extrapolation is less certain).

Fallbacks keep every case valid: if too few analogues survive the filter the
filter is relaxed in steps; if a single hour has too few points the
median-shape forecast is used for that hour; with no usable comparable at
all the forecast is persistence around hour 1 with a widening spread.

Input/output: $MAKEFUN_INPUT_DIR (else /input) and $MAKEFUN_OUTPUT_DIR
(else /output). Deterministic: same input bytes give same output bytes.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# Standard normal quantiles at the scored levels.
Z = [-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722]
MAX_VIEWERS = 1_000_000_000

# Filter / fit constants (see module docstring).
MIN_PEAK_SHARE = 0.05      # hour-1 print must be >= 5% of the launch's peak
MIN_DAY_HOURS = 20         # release-day hours a curve must have
MIN_CURVES = 5             # analogues needed before the filter is relaxed
MIN_POINTS_PER_HOUR = 3    # points needed for a per-hour regression
SPREAD_SCALE = 0.9         # residual sd -> forecast log-spread
SPREAD_FLOOR = 0.15        # minimum log-spread
SPREAD_WIDEN_PER_LOG = 0.25  # extra spread per log unit outside the fit range
SPREAD_WIDEN_CAP = 0.30
SLOPE_MIN = 0.0
SLOPE_MAX = 1.05
PERSIST_SIGMA = 0.15       # fallback persistence spread


def parse_time(text):
    """Parse an ISO 8601 UTC timestamp into an aware datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def round2(value):
    """Round to 2 decimals, half up; whole numbers come back as ints."""
    rounded = Decimal(repr(value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def clamp_quantile(value):
    """Keep a forecast quantile inside (0, 1e9] as the rules require."""
    if not math.isfinite(value) or value <= 0:
        return 1.0
    if value > MAX_VIEWERS:
        return MAX_VIEWERS
    return value


def release_day_curve(comp):
    """Hourly viewers {1..24: viewers} of a comparable's release day.

    Returns None when the comparable has no usable release day (no positive
    hour-1 print, or fewer than MIN_DAY_HOURS hours present).
    """
    day_start = parse_time(comp["day_start"])
    day = {}
    for point in comp.get("hourly") or []:
        viewers = point.get("viewers")
        if viewers is None or viewers <= 0:
            continue
        offset = (parse_time(point["from"]) - day_start).total_seconds() / 3600
        if 0 <= offset < 24:
            day[int(offset) + 1] = viewers
    if 1 not in day or len(day) < MIN_DAY_HOURS:
        return None
    return day


def is_analogue(comp, day):
    """Is this comparable a rolling-launch analogue of the target?

    Early-access launches and weak analogues are out (their release day does
    not start at hour 1), as are launches whose hour 1 is still pre-launch
    (simultaneous global unlock later in the UTC day): their hour-1 print is
    a small fraction of their own peak.
    """
    unlock = comp.get("unlock") or ""
    if "early-access" in unlock or "Tier: weak" in unlock:
        return False
    peak = max(day.values())
    return day[1] / peak >= MIN_PEAK_SHARE


def collect_curves(case, strict=True):
    """Release-day curves of the case's comparables, filtered if strict."""
    curves = []
    for comp in case.get("comparables") or []:
        day = release_day_curve(comp)
        if day is None:
            continue
        if strict and not is_analogue(comp, day):
            continue
        curves.append(day)
    return curves


def fit_hour(curves, hour):
    """OLS fit of log(viewers at `hour`) on log(hour-1 print).

    Returns (intercept, slope, residual_sd, n) or None when too few points.
    """
    points = [(math.log(day[1]), math.log(day[hour]))
              for day in curves if hour in day]
    n = len(points)
    if n < MIN_POINTS_PER_HOUR:
        return None
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
             if sxx > 0 else 0.0)
    intercept = mean_y - slope * mean_x
    if n > 2:
        resid_sd = math.sqrt(
            sum((y - (intercept + slope * x)) ** 2 for x, y in points)
            / (n - 2))
    else:
        resid_sd = 0.5
    return intercept, slope, resid_sd, n


def median_shape(curves, hour):
    """Median and IQR of log(viewers_h / viewers_1) across curves."""
    ratios = sorted(math.log(day[hour] / day[1])
                    for day in curves if hour in day)
    if not ratios:
        return None
    n = len(ratios)
    median = ratios[n // 2] if n % 2 else 0.5 * (ratios[n // 2 - 1] + ratios[n // 2])
    iqr = ratios[(3 * n) // 4] - ratios[n // 4]
    return median, max(SPREAD_FLOOR, iqr / 1.349)


def smoothed_resid_sd(curves, hour):
    """Residual sd for `hour`, averaged over neighbouring hours.

    The per-hour residual sd is estimated from only ~30 curves, so it is
    noisy hour to hour, while the true forecast uncertainty evolves
    smoothly across the day (viewing patterns change gradually). Averaging
    the fitted sd over hours h-1, h, h+1 (where a fit exists) removes
    estimation noise without biasing the level.
    """
    window = [fit_hour(curves, h) for h in range(max(2, hour - 1),
                                                min(24, hour + 1) + 1)]
    sds = [fit[2] for fit in window if fit is not None]
    return sum(sds) / len(sds) if sds else None


def hour_quantiles(p1, hour, curves, log_p1_range):
    """The five quantiles for one forecast hour."""
    log_p1 = math.log(p1)
    fit = fit_hour(curves, hour)
    if fit is not None:
        intercept, slope, resid_sd, _ = fit
        slope = min(SLOPE_MAX, max(SLOPE_MIN, slope))
        centre = intercept + slope * log_p1
        smoothed = smoothed_resid_sd(curves, hour)
        spread = max(SPREAD_FLOOR,
                     SPREAD_SCALE * (smoothed if smoothed is not None
                                     else resid_sd))
    else:
        shape = median_shape(curves, hour)
        if shape is None:
            # No analogue knows this hour: persistence with widening spread.
            centre = log_p1
            spread = PERSIST_SIGMA * math.sqrt(hour - 1)
        else:
            centre = log_p1 + shape[0]
            spread = max(SPREAD_FLOOR, shape[1])
    # Widen when hour 1's print extrapolates beyond the fitted range.
    if log_p1_range is not None:
        low, high = log_p1_range
        outside = max(0.0, low - log_p1, log_p1 - high)
        spread += min(SPREAD_WIDEN_CAP, SPREAD_WIDEN_PER_LOG * outside)
    return [round2(clamp_quantile(math.exp(centre + z * spread))) for z in Z]


def forecast_case(case):
    """Forecast hours 2..24 of one case."""
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    if first is not None and first.get("viewers"):
        p1 = max(1.0, float(first["viewers"]))
    else:
        # No hour-1 print: fall back to the latest pre-checkpoint point.
        hourly = case.get("history_hourly") or []
        p1 = 1.0
        if hourly:
            last = hourly[-1].get("viewers")
            if last:
                p1 = max(1.0, float(last))

    curves = collect_curves(case, strict=True)
    if len(curves) < MIN_CURVES:
        curves = collect_curves(case, strict=False)
    log_p1_range = None
    if curves:
        logs = [math.log(day[1]) for day in curves]
        log_p1_range = (min(logs), max(logs))

    hours = []
    for row in case["forecast"]:
        hour = row["hour"]
        if curves:
            quantiles = hour_quantiles(p1, hour, curves, log_p1_range)
        else:
            # No comparable at all: persistence around hour 1.
            quantiles = [round2(clamp_quantile(
                p1 * math.exp(z * PERSIST_SIGMA * math.sqrt(hour - 1))))
                for z in Z]
        hours.append({"hour": hour, "q": quantiles})
    return {"hours": hours}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
