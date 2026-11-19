#!/usr/bin/env python3
"""Garden manager CLI. Source of truth: data/garden.json. See GARDEN.md.

  garden.py build                              recompute schedule -> data/garden-data.js
  garden.py add FILE.json [FILE.json ...]      add planting(s) from JSON, then build
  garden.py list                               one line per planting
  garden.py log ID MILESTONE [YYYY-MM-DD]      record seedPrep|sown|germinated|transplanted|firstHarvest|finished
  garden.py done EVENT_ID [EVENT_ID ...]       tick off a task (e.g. a feeding)
  garden.py set ID PATH VALUE                  e.g. set tomato-sungold plan.sowDate 2027-01-20
  garden.py remove ID                          drop a planting
  garden.py tasks [--date D] [--days N] [--json]   tasks for a day (+ lookahead), for the daily brief
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "garden.json"
OUT = ROOT / "data" / "garden-data.js"
MILESTONES = ["seedPrep", "sown", "germinated", "transplanted", "firstHarvest", "finished"]
METHODS = ["indoor-start", "direct-sow", "nursery-transplant", "established"]
ACTIONABLE = {"seed-prep", "sow", "harden-off", "transplant", "fertilize"}
OVERDUE_DAYS = 21


def load():
    return json.loads(DATA.read_text())


def save(g):
    DATA.write_text(json.dumps(g, indent=2, ensure_ascii=False) + "\n")


def d(s):
    return dt.date.fromisoformat(s) if s else None


def lo(x, default=None):
    if x is None:
        return default
    return x[0] if isinstance(x, list) else x


def hi(x, default=None):
    if x is None:
        return default
    return x[-1] if isinstance(x, list) else x


def mid(x, default=None):
    if x is None:
        return default
    return (lo(x) + hi(x)) / 2


def md(s, year):
    m, day = (int(v) for v in s.split("-"))
    try:
        return dt.date(year, m, day)
    except ValueError:  # 02-29
        return dt.date(year, m, 28)


def in_window(date, w):
    s, e = md(w["start"], date.year), md(w["end"], date.year)
    return (s <= date <= e) if s <= e else (date >= s or date <= e)


def next_window(w, today):
    """(start, end) of the current or next occurrence of a MM-DD window."""
    for y in (today.year - 1, today.year, today.year + 1):
        s, e = md(w["start"], y), md(w["end"], y)
        if e < s:
            e = md(w["end"], y + 1)
        if e >= today:
            return s, e
    return None


def schedule(p, today):
    prof = p.get("profile") or {}
    plan = p.get("plan") or {}
    act = p.get("actual") or {}
    method = p.get("method", "direct-sow")
    done = set(p.get("done") or [])
    label = p["name"] + (f" '{p['variety']}'" if p.get("variety") else "")
    events, warnings = [], []

    def add(kind, date, title, detail="", end=None, milestone=None):
        eid = f"{p['id']}:{kind}:{date.isoformat()}"
        events.append({
            "id": eid, "plantingId": p["id"], "plant": label, "type": kind,
            "date": date.isoformat(), "end": end.isoformat() if end else None,
            "title": title, "detail": detail, "milestone": milestone,
            "done": bool(eid in done or (milestone and any(act.get(m) for m in MILESTONES[MILESTONES.index(milestone):]))),
            "location": p.get("location") or "",
        })

    windows = prof.get("windows") or []
    sow = tp = None
    if method in ("indoor-start", "direct-sow"):
        sow = d(act.get("sown")) or d(plan.get("sowDate"))
    germ = prof.get("germination") or {}
    gmin, gmax = lo(germ.get("days"), 7), hi(germ.get("days"), 14)
    germ_date = d(act.get("germinated"))

    if sow:
        prep = prof.get("seedPrep")
        if prep and prep.get("steps"):
            pd = d(act.get("seedPrep")) or sow - dt.timedelta(days=prep.get("leadDays", 0))
            add("seed-prep", pd, f"Prep seeds: {label}", " ".join(prep["steps"]), milestone="seedPrep")
        where = "indoors" if method == "indoor-start" else "outdoors"
        add("sow", sow, f"Sow {label} {where}", prof.get("sowing", ""), milestone="sown")
        if germ_date:
            add("germination", germ_date, f"{label} germinated", milestone="germinated")
        else:
            add("germination", sow + dt.timedelta(days=gmin), f"Watch for germination: {label}",
                germ.get("notes", ""), end=sow + dt.timedelta(days=gmax), milestone="germinated")
        wtype = "sow-indoors" if method == "indoor-start" else "direct-sow"
        ws = [w for w in windows if w["type"] == wtype]
        if ws and not act.get("sown") and not any(in_window(sow, w) for w in ws):
            warnings.append(f"Sow date {sow.isoformat()} is outside the {wtype} window")

    if method == "indoor-start":
        tp = d(act.get("transplanted")) or d(plan.get("transplantDate"))
        if not tp and sow:
            tp = sow + dt.timedelta(days=round(mid(prof.get("indoorWeeks"), 6) * 7))
    elif method == "nursery-transplant":
        tp = d(act.get("transplanted")) or d(plan.get("transplantDate"))
    if tp:
        h = prof.get("hardenOffDays") or 0
        if method == "indoor-start" and h:
            hs_ = tp - dt.timedelta(days=h)
            eid = f"{p['id']}:harden-off:{hs_.isoformat()}"
            add("harden-off", hs_, f"Start hardening off {label}",
                f"{h} days of increasing outdoor exposure before transplanting.", end=tp - dt.timedelta(days=1))
            if act.get("transplanted"):
                events[-1]["done"] = True
        add("transplant", tp, f"Transplant {label}" + (f" to {p['location']}" if p.get("location") else ""),
            prof.get("spacing", ""), milestone="transplanted")
        ws = [w for w in windows if w["type"] == "transplant"]
        if ws and not act.get("transplanted") and not any(in_window(tp, w) for w in ws):
            warnings.append(f"Transplant date {tp.isoformat()} is outside the transplant window")

    # harvest
    hs = he = None
    dtm = prof.get("daysToMaturity")
    base = None
    if dtm:
        base = tp if (dtm.get("from", "sow") == "transplant" and tp) else (sow or tp)
    if base:
        hs = d(act.get("firstHarvest")) or base + dt.timedelta(days=lo(dtm["days"]))
        he = d(act.get("finished")) or hs + dt.timedelta(days=round((prof.get("harvestWeeks") or 4) * 7))
        add("harvest", hs, f"Harvest begins: {label}" + ("" if act.get("firstHarvest") else " (est.)"),
            prof.get("harvestNotes", ""), end=he, milestone="firstHarvest")
    elif prof.get("harvestSeason"):
        nw = next_window(prof["harvestSeason"], today)
        if nw:
            hs, he = nw
            add("harvest", hs, f"Harvest season: {label}", prof.get("harvestNotes", ""), end=he)

    # fertiliser
    anchors = {"sow": sow, "transplant": tp, "harvest-start": hs,
               "germination": germ_date or (sow + dt.timedelta(days=gmax) if sow else None)}
    horizon = today + dt.timedelta(days=400)
    for f in prof.get("fertilizer") or []:
        detail = " — ".join(x for x in [f.get("product"), f.get("amount"), f.get("notes")] if x)
        title = f"Feed {label}: {f.get('label', 'fertilize')}"
        if f.get("start") == "calendar":
            for y in (today.year, today.year + 1):
                for s in f.get("dates") or []:
                    day = md(s, y)
                    if today - dt.timedelta(days=45) <= day <= horizon:
                        add("fertilize", day, title, detail)
            continue
        a = anchors.get(f.get("start", "transplant")) or tp or sow
        if not a:
            continue
        day = a + dt.timedelta(days=f.get("offsetDays", 0))
        every = f.get("everyDays")
        until = {"harvest-start": hs, "harvest-end": he}.get(f.get("until", "harvest-end"), he) or horizon
        count = f.get("count") or (60 if every else 1)
        n = 0
        while n < count and day <= horizon:
            if n and day > until:
                break
            add("fertilize", day, title, detail)
            n += 1
            if not every:
                break
            day += dt.timedelta(days=every)

    # unscheduled: surface the next window
    if method != "established" and not sow and not tp:
        want = {"indoor-start": "sow-indoors", "direct-sow": "direct-sow", "nursery-transplant": "transplant"}[method]
        for w in windows:
            if w["type"] != want:
                continue
            nw = next_window(w, today)
            if nw:
                s, e = nw
                when = max(s, today)
                add("window-open", when, f"Planting window for {label}: pick a date",
                    f"{want} window runs {s.strftime('%b %-d')} – {e.strftime('%b %-d')}.", end=e)

    # stage
    if act.get("finished"):
        stage = "finished"
    elif method == "established":
        stage = "established"
    elif act.get("firstHarvest") or (hs and base and today >= hs and (act.get("transplanted") or act.get("sown"))):
        stage = "harvesting"
    elif act.get("transplanted") or (method == "direct-sow" and act.get("germinated")):
        stage = "growing"
    elif act.get("germinated"):
        stage = "seedling"
    elif act.get("sown"):
        stage = "germinating"
    elif sow or tp:
        stage = "planned"
    else:
        stage = "unscheduled"

    iso = lambda x: x.isoformat() if x else None
    computed = {
        "stage": stage, "warnings": warnings,
        "seedPrep": next((e["date"] for e in events if e["type"] == "seed-prep"), None),
        "sow": iso(sow),
        "germination": [iso(germ_date), iso(germ_date)] if germ_date else
                       ([iso(sow + dt.timedelta(days=gmin)), iso(sow + dt.timedelta(days=gmax))] if sow else None),
        "transplant": iso(tp), "harvestStart": iso(hs), "harvestEnd": iso(he),
        "nextFeed": next((e["date"] for e in sorted(events, key=lambda e: e["date"])
                          if e["type"] == "fertilize" and not e["done"] and e["date"] >= today.isoformat()), None),
    }
    return computed, events


def build(g, today=None):
    today = today or dt.date.today()
    events, plantings, seen = [], [], set()
    for p in g.get("plantings", []):
        if p["id"] in seen:
            sys.exit(f"duplicate planting id: {p['id']}")
        seen.add(p["id"])
        if p.get("method", "direct-sow") not in METHODS:
            sys.exit(f"{p['id']}: method must be one of {METHODS}")
        computed, ev = schedule(p, today)
        plantings.append({**p, "computed": computed})
        events += ev
    events.sort(key=lambda e: (e["date"], e["plant"]))
    return {"generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
            "site": g.get("site", {}), "plantings": plantings, "events": events}


def write_js(g):
    out = build(g)
    OUT.write_text("// Generated by scripts/garden.py build — do not edit. Edit data/garden.json instead.\n"
                   "window.GARDEN = " + json.dumps(out, indent=1, ensure_ascii=False) + ";\n")
    return out


def find(g, pid):
    for p in g["plantings"]:
        if p["id"] == pid:
            return p
    sys.exit(f"no planting with id {pid!r}. Known: {', '.join(p['id'] for p in g['plantings']) or '(none)'}")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def cmd_add(a):
    g = load()
    ids = {p["id"] for p in g["plantings"]}
    added = []
    for f in a.files:
        items = json.loads(Path(f).read_text())
        for p in items if isinstance(items, list) else [items]:
            if not p.get("name"):
                sys.exit(f"{f}: planting needs a name")
            base = p.get("id") or slug(" ".join(x for x in [p["name"], p.get("variety")] if x))
            pid, n = base, 2
            while pid in ids:
                pid, n = f"{base}-{n}", n + 1
            p["id"] = pid
            ids.add(pid)
            p.setdefault("method", "direct-sow")
            p.setdefault("plan", {})
            p.setdefault("actual", {})
            p.setdefault("done", [])
            p.setdefault("addedOn", dt.date.today().isoformat())
            g["plantings"].append(p)
            added.append(pid)
    build(g)  # validate before saving
    save(g)
    write_js(g)
    print("added:", ", ".join(added))


def cmd_log(a):
    g = load()
    p = find(g, a.id)
    if a.milestone not in MILESTONES:
        sys.exit(f"milestone must be one of {MILESTONES}")
    p.setdefault("actual", {})[a.milestone] = a.date or dt.date.today().isoformat()
    save(g)
    write_js(g)
    print(f"{a.id}: {a.milestone} = {p['actual'][a.milestone]}")


def cmd_done(a):
    g = load()
    for eid in a.event_ids:
        p = find(g, eid.split(":")[0])
        if eid not in p.setdefault("done", []):
            p["done"].append(eid)
    save(g)
    write_js(g)
    print("marked done:", ", ".join(a.event_ids))


def cmd_set(a):
    g = load()
    node = find(g, a.id)
    keys = a.path.split(".")
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    try:
        val = json.loads(a.value)
    except ValueError:
        val = a.value
    node[keys[-1]] = val
    build(g)
    save(g)
    write_js(g)
    print(f"{a.id}: {a.path} = {val!r}")


def cmd_remove(a):
    g = load()
    find(g, a.id)
    g["plantings"] = [p for p in g["plantings"] if p["id"] != a.id]
    save(g)
    write_js(g)
    print("removed", a.id)


def cmd_list(a):
    out = build(load())
    for p in out["plantings"]:
        c = p["computed"]
        print(f"{p['id']:32} {c['stage']:12} sow={c['sow'] or '-':10} transplant={c['transplant'] or '-':10} "
              f"harvest={c['harvestStart'] or '-':10} nextFeed={c['nextFeed'] or '-'}")
    if not out["plantings"]:
        print("(no plantings yet)")


def tasks_for(out, day, days):
    t = day.isoformat()
    end = (day + dt.timedelta(days=days)).isoformat()
    floor = (day - dt.timedelta(days=OVERDUE_DAYS)).isoformat()
    today, overdue, upcoming = [], [], []
    for e in out["events"]:
        if e["done"]:
            continue
        if e["date"] == t:
            today.append(e)
        elif e["type"] == "germination" and e["end"] and e["date"] < t:
            if e["end"] < t and e["end"] >= floor:
                overdue.append({**e, "title": f"No germination logged for {e['plant']} — check, resow if needed"})
            elif e["end"] >= t:
                today.append({**e, "title": f"Check for sprouts: {e['plant']}"})
        elif e["type"] in ACTIONABLE and floor <= e["date"] < t:
            overdue.append(e)
        elif t < e["date"] <= end:
            upcoming.append(e)
    sens = []
    for p in out["plantings"]:
        if p["computed"]["stage"] in ("finished", "planned", "unscheduled"):
            continue
        prof = p.get("profile") or {}
        sens.append({"plant": p["name"], "stage": p["computed"]["stage"], "location": p.get("location", ""),
                     "frostTenderBelowF": prof.get("frostTenderBelowF"),
                     "heatStressAboveF": prof.get("heatStressAboveF")})
    return {"date": t, "site": out["site"], "today": today, "overdue": overdue,
            "upcoming": upcoming, "weatherSensitive": sens}


def cmd_tasks(a):
    day = d(a.date) or dt.date.today()
    g = load()
    res = tasks_for(build(g, day), day, a.days)
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return
    line = lambda e: f"- {e['title']}" + (f" [{e['location']}]" if e["location"] else "") + \
                     (f" — {e['detail']}" if e["detail"] else "") + f"  (id: {e['id']})"
    print(f"# Garden tasks for {res['date']}")
    for head, key in (("Overdue", "overdue"), ("Today", "today")):
        if res[key]:
            print(f"\n## {head}")
            for e in res[key]:
                print(line(e) + (f"  (was due {e['date']})" if key == "overdue" else ""))
    if not res["today"] and not res["overdue"]:
        print("\nNothing scheduled today.")
    if res["upcoming"]:
        print(f"\n## Next {a.days} days")
        for e in res["upcoming"]:
            print(f"- {e['date']}: {e['title']}")
    if res["weatherSensitive"]:
        print("\n## Weather thresholds (plants currently growing)")
        for s in res["weatherSensitive"]:
            print(f"- {s['plant']} ({s['stage']}): protect below {s['frostTenderBelowF'] or '?'}°F, "
                  f"heat stress above {s['heatStressAboveF'] or '?'}°F")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=lambda a: (write_js(load()), print("built", OUT)))
    s = sub.add_parser("add"); s.add_argument("files", nargs="+"); s.set_defaults(fn=cmd_add)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("log"); s.add_argument("id"); s.add_argument("milestone"); s.add_argument("date", nargs="?"); s.set_defaults(fn=cmd_log)
    s = sub.add_parser("done"); s.add_argument("event_ids", nargs="+"); s.set_defaults(fn=cmd_done)
    s = sub.add_parser("set"); s.add_argument("id"); s.add_argument("path"); s.add_argument("value"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("remove"); s.add_argument("id"); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("tasks"); s.add_argument("--date"); s.add_argument("--days", type=int, default=7); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_tasks)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
