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
| `index.html` | The panel: today's tasks, forecast and weather advisories, plantings (with harvesting steps, harvest log and repeat sowings), 12-month timeline, feeding schedule, add-plants box. |

Always change data through `scripts/garden.py` (or edit `garden.json`, then run `build`). Run from this folder with `python3`.

```
python3 scripts/garden.py build
python3 scripts/garden.py add research/tomato.json research/basil.json
python3 scripts/garden.py list
python3 scripts/garden.py log <id> <seedPrep|sown|germinated|transplanted|firstHarvest|finished> [YYYY-MM-DD] [--round N]
python3 scripts/garden.py harvest <id> [YYYY-MM-DD] [--amount "1 bunch"] [--note "..."] [--round N]
python3 scripts/garden.py done <event-id> [...]        # tick off a feeding or other task
python3 scripts/garden.py set <id> plan.sowDate 2027-01-20
python3 scripts/garden.py set <id> succession.everyDays 21   # resow every 3 weeks; `set <id> succession null` stops it
python3 scripts/garden.py remove <id>
python3 scripts/garden.py tasks [--date YYYY-MM-DD] [--days 7] [--json]
```

## A planting

See `data/planting-template.json`. Key fields:

- `method`: `indoor-start` (sow indoors, transplant later), `direct-sow`, `nursery-transplant` (bought as a start) or `established` (perennials and trees already in the ground).
- `plan.sowDate` / `plan.transplantDate`: the chosen date. Leave empty to keep the planting "unscheduled"; the panel then shows when its window opens.
- `actual.*`: dates David has logged. They override projections, and everything downstream recalculates. Repeat-sown plantings keep these per sowing in `sowings[]` instead.
- `profile.windows`: `MM-DD` ranges for **Pasadena**, typed `sow-indoors`, `direct-sow` or `transplant`. A window may wrap the new year.
- `profile.germination.days`, `indoorWeeks`, `daysToMaturity.days`: a number or `[min, max]`.
- `profile.daysToMaturity.from`: `transplant` or `sow`. Seed packets differ; record which one the source means.
- `profile.harvestSeason`: `{start, end}` in `MM-DD`, for established plants with no days-to-maturity.
- `profile.fertilizer[]`: each rule has `label`, `product`, `amount`, and a `start` anchor of `sow`, `germination`, `transplant`, `harvest-start` or `calendar`. Anchored rules take `offsetDays`, optional `everyDays`, and `until` (`harvest-start` or `harvest-end`) or `count`. Calendar rules take `dates: ["MM-DD", ...]`.
- `profile.frostTenderBelowF`, `profile.heatStressAboveF`: thresholds the weather advisories use.
- `profile.sources[]`: `{title, url}` for every page the numbers came from.
- `profile.succession`: `{everyDays, notes}`, the researched resowing interval for crops that are used up and sown again (cilantro, dill, green onions). `null` for plants sown once.
- `profile.harvest`: how to pick it. `steps[]` (where and how to cut), `firstCutWhen`, `takeAtMost`, `regrowDays` (number or `[min, max]` until it can be cut again), `maxCuts` (cuts one sowing gives before it is replaced; omit if there is no limit) and `replaceWhen`.

## Repeat sowing

A planting with `succession: {"everyDays": N}` is sown again and again instead of once. `add` turns this on automatically when the research filled `profile.succession.everyDays`; `set <id> succession.everyDays N` changes the interval and `set <id> succession null` stops it.

- The first sowing is `plan.sowDate`. Each later one is `everyDays` after the sowing before it, so a late sowing pushes the rest back. A sowing that would land outside the `direct-sow` / `sow-indoors` window moves to the day the window next opens. Sowings are projected a year ahead, or up to `succession.count` in total.
- Every sowing gets its own seed prep, sow, germination, transplant, harvest and feeding tasks, labelled "(sowing 3)". Event ids carry the sowing: `cilantro:r3:sow:2027-01-02`.
- Logged dates live in `sowings[]`, one object per sowing, in place of `actual`. `log <id> sown` fills the open sowing or starts the next one. Other milestones go to the oldest sowing that has not reached them; pass `--round N` to pick one.
- `set <id> succession.nextSowDate YYYY-MM-DD` moves the next sowing only; it clears once that sowing is logged.
- A projected sowing more than 21 days late with nothing logged is dropped and the rhythm carries on.
- The panel row shows the next sowing still to do and the harvest span of all live sowings. The care sheet lists every sowing, and the timeline marks each one with a tick.

## Harvest tracking

`harvest <id> [date] [--amount ...] [--note ...]` records one picking in the planting's `harvests[]` (`{date, round, amount, notes}`). The first one also sets `firstHarvest`. On a repeat-sown planting it goes to the oldest sowing that still has cuts left unless `--round N` says otherwise.

- With `profile.harvest.regrowDays`, a "Cut again" task appears that many days after the last picking and stays on today's list until the next picking is logged or the harvest period ends.
- With `profile.harvest.maxCuts`, a sowing that has given that many cuts gets a "pull it" task; ticking it logs `finished` for that sowing.
- The care sheet shows the harvesting steps and the harvest log; the Harvest column shows the count, the last picking and when it is ready again.

## Weather rules

The panel pulls a 7-day Open-Meteo forecast for the site coordinates. The daily brief applies the same rules:

- 0.25 in or more of rain within about 48 hours: skip irrigation, postpone feedings until it passes.
- Forecast low at or below a growing plant's `frostTenderBelowF` (or 34°F for anything): cover or bring in.
- Forecast high at or above a plant's `heatStressAboveF` (or 100°F): water deeply early, shade, move sowing and transplanting to the evening, hold feedings.
- Gusts of 35 mph or more: check stakes and covers, no spraying.

## Daily brief

A scheduled task ("Garden daily tasks") runs each morning on this computer: it runs `garden.py tasks`, checks the Pasadena forecast, and writes today's list to an all-day Google Calendar event titled "Garden tasks". The Morning brief shows that event's contents in a Garden section.

## Logging what got done

Tick tasks in the panel, press "Copy update for Claude", and paste it into a chat. Ticks are only stored in the browser until Claude records them with `log` / `harvest` / `done`. A picking that was not on the task list is recorded the same way: say what was picked and how much.
