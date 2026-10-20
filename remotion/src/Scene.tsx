import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, interpolate, random, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {clamp, ease} from './util';

/** Painted scene with a slow camera move (storybook animation). */
export const KenBurns: React.FC<{src: string; move: string; len: number}> = ({src, move, len}) => {
  const frame = useCurrentFrame();
  const t = ease(Math.min(1, frame / Math.max(1, len)));
  let scale = 1.0 + 0.14 * t;
  let x = 0;
  let y = 0;
  switch (move) {
    case 'zoom_out': scale = 1.16 - 0.14 * t; break;
    case 'pan_left': scale = 1.14; x = 6 - 12 * t; break;
    case 'pan_right': scale = 1.14; x = -6 + 12 * t; break;
    case 'rise': scale = 1.14; y = 5 - 10 * t; break;
    case 'push_focus': scale = 1.0 + 0.22 * t; break;
    // close-ups on one part of the painting: a different camera angle on a reused picture
    case 'detail_left': scale = 1.7 + 0.08 * t; x = 14 - 3 * t; break;
    case 'detail_right': scale = 1.7 + 0.08 * t; x = -14 + 3 * t; break;
    case 'detail_top': scale = 1.7 + 0.08 * t; y = 12 - 3 * t; break;
  }
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <Img
        src={staticFile(src)}
        style={{
          width: '100%', height: '100%', objectFit: 'cover',
          transform: `scale(${scale}) translate(${x}%, ${y}%)`,
          filter: 'saturate(1.08) contrast(1.05)',
        }}
      />
    </AbsoluteFill>
  );
};

/** Pre-rendered 3D camera move as numbered JPEG frames (cheaper to render than a video). */
export const FrameSeq: React.FC<{dir: string; count: number}> = ({dir, count}) => {
  const frame = useCurrentFrame();
  const i = Math.min(count - 1, Math.max(0, frame));
  return (
    <AbsoluteFill>
      <Img src={staticFile(`${dir}/${String(i).padStart(5, '0')}.jpg`)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
    </AbsoluteFill>
  );
};

/** Real filmed VFX (smoke, fog, sparks, light leaks on black) blended over the picture. */
export const VfxLayer: React.FC<{src: string; opacity: number; seed: string; fadeOut?: number}> = ({src, opacity, seed, fadeOut}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const start = Math.floor(random(seed) * 5 * fps);   // different part of the clip each time
  const inO = interpolate(frame, [0, fps * 0.3], [0, 1], clamp);
  const outO = fadeOut ? interpolate(frame, [fadeOut * 0.4, fadeOut], [1, 0], clamp) : 1;
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen', opacity: opacity * inO * outO, pointerEvents: 'none'}}>
      <OffthreadVideo src={staticFile(src)} muted startFrom={start} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
    </AbsoluteFill>
  );
};

/** The story's biggest moment: a decaying camera shake plus a white flash. */
export const shake = (frame: number, fps: number, seed: string) => {
  const k = Math.max(0, 1 - frame / (fps * 0.6));
  const a = 26 * k * k;
  return {transform: `translate(${(random(`${seed}x${frame}`) - 0.5) * a}px, ${(random(`${seed}y${frame}`) - 0.5) * a}px) scale(${1 + 0.04 * k})`};
};

export const Flash: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const o = interpolate(frame, [0, 2, fps * 0.35], [0, 0.85, 0], clamp);
  return <AbsoluteFill style={{background: '#fff', opacity: o, pointerEvents: 'none'}} />;
};

