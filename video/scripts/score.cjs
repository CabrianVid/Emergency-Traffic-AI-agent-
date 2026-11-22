// Original cue in D minor. All pitched voices, transients and ambience are
// synthesized here. A stereo delay and hard-coded cue sheet follow picture.
const fs = require('node:fs');
exports.makeScore = function makeScore(file) {
  const hz = 44100, duration = 100, n = hz * duration;
  const left = new Float32Array(n), right = new Float32Array(n);
  const tau = 2 * Math.PI;
  function voice(start, length, freq, level, type, pan=0) {
    const begin=Math.round(start*hz), end=Math.min(n,begin+Math.ceil(length*hz));
    for (let i=begin; i<end; i++) {
      const t=(i-begin)/hz, p=tau*freq*t;
      const release=Math.min(1,(end-i)/(hz*.035));
      let s;
      if(type==='bed') {
        const env=Math.min(1,t/.8,(length-t)/.9);
        s=env*(.72*Math.sin(p)+.18*Math.sin(p*1.003)+.1*Math.sin(2*p));
      } else if(type==='kick') {
        s=Math.sin(p+12*(1-Math.exp(-t*20)))*Math.exp(-t*11);
      } else if(type==='tick') {
        s=(Math.sin(p)+.3*Math.sin(p*1.54)) * Math.exp(-t*45)*Math.min(1,t*380);
      } else {
        s=Math.min(1,t*70)*Math.exp(-t*2.5)*(Math.sin(p)+.26*Math.sin(p*2.001)*Math.exp(-t*5)+.10*Math.sin(p*3));
      }
      const v=s*level*release;
      left[i]+=v*(1-pan)*.7; right[i]+=v*(1+pan)*.7;
    }
  }
  for(let t=0;t<11.5;) {
    voice(t,.24,460+t*16,.095,'tick',.3*Math.sin(t));
    voice(t,.54,41,.16,'kick'); t+=1.05-.064*t;
  }
  const midi=m=>440*2**((m-69)/12);
  const chords=[[50,57,60,64],[46,53,57,60],[48,55,58,62],[45,52,55,62]];
  for(let bar=0;bar<21;bar++) {
    const start=12+4*bar,chord=chords[bar%4], quiet=start>=54&&start<64;
    chord.forEach((note,i)=>voice(start,4.65,midi(note-12),quiet?.056:.078,'bed',(i-1.5)*.18));
    const step=start>=64&&start<76?.25:.5;
    for(let j=0;j<4/step;j++){
      if(quiet&&j%3!==0)continue;
      const i=[0,2,1,3,1,0,3,2][j%8];
      voice(start+j*step,1.2,midi(chord[i]+(j%4===3?12:0)),quiet?.06:.095,'key',j%2?.32:-.30);
      if(j%4===0&&!quiet)voice(start+j*step,.6,43,.12,'kick');
    }
  }
  for(const t of [12,21,26,35,41.6,46,56,64,76,95])voice(t,1.5,73.416,.21,'kick');
  [50,57,62,65,69].forEach((m,i)=>voice(95+i*.18,4.65-i*.18,midi(m),.13,'bed',(i-2)*.15));
  const b=Buffer.alloc(44+n*4);
  b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);
  b.writeUInt16LE(1,20);b.writeUInt16LE(2,22);b.writeUInt32LE(hz,24);b.writeUInt32LE(4*hz,28);
  b.writeUInt16LE(4,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(n*4,40);
  const delay=Math.round(hz*.3125);
  for(let i=0;i<n;i++) {
    const a=left[i]+(i>delay?.22*right[i-delay]:0), c=right[i]+(i>delay?.22*left[i-delay]:0);
    const envelope=Math.min(1,i/(hz*.06),(n-i)/(hz*.9));
    b.writeInt16LE(Math.round(32765*Math.tanh(a*1.65)*envelope),44+4*i);
    b.writeInt16LE(Math.round(32765*Math.tanh(c*1.65)*envelope),46+4*i);
  }
  fs.writeFileSync(file,b);
};
