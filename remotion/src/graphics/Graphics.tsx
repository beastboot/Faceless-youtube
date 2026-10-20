import React, {useMemo} from 'react';
import {AbsoluteFill, interpolate, random, useCurrentFrame, useVideoConfig} from 'remotion';
import {geoMercator, geoPath} from 'd3-geo';
import {feature} from 'topojson-client';
import world from 'world-atlas/countries-50m.json';
import type {Graphic} from '../types';
import {INK, RED, YELLOW, clamp, fadeInOut, fontFor, pop} from '../util';

type Fonts = {display: string; hindi: string; regional?: string};
type GP<T> = {g: T; len: number; fonts: Fonts; short: boolean};

const stroke = (px: number) => ({WebkitTextStroke: `${px}px #000`, paintOrder: 'stroke fill' as const});

/** Cinematic year reveal: golden dust gathers into the year in the centre of a darkened frame,
 * glows, then the year shrinks into the top-left corner and stays there while the shot plays. */
const DateStamp: React.FC<GP<Extract<Graphic, {type: 'date'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const hold = Math.min(Math.round(fps * 1.5), Math.max(1, len - fps));       // centre stage time
  const gather = interpolate(frame, [0, fps * 0.8], [0, 1], {...clamp, easing: (x) => 1 - Math.pow(1 - x, 3)});
  const dock = interpolate(frame, [hold, hold + fps * 0.45], [0, 1], {...clamp, easing: (x) => x * x * (3 - 2 * x)});
  // fit long dates ("May 8, 1902") to the frame: Anton glyphs are about half an em wide
  const em = Math.max(4, g.text.length) * 0.5;
  const big = Math.min(width * (short ? 0.24 : 0.14), (width * 0.9) / em);
  const small = Math.min(width * (short ? 0.11 : 0.055), (width * 0.6) / em);
  const size = big + (small - big) * dock;
  const cx = width / 2 + ((short ? width * 0.07 : width * 0.05) + 0 - width / 2) * dock;
  const cy = height / 2 + ((short ? height * 0.16 : height * 0.1) - height / 2) * dock;
  const scrim = 0.62 * (1 - dock) * interpolate(frame, [0, 6], [0, 1], clamp);
  const n = 70;
  return (
    <AbsoluteFill style={{opacity: fadeInOut(frame, len, 1, 8), pointerEvents: 'none'}}>
      <AbsoluteFill style={{background: `rgba(0,0,0,${scrim})`}} />
      {dock < 1 ? new Array(n).fill(0).map((_, i) => {
        // dust grains fly from scattered points into the text, then sparkle out
        const r = (k: string) => random(`yr-${g.text}-${i}-${k}`);
        const sx = (r('x') - 0.5) * width * 0.9, sy = (r('y') - 0.5) * height * 0.8;
        const tx = (r('tx') - 0.5) * size * g.text.length * 0.5, ty = (r('ty') - 0.5) * size * 0.6;
        const x = width / 2 + sx + (tx - sx) * gather, y = height / 2 + sy + (ty - sy) * gather;
        const o = (1 - dock) * (gather < 1 ? 0.9 : 0.6 * Math.abs(Math.sin(frame / 4 + i)));
        const d = (2 + r('s') * 5) * (width / 1920);
        return <div key={i} style={{position: 'absolute', left: x, top: y, width: d, height: d, borderRadius: '50%', opacity: o,
          background: '#ffe9a8', boxShadow: `0 0 ${d * 3}px #ffcc4d`}} />;
      }) : null}
      <div style={{position: 'absolute', left: cx, top: cy - size * 0.55, transform: `translateX(${-50 * (1 - dock)}%)`, whiteSpace: 'nowrap'}}>
        <div style={{fontFamily: fontFor(g.text, fonts), fontSize: size, lineHeight: 1, color: YELLOW, opacity: gather,
          filter: `blur(${(1 - gather) * 12}px)`, ...stroke(size * 0.035),
          textShadow: `0 0 ${size * 0.25 * (1 - dock * 0.7)}px rgba(255,190,60,0.85), 0 8px 30px rgba(0,0,0,0.6)`}}>{g.text}</div>
        {g.sub ? <div style={{fontFamily: fontFor(g.sub, fonts), fontSize: Math.max(size * 0.26, small * 0.34), color: '#fff',
          marginTop: size * 0.06, opacity: interpolate(frame, [fps * 0.6, fps * 0.9], [0, 1], clamp), ...stroke(2),
          textAlign: dock > 0.5 ? 'left' : 'center'}}>{g.sub}</div> : null}
      </div>
    </AbsoluteFill>
  );
};

