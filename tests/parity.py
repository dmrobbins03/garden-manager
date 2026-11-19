#!/usr/bin/env python3
"""Check that hosted/scheduler.js gives the same schedule and the same data changes as scripts/garden.py.

    python3 tests/parity.py

Builds a few gardens with the Python CLI in a temp folder, replays the same changes through the JavaScript
functions, then compares the stored data, the full schedule and the task lists on several dates.
"""
import datetime as dt
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATES = ["2026-11-18", "2026-11-19", "2026-12-28", "2027-02-28", "2027-03-16", "2027-06-01", "2027-10-02", "2028-02-29"]

HERB = {"watering": [{"start": "10-01", "end": "03-15", "everyDays": [2, 3], "amount": "keep moist"}], "germination": {"days": [7, 14]}, "windows": [{"type": "direct-sow", "start": "10-01", "end": "03-15"}],
        "daysToMaturity": {"days": [45, 55], "from": "sow"}, "harvestWeeks": 8, "harvestNotes": "Ready at 6 in.",
        "harvest": {"steps": ["Cut outer stems at the base."], "takeAtMost": "a third", "regrowDays": [7, 14]},
        "succession": {"everyDays": 21}, "frostTenderBelowF": 28, "heatStressAboveF": 85,
        "fertilizer": [{"label": "Light feed", "product": "fish emulsion", "start": "germination", "offsetDays": 14}]}

PLANTS = [
    {"name": "Tomato", "variety": "Sungold", "method": "indoor-start", "location": "Bed 1", "plan": {"sowDate": "2027-01-20"},
     "profile": {"seedPrep": {"leadDays": 1, "steps": ["Soak.", "Drain."]}, "germination": {"days": [5, 10], "notes": "Keep moist."},
                 "indoorWeeks": [5, 6], "hardenOffDays": 7, "sowing": "1/4 in deep", "spacing": "24 in",
                 "windows": [{"type": "sow-indoors", "start": "01-15", "end": "03-15"}, {"type": "transplant", "start": "03-01", "end": "05-31"}],
                 "daysToMaturity": {"days": [60, 70], "from": "transplant"}, "harvestWeeks": 1.5,
                 "fertilizer": [{"label": "At transplant", "product": "4-4-4", "amount": "2 tbsp", "start": "transplant", "offsetDays": 0},
                                {"label": "Side", "product": "3-4-6", "start": "transplant", "offsetDays": 21, "everyDays": 21, "until": "harvest-end", "notes": "n"},
                                {"label": "Bloom", "product": "x", "start": "harvest-start", "offsetDays": -7, "everyDays": 10, "count": 3},
                                {"product": "y", "start": "calendar", "dates": ["03-01", "02-29"]}]}},
    {"name": "Carrot", "method": "direct-sow", "plan": {"sowDate": "2026-07-04"}, "actual": {"sown": "2026-11-01", "germinated": "2026-11-12"},
     "profile": {"germination": {"days": [10, 21]}, "windows": [{"type": "direct-sow", "start": "09-15", "end": "03-31"}],
                 "daysToMaturity": {"days": 70, "from": "sow"}, "harvestWeeks": 4, "harvest": {"steps": ["Pull."], "maxCuts": 2},
                 "fertilizer": [{"label": "f", "product": "p", "start": "germination", "offsetDays": 14}]}},
    {"name": "Pepper", "method": "nursery-transplant", "location": "Bed 3", "plan": {"transplantDate": "2027-04-10"},
     "profile": {"daysToMaturity": {"days": [70, 80], "from": "transplant"}, "windows": [{"type": "transplant", "start": "04-15", "end": "06-15"}]}},
    {"name": "Longan", "method": "established", "profile": {"watering": [
        {"start": "03-01", "end": "05-31", "everyDays": 7, "amount": "deep soak"}, {"start": "06-01", "end": "09-30", "everyDays": [3, 5], "notes": "more in heat"},
        {"start": "10-01", "end": "11-30", "everyDays": 7}, {"start": "12-01", "end": "02-28", "everyDays": [14, 21], "notes": "only if dry"}], "harvestSeason": {"start": "09-15", "end": "11-15"}, "frostTenderBelowF": 32,
                                                              "fertilizer": [{"label": "c", "product": "EDDHA", "start": "calendar", "dates": ["03-15", "06-15"]}]}},
    {"name": "Lime", "method": "established", "profile": {"watering": [{"start": "03-01", "end": "11-30", "everyDays": [7, 14]}], "harvestSeason": {"start": "08-01", "end": "03-31"}, "harvestNotes": "Green."}},
    {"name": "Dill", "method": "direct-sow", "profile": {"windows": [{"type": "direct-sow", "start": "10-01", "end": "04-15"}], "succession": {"everyDays": 28}}},
    {"name": "Cilantro", "location": "Bed 2", "method": "direct-sow", "plan": {"sowDate": "2026-10-06"}, "profile": HERB},
    {"name": "Green onion", "method": "indoor-start", "plan": {"sowDate": "2026-09-01"}, "succession": {"everyDays": 14, "count": 9},
     "profile": {"germination": {"days": [7, 14]}, "indoorWeeks": 6, "hardenOffDays": 5, "daysToMaturity": {"days": 60, "from": "sow"},
                 "harvestWeeks": 1, "harvest": {"steps": ["Cut above the roots."], "regrowDays": 7, "maxCuts": 3, "replaceWhen": "Thin stalks."}}},
    {"name": "Parsley", "variety": "Giant Italian", "method": "direct-sow", "plan": {"sowDate": "2026-09-01"},
     "profile": {"daysToMaturity": {"days": 75, "from": "sow"}, "harvestWeeks": 30, "harvest": {"steps": ["Outer stems."], "regrowDays": [14, 21]}}},
    {"name": "Parsley", "variety": "Giant Italian", "method": "direct-sow", "profile": {}},
]

