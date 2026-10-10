import React from 'react';
import {Composition} from 'remotion';
import {Main} from './Video';
import type {Props} from './types';

const defaults: Props = {
  width: 1920, height: 1080, fps: 30, total: 5, lang: 'hi', short: false, shots: [],
  transitionSeconds: 0.35, captionsOn: true, fonts: {display: 'Anton', hindi: 'Mukta'}, logo: null, endCard: null,
};

export const Root: React.FC = () => (
  <Composition
    id="Main"
    component={Main as unknown as React.FC<Record<string, unknown>>}
    defaultProps={defaults as unknown as Record<string, unknown>}
    width={1920}
    height={1080}
    fps={30}
    durationInFrames={150}
    calculateMetadata={({props}) => {
      const p = props as unknown as Props;
      return {width: p.width, height: p.height, fps: p.fps, durationInFrames: Math.max(1, Math.ceil(p.total * p.fps))};
    }}
  />
);
