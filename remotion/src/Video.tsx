import React from 'react';
import {AbsoluteFill, Img, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Captions} from './Captions';
import {GraphicLayer} from './graphics/Graphics';
import {ClipVideo, FilmLook, KenBurns, Particles, Vignette, entrance} from './Scene';
import type {Props, Shot} from './types';
import {RED, YELLOW, clamp, fontFor, pop} from './util';

const FontFaces: React.FC<{fonts: Props['fonts']}> = ({fonts}) => (
  <style>{`
    @font-face { font-family: '${fonts.display}'; src: url('${staticFile('fonts/Anton-Regular.ttf')}'); }
    @font-face { font-family: '${fonts.hindi}'; src: url('${staticFile('fonts/Mukta-ExtraBold.ttf')}'); }
  `}</style>
);

const ShotView: React.FC<{shot: Shot; len: number; xf: number; index: number; props: Props}> = ({shot, len, xf, index, props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const narrationFrames = Math.round(shot.dur * fps);
  return (
    <AbsoluteFill style={index === 0 ? {} : entrance(shot.transition, frame, xf)}>
      {shot.video ? <ClipVideo src={shot.video} />
        : shot.image ? <KenBurns src={shot.image} move={shot.move} len={len} />
        : <AbsoluteFill style={{background: '#101418'}} />}
      <Particles kind={shot.ambient} seed={`shot-${index}`} />
      <Vignette />
      {shot.graphic ? (
        <Sequence from={Math.round(fps * 0.25)} durationInFrames={Math.max(1, narrationFrames - Math.round(fps * 0.25))} layout="none">
          <GraphicLayer g={shot.graphic} len={Math.max(1, narrationFrames - Math.round(fps * 0.25))} fonts={props.fonts} short={props.short} />
        </Sequence>
      ) : null}
      {props.captionsOn ? (
        <Sequence durationInFrames={narrationFrames} layout="none">
          <Captions captions={shot.captions} short={props.short} fonts={props.fonts} />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
};

/** Animated subscribe card over the last seconds of long videos. */
const EndCard: React.FC<{title: string; button: string; fonts: Props['fonts']}> = ({title, button, fonts}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const p = pop(frame, fps, 0, 12);
  const press = interpolate(frame, [fps * 1.4, fps * 1.55, fps * 1.7], [1, 0.9, 1], clamp);
  const size = width * 0.03;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: '9%'}}>
      <div style={{transform: `translateY(${(1 - p) * 140}%)`, display: 'flex', alignItems: 'center', gap: size,
        background: 'rgba(11,11,16,0.85)', padding: `${size * 0.5}px ${size}px`, borderRadius: size, border: `4px solid ${YELLOW}`}}>
        <span style={{fontFamily: fontFor(title, fonts), fontSize: size, color: '#fff'}}>{title}</span>
        <span style={{fontFamily: fontFor(button, fonts), fontSize: size, color: '#fff', background: RED, padding: `${size * 0.2}px ${size * 0.7}px`,
          borderRadius: size * 0.3, transform: `scale(${press})`, display: 'inline-block'}}>🔔 {button}</span>
      </div>
    </AbsoluteFill>
  );
};

export const Main: React.FC<Props> = (props) => {
  const {fps} = useVideoConfig();
  const xf = Math.round(props.transitionSeconds * fps);
  return (
    <AbsoluteFill style={{background: '#000'}}>
      <FontFaces fonts={props.fonts} />
      {props.shots.map((shot, i) => {
        const from = Math.round(shot.start * fps);
        const isLast = i === props.shots.length - 1;
        const len = Math.round(shot.dur * fps) + (isLast ? 0 : xf);
        return (
          <Sequence key={i} from={from} durationInFrames={Math.max(1, len)}>
            <ShotView shot={shot} len={len} xf={xf} index={i} props={props} />
          </Sequence>
        );
      })}
      <FilmLook seed="film" grain={props.grain ?? null} />
      {props.logo ? (
        <AbsoluteFill style={{alignItems: 'flex-end', padding: props.short ? '4% 5%' : '2.2% 2.4%', pointerEvents: 'none'}}>
          <Img src={staticFile(props.logo)} style={{width: props.short ? 120 : 92, height: props.short ? 120 : 92, opacity: 0.85, borderRadius: '50%'}} />
        </AbsoluteFill>
      ) : null}
      {props.endCard && !props.short ? (
        <Sequence from={Math.max(0, Math.round((props.total - 6) * fps))}>
          <EndCard title={props.endCard.title} button={props.endCard.button} fonts={props.fonts} />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
};
