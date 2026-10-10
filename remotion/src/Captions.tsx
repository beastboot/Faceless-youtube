import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Caption} from './types';
import {YELLOW, fontFor, pop} from './util';

/** Karaoke captions: the active chunk on screen, each word lighting up yellow as it is spoken,
 *  the current word popping slightly. Word times are spread over the chunk by word length. */
export const Captions: React.FC<{captions: Caption[]; short: boolean; fonts: {display: string; hindi: string; regional?: string}}> = ({captions, short, fonts}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const cap = captions.find((c) => t >= c.s - 0.02 && t <= c.e + 0.08);
  if (!cap) return null;
  const words = cap.text.split(/\s+/).filter(Boolean);
  const total = words.reduce((a, w) => a + w.length + 1, 0);
  let acc = 0;
  const times = words.map((w) => {
    const s = cap.s + ((cap.e - cap.s) * acc) / total;
    acc += w.length + 1;
    return s;
  });
  const appear = pop(frame - Math.round(cap.s * fps), fps, 0, 18);
  const size = short ? width * 0.078 : width * 0.034;
  const font = fontFor(cap.text, fonts);
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: short ? height * 0.27 : height * 0.07}}>
      <div style={{
        display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: `0 ${size * 0.38}px`,
        maxWidth: width * (short ? 0.86 : 0.78), transform: `scale(${0.9 + 0.1 * appear})`,
        padding: short ? 0 : `${size * 0.18}px ${size * 0.5}px`,
        background: short ? 'none' : 'rgba(0,0,0,0.45)', borderRadius: size * 0.3,
      }}>
        {words.map((w, i) => {
          const spoken = t >= times[i];
          const current = spoken && (i === words.length - 1 || t < times[i + 1]);
          const grow = current ? 1.08 : 1;
          return (
            <span key={i} style={{
              fontFamily: font, fontSize: size, lineHeight: 1.25, fontWeight: 800,
              color: spoken ? YELLOW : '#FFFFFF',
              textTransform: short && font === fonts.display ? 'uppercase' : 'none',
              WebkitTextStroke: short ? `${size * 0.09}px #000` : undefined,
              paintOrder: 'stroke fill',
              textShadow: '0 4px 12px rgba(0,0,0,0.6)',
              display: 'inline-block',
              transform: `scale(${grow})`, margin: `0 ${size * 0.04}px`,
            }}>{w}</span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
