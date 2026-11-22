import React from 'react';
import {AbsoluteFill, Img, staticFile} from 'remotion';
import {P, at, Field, GridMap, Label, Mark, Rule, Section, Text, assigned, callXY, decision, drawPath, mono, pointAt, roads, serif, strats, tween, useTime} from './graphics';
import button from '../data/button.json';

const Credit=()=> <Label x={86} y={1013} size={17} color="#d6ded4">Orthophoto © GURS 2024 · CC BY 4.0  /  © OpenStreetMap contributors</Label>;
const plate=(name:string)=>staticFile('plates/'+name+'.png');

export function Introduction(){
  const t=useTime(), reveal=tween(t,3.6,4.7);
  return <Field>
    <Img src={staticFile('generated/city.jpg')} style={{...at(0,0),width:1920,height:1080,objectFit:'cover',filter:'grayscale(.88) contrast(1.35)',transform:'scale('+tween(t,0,4.8,1.10,1.26)+')'}}/>
    <AbsoluteFill style={{background:'linear-gradient(90deg,#061617fa 0%,#0c2220eb 34%,#10272561 82%),linear-gradient(0deg,#091a19b8,transparent 40%)'}}/>
    <svg width="1920" height="1080" style={at(0,0)}>
      <g stroke="#f3f0e7" fill="none" opacity=".37">
        <path d="M1130 548H1900M1510 120V932" strokeDasharray="3 11"/>
        {[68,128,212].map((r,i)=><circle key={r} cx="1510" cy="548" r={r+tween(t,0,4,i*6,0)} strokeDasharray={i===1?'2 10':undefined}/>)}
      </g>
      <path d="M1496 548H1524M1510 534V562" stroke={P.amber} strokeWidth="4"/>
    </svg>
    <Section name="ADAHACK 2026"/>
    <div style={{...at(84,321),opacity:tween(t,0,.55),transform:'translateY('+tween(t,0,.9,32,0)+'px)'}}>
      <div style={{fontFamily:mono,fontSize:24,color:P.amber,letterSpacing:3}}>DISPATCH DECISION SUPPORT</div>
      <div style={{fontSize:147,lineHeight:1.18,fontWeight:600,letterSpacing:-9}}>RescueFlow <span style={{fontFamily:serif,fontWeight:400,fontStyle:'italic'}}>AI</span></div>
      <div style={{fontSize:40,color:'#cedacb',letterSpacing:-1}}>One desk for a changing shift.</div>
    </div>
    <Label x={1421} y={793} size={20}>LJUBLJANA / SI</Label><Credit/>
    <AbsoluteFill style={{background:P.paper,clipPath:'inset(0 '+(100-100*reveal)+'% 0 0)'}}>
      <Section name="OPERATOR DESK" dark={false}/>
      <Label x={1334} y={55} size={21} color="#475d55">LJUBLJANA  /  SUMO SNAPSHOT</Label>
      <div style={{...at(166,114),width:1588,height:893,overflow:'hidden',background:'#101918',boxShadow:'0 12px 36px #1c2b272c'}}>
        <Img src={plate('city')} style={{width:'100%',transformOrigin:'53% 45%',transform:'scale('+tween(t,3.7,7.8,1.58,1)+')'}}/>
      </div>
      <Label x={166} y={1031} size={17} color="#40564e">SIMULATED INCIDENTS AND VEHICLES   /   © OPENSTREETMAP CONTRIBUTORS · ORTHOPHOTO © GURS 2024, CC BY 4.0</Label>
    </AbsoluteFill>
  </Field>;
}

