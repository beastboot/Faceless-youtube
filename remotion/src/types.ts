// Props sent by the Python pipeline (src/motion.py). Times are in seconds.

export type Caption = {text: string; s: number; e: number};

export type Graphic =
  | {type: 'date'; text: string; sub?: string}
  | {type: 'counter'; value: number; prefix?: string; suffix?: string; label: string}
  | {type: 'map'; place: string; lat: number; lon: number; label?: string}
  | {type: 'name'; name: string; role?: string}
  | {type: 'quote'; text: string; by?: string}
  | {type: 'timeline'; points: {year: string; label: string}[]}
  | {type: 'fact'; text: string};

export type Shot = {
  start: number;
  dur: number; // narration length of the shot incl. tail (next shot starts at start + dur)
  image: string | null; // staticFile path
  video: string | null;
  move: string;
  ambient: string;
  transition: string;
  captions: Caption[];
  graphic?: Graphic | null;
};

export type Props = {
  width: number;
  height: number;
  fps: number;
  total: number;
  lang: 'hi' | 'en';
  short: boolean;
  shots: Shot[];
  transitionSeconds: number;
  captionsOn: boolean;
  fonts: {display: string; hindi: string; regional?: string; regionalFile?: string | null};
  logo: string | null;
  grain?: string | null;
  endCard: {title: string; button: string} | null;
};