# (python CLI args, equivalent JS op)
STEPS = [
    (["log", "cilantro", "sown", "2026-10-06"], {"op": "log", "id": "cilantro", "milestone": "sown", "date": "2026-10-06"}),
    (["log", "cilantro", "germinated", "2026-10-15"], {"op": "log", "id": "cilantro", "milestone": "germinated", "date": "2026-10-15"}),
    (["log", "cilantro", "sown", "2026-10-27"], {"op": "log", "id": "cilantro", "milestone": "sown", "date": "2026-10-27"}),
    (["log", "cilantro", "germinated", "2026-11-05", "--round", "2"], {"op": "log", "id": "cilantro", "milestone": "germinated", "date": "2026-11-05", "round": 2}),
    (["log", "cilantro", "seedPrep", "2026-11-16"], {"op": "log", "id": "cilantro", "milestone": "seedPrep", "date": "2026-11-16"}),
    (["log", "cilantro", "sown", "2026-11-17"], {"op": "log", "id": "cilantro", "milestone": "sown", "date": "2026-11-17"}),
    (["harvest", "cilantro", "2026-11-17", "--amount", "1 bunch", "--note", "tacos"], {"op": "harvest", "id": "cilantro", "date": "2026-11-17", "amount": "1 bunch", "note": "tacos"}),
    (["set", "cilantro", "succession.nextSowDate", "2026-12-20"], {"op": "set", "id": "cilantro", "path": "succession.nextSowDate", "value": "2026-12-20"}),
    (["log", "green-onion", "sown", "2026-09-01"], {"op": "log", "id": "green-onion", "milestone": "sown", "date": "2026-09-01"}),
    (["log", "green-onion", "sown", "2026-09-15"], {"op": "log", "id": "green-onion", "milestone": "sown", "date": "2026-09-15"}),
    (["log", "green-onion", "transplanted", "2026-10-14"], {"op": "log", "id": "green-onion", "milestone": "transplanted", "date": "2026-10-14"}),
    (["harvest", "green-onion", "2026-10-28"], {"op": "harvest", "id": "green-onion", "date": "2026-10-28"}),
    (["harvest", "green-onion", "2026-11-05", "--amount", "8"], {"op": "harvest", "id": "green-onion", "date": "2026-11-05", "amount": "8"}),
    (["harvest", "green-onion", "2026-11-12"], {"op": "harvest", "id": "green-onion", "date": "2026-11-12"}),
    (["harvest", "green-onion", "2026-11-18"], {"op": "harvest", "id": "green-onion", "date": "2026-11-18"}),
    (["harvest", "green-onion", "2026-11-10", "--round", "2"], {"op": "harvest", "id": "green-onion", "date": "2026-11-10", "round": 2}),
    (["log", "green-onion", "finished", "2026-11-19"], {"op": "log", "id": "green-onion", "milestone": "finished", "date": "2026-11-19"}),
    (["log", "parsley-giant-italian", "sown", "2026-09-01"], {"op": "log", "id": "parsley-giant-italian", "milestone": "sown", "date": "2026-09-01"}),
    (["harvest", "parsley-giant-italian", "2026-11-10", "--amount", "handful"], {"op": "harvest", "id": "parsley-giant-italian", "date": "2026-11-10", "amount": "handful"}),
    (["harvest", "carrot", "2027-01-12"], {"op": "harvest", "id": "carrot", "date": "2027-01-12"}),
    (["harvest", "carrot", "2027-01-20"], {"op": "harvest", "id": "carrot", "date": "2027-01-20"}),
    (["done", "longan:fertilize:2027-03-15", "longan:fertilize:2027-06-15"], {"op": "done", "id": "longan", "eventIds": ["longan:fertilize:2027-03-15", "longan:fertilize:2027-06-15"]}),
    (["set", "pepper", "plan.transplantDate", "2027-04-20"], {"op": "set", "id": "pepper", "path": "plan.transplantDate", "value": "2027-04-20"}),
    (["set", "dill", "plan.sowDate", "2026-12-01"], {"op": "set", "id": "dill", "path": "plan.sowDate", "value": "2026-12-01"}),
    (["set", "parsley-giant-italian-2", "succession.everyDays", "30"], {"op": "set", "id": "parsley-giant-italian-2", "path": "succession.everyDays", "value": 30}),
    (["set", "parsley-giant-italian-2", "succession", "null"], {"op": "set", "id": "parsley-giant-italian-2", "path": "succession", "value": None}),
    (["log", "tomato-sungold", "sown", "2027-01-22"], {"op": "log", "id": "tomato-sungold", "milestone": "sown", "date": "2027-01-22"}),
    (["water", "longan", "2026-11-10"], {"op": "water", "id": "longan", "date": "2026-11-10"}),
    (["water", "longan", "2026-11-17"], {"op": "water", "id": "longan", "date": "2026-11-17"}),
    (["water", "longan", "2026-11-17"], {"op": "water", "id": "longan", "date": "2026-11-17"}),
    (["water", "lime", "2026-11-19"], {"op": "water", "id": "lime", "date": "2026-11-19"}),
    (["water", "cilantro", "2026-11-18"], {"op": "water", "id": "cilantro", "date": "2026-11-18"}),
    (["water", "lime", "2026-12-20"], {"op": "water", "id": "lime", "date": "2026-12-20"}),
    (["remove", "pepper"], {"op": "remove", "id": "pepper"}),
]


