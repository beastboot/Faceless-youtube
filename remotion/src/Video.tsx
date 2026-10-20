import React from 'react';
import {AbsoluteFill, Img, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Captions} from './Captions';
import {GraphicLayer} from './graphics/Graphics';
import {ClipVideo, FilmLook, Flash, FrameSeq, KenBurns, Particles, VfxLayer, Vignette, entrance, shake} from './Scene';
import type {Props, Shot} from './types';
import {INK, RED, YELLOW, clamp, fontFor, pop} from './util';

const FontFaces: React.FC<{fonts: Props['fonts']}> = ({fonts}) => (
  <style>{`
    @font-face { font-family: '${fonts.display}'; src: url('${staticFile('fonts/Anton-Regular.ttf')}'); }
    @font-face { font-family: '${fonts.hindi}'; src: url('${staticFile('fonts/Mukta-ExtraBold.ttf')}'); }
    ${fonts.regionalFile ? `@font-face { font-family: '${fonts.regional}'; src: url('${staticFile(fonts.regionalFile)}'); }` : ''}
  `}</style>
);

const ShotView: React.FC<{shot: Shot; len: number; xf: number; index: number; props: Props}> = ({shot, len, xf, index, props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const narrationFrames = Math.round(shot.dur * fps);
  const cardFrames = shot.chapter ? Math.min(narrationFrames, Math.round(fps * 1.7)) : 0;
  // graphics wait for the chapter card to clear; "instant" ones (Short hooks) show on frame one
  const gStart = cardFrames ? Math.min(cardFrames, Math.max(0, narrationFrames - fps)) : (shot.graphic as any)?.instant ? 0 : Math.round(fps * 0.25);
  return (
    <AbsoluteFill style={index === 0 ? {} : entrance(shot.transition, frame, xf)}>
      <AbsoluteFill style={shot.impact ? shake(frame, fps, `imp-${index}`) : {}}>
        {shot.frames ? <FrameSeq dir={shot.frames.dir} count={shot.frames.count} />
          : shot.video ? <ClipVideo src={shot.video} />
          : shot.image ? <KenBurns src={shot.image} move={shot.move} len={len} />
          : <AbsoluteFill style={{background: '#101418'}} />}
        {shot.overlay ? <VfxLayer src={shot.overlay} opacity={shot.ambient === 'embers' ? 0.9 : 0.6} seed={`vfx-${index}`} /> : null}
      </AbsoluteFill>
      {shot.overlay ? null : <Particles kind={shot.ambient} seed={`shot-${index}`} />}
      {shot.leak ? (
        <Sequence durationInFrames={Math.round(fps * 0.7)} layout="none">
          <VfxLayer src={shot.leak} opacity={0.45} seed={`leak-${index}`} fadeOut={Math.round(fps * 0.7)} />
        </Sequence>
      ) : null}
      {shot.impact ? <Flash /> : null}
      <Vignette />
      {shot.tag ? <PhotoStamp text={shot.tag} fonts={props.fonts} short={props.short} /> : null}
      {shot.graphic ? (
        <Sequence from={gStart} durationInFrames={Math.max(1, narrationFrames - gStart)} layout="none">
          <GraphicLayer g={shot.graphic} len={Math.max(1, narrationFrames - gStart)} fonts={props.fonts} short={props.short} />
        </Sequence>
      ) : null}
      {shot.chapter ? (
        <Sequence durationInFrames={cardFrames} layout="none">
          <ChapterCard n={shot.chapter.n} title={shot.chapter.title} fonts={props.fonts} len={cardFrames} />
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

/** Honest stamp on real archive pictures ("REAL PHOTO"), under the framed photo. */
const PhotoStamp: React.FC<{text: string; fonts: Props['fonts']; short: boolean}> = ({text, fonts, short}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const p = pop(frame, fps, 3, 12);
  const size = width * (short ? 0.042 : 0.02);
  return (
    <AbsoluteFill style={{justifyContent: 'flex-start', alignItems: 'center', paddingTop: short ? '23%' : '4%', pointerEvents: 'none'}}>
      <div style={{fontFamily: fonts.display, fontSize: size, letterSpacing: 3, color: '#fff', border: `3px solid ${RED}`,
        background: 'rgba(0,0,0,0.55)', padding: `${size * 0.15}px ${size * 0.5}px`, transform: `rotate(-3deg) scale(${p})`,
        display: 'flex', alignItems: 'center', gap: size * 0.35}}>
        <span style={{width: size * 0.45, height: size * 0.45, borderRadius: '50%', background: RED, display: 'inline-block'}} />
        {text}
      </div>
    </AbsoluteFill>
  );
};

/** Full-screen chapter card ("[ 02 ]  THE CURSE") that opens a new part of a long video. */
const ChapterCard: React.FC<{n: number; title: string; fonts: Props['fonts']; len: number}> = ({n, title, fonts, len}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const out = interpolate(frame, [len - fps * 0.35, len], [1, 0], clamp);
  const line = interpolate(frame, [2, fps * 0.5], [0, 1], {...clamp, easing: (x) => 1 - Math.pow(1 - x, 3)});
  const p = pop(frame, fps, 4, 13);
  const size = width * 0.06;
  const num = String(n).padStart(2, '0');
  return (
    <AbsoluteFill style={{background: '#050507', opacity: out, justifyContent: 'center', alignItems: 'center'}}>
      <div style={{display: 'flex', alignItems: 'center', gap: size * 0.35, fontFamily: fonts.display, color: '#fff', fontSize: size * 0.55}}>
        <div style={{width: size * 2.2 * line, height: 2, background: 'rgba(255,255,255,0.7)'}} />
        <span>[</span><span style={{color: YELLOW, transform: `scale(${p})`, display: 'inline-block'}}>{num}</span><span>]</span>
        <div style={{width: size * 2.2 * line, height: 2, background: 'rgba(255,255,255,0.7)'}} />
      </div>
      <div style={{fontFamily: fontFor(title, fonts), fontSize: size, color: '#fff', marginTop: size * 0.2, letterSpacing: 1,
        opacity: interpolate(frame, [6, 14], [0, 1], clamp), transform: `translateY(${(1 - p) * 30}px)`,
        textTransform: 'uppercase'}}>{title}</div>
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

// Shorts: a silent "follow" tag in the last ~1.6 s (no voice, so the loop back to line 1 stays intact)
const FollowTag: React.FC<{text: string; fonts: Props['fonts']}> = ({text, fonts}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const size = width * 0.052;
  const s = Math.min(1, frame / (0.25 * fps));
  return (
    <AbsoluteFill style={{justifyContent: 'flex-start', alignItems: 'center', paddingTop: '13%', pointerEvents: 'none'}}>
      <div style={{fontFamily: fontFor(text, fonts), fontSize: size, color: INK, background: YELLOW, opacity: s,
        transform: `translateY(${(1 - s) * -40}px)`, padding: `${size * 0.25}px ${size * 0.6}px`, borderRadius: size * 0.4,
        textAlign: 'center', maxWidth: '86%'}}>➕ {text}</div>
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
      {props.followText && props.short ? (
        <Sequence from={Math.max(0, Math.round((props.total - 1.6) * fps))}>
          <FollowTag text={props.followText} fonts={props.fonts} />
        </Sequence>
      ) : null}
      {props.endCard && !props.short ? (
        <Sequence from={Math.max(0, Math.round((props.total - 6) * fps))}>
          <EndCard title={props.endCard.title} button={props.endCard.button} fonts={props.fonts} />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
};
