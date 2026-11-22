const fs = require('node:fs');
const path = require('node:path');
const {makeScore} = require('./score.cjs');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'public/generated');
fs.mkdirSync(out, {recursive:true});
const bundle = path.join(root, '../experiments/published/montecarlo-2026-11-22');
const summary = JSON.parse(fs.readFileSync(path.join(bundle,'summary.json')));
const policies = ['B0','B1','R1','A1','A2'];
const results = summary.scenarios.flatMap(s => policies.map(id => {
  const p = s.policies[id];
  return p.valid ? Math.round(p.mean_capped_s) : -1;
}));
const evidence = { count: summary.scenarios.length, valid: results.filter(r=>r>=0).length,
  cap: summary.response_cap_s, pairs: summary.pairs.map(({reference,candidate,mean_seconds_saved,ci95,valid_pairs}) =>
    ({reference,candidate,mean_seconds_saved,ci95,valid_pairs})), results };
fs.writeFileSync(path.join(root,'data/evidence.json'),JSON.stringify(evidence));
fs.copyFileSync(path.join(root,'../simulation/presentation/ljubljana-wide-aerial.jpg'),path.join(out,'city.jpg'));
makeScore(path.join(out,'score.wav'));