export const ClipVideo: React.FC<{src: string}> = ({src}) => (
  <AbsoluteFill>
    <OffthreadVideo src={staticFile(src)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
  </AbsoluteFill>
);

const COLORS: Record<string, string> = {
  fireflies: '255,236,140', sparkles: '255,255,230', dust: '255,240,210', leaves: '205,150,60',
  snow: '255,255,255', embers: '255,140,40', rain: '200,215,255',
};

/** Drifting light particles: fireflies, sparkles, dust, leaves, rain, snow, embers. */
export const Particles: React.FC<{kind: string; seed: string}> = ({kind, seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  if (!kind || kind === 'none' || !COLORS[kind]) return null;
  const n = {fireflies: 34, sparkles: 40, dust: 55, leaves: 14, rain: 90, snow: 70, embers: 40}[kind] ?? 30;
  const t = frame / fps;
  const s = width / 1920;
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      {new Array(n).fill(0).map((_, i) => {
        const r = (k: string) => random(`${seed}-${kind}-${i}-${k}`);
        const size = (kind === 'rain' ? 2 : kind === 'leaves' ? 14 : 4 + r('s') * 10) * s * (kind === 'dust' ? 0.7 : 1);
        let vx = (r('vx') - 0.5) * 30, vy = (r('vy') - 0.6) * 20;
        if (kind === 'leaves') { vx = 40 + r('vx') * 30; vy = 60 + r('vy') * 50; }
        if (kind === 'snow') { vy = 40 + r('vy') * 50; }
        if (kind === 'rain') { vx = -60; vy = 1600 + r('vy') * 400; }
        if (kind === 'embers') { vy = -(60 + r('vy') * 70); }
        const x = (((r('x') * width + vx * s * t + Math.sin(t * 0.9 + r('p') * 6) * 14 * s) % width) + width) % width;
        const y = (((r('y') * height + vy * s * t) % height) + height) % height;
        const tw = kind === 'fireflies' || kind === 'sparkles' ? 0.45 + 0.55 * Math.abs(Math.sin(t * (kind === 'sparkles' ? 2.6 : 1.3) + r('p') * 9)) : 0.7;
        const c = COLORS[kind];
        const common: React.CSSProperties = {position: 'absolute', left: x, top: y, opacity: tw};
        if (kind === 'rain') {
          return <div key={i} style={{...common, width: 2 * s, height: 34 * s, background: `rgba(${c},0.45)`, transform: 'rotate(8deg)'}} />;
        }
        if (kind === 'leaves') {
          return <div key={i} style={{...common, width: size, height: size * 0.55, borderRadius: '60% 0', background: `rgba(${c},0.85)`, transform: `rotate(${t * 90 + r('a') * 360}deg)`}} />;
        }
        return (
          <div key={i} style={{...common, width: size, height: size, borderRadius: '50%',
            background: `radial-gradient(circle, rgba(${c},1) 0%, rgba(${c},0.35) 45%, rgba(${c},0) 72%)`,
            boxShadow: kind === 'fireflies' ? `0 0 ${size * 2}px rgba(${c},0.8)` : undefined}} />
        );
      })}
    </AbsoluteFill>
  );
};

export const Vignette: React.FC = () => (
  <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,0.42) 100%)', pointerEvents: 'none'}} />
);

/** How a new shot arrives over the previous one during the transition window. */
export const entrance = (kind: string, frame: number, xf: number): React.CSSProperties => {
  if (xf <= 0) return {};
  const p = ease(interpolate(frame, [0, xf], [0, 1], clamp));
  switch (kind) {
    case 'slide-left': return {transform: `translateX(${(1 - p) * 100}%)`};
    case 'slide-right': return {transform: `translateX(${-(1 - p) * 100}%)`};
    case 'slide-up': return {transform: `translateY(${(1 - p) * 100}%)`};
    case 'zoom': return {transform: `scale(${1.35 - 0.35 * p})`, opacity: p};
    case 'wipe': return {clipPath: `inset(0 ${(1 - p) * 100}% 0 0)`};
    case 'circle': return {clipPath: `circle(${p * 75}% at 50% 50%)`};
    default: return {opacity: p};
  }
};

/** Warm light leak drifting across the frame now and then, plus fine film grain. */
export const FilmLook: React.FC<{seed: string; grain: string | null}> = ({seed, grain}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const leak = Math.max(0, Math.sin(frame / 70 + random(seed) * 6));
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <AbsoluteFill style={{opacity: 0.18 * leak, mixBlendMode: 'screen',
        background: `radial-gradient(circle at ${20 + 60 * ((frame / 300) % 1)}% 20%, rgba(255,170,80,0.9), rgba(255,170,80,0) 45%)`}} />
      {grain ? <Img src={staticFile(grain)} style={{position: 'absolute', width: width * 1.2, height: height * 1.2, opacity: 0.07,
        mixBlendMode: 'overlay', left: -((frame * 37) % (width * 0.2)), top: -((frame * 53) % (height * 0.2))}} /> : null}
    </AbsoluteFill>
  );
};
