# Garden Manager

A control panel for the home garden in Pasadena, CA (USDA zone 10b). Open `index.html` in a browser.

## How it fits together

| File | Role |
| --- | --- |
| `data/garden.json` | Source of truth: the site and every planting with its researched care profile, plan and logged dates. |
| `scripts/garden.py` | Computes the schedule (seed prep, sowing, germination, transplant, harvest, feedings) and writes `data/garden-data.js`. |
| `data/garden-data.js` | Generated. What `index.html` reads. Never edit by hand. |
| `data/planting-template.json` | Shape of one planting. Placeholder values only. |
| `research/` | One markdown note per plant with the research behind its profile. |
| `index.html` | The panel: today's tasks, forecast and weather advisories, plantings, 12-month timeline, feeding schedule, add-plants box. |

Always change data through `scripts/garden.py` (or edit `garden.json`, then run `build`). Run from this folder with `python3`.

```
python3 scripts/garden.py build
python3 scripts/garden.py add research/tomato.json research/basil.json
python3 scripts/garden.py list
python3 scripts/garden.py log <id> <seedPrep|sown|germinated|transplanted|firstHarvest|finished> [YYYY-MM-DD]
python3 scripts/garden.py done <event-id> [...]        # tick off a feeding or other task
python3 scripts/garden.py set <id> plan.sowDate 2027-01-20
python3 scripts/garden.py remove <id>
python3 scripts/garden.py tasks [--date YYYY-MM-DD] [--days 7] [--json]
```

## A planting

See `data/planting-template.json`. Key fields:

- `method`: `indoor-start` (sow indoors, transplant later), `direct-sow`, `nursery-transplant` (bought as a start) or `established` (perennials and trees already in the ground).
- `plan.sowDate` / `plan.transplantDate`: the chosen date. Leave empty to keep the planting "unscheduled"; the panel then shows when its window opens.
- `actual.*`: dates David has logged. They override projections, and everything downstream recalculates.
- `profile.windows`: `MM-DD` ranges for **Pasadena**, typed `sow-indoors`, `direct-sow` or `transplant`. A window may wrap the new year.
- `profile.germination.days`, `indoorWeeks`, `daysToMaturity.days`: a number or `[min, max]`.
- `profile.daysToMaturity.from`: `transplant` or `sow`. Seed packets differ; record which one the source means.
- `profile.harvestSeason`: `{start, end}` in `MM-DD`, for established plants with no days-to-maturity.
- `profile.fertilizer[]`: each rule has `label`, `product`, `amount`, and a `start` anchor of `sow`, `germination`, `transplant`, `harvest-start` or `calendar`. Anchored rules take `offsetDays`, optional `everyDays`, and `until` (`harvest-start` or `harvest-end`) or `count`. Calendar rules take `dates: ["MM-DD", ...]`.
- `profile.frostTenderBelowF`, `profile.heatStressAboveF`: thresholds the weather advisories use.
- `profile.sources[]`: `{title, url}` for every page the numbers came from.

## Weather rules

The panel pulls a 7-day Open-Meteo forecast for the site coordinates. The daily brief applies the same rules:

- 0.25 in or more of rain within about 48 hours: skip irrigation, postpone feedings until it passes.
- Forecast low at or below a growing plant's `frostTenderBelowF` (or 34°F for anything): cover or bring in.
- Forecast high at or above a plant's `heatStressAboveF` (or 100°F): water deeply early, shade, move sowing and transplanting to the evening, hold feedings.
- Gusts of 35 mph or more: check stakes and covers, no spraying.

## Daily brief

A scheduled task ("Garden daily tasks") runs each morning on this computer: it runs `garden.py tasks`, checks the Pasadena forecast, and writes today's list to an all-day Google Calendar event titled "Garden tasks". The Morning brief shows that event's contents in a Garden section.

## Logging what got done

Tick tasks in the panel, press "Copy update for Claude", and paste it into a chat. Ticks are only stored in the browser until Claude records them with `log` / `done`.
