import React, {useMemo} from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {geoMercator, geoPath} from 'd3-geo';
import {feature} from 'topojson-client';
import world from 'world-atlas/countries-50m.json';
import type {Graphic} from '../types';
import {INK, RED, YELLOW, clamp, fadeInOut, fontFor, pop} from '../util';

type Fonts = {display: string; hindi: string; regional?: string};
type GP<T> = {g: T; len: number; fonts: Fonts; short: boolean};

const stroke = (px: number) => ({WebkitTextStroke: `${px}px #000`, paintOrder: 'stroke fill' as const});

/** Big year/date stamp that types in, with a red underline that draws itself. */
const DateStamp: React.FC<GP<Extract<Graphic, {type: 'date'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const chars = Math.round(interpolate(frame, [4, 4 + g.text.length * 3], [0, g.text.length], clamp));
  const size = width * (short ? 0.15 : 0.075);
  const line = interpolate(frame, [10, 28], [0, 100], clamp);
  return (
    <AbsoluteFill style={{padding: short ? '14% 7%' : '5% 5%', opacity: fadeInOut(frame, len)}}>
      <div style={{fontFamily: fontFor(g.text, fonts), fontSize: size, color: YELLOW, lineHeight: 1, ...stroke(size * 0.06),
        textShadow: '0 8px 30px rgba(0,0,0,0.6)', transform: `scale(${0.85 + 0.15 * pop(frame, fps)})`, transformOrigin: 'left'}}>
        {g.text.slice(0, chars)}
      </div>
      <div style={{height: size * 0.08, width: `${line * (short ? 0.7 : 0.25)}%`, background: RED, marginTop: size * 0.08}} />
      {g.sub ? <div style={{fontFamily: fontFor(g.sub, fonts), fontSize: size * 0.32, color: '#fff', marginTop: size * 0.12,
        opacity: interpolate(frame, [16, 26], [0, 1], clamp), ...stroke(size * 0.02)}}>{g.sub}</div> : null}
    </AbsoluteFill>
  );
};

/** Number rolling up to its value, with a label. */
const Counter: React.FC<GP<Extract<Graphic, {type: 'counter'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const v = interpolate(frame, [4, 4 + fps * 1.2], [0, g.value], {...clamp, easing: (x) => 1 - Math.pow(1 - x, 3)});
  const shown = Math.round(v).toLocaleString('en-IN');
  const size = width * (short ? 0.2 : 0.1);
  return (
    <AbsoluteFill style={{justifyContent: short ? 'flex-start' : 'center', alignItems: short ? 'center' : 'flex-end',
      padding: short ? '16% 0 0 0' : '0 7% 0 0', opacity: fadeInOut(frame, len)}}>
      <div style={{textAlign: 'center', transform: `scale(${0.7 + 0.3 * pop(frame, fps)})`}}>
        <div style={{fontFamily: fonts.display, fontSize: size, color: YELLOW, lineHeight: 1, ...stroke(size * 0.05),
          textShadow: '0 10px 40px rgba(0,0,0,0.7)'}}>{g.prefix ?? ''}{shown}{g.suffix ?? ''}</div>
        <div style={{display: 'inline-block', marginTop: size * 0.08, padding: `${size * 0.04}px ${size * 0.14}px`, background: RED,
          color: '#fff', fontFamily: fontFor(g.label, fonts), fontSize: size * 0.24, borderRadius: size * 0.05}}>{g.label}</div>
      </div>
    </AbsoluteFill>
  );
};

