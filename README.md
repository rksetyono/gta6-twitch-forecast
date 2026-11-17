[![make.fun](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/rksetyono/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/rksetyono/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)

# GTA VI launch-day Twitch forecast

Forecasts how many people watch Grand Theft Auto VI live on Twitch in each
hour of its release day (2026-11-19 UTC), after the first: for hours 2–24,
five quantiles [0.05, 0.25, 0.5, 0.75, 0.95] of the category's concurrent
viewers. Entry for the [make.fun bounty](https://make.fun/bounty/gta6-twitch-ccu-forecast-01).

## How it works

`main.py` (Python 3.13, standard library only, no network) reads every
`<id>.in` case in the input directory and writes one `<id>.out` forecast per
case. The forecast is anchored on hour 1's print and fitted fresh on every
run from the case's own `comparables` (past big game launches):

1. Each comparable's release-day curve is rebuilt from its hourly viewers.
2. Only rolling-launch analogues are kept: early-access-contaminated launches
   and weak analogues are dropped, as are launches whose hour-1 print is
   under 5% of their own peak (their game unlocked simultaneously later in
   the UTC day, so hour 1 was still pre-launch).
3. Per forecast hour, log(viewers) is regressed on log(hour-1 print) across
   the kept curves. The slope runs from ~1.0 in the small hours to ~0.5 at
   the evening peak: bigger launches rise less in relative terms (the
   correlation between log hour-1 print and log peak/hour-1 is about −0.94
   across the comparables).
4. Quantiles are log-normal around the regression prediction, with spread
   0.9× the per-hour residual standard deviation (floored at 0.15 log
   units), widened a little when hour 1's print extrapolates beyond the
   fitted range.

Fallbacks keep every case valid: the filter relaxes in steps when too few
analogues survive, a single sparse hour falls back to the median shape, and
with no usable comparable at all the forecast is persistence around hour 1.
Output is deterministic: two runs on the same case write identical bytes.

## Local validation

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

On the kit's four practice cases (answers: SullyGnome hourly averages for
the release day, fetched once from the JSON endpoint documented in the kit's
`input/NOTICE.txt`; hour-1 values match the cases' known prints exactly),
mean log-scale pinball loss:

| case | this entry | house baseline (persistence) |
| ---- | ---------: | ---------------------------: |
| p00 Marvel's Wolverine | 0.2443 | 0.7436 |
| p01 Red Dead Redemption 2 | 0.1664 | 0.2868 |
| p02 God of War Ragnarök | 0.1271 | 0.2146 |
| p03 Marvel's Spider-Man 2 | 0.1784 | 0.3033 |
| **mean** | **0.1791** | **0.3871** |

Leave-one-out over the 19 filtered comparables: 0.2693 vs 0.3225 for a
median-shape forecast and 0.4026 for the baseline.

## License

MIT — see [LICENSE](LICENSE).
