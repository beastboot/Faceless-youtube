import {Easing, interpolate, spring} from 'remotion';

export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

export const ease = Easing.bezier(0.33, 0, 0.2, 1);

/** 0 -> 1 spring that starts at `delay` frames. */
export const pop = (frame: number, fps: number, delay = 0, damping = 14) =>
  spring({frame: frame - delay, fps, config: {damping, mass: 0.7, stiffness: 140}});

/** Fade in over `inF` frames and out over the last `outF` frames of `len`. */
export const fadeInOut = (frame: number, len: number, inF = 8, outF = 8) =>
  Math.min(
    interpolate(frame, [0, inF], [0, 1], clamp),
    interpolate(frame, [len - outF, len], [1, 0], clamp),
  );

export const isDevanagari = (s: string) => /[ऀ-ॿ]/.test(s);

export const fontFor = (text: string, fonts: {display: string; hindi: string}) =>
  isDevanagari(text) ? fonts.hindi : fonts.display;

export const YELLOW = '#FFD60A';
export const RED = '#E5121B';
export const INK = '#0B0B10';
