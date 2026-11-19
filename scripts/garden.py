#!/usr/bin/env python3
"""Garden manager CLI. Source of truth: data/garden.json. See GARDEN.md.

  garden.py build                              recompute schedule -> data/garden-data.js
  garden.py add FILE.json [FILE.json ...]      add planting(s) from JSON, then build
  garden.py list                               one line per planting
  garden.py log ID MILESTONE [YYYY-MM-DD] [--round N]
                                               record seedPrep|sown|germinated|transplanted|firstHarvest|finished
                                               (on a repeat-sown planting, `sown` starts the next sowing)
  garden.py harvest ID [YYYY-MM-DD] [--amount TEXT] [--note TEXT] [--round N]
                                               record one picking or cut (the first also sets firstHarvest)
  garden.py done EVENT_ID [EVENT_ID ...]       tick off a task (e.g. a feeding)
  garden.py set ID PATH VALUE                  e.g. set tomato-sungold plan.sowDate 2027-01-20
                                               set cilantro succession.everyDays 21   (resow every 3 weeks)
                                               set cilantro succession null           (stop resowing)
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
SOWN_METHODS = ("indoor-start", "direct-sow")
SUCCESSION_HORIZON_DAYS = 365
SUCCESSION_MAX_ROUNDS = 60
STAGE_RANK = ["harvesting", "growing", "seedling", "germinating", "planned", "unscheduled"]


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


def sow_window_type(method):
    return "sow-indoors" if method == "indoor-start" else "direct-sow"


def snap_to_window(date, ws):
    """date if it is inside one of the windows (or there are none), else the next window opening."""
    if not ws or any(in_window(date, w) for w in ws):
        return date
    starts = [nw[0] for nw in (next_window(w, date) for w in ws) if nw]
    return min(starts) if starts else date


def succession_of(p):
    """{'everyDays', 'count'} when the planting is resown on a repeat, else None.

    The planting's own `succession.everyDays` wins; the researched `profile.succession.everyDays` is the fallback.
    """
    s = p.get("succession")
    if not isinstance(s, dict):
        return None
    every = s.get("everyDays") or ((p.get("profile") or {}).get("succession") or {}).get("everyDays")
    if not every:
        return None
    return {"everyDays": int(every), "count": s.get("count"), "nextSowDate": s.get("nextSowDate")}


def logged_rounds(p):
    act = p.get("actual") or {}
    return ([act] if any(act.values()) else []) + list(p.get("sowings") or [])


def plan_rounds(p, today):
    """[(round_no, logged_dates, sow_date)]. One entry with round_no None for an ordinary planting.

    A repeat-sown planting gets every logged sowing, then projected ones `everyDays` apart. Each is counted
    from the sowing before it (so a late sowing pushes the rest back), skips to the next window opening when
    it would land outside the sowing window, and stops at `succession.count` or a year out. A projected
    sowing more than OVERDUE_DAYS late with nothing logged is dropped.
    """
    plan = p.get("plan") or {}
    succ = succession_of(p)
    if not succ and not p.get("sowings"):
        return [(None, p.get("actual") or {}, d(plan.get("sowDate")))]
    ws = [w for w in (p.get("profile") or {}).get("windows") or []
          if w["type"] == sow_window_type(p.get("method", "direct-sow"))]
    every = succ["everyDays"] if succ else None
    first, pin = d(plan.get("sowDate")), d(succ.get("nextSowDate")) if succ else None
    state = {"prev": None, "pin": pin}

    def next_sow():
        prev = state["prev"]
        if prev is None:
            return first
        if state["pin"] and state["pin"] > prev:
            return state["pin"]
        return snap_to_window(prev + dt.timedelta(days=every), ws) if every else None

    rounds = []
    for act in logged_rounds(p):
        sow = d(act.get("sown"))
        if not sow:
            sow = next_sow()
            state["pin"] = None
        rounds.append((len(rounds) + 1, act, sow))
        state["prev"] = sow or state["prev"]
    if succ:
        limit = min(succ.get("count") or SUCCESSION_MAX_ROUNDS, SUCCESSION_MAX_ROUNDS)
        horizon = today + dt.timedelta(days=SUCCESSION_HORIZON_DAYS)
        while len(rounds) < limit:
            sow = next_sow()
            state["pin"] = None
            if sow is None or sow > horizon:
                break
            state["prev"] = sow
            if sow < today - dt.timedelta(days=OVERDUE_DAYS):
                continue  # a sowing that was missed and never logged: drop it, keep the rhythm
            rounds.append((len(rounds) + 1, {}, sow))
    return rounds


def schedule(p, today):
    prof = p.get("profile") or {}
    plan = p.get("plan") or {}
    method = p.get("method", "direct-sow")
    done = set(p.get("done") or [])
    label = p["name"] + (f" '{p['variety']}'" if p.get("variety") else "")
    events, warnings = [], []
    windows = prof.get("windows") or []
    germ = prof.get("germination") or {}
    gmin, gmax = lo(germ.get("days"), 7), hi(germ.get("days"), 14)
    horizon = today + dt.timedelta(days=400)
    iso = lambda x: x.isoformat() if x else None
    succ = succession_of(p)
    hv = prof.get("harvest") or {}
    how = " ".join(x for x in [prof.get("harvestNotes", "")] + list(hv.get("steps") or []) if x)
    cut_detail = " ".join(x for x in list(hv.get("steps") or []) + [f"Take at most {hv['takeAtMost']}." if hv.get("takeAtMost") else ""] if x)
    picked = p.get("harvests") or []

    def one(rno, act, sow_in, calendar_feeds):
        """Schedule one sowing. rno is None for an ordinary planting, 1.. for a repeat-sown one."""
        rid = f"{p['id']}:r{rno}" if rno else p["id"]
        nth = f" (sowing {rno})" if rno else ""

        def add(kind, date, title, detail="", end=None, milestone=None, key=None):
            eid = f"{rid}:{key or kind}:{date.isoformat()}"
            events.append({
                "id": eid, "plantingId": p["id"], "plant": label, "type": kind,
                "date": date.isoformat(), "end": end.isoformat() if end else None,
                "title": title, "detail": detail, "milestone": milestone, "round": rno,
                "done": bool(eid in done or (milestone and any(act.get(m) for m in MILESTONES[MILESTONES.index(milestone):]))),
                "location": p.get("location") or "",
            })

        sow = tp = None
        if method in SOWN_METHODS:
            sow = d(act.get("sown")) or sow_in
        germ_date = d(act.get("germinated"))
        seed_prep = None

        if sow:
            prep = prof.get("seedPrep")
            if prep and prep.get("steps"):
                seed_prep = d(act.get("seedPrep")) or sow - dt.timedelta(days=prep.get("leadDays", 0))
                add("seed-prep", seed_prep, f"Prep seeds: {label}{nth}", " ".join(prep["steps"]), milestone="seedPrep")
            where = "indoors" if method == "indoor-start" else "outdoors"
            add("sow", sow, f"Sow {label} {where}{nth}", prof.get("sowing", ""), milestone="sown")
            if germ_date:
                add("germination", germ_date, f"{label} germinated{nth}", milestone="germinated")
            else:
                add("germination", sow + dt.timedelta(days=gmin), f"Watch for germination: {label}{nth}",
                    germ.get("notes", ""), end=sow + dt.timedelta(days=gmax), milestone="germinated")
            wtype = sow_window_type(method)
            ws = [w for w in windows if w["type"] == wtype]
            if ws and not act.get("sown") and not any(in_window(sow, w) for w in ws):
                warnings.append(f"Sow date {sow.isoformat()} is outside the {wtype} window")

        plan_tp = d(plan.get("transplantDate")) if rno in (None, 1) else None
        if method == "indoor-start":
            tp = d(act.get("transplanted")) or plan_tp
            if not tp and sow:
                tp = sow + dt.timedelta(days=round(mid(prof.get("indoorWeeks"), 6) * 7))
        elif method == "nursery-transplant":
            tp = d(act.get("transplanted")) or plan_tp
        if tp:
            h = prof.get("hardenOffDays") or 0
            if method == "indoor-start" and h:
                add("harden-off", tp - dt.timedelta(days=h), f"Start hardening off {label}{nth}",
                    f"{h} days of increasing outdoor exposure before transplanting.", end=tp - dt.timedelta(days=1))
                if act.get("transplanted"):
                    events[-1]["done"] = True
            add("transplant", tp, f"Transplant {label}{nth}" + (f" to {p['location']}" if p.get("location") else ""),
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
            add("harvest", hs, f"Harvest begins: {label}{nth}" + ("" if act.get("firstHarvest") else " (est.)"),
                how, end=he, milestone="firstHarvest")
        elif prof.get("harvestSeason"):
            nw = next_window(prof["harvestSeason"], today)
            if nw:
                hs, he = nw
                add("harvest", hs, f"Harvest season: {label}", prof.get("harvestNotes", ""), end=he)

        # pickings logged so far, and when this sowing can be cut again or is used up
        cuts = sorted(h["date"] for h in picked if rno is None or h.get("round") == rno)
        last_cut = d(cuts[-1]) if cuts else None
        max_cuts = hv.get("maxCuts")
        spent = bool(max_cuts and len(cuts) >= max_cuts and not act.get("finished"))
        next_cut = None
        if last_cut and not act.get("finished"):
            if spent:
                add("harvest", max(last_cut, today), f"{label}{nth} has given its {max_cuts} cuts: pull it"
                    + (", the next sowing takes over" if succ else " and resow"),
                    hv.get("replaceWhen", ""), milestone="finished", key="spent")
            elif lo(hv.get("regrowDays")):
                next_cut = last_cut + dt.timedelta(days=lo(hv["regrowDays"]))
                when = max(next_cut, today)
                if not he or when <= he:
                    add("harvest", when, f"Cut again: {label}{nth}",
                        (f"Regrown since {next_cut.strftime('%b %-d')}. " if next_cut < today else "") + cut_detail, key="recut")

        # fertiliser
        anchors = {"sow": sow, "transplant": tp, "harvest-start": hs,
                   "germination": germ_date or (sow + dt.timedelta(days=gmax) if sow else None)}
        for f in prof.get("fertilizer") or []:
            detail = " — ".join(x for x in [f.get("product"), f.get("amount"), f.get("notes")] if x)
            title = f"Feed {label}{nth}: {f.get('label', 'fertilize')}"
            if f.get("start") == "calendar":
                if not calendar_feeds:
                    continue
                for y in (today.year, today.year + 1):
                    for s in f.get("dates") or []:
                        day = md(s, y)
                        if today - dt.timedelta(days=45) <= day <= horizon:
                            add("fertilize", day, f"Feed {label}: {f.get('label', 'fertilize')}", detail)
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

        return {
            "round": rno, "stage": stage, "sown": bool(act.get("sown")),
            "seedPrep": iso(seed_prep), "sow": iso(sow),
            "germination": [iso(germ_date), iso(germ_date)] if germ_date else
                           ([iso(sow + dt.timedelta(days=gmin)), iso(sow + dt.timedelta(days=gmax))] if sow else None),
            "transplant": iso(tp), "harvestStart": iso(hs), "harvestEnd": iso(he),
            "cuts": len(cuts), "lastCut": iso(last_cut), "nextCut": iso(next_cut), "spent": spent,
        }

    rounds = [one(rno, act, sow_in, i == 0) for i, (rno, act, sow_in) in enumerate(plan_rounds(p, today))]
    if not rounds:  # repeat-sown, but nothing sown or dated yet
        rounds = [one(None, {}, None, True)]

    # unscheduled: surface the next window
    if method != "established" and not any(r["sow"] or r["transplant"] for r in rounds):
        want = {"indoor-start": "sow-indoors", "direct-sow": "direct-sow", "nursery-transplant": "transplant"}[method]
        for w in windows:
            if w["type"] != want:
                continue
            nw = next_window(w, today)
            if nw:
                s, e = nw
                when = max(s, today)
                eid = f"{p['id']}:window-open:{when.isoformat()}"
                events.append({
                    "id": eid, "plantingId": p["id"], "plant": label, "type": "window-open",
                    "date": when.isoformat(), "end": e.isoformat(),
                    "title": f"Planting window for {label}: pick a date",
                    "detail": f"{want} window runs {s.strftime('%b %-d')} – {e.strftime('%b %-d')}.",
                    "milestone": None, "round": None, "done": eid in done, "location": p.get("location") or "",
                })

    next_feed = next((e["date"] for e in sorted(events, key=lambda e: e["date"])
                      if e["type"] == "fertilize" and not e["done"] and e["date"] >= today.isoformat()), None)

    if rounds[0]["round"] is None:  # ordinary planting: one sowing
        r = rounds[0]
        computed = {k: r[k] for k in ("stage", "seedPrep", "sow", "germination", "transplant", "harvestStart", "harvestEnd")}
        computed.update({"warnings": warnings, "nextFeed": next_feed,
                         "harvests": {"count": r["cuts"], "last": r["lastCut"], "nextCut": r["nextCut"]}})
        if succ:
            computed["succession"] = {"everyDays": succ["everyDays"], "nextSow": None, "sown": 0}
        return computed, events

    # repeat-sown: the row shows the next sowing still to do, and the harvest all live sowings add up to
    live = [r for r in rounds if r["stage"] != "finished"]
    focus = next((r for r in rounds if not r["sown"]), rounds[-1])
    starts = [r["harvestStart"] for r in live if r["harvestStart"]]
    ends = [r["harvestEnd"] for r in live if r["harvestEnd"]]
    every = succ["everyDays"] if succ else None
    hw = prof.get("harvestWeeks")
    if every and hw and every > hw * 7:
        warnings.append(f"Resown every {every} days but each sowing is picked for about {hw} weeks: expect gaps between harvests")
    computed = {
        "stage": min((r["stage"] for r in live), key=STAGE_RANK.index) if live else "finished",
        "warnings": warnings,
        "seedPrep": focus["seedPrep"], "sow": focus["sow"], "germination": focus["germination"],
        "transplant": focus["transplant"],
        "harvestStart": min(starts) if starts else None, "harvestEnd": max(ends) if ends else None,
        "nextFeed": next_feed, "rounds": rounds,
        "harvests": {"count": len(picked), "last": max((h["date"] for h in picked), default=None),
                     "nextCut": min((r["nextCut"] for r in live if r["nextCut"]), default=None)},
        "succession": {"everyDays": every, "nextSow": None if focus["sown"] else focus["sow"],
                       "sown": sum(1 for r in rounds if r["sown"])},
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
        for h in p.get("harvests") or []:
            if not isinstance(h, dict) or not h.get("date"):
                sys.exit(f"{p['id']}: every entry in harvests needs a date")
            d(h["date"])
        if p.get("succession") is not None:
            s = p["succession"]
            if not isinstance(s, dict):
                sys.exit(f"{p['id']}: succession must be an object like {{\"everyDays\": 21}}, or null")
            if p.get("method", "direct-sow") not in SOWN_METHODS:
                sys.exit(f"{p['id']}: succession only applies to indoor-start and direct-sow plantings")
            e = s.get("everyDays")
            if e is not None and (not isinstance(e, (int, float)) or isinstance(e, bool) or e < 1):
                sys.exit(f"{p['id']}: succession.everyDays must be a number of days, 1 or more")
            if not succession_of(p):
                sys.exit(f"{p['id']}: succession needs everyDays (here or in profile.succession)")
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
            rec = ((p.get("profile") or {}).get("succession") or {}).get("everyDays")
            if rec and "succession" not in p and p["method"] in SOWN_METHODS:
                p["succession"] = {"everyDays": rec}
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
    m = a.milestone
    if m not in MILESTONES:
        sys.exit(f"milestone must be one of {MILESTONES}")
    date = a.date or dt.date.today().isoformat()
    d(date)  # must be a real YYYY-MM-DD
    if not (succession_of(p) or p.get("sowings")):
        if a.round:
            sys.exit(f"{a.id} is not repeat-sown; drop --round, or turn it on with: set {a.id} succession.everyDays N")
        p.setdefault("actual", {})[m] = date
        save(g)
        write_js(g)
        print(f"{a.id}: {m} = {date}")
        return
    # repeat-sown: each sowing keeps its own dates in p["sowings"]
    rounds = p.setdefault("sowings", [])
    if any((p.get("actual") or {}).values()):  # dates logged before succession was turned on are sowing 1
        rounds.insert(0, dict(p["actual"]))
        p["actual"] = {}
    later = MILESTONES[MILESTONES.index(m):]
    if a.round:
        if a.round == len(rounds) + 1:
            rounds.append({})
        elif not 1 <= a.round <= len(rounds):
            sys.exit(f"{a.id} has {len(rounds)} logged sowing(s); --round must be 1..{len(rounds) + 1}")
        n = a.round
    elif m in ("seedPrep", "sown"):
        # the open sowing is the last one if it is not in the ground yet; otherwise this starts a new one
        if rounds and not any(rounds[-1].get(x) for x in later):
            n = len(rounds)
        else:
            rounds.append({})
            n = len(rounds)
    else:
        # the oldest sowing that has not reached this point yet
        n = next((i + 1 for i, r in enumerate(rounds) if r.get("sown") and not any(r.get(x) for x in later)), None)
        if n is None:
            sys.exit(f"{a.id}: no sowing is waiting for {m!r}. Log `sown` first, or pass --round N.")
    rounds[n - 1][m] = date
    if m == "sown" and isinstance(p.get("succession"), dict):
        p["succession"].pop("nextSowDate", None)
    build(g)
    save(g)
    write_js(g)
    print(f"{a.id}: sowing {n} {m} = {date}")


def cmd_harvest(a):
    g = load()
    p = find(g, a.id)
    date = a.date or dt.date.today().isoformat()
    d(date)
    entry = {"date": date}
    if succession_of(p) or p.get("sowings"):
        rounds = p.setdefault("sowings", [])
        if any((p.get("actual") or {}).values()):
            rounds.insert(0, dict(p["actual"]))
            p["actual"] = {}
        if a.round:
            if not 1 <= a.round <= len(rounds):
                sys.exit(f"{a.id} has {len(rounds)} logged sowing(s); --round must be 1..{len(rounds)}")
            n = a.round
        else:
            # the oldest sowing still standing that has cuts left in it
            max_cuts = ((p.get("profile") or {}).get("harvest") or {}).get("maxCuts")
            count = lambda i: sum(1 for h in p.get("harvests") or [] if h.get("round") == i + 1)
            live = [i for i, r in enumerate(rounds) if r.get("sown") and not r.get("finished")]
            fresh = [i for i in live if not (max_cuts and count(i) >= max_cuts)]
            if not live:
                sys.exit(f"{a.id}: no sowing in the ground to pick from. Log `sown` first, or pass --round N.")
            n = (fresh or live)[0] + 1
        entry["round"] = n
        act = rounds[n - 1]
    else:
        if a.round:
            sys.exit(f"{a.id} is not repeat-sown; drop --round")
        act = p.setdefault("actual", {})
    if not act.get("firstHarvest") or date < act["firstHarvest"]:
        act["firstHarvest"] = date
    if a.amount:
        entry["amount"] = a.amount
    if a.note:
        entry["notes"] = a.note
    p.setdefault("harvests", []).append(entry)
    p["harvests"].sort(key=lambda h: h["date"])
    build(g)
    save(g)
    out = write_js(g)
    c = next(x for x in out["plantings"] if x["id"] == a.id)["computed"]
    r = next((x for x in c.get("rounds") or [] if x["round"] == entry.get("round")), None)
    n_cuts = r["cuts"] if r else c["harvests"]["count"]
    nxt = (r or c["harvests"]).get("nextCut")
    print(f"{a.id}: harvest logged {date}" + (f" (sowing {entry['round']})" if entry.get("round") else "")
          + f", cut {n_cuts}" + (f", ready again about {nxt}" if nxt else "")
          + (", this sowing is used up" if r and r["spent"] else ""))


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
        if node.get(k) is None:
            node[k] = {}
        node = node[k]
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
              f"harvest={c['harvestStart'] or '-':10} nextFeed={c['nextFeed'] or '-'}"
              + (f"  resow every {c['succession']['everyDays']}d ({c['succession']['sown']} sown, "
                 f"next {c['succession']['nextSow'] or '-'})" if (c.get("succession") or {}).get("everyDays") else "")
              + (f"  picked {c['harvests']['count']}x, last {c['harvests']['last']}" if c["harvests"]["count"] else ""))
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
        who = e["plant"] + (f" (sowing {e['round']})" if e.get("round") else "")
        if e["date"] == t:
            today.append(e)
        elif e["type"] == "germination" and e["end"] and e["date"] < t:
            if e["end"] < t and e["end"] >= floor:
                overdue.append({**e, "title": f"No germination logged for {who} — check, resow if needed"})
            elif e["end"] >= t:
                today.append({**e, "title": f"Check for sprouts: {who}"})
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
    s = sub.add_parser("log"); s.add_argument("id"); s.add_argument("milestone"); s.add_argument("date", nargs="?"); s.add_argument("--round", type=int); s.set_defaults(fn=cmd_log)
    s = sub.add_parser("harvest"); s.add_argument("id"); s.add_argument("date", nargs="?"); s.add_argument("--amount"); s.add_argument("--note"); s.add_argument("--round", type=int); s.set_defaults(fn=cmd_harvest)
    s = sub.add_parser("done"); s.add_argument("event_ids", nargs="+"); s.set_defaults(fn=cmd_done)
    s = sub.add_parser("set"); s.add_argument("id"); s.add_argument("path"); s.add_argument("value"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("remove"); s.add_argument("id"); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("tasks"); s.add_argument("--date"); s.add_argument("--days", type=int, default=7); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_tasks)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