def strip(o):
    """Drop keys that legitimately differ and treat missing as null."""
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in ("generatedAt", "today", "addedOn") and v is not None}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


def diff(a, b, path=""):
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'python' if k in a else 'js'} ({(a.get(k) if k in a else b.get(k))!r})")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="garden-parity-"))
    try:
        for d in ("scripts", "data"):
            shutil.copytree(ROOT / d, tmp / d)
        empty = {"site": {"name": "Test", "latitude": 34.1, "longitude": -118.1}, "plantings": []}
        (tmp / "data" / "garden.json").write_text(json.dumps(empty))
        py = [sys.executable, str(tmp / "scripts" / "garden.py")]
        node = ["node", str(ROOT / "hosted" / "cli.js")]

        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp)
            if r.returncode:
                sys.exit(f"FAILED: {' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
            return r.stdout

        today = dt.date.today().isoformat()
        (tmp / "plants.json").write_text(json.dumps(PLANTS))
        run(py + ["add", str(tmp / "plants.json")])
        ops = [{"op": "add", "planting": p} for p in PLANTS]

        failures, checks = [], 0

        def compare(tag):
            nonlocal checks
            g_py = json.loads((tmp / "data" / "garden.json").read_text())
            (tmp / "empty.json").write_text(json.dumps(empty))
            (tmp / "ops.json").write_text(json.dumps(ops))
            g_js = json.loads(run(node + ["apply", str(tmp / "empty.json"), str(tmp / "ops.json"), "--date", today]))
            d0 = diff(strip(g_py), strip(g_js), "data")
            checks += 1
            if d0:
                failures.append((tag, "stored data", d0[:6]))
                return
            (tmp / "g.json").write_text(json.dumps(g_py))
            spec = importlib.util.spec_from_file_location("garden", tmp / "scripts" / "garden.py")
            garden = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(garden)
            for day in DATES:
                b_py = garden.build(g_py, dt.date.fromisoformat(day))
                b_js = json.loads(run(node + ["build", str(tmp / "g.json"), "--date", day]))
                for part in ("events", "plantings"):
                    checks += 1
                    dd = diff(strip(b_py[part]), strip(b_js[part]), part)
                    if dd:
                        failures.append((tag, f"{part} on {day}", dd[:6]))
                t_py = garden.tasks_for(b_py, dt.date.fromisoformat(day), 14)
                t_js = json.loads(run(node + ["tasks", str(tmp / "g.json"), "--date", day, "--days", "14", "--json"]))
                checks += 1
                dd = diff(strip(t_py), strip(t_js), "tasks")
                if dd:
                    failures.append((tag, f"tasks on {day}", dd[:6]))
                txt_py = run(py + ["tasks", "--date", day, "--days", "14"])
                txt_js = run(node + ["tasks", str(tmp / "g.json"), "--date", day, "--days", "14"])
                checks += 1
                if txt_py != txt_js:
                    failures.append((tag, f"task text on {day}", [txt_py[:300], txt_js[:300]]))

        compare("after add")
        for i, (args, op) in enumerate(STEPS):
            run(py + args)
            ops.append(op)
            if i % 4 == 3 or i == len(STEPS) - 1:
                compare(f"after step {i + 1}: {' '.join(args)}")

        if failures:
            for tag, what, lines in failures[:12]:
                print(f"MISMATCH {tag} / {what}")
                for ln in lines:
                    print("   ", ln)
            sys.exit(f"{len(failures)} mismatches in {checks} comparisons")
        print(f"parity ok: {checks} comparisons, {len(PLANTS)} plantings, {len(STEPS)} changes, {len(DATES)} dates")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
