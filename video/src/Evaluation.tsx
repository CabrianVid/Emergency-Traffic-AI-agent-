import React, {useLayoutEffect, useRef} from 'react';
import {AbsoluteFill} from 'remotion';
import tileData from '../data/tiles.json';
import ev from '../data/evidence.json';
import {at, Frame, Field, Label, Mark, P, Rule, Section, Text, linear, mono, pointAt, tween, useTime} from './graphics';

const films=tileData as unknown as {seed:number,policy:string,density:string,frames:Frame[]}[];
const policyColors=['#819697','#9bd6b6','#a7aec0','#ffa33d','#e1b58b'];
const mod=(n:number,m:number)=>((n%m)+m)%m;

// The normalised camera moves across a contact-print wall. Only visible tiles
// are painted. Each plate reuses a verified trajectory at a named seed/policy.
function renderWall(ctx:CanvasRenderingContext2D,t:number){
  const w=1920,h=1080, cellW=1000,cellH=660;
  ctx.fillStyle='#0b191a';ctx.fillRect(0,0,w,h);
  const zoom=1.7*Math.exp(-tween(t,2,9.3,0,3.48));
  const pan=tween(t,2,5.4,0,.76)+tween(t,4.4,9.3,0,2.32);
  const cx=w/2-pan*cellW*zoom,cy=h/2+tween(t,4,9.3,0,145);
  const cols=Math.ceil(w/cellW/zoom/2)+2,rows=Math.ceil(h/cellH/zoom/2)+2;
  for(let row=-rows;row<=rows;row++)for(let col=-cols+Math.floor(pan);col<=cols+Math.floor(pan);col++){
    const id=mod(col+row*7,films.length),tile=films[id],hero=col===0&&row===0;
    const x=cx+(col*cellW-cellW/2)*zoom,y=cy+(row*cellH-cellH/2)*zoom;
    if(x>w||y>h||x+cellW*zoom<0||y+cellH*zoom<0)continue;
    ctx.save();ctx.translate(x,y);ctx.scale(zoom,zoom);
    ctx.fillStyle=['#193331','#213336','#293330','#253437','#243733','#263b38'][id];ctx.fillRect(6,6,988,648);
    const play=(t*9+mod(col*19+row*33,47))%(tile.frames.length-1);
    const margin=56,gridSize=510, mapX=44,mapY=82;
    const X=(n:number)=>mapX+margin+n*gridSize/2400;
    const Y=(n:number)=>mapY+(2400-n)*gridSize/2400;
    ctx.strokeStyle='#63796e';ctx.lineWidth=2.3;
    ctx.beginPath();
    for(let i=0;i<7;i++){const k=i*400;ctx.moveTo(X(k),Y(0));ctx.lineTo(X(k),Y(2400));ctx.moveTo(X(0),Y(k));ctx.lineTo(X(2400),Y(k));}
    ctx.stroke();
    ctx.fillStyle='#3c5045';
    for(let k=0;k<36;k++){if((k+id)%4!==0)continue;ctx.fillRect(X(k%6*400)+6,Y((1+Math.floor(k/6))*400)+6,gridSize/6-12,gridSize/6-12);}
    ctx.fillStyle='#d3d4bd';
    pointAt(tile.frames,play,'traffic').forEach(v=>ctx.fillRect(X(v.x)-1.8,Y(v.y)-1.8,3.6,3.6));
    const vehicles=pointAt(tile.frames,play,'fleet');
    vehicles.forEach((v,i)=>{
      const color=i===0?'#ff9a51':'#a4e0ba';
      ctx.fillStyle=color;ctx.fillRect(X(v.x)-5,Y(v.y)-5,10,10);
      if(zoom>.42){ctx.font='13px Consolas';ctx.fillText(v.id,X(v.x)+8,Y(v.y)-6);}
    });
    ctx.fillStyle='#0f211f';ctx.fillRect(645,7,349,646);
    ctx.fillStyle='#b1c1b2';ctx.font='18px Consolas';
    ctx.fillText('SIMULATION REPLAY',673,69);
    ctx.fillStyle='#f2f0e5';ctx.font='bold 49px Arial';ctx.fillText(tile.policy,670,140);
    ctx.fillStyle='#a2d6b7';ctx.font='23px Consolas';ctx.fillText('SEED '+String(tile.seed).padStart(4,'0'),672,183);
    ctx.fillStyle='#a5b9aa';ctx.font='18px Consolas';ctx.fillText(tile.density.toUpperCase()+' TRAFFIC',672,237);
    ctx.fillRect(673,270,255,1);ctx.fillText('FLEET TELEMETRY',673,311);
    vehicles.slice(0,5).forEach((v,i)=>{
      ctx.fillStyle=i===0?'#ffa552':'#c6d4c2';
      ctx.font='bold 22px Consolas';ctx.fillText(v.id,672,355+i*44);
      ctx.font='17px Consolas';ctx.fillText(Math.round(v.x)+' / '+Math.round(v.y),742,355+i*44);
    });
    ctx.fillStyle='#dddfd0';ctx.font='19px Consolas';ctx.fillText('T + '+Math.floor(tile.frames[0].t+play)+' s',672,604);
    ctx.fillStyle=hero?'#ff9b43':'#849989';ctx.fillRect(6,6,988,4);
    ctx.fillStyle='#c6d4c5';ctx.font='17px Consolas';ctx.fillText('SUMO  /  TRAINING GRID',64,49);
    ctx.restore();
  }
}

