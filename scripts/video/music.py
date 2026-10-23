"""An original 30-second house track, synthesised from scratch (no samples, no licences).

128 BPM, 16 bars in C major (C - G - Am - F): filtered intro, riser into a drop at bar 4,
four-on-the-floor kick, claps, off-beat hats, pumping bass, chord stabs, plucky lead.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
BPM = 128
BEAT = 60 / BPM
BAR = BEAT * 4
BARS = 16
LENGTH = BAR * BARS  # 30.0 s
DROP_BAR = 3  # bars 0-1 intro, bar 2 riser, drop at bar 3 (5.625 s)

CHORDS = [[60, 64, 67], [59, 62, 67], [57, 60, 64], [57, 60, 65]]  # C, G/B, Am, F
ROOTS = [36, 43, 45, 41]
rng = np.random.default_rng(7)


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def t_(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def add(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR)
    if i >= len(buf):
        return
    n = min(len(sig), len(buf) - i)
    buf[i:i + n] += sig[:n] * gain


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def saw(f: float, t: np.ndarray) -> np.ndarray:
    return 2 * ((t * f) % 1.0) - 1


def kick() -> np.ndarray:
    t = t_(0.35)
    f = 45 + 110 * np.exp(-t * 28)
    phase = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(phase) * np.exp(-t * 9)
    click = np.exp(-t * 400) * 0.6
    return np.tanh((body + click) * 1.6)


def clap() -> np.ndarray:
    t = t_(0.22)
    n = rng.uniform(-1, 1, len(t))
    n = np.diff(n, prepend=0)  # brighter
    env = np.exp(-t * 22) + 0.6 * np.exp(-((t - 0.012) ** 2) / 0.00002) + 0.5 * np.exp(-((t - 0.024) ** 2) / 0.00002)
    return n * env * 0.7


def hat(open_: bool) -> np.ndarray:
    t = t_(0.16 if open_ else 0.05)
    n = np.diff(np.diff(rng.uniform(-1, 1, len(t) + 2)))
    return n * np.exp(-t * (18 if open_ else 70)) * 0.35


def stab(notes: list[int], dur: float = 0.2) -> np.ndarray:
    t = t_(dur)
    sig = np.zeros_like(t)
    for m in notes:
        for d in (-0.12, -0.05, 0, 0.05, 0.12):  # supersaw detune (semitones)
            sig += saw(hz(m + d), t + rng.uniform(0, 1))
    sig /= len(notes) * 5
    return lowpass(sig * np.exp(-t * 9), 3200)


def bass(m: int, dur: float) -> np.ndarray:
    t = t_(dur)
    sig = 0.6 * saw(hz(m), t) + 0.4 * np.sin(2 * np.pi * hz(m) * t)
    env = np.minimum(t / 0.005, 1) * np.exp(-t * 5)
    return lowpass(sig * env, 900)


def pluck(m: int, dur: float = 0.22) -> np.ndarray:
    t = t_(dur)
    sq = np.sign(np.sin(2 * np.pi * hz(m) * t)) * 0.5 + saw(hz(m) * 1.003, t) * 0.5
    return lowpass(sq * np.exp(-t * 14), 4200)


def riser(dur: float) -> np.ndarray:
    t = t_(dur)
    n = rng.uniform(-1, 1, len(t))
    n = np.diff(n, prepend=0) * (t / dur) ** 2
    tone = np.sin(2 * np.pi * np.cumsum(200 + 1800 * (t / dur) ** 2) / SR) * (t / dur) ** 3 * 0.3
    return n * 0.5 + tone


def crash(dur: float = 2.2) -> np.ndarray:
    t = t_(dur)
    n = np.diff(rng.uniform(-1, 1, len(t) + 1))
    return n * np.exp(-t * 2.2) * 0.35


def render(path: Path) -> Path:
    n = int((LENGTH + 1.5) * SR)
    drums, music, side = np.zeros(n), np.zeros(n), np.zeros(n)
    K = kick()
    for bar in range(BARS):
        b0 = bar * BAR
        chord, root = CHORDS[bar % 4], ROOTS[bar % 4]
        intro = bar < DROP_BAR
        for beat in range(4):
            at = b0 + beat * BEAT
            if not intro or (bar == DROP_BAR - 1 and beat >= 2):
                add(drums, K, at, 1.0)
                add(side, np.exp(-t_(0.3) * 12), at)  # sidechain envelope
            if beat in (1, 3) and (not intro or bar >= 1):
                add(drums, clap(), at, 0.8 if not intro else 0.6)
            add(drums, hat(open_=not intro), at + BEAT / 2, 0.9 if not intro else 1.0)
            if intro:
                add(drums, hat(False), at, 0.7)
            if not intro:
                for s in (0.25, 0.75):
                    add(drums, hat(False), at + BEAT * s, 0.4)
            # stabs on the off-beat ("and"), filtered darker in the intro
            st = stab(chord + [chord[0] + 12])
            add(music, lowpass(st, 1400) if intro else st, at + BEAT / 2, 0.55 if not intro else 1.6)
            if not intro:
                add(music, bass(root, BEAT / 2 * 0.95), at + BEAT / 2, 0.9)
                add(music, bass(root, BEAT / 4), at + BEAT * 0.75, 0.5)
        # plucky lead over the main section
        if DROP_BAR + 1 <= bar <= 14:
            pattern = [0, 1, 2, 3, 2, 1, 2, 3] if bar % 2 else [3, 2, 1, 2, 0, 1, 2, 1]
            tones = chord + [chord[0] + 12]
            for k, idx in enumerate(pattern):
                add(music, pluck(tones[idx] + 12), b0 + k * BEAT / 2, 0.28)
    add(music, riser(BAR), (DROP_BAR - 1) * BAR, 0.9)
    add(drums, crash(), DROP_BAR * BAR, 1.0)
    # final hit and ring-out
    add(drums, K, LENGTH, 1.0)
    add(drums, crash(1.5), LENGTH, 0.8)
    add(music, stab(CHORDS[0] + [72, 76], 1.4), LENGTH, 0.8)

    pump = 1 - 0.55 * np.clip(side, 0, 1)
    mix = drums * 0.9 + music * pump
    mix = np.tanh(mix * 1.3)
    t_all = np.arange(n) / SR
    mix *= np.clip((LENGTH + 1.4 - t_all) / 0.6, 0, 1)  # fade the tail
    mix = mix / np.max(np.abs(mix)) * 0.89  # about -1 dBFS
    stereo = np.stack([mix, np.roll(mix, 9) * 0.98], axis=1)  # tiny width
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


if __name__ == "__main__":
    print(render(Path("music.wav")))