/** World map card that zooms from a wide view onto the location, with a pulsing pin. */
const MapCard: React.FC<GP<Extract<Graphic, {type: 'map'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const W = short ? width * 0.86 : width * 0.42;
  const H = short ? W * 0.75 : W * 0.66;
  const {paths, pin} = useMemo(() => {
    const countries = feature(world as any, (world as any).objects.countries) as any;
    // Fixed projection centred on the place; the zoom is a cheap CSS transform on top.
    const proj = geoMercator().center([g.lon, g.lat]).scale(W * 0.55).translate([W / 2, H / 2]);
    const path = geoPath(proj);
    return {paths: countries.features.map((f: any) => path(f) ?? ''), pin: proj([g.lon, g.lat]) ?? [W / 2, H / 2]};
  }, [g.lat, g.lon, W, H]);
  const z = interpolate(frame, [6, 6 + fps * 1.6], [1, 3.0], {...clamp, easing: (x) => 1 - Math.pow(1 - x, 3)});
  const appear = pop(frame, fps);
  const pulse = 1 + 0.35 * Math.abs(Math.sin(frame / 6));
  const label = g.label || g.place;
  return (
    <AbsoluteFill style={{justifyContent: short ? 'flex-start' : 'center', alignItems: short ? 'center' : 'flex-end',
      padding: short ? '12% 0 0 0' : '0 4% 0 0', opacity: fadeInOut(frame, len)}}>
      <div style={{width: W, height: H, borderRadius: 24, overflow: 'hidden', position: 'relative', background: '#0d2b45',
        boxShadow: '0 20px 60px rgba(0,0,0,0.55)', border: `6px solid ${YELLOW}`, transform: `scale(${0.8 + 0.2 * appear})`}}>
        <svg width={W} height={H} style={{transform: `scale(${z})`, transformOrigin: `${pin[0]}px ${pin[1]}px`}}>
          {paths.map((d: string, i: number) => (
            <path key={i} d={d} fill="#e9d8a6" stroke="#7a5c2e" strokeWidth={1.2 / z} />
          ))}
        </svg>
        <div style={{position: 'absolute', left: W / 2 - 14, top: H / 2 - 14, width: 28, height: 28, borderRadius: '50%',
          background: RED, border: '4px solid #fff', transform: `scale(${interpolate(frame, [fps * 1.2, fps * 1.5], [0, 1], clamp)})`,
          boxShadow: `0 0 0 ${10 * pulse}px rgba(229,18,27,0.25)`}} />
        <div style={{position: 'absolute', left: 0, right: 0, bottom: 16, textAlign: 'center',
          opacity: interpolate(frame, [fps * 1.4, fps * 1.7], [0, 1], clamp)}}>
          <span style={{background: INK, color: '#fff', padding: '8px 20px', borderRadius: 12, fontFamily: fontFor(label, fonts),
            fontSize: short ? width * 0.05 : width * 0.022}}>📍 {label}</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

/** Lower-third name tag sliding in from the left. */
const NameTag: React.FC<GP<Extract<Graphic, {type: 'name'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const p = pop(frame, fps, 0, 16);
  const size = width * (short ? 0.06 : 0.03);
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', padding: short ? `0 0 ${height * 0.42}px 6%` : `0 0 ${height * 0.2}px 4%`,
      opacity: fadeInOut(frame, len)}}>
      <div style={{transform: `translateX(${(p - 1) * 120}%)`}}>
        <div style={{display: 'inline-block', background: YELLOW, color: INK, fontFamily: fontFor(g.name, fonts), fontSize: size,
          padding: `${size * 0.12}px ${size * 0.45}px`, borderLeft: `${size * 0.25}px solid ${RED}`}}>{g.name}</div>
        {g.role ? <div><div style={{display: 'inline-block', background: INK, color: '#fff', fontFamily: fontFor(g.role, fonts),
          fontSize: size * 0.62, padding: `${size * 0.08}px ${size * 0.45}px`}}>{g.role}</div></div> : null}
      </div>
    </AbsoluteFill>
  );
};

/** Quote card with big quote marks. */
const QuoteCard: React.FC<GP<Extract<Graphic, {type: 'quote'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const size = width * (short ? 0.055 : 0.03);
  return (
    <AbsoluteFill style={{justifyContent: short ? 'flex-start' : 'center', alignItems: 'center',
      padding: short ? '18% 6% 0' : '0', opacity: fadeInOut(frame, len)}}>
      <div style={{maxWidth: short ? '100%' : '60%', background: 'rgba(11,11,16,0.82)', borderRadius: 28, padding: size,
        borderTop: `8px solid ${YELLOW}`, transform: `scale(${0.85 + 0.15 * pop(frame, fps)})`, textAlign: 'center'}}>
        <div style={{fontFamily: 'Georgia, serif', fontSize: size * 2.4, color: YELLOW, lineHeight: 0.6}}>“</div>
        <div style={{fontFamily: fontFor(g.text, fonts), fontSize: size, color: '#fff', lineHeight: 1.35}}>{g.text}</div>
        {g.by ? <div style={{fontFamily: fontFor(g.by, fonts), fontSize: size * 0.7, color: YELLOW, marginTop: size * 0.5}}>— {g.by}</div> : null}
      </div>
    </AbsoluteFill>
  );
};