// One cell per attempted run, laid out by seed and then policy. Muted tones
// encode response, and each of the five invalid runs is marked in vermilion.
function matrix(ctx:CanvasRenderingContext2D,x:number,y:number,w:number,h:number,opacity:number){
  const cols=100,rows=50,cw=w/cols,ch=h/rows;
  ev.results.forEach((v,i)=>{
    const seed=Math.floor(i/5),p=i%5, row=p*10+Math.floor(seed/100);
    ctx.globalAlpha=opacity*(v<0?1:.28+.72*(1-Math.min(1,v/620)));
    ctx.fillStyle=v<0?'#ee5945':policyColors[p];ctx.fillRect(x+(seed%100)*cw,y+row*ch,Math.max(1,cw-1.7),Math.max(1,ch-1.6));
  });
  ctx.globalAlpha=1;
}
export function Mosaic(){
  const t=useTime(),ref=useRef<HTMLCanvasElement>(null), pull=tween(t,7.1,9.15);
  useLayoutEffect(()=>{
    const c=ref.current!.getContext('2d')!;
    renderWall(c,t);
    if(pull){
      c.fillStyle='rgba(11,26,26,'+pull+')';c.fillRect(0,0,1920,1080);
      matrix(c,pull*1000,pull*214,1920-pull*1104,1080-pull*442,pull);
    }
  },[t,pull]);
  return <Field><canvas ref={ref} width={1920} height={1080}/>
    <div style={{...at(70,930),padding:'16px 24px',background:P.deep,font:'23px '+mono,color:P.paper,opacity:tween(t,2.4,3.15)*(1-tween(t,6.6,7.3))}}>SIX RECORDED EXCERPTS  /  MATCHED TRAINING GRID</div>
    <AbsoluteFill style={{opacity:tween(t,8.1,9.1)}}><Section name="EXPERIMENT REGISTER"/>
      <Label x={87} y={218} color={P.mint}>MATCHED SYNTHETIC SCENARIOS</Label>
      <Text x={79} y={256} size={148} weight={600}>1,000</Text>
      <Text x={89} y={439} size={50}>× five dispatch policies</Text>
      <Rule x={89} y={554} w={760} color="#456357"/>
      <Text x={89} y={601} size={69} color={P.mint}>5,000 attempted</Text>
      <Text x={89} y={707} size={64}>4,995 valid</Text>
      <Label x={89} y={956} size={21}>SAME INPUTS AND EMERGENCY DRIVING RULES WITHIN EACH SEED</Label>
      <Label x={1000} y={881} size={19}>1 CELL = 1 RUN   /   RED = INVALID</Label>
      {['B0','B1','R1','A1','A2'].map((p,i)=><Label key={p} x={940} y={265+i*127.6} size={23} color={policyColors[i]}>{p}</Label>)}
    </AbsoluteFill>
  </Field>;
}