const registeredLayers=['Road network','Active assignments','Incoming call'];
function StateLayers({t}:{t:number}){
  const unfold=tween(t,.6,3.4);
  const current=decision.frames[0];
  // Three transparent sheets share one physical coordinate space. The deck
  // lifts the call away from the roads while keeping its junction registered.
  return <div style={{...at(923,0),width:997,height:1080,overflow:'hidden',background:'radial-gradient(ellipse at 50% 55%, #304a3f 0%, #142f2e 57%, #0b2124 100%)'}}>
    <div style={{...at(67,177),width:850,height:850,perspective:1600}}>
      <div style={{width:'100%',height:'100%',transformStyle:'preserve-3d',transform:'translateY('+(-49+unfold*48)+'px) rotateX('+(unfold*44)+'deg) rotateZ('+(-unfold*19)+'deg) scale('+(1-unfold*.14)+')'}}>
        {registeredLayers.map((layer,i)=><div key={layer} style={{position:'absolute',inset:0,transform:'translateZ('+(i*92*unfold)+'px)',border:'1px solid rgba(204,230,207,'+(.22*unfold)+')',background:i===0?'#183431':'rgba(33,67,55,'+(.12*unfold)+')'}}>
          <svg width="100%" height="100%" viewBox="-155 -155 2710 2710">
            {i===0&&<><path d={roads} fill="none" stroke="#779184" strokeWidth="9"/>{pointAt([current],0,'traffic').map(v=><circle key={v.id} cx={v.x} cy={2400-v.y} r="10" fill={P.paper}/>)}</>}
            {i===1&&<>{Object.entries(decision.committedRoutes).map(([id,list])=><path key={id} d={list.map(edge=>decision.geometry.edges[edge]).filter(Boolean).map(e=>drawPath(e.shape)).join(' ')} fill="none" stroke={P.mint} opacity=".8" strokeWidth="12"/>)}{pointAt([current],0,'fleet').map(v=><g key={v.id} transform={'translate('+v.x+' '+(2400-v.y)+')'}><rect x="-20" y="-20" width="40" height="40" fill={P.paper}/><text x="32" y="-16" fontFamily={mono} fontSize="68" fill={P.paper}>{v.id}</text></g>)}</>}
            {i===2&&<g transform={'translate('+callXY[0]+' '+(2400-callXY[1])+')'}><circle r={92+tween(t,0,1.1,100,0)} fill="none" stroke={P.amber} strokeWidth="7" opacity=".8"/><path d="M0 -50 50 0 0 50 -50 0Z" fill={P.amber}/><text x="113" y="23" fontFamily={mono} fontSize="78" fill={P.paper}>CALL-1</text></g>}
          </svg>
        </div>)}
      </div>
    </div>
    <div style={{...at(50,984),opacity:unfold,display:'flex',gap:25,color:P.paper,font:'21px '+mono}}>
      {registeredLayers.map((s,i)=><span key={s}><b style={{color:['#93a79b',P.mint,P.amber][i]}}>■</b> {s}</span>)}
    </div>
  </div>;
}
function Intake({t}:{t:number}){
  const rows=[['TYPE','New emergency'],['PRIORITY','High'],['PICKUP','Junction E5'],['FLEET','6 ambulances']];
  return <Field><Section name="DECISION REPLAY"/>
    <StateLayers t={t}/>
    <Label x={90} y={167} size={24} color={P.amber}>PUBLISHED TRAINING GRID  /  SEED 0000</Label>
    <Text x={83} y={254} size={103}>A call at E5.</Text>
    <Text x={89} y={381} size={42} color="#c9d7cb" width={750}>The desk turns the incoming call into a structured event.</Text>
    {rows.map(([k,v],i)=><div key={k} style={{...at(91,575+i*82),width:730,opacity:tween(t,.4+i*.23,1+i*.23),transform:'translateX('+tween(t,.4+i*.23,1+i*.23,-24,0)+'px)'}}>
      <Rule x={0} y={0} w={719} color="#47615a"/><Label x={0} y={17} size={21}>{k}</Label><Text x={270} y={12} size={37}>{v}</Text>
    </div>)}
    <Label x={90} y={1002} size={20}>CALL-1   /   REPORTED AT SIMULATION SECOND 61</Label>
  </Field>;
}

function Alternatives({t}:{t:number}){
  // Order by strategy in the solver view. The subsequent desk is rank-ordered.
  const order=[2,0,1], ranked=tween(t,5.1,5.85);
  return <Field background={P.paper}><Section name="CANDIDATE PLANS" dark={false}/>
    <Text x={82} y={132} size={83} color={P.ink}>One call. Three feasible plans.</Text>
    <Text x={88} y={246} size={35} color="#44594f">Deterministic planning checks crew eligibility, locked work and equipment custody.</Text>
    {order.map((pi,i)=>{
      const p=decision.proposals[pi], call=assigned(p), x=88+i*597, sel=pi===0;
      const eta=Math.round(call.pickup_s!-decision.time);
      return <div key={pi} style={{...at(x,335),width:548,height:617,opacity:tween(t,.08*i,.55+i*.1),transform:'translateY('+tween(t,.08*i,.85+i*.1,54,0)+'px)'}}>
        <div style={{width:548,height:375,position:'relative',background:'#152d2d',overflow:'hidden'}}>
          <div style={{...at(0,0),width:548,height:375}}><GridMap compact option={pi} index={pi} progress={tween(t,.35+i*.26,2.9+i*.3)} vehicles={false}/></div>
          <div style={{...at(16,17),padding:'6px 11px',background:P.deep,color:P.paper,font:'23px '+mono}}>PLAN {p.option_id}</div>
          {sel&&<div style={{position:'absolute',bottom:0,right:0,background:P.amber,color:P.ink,padding:'9px 16px',font:'bold 20px '+mono,opacity:ranked}}>AI FIRST CHOICE</div>}
        </div>
        <Text x={0} y={400} size={46} color={P.ink} weight={600}>{strats[p.strategy]}</Text>
        <Text x={0} y={469} size={33} color="#3f534b">{call.vehicle_id} to call · {eta} s ETA</Text>
        <Text x={0} y={518} size={29} color="#506159">{p.metrics.reassigned_task_ids.length} existing jobs reassigned</Text>
        <Rule x={0} y={579} w={548} color={sel&&ranked>.5?P.rust:'#adb5a8'}/>
      </div>;
    })}
    <Label x={89} y={1008} size={20} color="#40564e">SOLVER ESTIMATES FROM THE DECISION POINT   /   ARCHIVED FIRST-GENERATION OPTIONS</Label>
  </Field>;
}

