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


def music_for(seconds, work):
    """A royalty-free track from assets/music if present, else a synthesised bed."""
    tracks = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    if tracks:
        t = random.choice(tracks)
        log.info("Background music: %s", t.name)
        return t, True
    log.info("Background music: synthesised ambient bed (add tracks to assets/music/ to replace)")
    return ambient_bed(seconds, work / "bed.wav"), False