const comparisons=[
  ['B0','A1','AI dispatch','vs nearest free unit'],
  ['B1','A1','AI dispatch','vs central queue'],
  ['R1','A1','AI ranking','vs fixed ranking'],
  ['A1','A2','Signal priority','added to AI dispatch'],
].map(([ref,cand,title,sub])=>({title,sub,ref,cand,data:ev.pairs.find(p=>p.reference===ref&&p.candidate===cand)!}));
const number=(n:number)=>n.toFixed(1).replace('-','−');
function Finding({second,t}:{second:boolean,t:number}){
  const lo=second?-5:-40,hi=second?10:50;
  const domain=(val:number)=>785+(val-lo)/(hi-lo)*1005;
  const ticks=second?[-5,0,5,10]:[-40,-20,0,20,40];
  const details=second?comparisons.slice(2):comparisons.slice(0,2);
  const color=second?'#a84222':'#216850';
  const hero=second?details[0].data:details[1].data;
  return <Field background={P.paper}><Section name="MEASURED RESPONSE" dark={false}/>
    <Label x={1360} y={55} color="#486056" size={21}>SYNTHETIC SUMO  /  7 × 7 GRID</Label>
    <Text x={83} y={136} size={79} color={P.ink} weight={600}>{second?'What did AI ranking add?':'The central queue was fastest.'}</Text>
    <Rule x={88} y={272} w={1744}/>
    <Label x={89} y={328} color={color} size={23}>{second?'R1 → A1  /  RANKING ALONE':'A1 → B1  /  CENTRAL QUEUE'}</Label>
    <Text x={74} y={389} size={157} color={color} weight={600}>{Math.abs(hero.mean_seconds_saved).toFixed(1)}<span style={{fontSize:88}}> s</span></Text>
    <Text x={87} y={580} width={585} size={49} color={P.ink} italic>{second?'faster with AI ranking':'faster with a central queue'}</Text>
    <Text x={89} y={718} size={33} width={565} color="#485c51">{second?'The two rankings tied in 635 of 998 matched scenarios.':'The queue dispatches again as soon as a unit becomes free.'}</Text>
    <Label x={785} y={326} size={21} color="#43584e">SECONDS SAVED   /   RIGHT IS FASTER</Label>
    <svg width="1920" height="1080" style={at(0,0)}>
      {ticks.map(val=><g key={val}><line x1={domain(val)} y1="427" x2={domain(val)} y2="784" stroke={val===0?'#7c8c7f':'#d3d8cc'} strokeDasharray={val===0?undefined:'4 7'}/><text x={domain(val)} y="824" textAnchor="middle" style={{font:'24px '+mono,fill:'#4d6054'}}>{val>0?'+':''}{val}</text></g>)}
      {details.map(({data,ref,cand},i)=>{
        const y=505+197*i,p=tween(t,.25+i*.3,1.4+i*.3);
        const x=(v:number)=>domain(v*p), value=data.mean_seconds_saved, tone=i===0?'#a84222':'#216850';
        const title=(second?['AI vs fixed ranking','AI with vs without signal priority']:['AI vs nearest free unit','AI vs central queue'])[i];
        return <g key={ref+cand}>
          <rect x="760" y={y-110} width="1070" height="67" fill={P.paper}/>
          <text x="785" y={y-57} fontSize={33} fill={P.ink} fontWeight="600">{title}</text>
          <text x="1794" y={y-57} textAnchor="end" fontSize={35} fill={tone} fontWeight="600">{value>0?'+':''}{number(value)} s</text>
          <line x1={domain(0)} x2={x(value)} y1={y} y2={y} stroke={tone} strokeWidth="22" opacity=".15"/>
          <line x1={x(data.ci95[0])} x2={x(data.ci95[1])} y1={y} y2={y} stroke={tone} strokeWidth="6"/>
          {data.ci95.map((v,j)=><line key={j} x1={x(v)} x2={x(v)} y1={y-11} y2={y+11} stroke={tone} strokeWidth="4"/>)}
          <circle cx={x(value)} cy={y} r="11" fill={tone}/>
          <text x="788" y={y+50} style={{font:'22px '+mono,fill:'#4a5b50'}}>{ref} → {cand}   ·   95% CI [{number(data.ci95[0])}, {number(data.ci95[1])}] s</text>
        </g>;
      })}
    </svg>
    <Rule x={88} y={886} w={1744}/>
    <Text x={88} y={911} size={32} color={P.ink}>{second?'Signal priority showed no measurable average benefit in this benchmark.':'Seconds from call to eligible arrival, paired within each synthetic scenario.'}</Text>
    <Text x={89} y={968} size={25} color="#526559">Response capped at 900 s, including missed calls. Equal scenario weight. Intervals from a paired bootstrap.</Text>
    <Label x={89} y={1022} size={17} color="#5c695f">PUBLISHED FIRST-GENERATION POLICIES  /  {second?'998–999':'999'} VALID PAIRS PER COMPARISON</Label>
  </Field>;
}
export function Evidence() {
  const t=useTime(), next=t-9.4;
  return <Field><Finding t={t} second={false}/>{next>=0&&<AbsoluteFill style={{clipPath:'inset(0 '+(100-100*tween(next,0,.58))+'% 0 0)'}}><Finding second t={next}/></AbsoluteFill>}</Field>;
}
