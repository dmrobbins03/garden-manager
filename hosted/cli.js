#!/usr/bin/env node
/* Command-line use of hosted/scheduler.js, for agents working from a JSON export of the hosted page's data.
 *
 *   node hosted/cli.js tasks GARDEN.json [--date YYYY-MM-DD] [--days N] [--json]
 *   node hosted/cli.js build GARDEN.json [--date YYYY-MM-DD]          full schedule as JSON
 *   node hosted/cli.js prepare PLANTING.json GARDEN.json [--date D]   validate a new planting, assign its id, print it
 *   node hosted/cli.js apply GARDEN.json OPS.json [--date D]          apply changes, print the changed garden
 *
 * GARDEN.json is {"site": {...}, "plantings": [...]}.
 * OPS.json is a list of {"op": "log"|"harvest"|"done"|"set"|"add"|"remove", ...}.
 */
'use strict';
var fs = require('fs');
var S = require('./scheduler.js');

function arg(name, dflt) { var i = process.argv.indexOf('--' + name); return i >= 0 ? process.argv[i + 1] : dflt; }
function flag(name) { return process.argv.indexOf('--' + name) >= 0; }
function read(f) { return JSON.parse(fs.readFileSync(f, 'utf8')); }

function applyOps(g, ops, today) {
  ops.forEach(function (o) {
    if (o.op === 'add') {
      var ids = g.plantings.map(function (p) { return p.id; });
      g.plantings.push(S.preparePlanting(o.planting, ids, today));
      return;
    }
    var i = -1;
    g.plantings.forEach(function (p, k) { if (p.id === o.id) i = k; });
    if (i < 0) throw new Error('no planting with id ' + o.id);
    if (o.op === 'remove') { g.plantings.splice(i, 1); return; }
    var p = g.plantings[i], r;
    if (o.op === 'log') r = S.logMilestone(p, o.milestone, o.date, o.round, today);
    else if (o.op === 'harvest') r = S.logHarvest(p, o.date, { amount: o.amount, note: o.note, round: o.round }, today);
    else if (o.op === 'done') r = S.markDone(p, o.eventIds);
    else if (o.op === 'set') r = S.setPath(p, o.path, o.value, today);
    else throw new Error('unknown op ' + o.op);
    g.plantings[i] = r.planting;
  });
  return g;
}

function main() {
  var cmd = process.argv[2];
  var today = arg('date', S.todayLocal());
  if (cmd === 'build') {
    process.stdout.write(JSON.stringify(S.build(read(process.argv[3]), today), null, 1) + '\n');
  } else if (cmd === 'tasks') {
    var days = parseInt(arg('days', '7'), 10);
    var res = S.tasksFor(S.build(read(process.argv[3]), today), today, days);
    if (flag('json')) { process.stdout.write(JSON.stringify(res, null, 1) + '\n'); return; }
    var line = function (e) { return '- ' + e.title + (e.location ? ' [' + e.location + ']' : '') + (e.detail ? ' — ' + e.detail : '') + '  (id: ' + e.id + ')'; };
    var out = ['# Garden tasks for ' + res.date];
    [['Overdue', 'overdue'], ['Today', 'today']].forEach(function (h) {
      if (!res[h[1]].length) return;
      out.push('', '## ' + h[0]);
      res[h[1]].forEach(function (e) { out.push(line(e) + (h[1] === 'overdue' ? '  (was due ' + e.date + ')' : '')); });
    });
    if (!res.today.length && !res.overdue.length) out.push('', 'Nothing scheduled today.');
    if (res.upcoming.length) {
      out.push('', '## Next ' + days + ' days');
      res.upcoming.forEach(function (e) { out.push('- ' + e.date + ': ' + e.title); });
    }
    if (res.weatherSensitive.length) {
      out.push('', '## Weather thresholds (plants currently growing)');
      res.weatherSensitive.forEach(function (s) {
        out.push('- ' + s.plant + ' (' + s.stage + '): protect below ' + (s.frostTenderBelowF || '?') + '°F, heat stress above ' + (s.heatStressAboveF || '?') + '°F');
      });
    }
    process.stdout.write(out.join('\n') + '\n');
  } else if (cmd === 'prepare') {
    var g = read(process.argv[4]);
    var p = S.preparePlanting(read(process.argv[3]), g.plantings.map(function (x) { return x.id; }), today);
    process.stdout.write(JSON.stringify(p, null, 1) + '\n');
  } else if (cmd === 'apply') {
    process.stdout.write(JSON.stringify(applyOps(read(process.argv[3]), read(process.argv[4]), today), null, 1) + '\n');
  } else {
    process.stderr.write('usage: cli.js tasks|build|prepare|apply ... (see the top of this file)\n');
    process.exit(2);
  }
}

try { main(); } catch (e) { process.stderr.write(String(e.message || e) + '\n'); process.exit(1); }
