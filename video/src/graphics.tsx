import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import raw from '../data/decision.json';

export const P = {ink:'#102627', deep:'#0a191b', paper:'#f3f0e7', light:'#faf8f0', rule:'#a6afa5', rust:'#dc6637', amber:'#ff8f00', mint:'#94dbb9', muted:'#a3b6ab'};
export const sans = 'Arial, sans-serif', serif = 'Georgia, serif', mono = 'Consolas, monospace';
export const useTime = () => useCurrentFrame()/useVideoConfig().fps;
export const tween = (t:number,a:number,b:number,from=0,to=1) => interpolate(t,[a,b],[from,to],{
  extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:Easing.bezier(.42,0,.18,1),
});
export const linear=(n:number)=>Math.min(1,Math.max(0,n));
export const at=(left:number,top:number):React.CSSProperties=>({position:'absolute',left,top});
export const Field=({children,background=P.ink}:{children:React.ReactNode,background?:string})=><AbsoluteFill style={{background,overflow:'hidden',color:P.paper,fontFamily:sans}}>{children}</AbsoluteFill>;
export const Label=({children,x,y,color=P.muted,size=22}:{children:React.ReactNode,x:number,y:number,color?:string,size?:number})=><div style={{...at(x,y),color,fontFamily:mono,fontSize:size,letterSpacing:1.5,whiteSpace:'nowrap'}}>{children}</div>;
export const Text=({children,x,y,size=64,color=P.paper,width,italic=false,weight=500}:{children:React.ReactNode,x:number,y:number,size?:number,color?:string,width?:number,italic?:boolean,weight?:number})=><div style={{...at(x,y),fontSize:size,color,width,fontFamily:italic?serif:sans,fontStyle:italic?'italic':'normal',fontWeight:weight,letterSpacing:size>58?'-0.055em':'-0.022em',lineHeight:1.04}}>{children}</div>;
export const Mark=({x,y,size=46,color=P.amber}:{x:number,y:number,size?:number,color?:string})=><svg style={at(x,y)} width={size} height={size} viewBox="0 0 64 64"><path fillRule="evenodd" d="M32 0 64 32 32 64 0 32ZM32 19 19 32 32 45 45 32Z" fill={color}/></svg>;
export const Rule=({x,y,w,color=P.rule}:{x:number,y:number,w:number,color?:string})=><div style={{...at(x,y),width:w,height:1,background:color}}/>;
export const Section=({name,dark=true}:{name:string,dark?:boolean})=><><Mark x={86} y={56} size={25} color={dark?P.amber:P.rust}/><Label x={128} y={55} color={dark?P.paper:P.ink} size={22}>RESCUEFLOW  /  {name}</Label></>;
export type Point=number[];
export type Frame={t:number,fleet:Record<string,Point>,traffic:Record<string,Point>};
type Proposal={strategy:string,option_id:string,routes:Record<string,string[]>,metrics:{reassigned_task_ids:string[]},assignments:{task_id:string,vehicle_id:string,pickup_s:number|null}[]};
type Decision={time:number,task:string,callNode:string,probabilities:Record<string,number>,proposals:Proposal[],committedRoutes:Record<string,string[]>,
  geometry:{edges:Record<string,{shape:Point[]}>,nodes:Record<string,Point>},frames:Frame[]};
export const decision=raw as unknown as Decision;
export const strats:Record<string,string>={urgent_first:'Urgent first',nearest_free_unit:'Nearest free unit',minimum_disruption:'Fewest changes'};
export const pointAt=(frames:Frame[],index:number,group:'fleet'|'traffic')=>{
  const pos=Math.max(0,Math.min(frames.length-1,index)), idx=Math.floor(pos), a=frames[idx][group], b=frames[Math.min(idx+1,frames.length-1)][group];
  return Object.entries(a).map(([id,xy])=>{const dest=b[id]??xy, f=pos-idx;
    return {id,x:xy[0]+(dest[0]-xy[0])*f,y:xy[1]+(dest[1]-xy[1])*f};});
};
export function drawPath(points:Point[]) {return points.map(([x,y],i)=>(i?'L':'M')+x.toFixed(1)+' '+(2400-y).toFixed(1)).join(' ');}
export const roads=Object.values(decision.geometry.edges).map(e=>drawPath(e.shape)).join(' ');
export const assigned=(p:Proposal)=>p.assignments.find(a=>a.task_id===decision.task)!;
export function routeToCall(p:Proposal) {
  const unit=assigned(p).vehicle_id, route=p.routes[unit]||[];
  const stop=route.findIndex(edge=>edge.endsWith(decision.callNode));
  return drawPath((stop<0?route:route.slice(0,stop+1)).flatMap(id=>decision.geometry.edges[id]?.shape??[]));
}
export const callXY=decision.geometry.nodes[decision.callNode];
export function GridMap({index=0,option=0,progress=1,vehicles=true,frameIndex=0,compact=false}:{index?:number,option?:number,progress?:number,vehicles?:boolean,frameIndex?:number,compact?:boolean}) {
  const p=decision.proposals[option], accent=[P.amber,P.mint,'#c4b8e0'][index%3];
  const first=decision.geometry.edges[p.routes[assigned(p).vehicle_id][0]].shape[0];
  return <svg viewBox={compact?'160 -310 2090 2140':'-190 -190 2780 2780'} width="100%" height="100%" style={{display:'block'}}>
    <rect x="-190" y="-190" width="2780" height="2780" fill="#152d2d"/>
    {Array.from({length:36},(_,i)=><rect key={i} x={(i%6)*400+36} y={Math.floor(i/6)*400+36} width={328} height={328} fill={(i*13)%7<3?'#1d3936':'#19332f'}/>)}
    <path d={roads} fill="none" stroke="#627b70" strokeWidth={compact?10:9} opacity=".8"/>
    <path d={routeToCall(p)} fill="none" stroke={accent} strokeWidth={compact?36:20} strokeLinejoin="round" pathLength={1} strokeDasharray={progress+' 2'}/>
    {compact && <g transform={'translate('+first[0]+' '+(2400-first[1])+')'}><rect x="-43" y="-43" width="86" height="86" fill={accent} stroke={P.ink} strokeWidth="13"/><text x="-62" y="38" textAnchor="end" fill={P.paper} style={{fontFamily:mono,fontSize:124,paintOrder:'stroke',stroke:P.ink,strokeWidth:16}}>{assigned(p).vehicle_id}</text></g>}
    {vehicles && pointAt(decision.frames,frameIndex,'traffic').map(v=><circle key={v.id} cx={v.x} cy={2400-v.y} r={9} fill="#a7afa4"/>)}
    {vehicles && pointAt(decision.frames,frameIndex,'fleet').map(v=><g key={v.id} transform={'translate('+v.x+' '+(2400-v.y)+')'}><rect x="-15" y="-15" width="30" height="30" fill={v.id===assigned(p).vehicle_id?accent:P.paper}/><text x="28" y="-14" fontFamily={mono} fontSize="62" fill={P.paper} stroke={P.ink} strokeWidth="9" paintOrder="stroke">{v.id}</text></g>)}
    <g transform={'translate('+callXY[0]+' '+(2400-callXY[1])+')'}><circle r={compact?73:57} fill={P.deep} stroke={accent} strokeWidth="9"/><path d="M-28 0H28M0 -28V28" stroke={accent} strokeWidth="12"/></g>
    {!compact&&<text x={callXY[0]+82} y={2400-callXY[1]-60} fill={P.paper} fontFamily={mono} fontSize={64}>CALL-1</text>}
  </svg>;
}
