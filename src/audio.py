"""Copyright-free sound: synthesised whoosh transitions and an ambient documentary bed (ffmpeg only).
Royalty-free tracks dropped into assets/music/ are used instead of the synthesised bed."""
import random

from .common import ROOT, log, run

SFX = ROOT / "assets" / "sfx"


def whoosh():
    """0.6s filtered-noise swoosh, generated once."""
    p = SFX / "whoosh.wav"
    if not p.exists():
        SFX.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "anoisesrc=d=0.6:c=pink:r=44100:a=0.6",
             "-af", "highpass=f=300,lowpass=f=5000,afade=t=in:d=0.25,afade=t=out:st=0.3:d=0.3,"
                    "aecho=0.6:0.4:40:0.3,volume=4.0", "-ac", "2", p])
    return p


def ambient_bed(seconds, out):
    """Low, slowly pulsing drone in a random minor key: sits under narration, never distracts."""
    root = random.choice([55.0, 58.27, 61.74, 65.41])          # A1, A#1, B1, C2
    tones = [root, root * 1.5, root * 2.0, root * 2.378]       # root, fifth, octave, minor tenth
    inputs = []
    for f in tones:
        inputs += ["-f", "lavfi", "-i", f"sine=f={f:.2f}:d={seconds + 2:.1f}:r=44100"]
    n = len(tones)
    graph = (";".join(f"[{i}]volume={0.5 / (i + 1):.3f},tremolo=f={0.11 + i * 0.04:.2f}:d=0.5[t{i}]"
                      for i in range(n))
             + ";" + "".join(f"[t{i}]" for i in range(n))
             + f"amix=inputs={n}:normalize=0,lowpass=f=900,aecho=0.8:0.7:600|900:0.3|0.2,"
               f"afade=t=in:d=3,afade=t=out:st={max(seconds - 3, 0):.1f}:d=3[a]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[a]",
         "-ac", "2", "-t", f"{seconds:.2f}", out])
    return out


def _gen(name, args):
    p = SFX / name
    if not p.exists():
        SFX.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", *args, "-ac", "2", "-ar", "44100", p])
    return p


def pop():
    """Short bright 'pop' as a graphic lands."""
    return _gen("pop.wav", ["-f", "lavfi", "-i", "sine=f=880:d=0.14", "-af",
                            "asetrate=44100*1.0,vibrato=f=12:d=0.3,afade=t=out:st=0.02:d=0.12,volume=1.6"])


def impact():
    """Low cinematic boom for big reveals (counters, facts)."""
    return _gen("impact.wav", ["-f", "lavfi", "-i", "sine=f=48:d=1.4", "-f", "lavfi", "-i",
                               "anoisesrc=d=0.5:c=brown:a=0.8", "-filter_complex",
                               "[0]afade=t=out:st=0.05:d=1.3,volume=3[b];[1]lowpass=f=400,afade=t=out:st=0.02:d=0.45[n];"
                               "[b][n]amix=inputs=2:normalize=0,aecho=0.7:0.5:90:0.25"])


def riser():
    """1.6 s rising swell that leads into a new chapter or the final reveal."""
    return _gen("riser.wav", ["-f", "lavfi", "-i", "anoisesrc=d=1.6:c=pink:a=0.5", "-f", "lavfi", "-i",
                              "aevalsrc=0.4*sin(2*PI*(200*t+300*t*t)):d=1.6", "-filter_complex",
                              "[0]highpass=f=600,lowpass=f=6000[n];[n][1]amix=inputs=2:normalize=0,"
                              "afade=t=in:d=1.5:curve=exp,afade=t=out:st=1.5:d=0.1,volume=1.5"])


def sfx_track(events, seconds, out):
    """events: [(time_seconds, path, volume)] -> one wav of `seconds` with every effect placed."""
    if not events:
        return None
    events = events[:60]
    inputs, chains = [], []
    for k, (t, path, vol) in enumerate(events):
        inputs += ["-i", path]
        ms = max(0, int(t * 1000))
        chains.append(f"[{k}]volume={vol},adelay={ms}|{ms}[e{k}]")
    graph = ";".join(chains) + ";" + "".join(f"[e{k}]" for k in range(len(events))) + \
        f"amix=inputs={len(events)}:normalize=0:duration=longest,apad=whole_dur={seconds:.2f}[a]"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[a]",
         "-t", f"{seconds:.2f}", "-ac", "2", "-ar", "44100", out])
    return out


MOODS = ("mystery", "epic", "devotional", "sad", "adventure", "dark")


def music_for(seconds, work, mood=None):
    """A royalty-free track for the video's mood (assets/music/<mood>/*.mp3), any track in
    assets/music, or else a synthesised bed."""
    base = ROOT / "assets" / "music"
    tracks = sorted((base / mood).glob("*.mp3")) if mood else []
    tracks = tracks or sorted(base.rglob("*.mp3"))
    if tracks:
        t = random.choice(tracks)
        log.info("Background music: %s", t.name)
        return t, True
    log.info("Background music: synthesised ambient bed (add tracks to assets/music/ to replace)")
    return ambient_bed(seconds, work / "bed.wav"), False
