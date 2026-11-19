/* Garden scheduler: the JavaScript twin of scripts/garden.py.
 *
 * Works in the hosted page (window.GardenScheduler) and in Node (require).
 * Dates are ISO strings (YYYY-MM-DD) throughout, so they compare as text.
 * tests/parity.py checks that this file and garden.py give the same schedule.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.GardenScheduler = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var MILESTONES = ['seedPrep', 'sown', 'germinated', 'transplanted', 'firstHarvest', 'finished'];
  var METHODS = ['indoor-start', 'direct-sow', 'nursery-transplant', 'established'];
  var ACTIONABLE = { 'seed-prep': 1, sow: 1, 'harden-off': 1, transplant: 1, fertilize: 1 };
  var OVERDUE_DAYS = 21;
  var SOWN_METHODS = ['indoor-start', 'direct-sow'];
  var SUCCESSION_HORIZON_DAYS = 365;
  var SUCCESSION_MAX_ROUNDS = 60;
  var WATER_HORIZON_DAYS = 400;
  var WATER_HISTORY_DAYS = 60;
  var STAGE_RANK = ['harvesting', 'growing', 'seedling', 'germinating', 'planned', 'unscheduled'];
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // ---- dates
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function ymd(y, m, d) { return y + '-' + pad(m) + '-' + pad(d); }
  function valid(y, m, d) {
    var t = new Date(Date.UTC(y, m - 1, d));
    return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
  }
  function isDate(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    return valid(+s.slice(0, 4), +s.slice(5, 7), +s.slice(8, 10));
  }
  function mustDate(s) { if (!isDate(s)) throw new Error('Not a date (YYYY-MM-DD): ' + s); return s; }
  function toDays(s) { return Math.round(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 86400000); }
  function fromDays(n) { var t = new Date(n * 86400000); return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()); }
  function addDays(s, n) { return fromDays(toDays(s) + Math.floor(n)); }
  function yearOf(s) { return +s.slice(0, 4); }
  function fmtBD(s) { return MON[+s.slice(5, 7) - 1] + ' ' + (+s.slice(8, 10)); }
  function md(s, year) {
    var p = s.split('-'), m = +p[0], day = +p[1];
    return valid(year, m, day) ? ymd(year, m, day) : ymd(year, m, 28);
  }
  function todayLocal() { var n = new Date(); return ymd(n.getFullYear(), n.getMonth() + 1, n.getDate()); }

  // ---- numbers
  function lo(x, dflt) { if (x == null) return dflt; return Array.isArray(x) ? x[0] : x; }
  function hi(x, dflt) { if (x == null) return dflt; return Array.isArray(x) ? x[x.length - 1] : x; }
  function mid(x, dflt) { if (x == null) return dflt; return (lo(x) + hi(x)) / 2; }
  function pyRound(x) { // Python rounds halves to the even number
    var f = Math.floor(x), diff = x - f;
    if (diff < 0.5) return f;
    if (diff > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }
  function anyValue(o) { if (!o) return false; for (var k in o) if (o[k]) return true; return false; }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }

  // ---- windows
  function inWindow(date, w) {
    var s = md(w.start, yearOf(date)), e = md(w.end, yearOf(date));
    return s <= e ? (s <= date && date <= e) : (date >= s || date <= e);
  }
  function nextWindow(w, today) {
    var ty = yearOf(today);
    for (var y = ty - 1; y <= ty + 1; y++) {
      var s = md(w.start, y), e = md(w.end, y);
      if (e < s) e = md(w.end, y + 1);
      if (e >= today) return [s, e];
    }
    return null;
  }
  function sowWindowType(method) { return method === 'indoor-start' ? 'sow-indoors' : 'direct-sow'; }
  function snapToWindow(date, ws) {
    if (!ws.length || ws.some(function (w) { return inWindow(date, w); })) return date;
    var starts = ws.map(function (w) { return nextWindow(w, date); }).filter(Boolean).map(function (nw) { return nw[0]; });
    return starts.length ? starts.slice().sort()[0] : date;
  }

  // ---- repeat sowing
  function successionOf(p) {
    var s = p.succession;
    if (!s || typeof s !== 'object' || Array.isArray(s)) return null;
    var every = s.everyDays || (((p.profile || {}).succession || {}).everyDays);
    if (!every) return null;
    return { everyDays: Math.trunc(every), count: s.count == null ? null : s.count, nextSowDate: s.nextSowDate || null };
  }
  function loggedRounds(p) {
    var act = p.actual || {};
    return (anyValue(act) ? [act] : []).concat(p.sowings || []);
  }
  function planRounds(p, today) {
    var plan = p.plan || {}, succ = successionOf(p);
    if (!succ && !(p.sowings && p.sowings.length)) return [[null, p.actual || {}, plan.sowDate || null]];
    var wtype = sowWindowType(p.method || 'direct-sow');
    var ws = ((p.profile || {}).windows || []).filter(function (w) { return w.type === wtype; });
    var every = succ ? succ.everyDays : null;
    var first = plan.sowDate || null;
    var state = { prev: null, pin: succ ? succ.nextSowDate : null };
    function nextSow() {
      var prev = state.prev;
      if (prev === null) return first;
      if (state.pin && state.pin > prev) return state.pin;
      return every ? snapToWindow(addDays(prev, every), ws) : null;
    }
    var rounds = [];
    loggedRounds(p).forEach(function (act) {
      var sow = act.sown || null;
      if (!sow) { sow = nextSow(); state.pin = null; }
      rounds.push([rounds.length + 1, act, sow]);
      state.prev = sow || state.prev;
    });
    if (succ) {
      var limit = Math.min(succ.count || SUCCESSION_MAX_ROUNDS, SUCCESSION_MAX_ROUNDS);
      var horizon = addDays(today, SUCCESSION_HORIZON_DAYS), floor = addDays(today, -OVERDUE_DAYS);
      while (rounds.length < limit) {
        var sow = nextSow();
        state.pin = null;
        if (sow === null || sow > horizon) break;
        state.prev = sow;
        if (sow < floor) continue;
        rounds.push([rounds.length + 1, {}, sow]);
      }
    }
    return rounds;
  }

  // ---- one planting
  function schedule(p, today) {
    var prof = p.profile || {}, plan = p.plan || {};
    var method = p.method || 'direct-sow';
    var done = {}; (p.done || []).forEach(function (x) { done[x] = 1; });
    var label = p.name + (p.variety ? " '" + p.variety + "'" : '');
    var events = [], warnings = [];
    var windows = prof.windows || [];
    var germ = prof.germination || {};
    var gmin = lo(germ.days, 7), gmax = hi(germ.days, 14);
    var horizon = addDays(today, 400);
    var succ = successionOf(p);
    var hv = prof.harvest || {};
    var steps = hv.steps || [];
    var how = [prof.harvestNotes || ''].concat(steps).filter(Boolean).join(' ');
    var cutDetail = steps.concat([hv.takeAtMost ? 'Take at most ' + hv.takeAtMost + '.' : '']).filter(Boolean).join(' ');
    var picked = p.harvests || [];
    var location = p.location || '';

    function one(rno, act, sowIn, calendarFeeds) {
      var rid = rno ? p.id + ':r' + rno : p.id;
      var nth = rno ? ' (sowing ' + rno + ')' : '';
      function add(kind, date, title, detail, end, milestone, key) {
        var eid = rid + ':' + (key || kind) + ':' + date;
        var isDone = !!done[eid];
        if (!isDone && milestone) {
          for (var i = MILESTONES.indexOf(milestone); i < MILESTONES.length; i++) if (act[MILESTONES[i]]) { isDone = true; break; }
        }
        events.push({
          id: eid, plantingId: p.id, plant: label, type: kind, date: date, end: end || null,
          title: title, detail: detail || '', milestone: milestone || null, round: rno,
          done: isDone, location: location
        });
      }

      var sow = null, tp = null;
      if (SOWN_METHODS.indexOf(method) >= 0) sow = act.sown || sowIn || null;
      var germDate = act.germinated || null;
      var seedPrep = null;

      if (sow) {
        var prep = prof.seedPrep;
        if (prep && prep.steps && prep.steps.length) {
          seedPrep = act.seedPrep || addDays(sow, -(prep.leadDays || 0));
          add('seed-prep', seedPrep, 'Prep seeds: ' + label + nth, prep.steps.join(' '), null, 'seedPrep');
        }
        add('sow', sow, 'Sow ' + label + ' ' + (method === 'indoor-start' ? 'indoors' : 'outdoors') + nth, prof.sowing || '', null, 'sown');
        if (germDate) add('germination', germDate, label + ' germinated' + nth, '', null, 'germinated');
        else add('germination', addDays(sow, gmin), 'Watch for germination: ' + label + nth, germ.notes || '', addDays(sow, gmax), 'germinated');
        var wtype = sowWindowType(method);
        var sws = windows.filter(function (w) { return w.type === wtype; });
        if (sws.length && !act.sown && !sws.some(function (w) { return inWindow(sow, w); })) {
          warnings.push('Sow date ' + sow + ' is outside the ' + wtype + ' window');
        }
      }

      var planTp = (rno === null || rno === 1) ? (plan.transplantDate || null) : null;
      if (method === 'indoor-start') {
        tp = act.transplanted || planTp;
        if (!tp && sow) tp = addDays(sow, pyRound(mid(prof.indoorWeeks, 6) * 7));
      } else if (method === 'nursery-transplant') {
        tp = act.transplanted || planTp;
      }
      if (tp) {
        var h = prof.hardenOffDays || 0;
        if (method === 'indoor-start' && h) {
          add('harden-off', addDays(tp, -h), 'Start hardening off ' + label + nth,
            h + ' days of increasing outdoor exposure before transplanting.', addDays(tp, -1));
          if (act.transplanted) events[events.length - 1].done = true;
        }
        add('transplant', tp, 'Transplant ' + label + nth + (p.location ? ' to ' + p.location : ''), prof.spacing || '', null, 'transplanted');
        var tws = windows.filter(function (w) { return w.type === 'transplant'; });
        if (tws.length && !act.transplanted && !tws.some(function (w) { return inWindow(tp, w); })) {
          warnings.push('Transplant date ' + tp + ' is outside the transplant window');
        }
      }

      // harvest
      var hs = null, he = null, base = null;
      var dtm = prof.daysToMaturity;
      if (dtm) base = ((dtm.from || 'sow') === 'transplant' && tp) ? tp : (sow || tp);
      if (base) {
        hs = act.firstHarvest || addDays(base, lo(dtm.days));
        he = act.finished || addDays(hs, pyRound((prof.harvestWeeks || 4) * 7));
        add('harvest', hs, 'Harvest begins: ' + label + nth + (act.firstHarvest ? '' : ' (est.)'), how, he, 'firstHarvest');
      } else if (prof.harvestSeason) {
        var nw = nextWindow(prof.harvestSeason, today);
        if (nw) { hs = nw[0]; he = nw[1]; add('harvest', hs, 'Harvest season: ' + label, prof.harvestNotes || '', he); }
      }

      // pickings logged so far, and when this sowing can be cut again or is used up
      var cuts = picked.filter(function (x) { return rno === null || x.round === rno; }).map(function (x) { return x.date; }).sort();
      var lastCut = cuts.length ? cuts[cuts.length - 1] : null;
      var maxCuts = hv.maxCuts;
      var spent = !!(maxCuts && cuts.length >= maxCuts && !act.finished);
      var nextCut = null;
      if (lastCut && !act.finished) {
        if (spent) {
          add('harvest', lastCut > today ? lastCut : today,
            label + nth + ' has given its ' + maxCuts + ' cuts: pull it' + (succ ? ', the next sowing takes over' : ' and resow'),
            hv.replaceWhen || '', null, 'finished', 'spent');
        } else if (lo(hv.regrowDays)) {
          nextCut = addDays(lastCut, lo(hv.regrowDays));
          var when = nextCut > today ? nextCut : today;
          if (!he || when <= he) {
            add('harvest', when, 'Cut again: ' + label + nth,
              (nextCut < today ? 'Regrown since ' + fmtBD(nextCut) + '. ' : '') + cutDetail, null, null, 'recut');
          }
        }
      }

      // fertiliser
      var anchors = { sow: sow, transplant: tp, 'harvest-start': hs, germination: germDate || (sow ? addDays(sow, gmax) : null) };
      (prof.fertilizer || []).forEach(function (f) {
        var detail = [f.product, f.amount, f.notes].filter(Boolean).join(' — ');
        var name = f.label == null ? 'fertilize' : f.label;
        if (f.start === 'calendar') {
          if (!calendarFeeds) return;
          var ty = yearOf(today), from = addDays(today, -45);
          [ty, ty + 1].forEach(function (y) {
            (f.dates || []).forEach(function (s) {
              var day = md(s, y);
              if (from <= day && day <= horizon) add('fertilize', day, 'Feed ' + label + ': ' + name, detail);
            });
          });
          return;
        }
        var startKey = f.start == null ? 'transplant' : f.start;
        var a = anchors[startKey] || tp || sow;
        if (!a) return;
        var day = addDays(a, f.offsetDays || 0);
        var every = f.everyDays;
        var untilKey = f.until == null ? 'harvest-end' : f.until;
        var until = (untilKey === 'harvest-start' ? hs : he) || horizon;
        if (untilKey !== 'harvest-start' && untilKey !== 'harvest-end') until = he || horizon;
        var count = f.count || (every ? 60 : 1);
        var n = 0;
        while (n < count && day <= horizon) {
          if (n && day > until) break;
          add('fertilize', day, 'Feed ' + label + nth + ': ' + name, detail);
          n += 1;
          if (!every) break;
          day = addDays(day, every);
        }
      });

      // stage
      var stage;
      if (act.finished) stage = 'finished';
      else if (method === 'established') stage = 'established';
      else if (act.firstHarvest || (hs && base && today >= hs && (act.transplanted || act.sown))) stage = 'harvesting';
      else if (act.transplanted || (method === 'direct-sow' && act.germinated)) stage = 'growing';
      else if (act.germinated) stage = 'seedling';
      else if (act.sown) stage = 'germinating';
      else if (sow || tp) stage = 'planned';
      else stage = 'unscheduled';

      return {
        round: rno, stage: stage, sown: !!act.sown, seedPrep: seedPrep, sow: sow,
        germination: germDate ? [germDate, germDate] : (sow ? [addDays(sow, gmin), addDays(sow, gmax)] : null),
        transplant: tp, harvestStart: hs, harvestEnd: he,
        cuts: cuts.length, lastCut: lastCut, nextCut: nextCut, spent: spent
      };
    }

    var rounds = planRounds(p, today).map(function (r, i) { return one(r[0], r[1], r[2], i === 0); });
    if (!rounds.length) rounds = [one(null, {}, null, true)];

    // unscheduled: surface the next window
    if (method !== 'established' && !rounds.some(function (r) { return r.sow || r.transplant; })) {
      var want = { 'indoor-start': 'sow-indoors', 'direct-sow': 'direct-sow', 'nursery-transplant': 'transplant' }[method];
      windows.forEach(function (w) {
        if (w.type !== want) return;
        var nw = nextWindow(w, today);
        if (!nw) return;
        var when = nw[0] > today ? nw[0] : today;
        var eid = p.id + ':window-open:' + when;
        events.push({
          id: eid, plantingId: p.id, plant: label, type: 'window-open', date: when, end: nw[1],
          title: 'Planting window for ' + label + ': pick a date',
          detail: want + ' window runs ' + fmtBD(nw[0]) + ' – ' + fmtBD(nw[1]) + '.',
          milestone: null, round: null, done: !!done[eid], location: location
        });
      });
    }

    // watering: counted from the last logged watering, at the interval the season's rule gives
    var rules = (prof.watering || []).filter(function (w) { return w.everyDays; });
    var inGround = method === 'established' || rounds.some(function (r) { return ['germinating', 'seedling', 'growing', 'harvesting'].indexOf(r.stage) >= 0; });
    var wset = {}; (p.watered || []).forEach(function (s) { wset[s] = 1; });
    var watered = Object.keys(wset).sort();
    var lastWatered = watered.length ? watered[watered.length - 1] : null;
    var nextWater = null;
    function pushWater(day, title, detail, isDone) {
      var eid = p.id + ':water:' + day;
      events.push({ id: eid, plantingId: p.id, plant: label, type: 'water', date: day, end: null, title: title, detail: detail,
        milestone: null, round: null, done: !!(isDone || done[eid]), location: location });
    }
    var histFloor = addDays(today, -WATER_HISTORY_DAYS);
    watered.forEach(function (s) { if (s >= histFloor) pushWater(s, 'Watered ' + label, '', true); });
    if (rules.length && inGround) {
      var ruleAt = function (day) { for (var i = 0; i < rules.length; i++) if (inWindow(day, rules[i])) return rules[i]; return null; };
      var everyOf = function (w) { return Math.max(1, pyRound(mid(w.everyDays))); };
      var detailOf = function (w) { return [w.amount, w.notes].filter(Boolean).join(' — '); };
      var cursor = today, firstNote = '';
      if (lastWatered) {
        var w0 = ruleAt(lastWatered) || ruleAt(today);
        cursor = w0 ? addDays(lastWatered, everyOf(w0)) : today;
        if (cursor < today) { cursor = today; firstNote = 'Last watered ' + fmtBD(lastWatered) + '. '; }
        else if (cursor === lastWatered) cursor = addDays(lastWatered, 1);
      } else {
        firstNote = 'No watering logged yet: water if the soil is dry a few inches down, then tick this to start the rhythm. ';
      }
      var wEnd = addDays(today, WATER_HORIZON_DAYS);
      for (var k = 0; k < WATER_HORIZON_DAYS + 5; k++) {
        var w = ruleAt(cursor);
        if (!w) {
          var nxt = snapToWindow(cursor, rules);
          if (nxt === cursor) break;
          cursor = nxt;
          continue;
        }
        if (cursor > wEnd) break;
        if (nextWater === null) nextWater = cursor;
        pushWater(cursor, 'Water ' + label, firstNote + detailOf(w), false);
        firstNote = '';
        cursor = addDays(cursor, everyOf(w));
      }
    }

    var nextFeed = null;
    events.slice().sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; }).some(function (e) {
      if (e.type === 'fertilize' && !e.done && e.date >= today) { nextFeed = e.date; return true; }
      return false;
    });

    var computed;
    if (rounds[0].round === null) {
      var r = rounds[0];
      computed = {
        stage: r.stage, seedPrep: r.seedPrep, sow: r.sow, germination: r.germination, transplant: r.transplant,
        harvestStart: r.harvestStart, harvestEnd: r.harvestEnd, warnings: warnings, nextFeed: nextFeed,
        nextWater: nextWater, lastWatered: lastWatered,
        harvests: { count: r.cuts, last: r.lastCut, nextCut: r.nextCut }
      };
      if (succ) computed.succession = { everyDays: succ.everyDays, nextSow: null, sown: 0 };
      return { computed: computed, events: events };
    }

    var live = rounds.filter(function (x) { return x.stage !== 'finished'; });
    var focus = rounds[rounds.length - 1];
    for (var i = 0; i < rounds.length; i++) if (!rounds[i].sown) { focus = rounds[i]; break; }
    var starts = live.map(function (x) { return x.harvestStart; }).filter(Boolean).sort();
    var ends = live.map(function (x) { return x.harvestEnd; }).filter(Boolean).sort();
    var every = succ ? succ.everyDays : null;
    var hw = prof.harvestWeeks;
    if (every && hw && every > hw * 7) {
      warnings.push('Resown every ' + every + ' days but each sowing is picked for about ' + hw + ' weeks: expect gaps between harvests');
    }
    var stage = 'finished';
    if (live.length) {
      stage = live[0].stage;
      live.forEach(function (x) { if (STAGE_RANK.indexOf(x.stage) < STAGE_RANK.indexOf(stage)) stage = x.stage; });
    }
    var lastPicked = picked.map(function (x) { return x.date; }).sort();
    var nextCuts = live.map(function (x) { return x.nextCut; }).filter(Boolean).sort();
    computed = {
      stage: stage, warnings: warnings,
      seedPrep: focus.seedPrep, sow: focus.sow, germination: focus.germination, transplant: focus.transplant,
      harvestStart: starts.length ? starts[0] : null, harvestEnd: ends.length ? ends[ends.length - 1] : null,
      nextFeed: nextFeed, rounds: rounds, nextWater: nextWater, lastWatered: lastWatered,
      harvests: { count: picked.length, last: lastPicked.length ? lastPicked[lastPicked.length - 1] : null, nextCut: nextCuts.length ? nextCuts[0] : null },
      succession: { everyDays: every, nextSow: focus.sown ? null : focus.sow, sown: rounds.filter(function (x) { return x.sown; }).length }
    };
    return { computed: computed, events: events };
  }

  // ---- whole garden
  function validate(p) {
    if (METHODS.indexOf(p.method || 'direct-sow') < 0) throw new Error(p.id + ': method must be one of ' + METHODS.join(', '));
    (p.watered || []).forEach(function (s) { mustDate(s); });
    ((p.profile || {}).watering || []).forEach(function (w) {
      if (!w || typeof w !== 'object' || !w.start || !w.end || !w.everyDays) throw new Error(p.id + ': every profile.watering rule needs start, end (MM-DD) and everyDays');
    });
    (p.harvests || []).forEach(function (h) {
      if (!h || typeof h !== 'object' || !h.date) throw new Error(p.id + ': every entry in harvests needs a date');
      mustDate(h.date);
    });
    if (p.succession != null) {
      var s = p.succession;
      if (typeof s !== 'object' || Array.isArray(s)) throw new Error(p.id + ': succession must be an object like {"everyDays": 21}, or null');
      if (SOWN_METHODS.indexOf(p.method || 'direct-sow') < 0) throw new Error(p.id + ': succession only applies to indoor-start and direct-sow plantings');
      var e = s.everyDays;
      if (e != null && (typeof e !== 'number' || e < 1)) throw new Error(p.id + ': succession.everyDays must be a number of days, 1 or more');
      if (!successionOf(p)) throw new Error(p.id + ': succession needs everyDays (here or in profile.succession)');
    }
  }
  function build(g, today) {
    today = today || todayLocal();
    var events = [], plantings = [], seen = {};
    (g.plantings || []).forEach(function (p) {
      if (seen[p.id]) throw new Error('duplicate planting id: ' + p.id);
      seen[p.id] = 1;
      validate(p);
      var r = schedule(p, today);
      var q = {}; for (var k in p) q[k] = p[k];
      q.computed = r.computed;
      plantings.push(q);
      events = events.concat(r.events);
    });
    events = events.map(function (e, i) { return [e, i]; }).sort(function (a, b) {
      if (a[0].date !== b[0].date) return a[0].date < b[0].date ? -1 : 1;
      if (a[0].plant !== b[0].plant) return a[0].plant < b[0].plant ? -1 : 1;
      return a[1] - b[1];
    }).map(function (x) { return x[0]; });
    return { site: g.site || {}, plantings: plantings, events: events, today: today };
  }

  function tasksFor(out, day, days) {
    var t = day, end = addDays(day, days), floor = addDays(day, -OVERDUE_DAYS);
    var today = [], overdue = [], upcoming = [];
    function withTitle(e, title) { var x = {}; for (var k in e) x[k] = e[k]; x.title = title; return x; }
    out.events.forEach(function (e) {
      if (e.done) return;
      var who = e.plant + (e.round ? ' (sowing ' + e.round + ')' : '');
      if (e.date === t) today.push(e);
      else if (e.type === 'germination' && e.end && e.date < t) {
        if (e.end < t && e.end >= floor) overdue.push(withTitle(e, 'No germination logged for ' + who + ' — check, resow if needed'));
        else if (e.end >= t) today.push(withTitle(e, 'Check for sprouts: ' + who));
      } else if (ACTIONABLE[e.type] && floor <= e.date && e.date < t) overdue.push(e);
      else if (t < e.date && e.date <= end) upcoming.push(e);
    });
    var sens = [];
    out.plantings.forEach(function (p) {
      var st = p.computed.stage;
      if (st === 'finished' || st === 'planned' || st === 'unscheduled') return;
      var prof = p.profile || {};
      sens.push({
        plant: p.name, stage: st, location: p.location == null ? '' : p.location,
        frostTenderBelowF: prof.frostTenderBelowF == null ? null : prof.frostTenderBelowF,
        heatStressAboveF: prof.heatStressAboveF == null ? null : prof.heatStressAboveF
      });
    });
    return { date: t, site: out.site, today: today, overdue: overdue, upcoming: upcoming, weatherSensitive: sens };
  }

  // ---- changes to one planting. Each takes a planting, returns {planting, message}; the input is not modified.
  function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''); }

  function preparePlanting(p, takenIds, today) {
    p = clone(p);
    if (!p.name) throw new Error('planting needs a name');
    var base = p.id || slug([p.name, p.variety].filter(Boolean).join(' '));
    var pid = base, n = 2;
    while (takenIds.indexOf(pid) >= 0) { pid = base + '-' + n; n += 1; }
    p.id = pid;
    if (p.method == null) p.method = 'direct-sow';
    var rec = ((p.profile || {}).succession || {}).everyDays;
    if (rec && !('succession' in p) && SOWN_METHODS.indexOf(p.method) >= 0) p.succession = { everyDays: rec };
    if (p.plan == null) p.plan = {};
    if (p.actual == null) p.actual = {};
    if (p.done == null) p.done = [];
    if (p.addedOn == null) p.addedOn = today || todayLocal();
    delete p._comment;
    validate(p);
    schedule(p, today || todayLocal());
    return p;
  }

  function isRepeat(p) { return !!(successionOf(p) || (p.sowings && p.sowings.length)); }
  function foldActual(p) {
    if (!p.sowings) p.sowings = [];
    if (anyValue(p.actual)) { p.sowings.unshift(clone(p.actual)); p.actual = {}; }
    return p.sowings;
  }
  function check(p, today) { validate(p); schedule(p, today || todayLocal()); }

  function logMilestone(p0, m, date, round, today) {
    var p = clone(p0);
    if (MILESTONES.indexOf(m) < 0) throw new Error('milestone must be one of ' + MILESTONES.join(', '));
    date = mustDate(date || today || todayLocal());
    if (!isRepeat(p)) {
      if (round) throw new Error(p.id + ' is not repeat-sown; leave the sowing number out');
      if (!p.actual) p.actual = {};
      p.actual[m] = date;
      return { planting: p, round: null, message: p.name + ': ' + m + ' = ' + date };
    }
    var rounds = foldActual(p);
    var later = MILESTONES.slice(MILESTONES.indexOf(m));
    function reached(r) { return later.some(function (x) { return r[x]; }); }
    var n = null;
    if (round) {
      if (round === rounds.length + 1) rounds.push({});
      else if (!(round >= 1 && round <= rounds.length)) throw new Error(p.id + ' has ' + rounds.length + ' logged sowing(s); the sowing number must be 1..' + (rounds.length + 1));
      n = round;
    } else if (m === 'seedPrep' || m === 'sown') {
      if (rounds.length && !reached(rounds[rounds.length - 1])) n = rounds.length;
      else { rounds.push({}); n = rounds.length; }
    } else {
      for (var i = 0; i < rounds.length; i++) if (rounds[i].sown && !reached(rounds[i])) { n = i + 1; break; }
      if (n === null) throw new Error(p.id + ': no sowing is waiting for ' + m + '. Log a sowing first, or name the sowing number.');
    }
    rounds[n - 1][m] = date;
    if (m === 'sown' && p.succession && typeof p.succession === 'object') delete p.succession.nextSowDate;
    check(p, today);
    return { planting: p, round: n, message: p.name + ': sowing ' + n + ' ' + m + ' = ' + date };
  }

  function logHarvest(p0, date, opts, today) {
    var p = clone(p0); opts = opts || {};
    date = mustDate(date || today || todayLocal());
    var entry = { date: date }, act;
    if (isRepeat(p)) {
      var rounds = foldActual(p), n;
      if (opts.round) {
        if (!(opts.round >= 1 && opts.round <= rounds.length)) throw new Error(p.id + ' has ' + rounds.length + ' logged sowing(s); the sowing number must be 1..' + rounds.length);
        n = opts.round;
      } else {
        var maxCuts = ((p.profile || {}).harvest || {}).maxCuts;
        var count = function (i) { return (p.harvests || []).filter(function (h) { return h.round === i + 1; }).length; };
        var live = [], fresh = [];
        rounds.forEach(function (r, i) {
          if (r.sown && !r.finished) { live.push(i); if (!(maxCuts && count(i) >= maxCuts)) fresh.push(i); }
        });
        if (!live.length) throw new Error(p.id + ': no sowing in the ground to pick from. Log a sowing first.');
        n = (fresh.length ? fresh : live)[0] + 1;
      }
      entry.round = n;
      act = rounds[n - 1];
    } else {
      if (opts.round) throw new Error(p.id + ' is not repeat-sown; leave the sowing number out');
      if (!p.actual) p.actual = {};
      act = p.actual;
    }
    if (!act.firstHarvest || date < act.firstHarvest) act.firstHarvest = date;
    if (opts.amount) entry.amount = String(opts.amount);
    if (opts.note) entry.notes = String(opts.note);
    if (!p.harvests) p.harvests = [];
    p.harvests.push(entry);
    p.harvests = p.harvests.map(function (h, i) { return [h, i]; }).sort(function (a, b) {
      return a[0].date < b[0].date ? -1 : a[0].date > b[0].date ? 1 : a[1] - b[1];
    }).map(function (x) { return x[0]; });
    check(p, today);
    return { planting: p, round: entry.round || null, message: p.name + ': picking logged ' + date + (entry.round ? ' (sowing ' + entry.round + ')' : '') };
  }

  function logWater(p0, date, today) {
    var p = clone(p0);
    date = mustDate(date || today || todayLocal());
    var set = {}; (p.watered || []).concat([date]).forEach(function (s) { set[s] = 1; });
    p.watered = Object.keys(set).sort().slice(-WATER_HISTORY_DAYS);
    check(p, today);
    return { planting: p, message: p.name + ': watered ' + date };
  }

  function markDone(p0, eventIds) {
    var p = clone(p0);
    if (!p.done) p.done = [];
    eventIds.forEach(function (eid) { if (p.done.indexOf(eid) < 0) p.done.push(eid); });
    return { planting: p, message: 'Marked done: ' + eventIds.join(', ') };
  }

  function setPath(p0, path, value, today) {
    var p = clone(p0), node = p, keys = path.split('.');
    if (keys[0] === 'id') throw new Error('The id cannot be changed');
    for (var i = 0; i < keys.length - 1; i++) {
      if (node[keys[i]] == null || typeof node[keys[i]] !== 'object') node[keys[i]] = {};
      node = node[keys[i]];
    }
    node[keys[keys.length - 1]] = value;
    check(p, today);
    return { planting: p, message: p.name + ': ' + path + ' = ' + JSON.stringify(value) };
  }

  return {
    MILESTONES: MILESTONES, METHODS: METHODS, ACTIONABLE: ACTIONABLE, OVERDUE_DAYS: OVERDUE_DAYS,
    isDate: isDate, addDays: addDays, todayLocal: todayLocal,
    successionOf: successionOf, schedule: schedule, build: build, tasksFor: tasksFor,
    preparePlanting: preparePlanting, logMilestone: logMilestone, logHarvest: logHarvest,
    logWater: logWater, markDone: markDone, setPath: setPath, slug: slug
  };
});
