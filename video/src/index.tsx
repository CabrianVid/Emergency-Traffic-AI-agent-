import React from 'react';
import {AbsoluteFill, Audio, Composition, registerRoot, Sequence, staticFile, useVideoConfig} from 'remotion';
import {Legacy, Signals, People, Closing} from './Interludes';
import {Introduction, DecisionStory} from './DecisionFilm';
import {Evidence, Mosaic} from './Evaluation';

function Film() {
  const {fps}=useVideoConfig();
  return <AbsoluteFill style={{background:'#102627'}}>
    <Sequence name="Dispatch under pressure" durationInFrames={12*fps}><Legacy/></Sequence>
    <Sequence name="Ljubljana operator desk" from={12*fps} durationInFrames={9*fps}><Introduction/></Sequence>
    <Sequence name="From a call to an approved route" from={21*fps} durationInFrames={25*fps}><DecisionStory/></Sequence>
    <Sequence name="Inside the SUMO junction" from={46*fps} durationInFrames={10*fps}><Signals/></Sequence>
    <Sequence name="Practitioner input" from={56*fps} durationInFrames={8*fps}><People/></Sequence>
    <Sequence name="The simulation wall" from={64*fps} durationInFrames={12*fps}><Mosaic/></Sequence>
    <Sequence name="Paired response times" from={76*fps} durationInFrames={19*fps}><Evidence/></Sequence>
    <Sequence name="Closing" from={95*fps} durationInFrames={5*fps}><Closing/></Sequence>
    <Audio src={staticFile('generated/score.wav')}/>
  </AbsoluteFill>;
}
const Root=()=> <Composition id="RescueFlow" component={Film} durationInFrames={3000} fps={30} width={1920} height={1080}/>;
registerRoot(Root);