/** One to three words in bold yellow that punch in near the top-left (SeeKen-style key word). */
const Keyword: React.FC<GP<Extract<Graphic, {type: 'keyword'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const p = pop(frame, fps, 0, 9);
  const size = width * (short ? 0.11 : 0.06);
  const wobble = Math.sin(frame / 9) * 1.2;
  return (
    <AbsoluteFill style={{padding: short ? '17% 7% 0' : '4.5% 5% 0', opacity: fadeInOut(frame, len, 1, 6),
      alignItems: short ? 'center' : 'flex-start'}}>
      <div style={{fontFamily: fontFor(g.text, fonts), fontSize: size, color: YELLOW, lineHeight: 1.05, ...stroke(size * 0.06),
        textShadow: '0 6px 0 rgba(0,0,0,0.55), 0 12px 40px rgba(0,0,0,0.6)',
        transform: `scale(${0.3 + 0.7 * p}) rotate(${wobble - 2}deg)`, transformOrigin: short ? 'center' : 'left center'}}>{g.text}</div>
    </AbsoluteFill>
  );
};

/** Short labels that pop in around the subject, each with a dot and a hairline pointing inwards. */
const SLOTS_WIDE = [[0.06, 0.2], [0.72, 0.16], [0.07, 0.62], [0.73, 0.6]];
const SLOTS_TALL = [[0.08, 0.2], [0.55, 0.28], [0.08, 0.66], [0.55, 0.74]];
const Labels: React.FC<GP<Extract<Graphic, {type: 'labels'}>>> = ({g, len, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const items = (g.items || []).slice(0, 4);
  const size = width * (short ? 0.046 : 0.025);
  const slots = short ? SLOTS_TALL : SLOTS_WIDE;
  return (
    <AbsoluteFill style={{opacity: fadeInOut(frame, len, 1, 8)}}>
      {items.map((t, i) => {
        const [x, y] = slots[i];
        const s = pop(frame, fps, 4 + i * Math.round(fps * 0.45), 12);
        const left = x < 0.5;
        const line = interpolate(frame, [4 + i * fps * 0.45, 14 + i * fps * 0.45], [0, 1], clamp);
        return (
          <div key={i} style={{position: 'absolute', left: x * width, top: y * height, display: 'flex', alignItems: 'center',
            flexDirection: left ? 'row' : 'row-reverse', transform: `scale(${s})`, transformOrigin: left ? 'left center' : 'right center'}}>
            <div style={{fontFamily: fontFor(t, fonts), fontSize: size, color: '#fff', background: 'rgba(11,11,16,0.62)',
              padding: `${size * 0.25}px ${size * 0.55}px`, borderRadius: size * 0.3, border: '1.5px solid rgba(255,255,255,0.55)',
              backdropFilter: 'blur(4px)', whiteSpace: 'nowrap'}}>{t}</div>
            <div style={{width: size * 2.6 * line, height: 2, background: 'rgba(255,255,255,0.85)'}} />
            <div style={{width: size * 0.45, height: size * 0.45, borderRadius: '50%', background: YELLOW,
              boxShadow: `0 0 0 ${size * 0.18 * (1 + Math.sin(frame / 5 + i))}px rgba(255,214,10,0.3)`}} />
          </div>
        );
      })}
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
  // Frame-one hook of a Short: fully readable at once (viewers decide to stay or swipe in ~1 second)
  const instant = !!g.instant;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-start', alignItems: 'center', padding: short ? '15% 5% 0' : '6% 6% 0',
      opacity: fadeInOut(frame, len, instant ? 1 : 8)}}>
      {g.badge ? <div style={{fontFamily: fontFor(g.badge, fonts), fontSize: size * 0.44, color: INK, background: YELLOW,
        padding: `${size * 0.06}px ${size * 0.22}px`, borderRadius: size * 0.1, marginBottom: size * 0.18,
        letterSpacing: size * 0.01}}>{g.badge}</div> : null}
      <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: `${size * 0.1}px ${size * 0.25}px`,
        background: 'rgba(11,11,16,0.55)', padding: `${size * 0.15}px ${size * 0.4}px`, borderRadius: size * 0.25}}>
        {words.map((w, i) => {
          const s = instant ? 1 : pop(frame, fps, 3 + i * 4, 11);
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
    case 'keyword': return <Keyword {...(p as any)} />;
    case 'labels': return <Labels {...(p as any)} />;
    default: return null;
  }
};
