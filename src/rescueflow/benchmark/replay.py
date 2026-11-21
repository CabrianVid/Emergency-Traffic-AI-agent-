"""Write a standalone player from captured SUMO frames and events."""

import json
from pathlib import Path


def render_replay(source: Path, output: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    roads = []
    seen = set()
    for edge in data["geometry"]["edges"].values():
        shape = tuple(tuple(round(value, 1) for value in point) for point in edge["shape"])
        if shape not in seen:
            roads.append(shape)
            seen.add(shape)
    recorded_events = {
        "incident_validated", "plan_approved", "vehicle_frozen", "stop_begin",
        "stop_completed", "phase_shortened", "restored",
    }
    events = [event for event in data["events"] if event["kind"] in recorded_events]
    frames = []
    for frame in data["frames"]:
        xy = {key: [round(value, 1) for value in unit["xy"]] for key, unit in frame["fleet"].items()}
        frames.append({"t": frame["time_s"], "xy": xy, "kit": frame["kit"], "completed": frame["completed"]})
    payload = json.dumps({
        "source": data["source"], "hash": data["inputs_sha256"], "roads": roads,
        "bounds": data["geometry"]["bounds"], "events": events, "frames": frames,
    }, separators=(",", ":")).replace("<", "\\u003c")
    output.write_text(_PLAYER.replace("/* DATA */", payload), encoding="utf-8")


_PLAYER = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RescueFlow · Recorded SUMO replay</title>
<style>
body{background:#10151d;color:#e6edf5;margin:0;font:16px system-ui}
main{max-width:960px;margin:auto;padding:24px}h1{margin:0}h2{font-size:16px}
small,li{color:#b5c4d7} .tag{color:#6be4c1;font-size:12px;font-weight:700;letter-spacing:.1em}
.card{background:#161d27;border:1px solid #2e3d50;border-radius:10px;padding:14px;margin:12px 0}
.grid{display:grid;grid-template-columns:1fr 300px;gap:10px}svg{width:100%;background:#111a25;min-height:270px}
button{border:1px solid #6be4c1;background:#184039;color:white;border-radius:6px;padding:7px 16px}
input{width:100%;accent-color:#6be4c1}ol{padding-left:22px}li{margin-bottom:9px}
@media(max-width:720px){.grid{display:block}}
</style>
<main>
<span class="tag">SYNTHETIC SUMO SIMULATION · OFFLINE REPLAY</span>
<h1>RescueFlow · K1 recovery</h1><p id="source"></p>
<div class="grid"><section class="card"><svg id="map" role="img" aria-label="Recorded ambulance positions"></svg>
<p><button id="play">Play</button> <strong id="time"></strong> <span id="kit"></span></p>
<label>Recorded frames <input id="slider" type="range" min="0" value="0" step="1"></label>
<small>Positions are sampled every 20 simulation seconds.</small></section>
<section class="card"><h2>Recorded events</h2><ol id="events"></ol></section></div>
</main>
<script>
const run = /* DATA */;
const $ = id => document.getElementById(id);
const map = $('map'), slider = $('slider'), ns = 'http://www.w3.org/2000/svg';
const [left,bottom,right,top] = run.bounds;
map.setAttribute('viewBox',[left-80,-top-80,right-left+160,top-bottom+160].join(' '));
function svg(tag, attrs) {
  const item = document.createElementNS(ns,tag);
  for (const [key,value] of Object.entries(attrs)) item.setAttribute(key,value);
  return item;
}
for (const road of run.roads) map.append(svg('polyline',{
  points:road.map(([x,y])=>x+','+(-y)).join(' '),fill:'none',stroke:'#46586e','stroke-width':8
}));
const layer = svg('g',{}); map.append(layer);
const clock = t => Math.floor(t/60)+':'+String(t%60).padStart(2,'0');
$('source').textContent = run.source+' · inputs '+run.hash.slice(0,12);
slider.max = run.frames.length-1;
function describe(e) {
  if(e.kind==='incident_validated') return e.impact;
  if(e.kind==='plan_approved') return e.policy+' approval · '+e.dispatch.length+' mock messages';
  if(e.kind==='stop_begin'||e.kind==='stop_completed') return e.vehicle_id+' '+e.kind.replaceAll('_',' ')+' · '+e.task_id;
  return e.kind.replaceAll('_',' ')+(e.tls_id?' · '+e.tls_id:'');
}
function show() {
  const frame=run.frames[Number(slider.value)]; layer.replaceChildren();
  for (const [id,[x,y]] of Object.entries(frame.xy)) {
    layer.append(svg('circle',{cx:x,cy:-y,r:20,fill:id==='R2'?'#ff8b83':'#6be4c1'}));
    const label=svg('text',{x:x+28,y:-y-10,fill:'#f1f5ff','font-size':46});label.textContent=id;layer.append(label);
  }
  $('time').textContent=clock(frame.t);
  $('kit').textContent=' · K1 '+frame.kit.kind+' '+frame.kit.ref_id+' · '+frame.completed+' tasks completed';
  const list=run.events.filter(e=>e.sim_time_s<=frame.t).slice(-8).reverse();
  $('events').replaceChildren(...list.map(e=>{const li=document.createElement('li');li.textContent=clock(e.sim_time_s)+'  '+describe(e);return li;}));
}
let timer;
function pause(){clearInterval(timer);timer=null;$('play').textContent='Play';}
$('play').onclick=()=>{if(timer){pause();return;} if(slider.value===slider.max) slider.value=0;
  $('play').textContent='Pause';timer=setInterval(()=>{if(slider.value===slider.max){pause();return;} slider.stepUp();show();},400);};
slider.oninput=show;show();
</script></html>
"""