/** Horizontal timeline drawing itself, points popping in one by one. */
const Timeline: React.FC<GP<Extract<Graphic, {type: 'timeline'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const pts = g.points.slice(0, 4);
  const draw = interpolate(frame, [4, 4 + fps], [0, 100], clamp);
  const size = width * (short ? 0.045 : 0.022);
  return (
    <AbsoluteFill style={{justifyContent: short ? 'flex-start' : 'flex-start', padding: short ? '16% 6% 0' : '6% 8% 0',
      opacity: fadeInOut(frame, len)}}>
      <div style={{position: 'relative', height: size * 6, background: 'rgba(11,11,16,0.7)', borderRadius: 20, padding: '0 4%'}}>
        <div style={{position: 'absolute', left: '6%', top: '50%', height: 6, width: `${draw * 0.88}%`, background: YELLOW}} />
        {pts.map((p, i) => {
          const at = 6 + (88 * (pts.length === 1 ? 0.5 : i / (pts.length - 1)));
          const s = pop(frame, fps, 8 + i * 10);
          return (
            <div key={i} style={{position: 'absolute', left: `${at}%`, top: '50%', transform: `translate(-50%,-50%) scale(${s})`, textAlign: 'center'}}>
              <div style={{fontFamily: fonts.display, fontSize: size * 1.25, color: YELLOW, ...stroke(2), marginBottom: size * 0.4}}>{p.year}</div>
              <div style={{width: size * 0.9, height: size * 0.9, borderRadius: '50%', background: RED, border: '4px solid #fff', margin: '0 auto'}} />
              <div style={{fontFamily: fontFor(p.label, fonts), fontSize: size * 0.8, color: '#fff', marginTop: size * 0.4, maxWidth: size * 8}}>{p.label}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

/** Kinetic key phrase: words punch in one after another on a highlight band. */
const Fact: React.FC<GP<Extract<Graphic, {type: 'fact'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const words = g.text.split(/\s+/).filter(Boolean).slice(0, 8);
  const size = width * (short ? 0.085 : 0.045);
  return (
    <AbsoluteFill style={{justifyContent: 'flex-start', alignItems: 'center', padding: short ? '15% 5% 0' : '6% 6% 0', opacity: fadeInOut(frame, len)}}>
      <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: `${size * 0.1}px ${size * 0.25}px`,
        background: 'rgba(11,11,16,0.55)', padding: `${size * 0.15}px ${size * 0.4}px`, borderRadius: size * 0.25}}>
        {words.map((w, i) => {
          const s = pop(frame, fps, 3 + i * 4, 11);
          return <span key={i} style={{fontFamily: fontFor(w, fonts), fontSize: size, color: i % 3 === 1 ? YELLOW : '#fff',
            display: 'inline-block', transform: `scale(${s}) translateY(${(1 - s) * 30}px)`, ...stroke(size * 0.05)}}>{w}</span>;
        })}
      </div>
    </AbsoluteFill>
  );
};

export const GraphicLayer: React.FC<{g: Graphic; len: number; fonts: Fonts; short: boolean}> = (p) => {
  switch (p.g.type) {
    case 'date': return <DateStamp {...(p as any)} />;
    case 'counter': return <Counter {...(p as any)} />;
    case 'map': return <MapCard {...(p as any)} />;
    case 'name': return <NameTag {...(p as any)} />;
    case 'quote': return <QuoteCard {...(p as any)} />;
    case 'timeline': return <Timeline {...(p as any)} />;
    case 'fact': return <Fact {...(p as any)} />;
    default: return null;
  }
};