function Review({t}:{t:number}){
  const alt=t>.95&&t<2.55, press=tween(t,5.7,5.94), s=1.52;
  // Isolate the right-hand pane while keeping the adjacent map and route visible.
  const dx=930-1440*s,dy=-74;
  const px=dx+(button.x+button.width*.58)*s, py=dy+(button.y+button.height*.56)*s;
  return <Field>
    <div style={{...at(990,0),width:930,height:1080,overflow:'hidden',background:'#000'}}>
      <Img src={plate(alt?'alternative':'decision')} style={{...at(dx,dy),width:1440*s,height:810*s}}/>
      <svg width="52" height="60" viewBox="0 0 52 60" style={{...at(px+tween(t,3.8,5.5,156,0),py+tween(t,3.8,5.5,79,0)),opacity:tween(t,3.7,4.3),transform:'scale('+(1-.15*press)+')'}}><path d="M3 2 42 31 23 33 15 51Z" fill="#fff" stroke="#131c18" strokeWidth="3"/></svg>
      <div style={{...at(px-36,py-36),width:72,height:72,border:'3px solid white',borderRadius:'50%',transform:'scale('+(.3+press)+')',opacity:press>0?1-press:0}}/>
    </div>
    <Section name="RECOMMENDATION"/>
    <Label x={88} y={172} size={23} color={P.amber}>OPENAI DECISIONS  /  STORED ANSWER</Label>
    <Text x={82} y={244} size={86} width={836}>The AI ranks.</Text>
    <Text x={82} y={352} size={79} width={850} italic>The dispatcher decides.</Text>
    <div style={{...at(88,537),width:805}}>
      <div style={{font:'22px '+mono,letterSpacing:2,color:P.muted}}>MODEL PREFERENCE</div>
      <div style={{display:'flex',height:22,gap:4,margin:'22px 0 18px'}}>
        {decision.proposals.map((p,i)=><div key={p.option_id} style={{width:(decision.probabilities[p.option_id]*100)+'%',background:[P.amber,P.mint,'#b3a1bd'][i],transformOrigin:'left',transform:'scaleX('+tween(t,0,.9)+')'}}/>)}
      </div>
      <div style={{display:'flex',justifyContent:'space-between',font:'26px '+mono,color:P.paper}}>{decision.proposals.map(p=><span key={p.option_id}>{p.option_id} · {Math.round(decision.probabilities[p.option_id]*100)}%</span>)}</div>
    </div>
    <Rule x={88} y={780} w={805} color="#53695e"/>
    <Text x={88} y={816} size={36} width={820}>{alt?'Review another unit and its trade-offs.':t<3?'Compare the checked options.':'Approval rechecks the plan and its revision.'}</Text>
    <Label x={88} y={1003} size={19}>STAGED OPERATOR REVIEW  /  ARCHIVED OPTIONS</Label>
  </Field>;
}

function Execution({t}:{t:number}){
  const idx=Math.min(99,3+t*22);
  return <Field><div style={{...at(780,-130),width:1210,height:1270,transform:'scale(1.05)'}}><GridMap frameIndex={idx}/></div>
    <Section name="EXECUTION"/><Label x={90} y={221} color={P.mint}>PLAN B  /  APPROVED</Label>
    <Text x={82} y={311} size={87} width={680}>R1 takes the route.</Text>
    <Text x={89} y={553} size={38} width={600} color="#c9d7cb">SUMO moves the fleet along the approved itinerary.</Text>
    <Label x={90} y={898} size={23}>BENCHMARK REPLAY  /  22× SPEED</Label>
    <Text x={88} y={940} size={40} color={P.mint}>T + {Math.floor(decision.time+idx)} s</Text>
  </Field>;
}

export function DecisionStory(){
  const t=useTime();
  const beats=[0,5.5,12.9,20.3], which=t<beats[1]?0:t<beats[2]?1:t<beats[3]?2:3;
  const local=t-beats[which], components=[Intake,Alternatives,Review,Execution], Shot=components[which];
  const Previous=components[Math.max(0,which-1)];
  return <Field>{which>0&&local<.54&&<Previous t={beats[which]-beats[which-1]}/>}
    <AbsoluteFill style={{clipPath:which===0?undefined:'inset(0 0 '+(100-100*tween(local,0,.48))+'% 0)'}}><Shot t={local}/></AbsoluteFill>
  </Field>;
}
