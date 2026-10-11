"""Double-clap detection from raw microphone audio.

This is a heuristic, not speech recognition: it looks for two sharp, short
transients 0.15–0.8 s apart. Loud door knocks or a dropped object can still
trigger it, which is why it is off by default and the wake word or hotkey
remain the reliable activation methods.
"""

from __future__ import annotations

import numpy as np

WINDOW = 160  # 10 ms at 16 kHz


class ClapDetector:
    def __init__(self, threshold: float = 0.35, sample_rate: int = 16000,
                 min_gap_s: float = 0.15, max_gap_s: float = 0.8, cooldown_s: float = 1.5) -> None:
        self.threshold = threshold
        self.rate = sample_rate
        self.min_gap = min_gap_s
        self.max_gap = max_gap_s
        self.cooldown = cooldown_s
        self._time = 0.0
        self._noise = 0.01
        self._prev_rms = 0.0
        self._candidate: tuple[float, float] | None = None  # (time, onset rms)
        self._candidate_tail: list[float] = []
        self._last_clap: float | None = None
        self._last_trigger = -10.0
        self._leftover = np.zeros(0, dtype=np.float32)

    def process(self, pcm16: bytes) -> bool:
        """Feed audio; True once when a double clap is detected."""
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        samples = np.concatenate([self._leftover, samples])
        usable = len(samples) // WINDOW * WINDOW
        self._leftover = samples[usable:]
        triggered = False
        for window in samples[:usable].reshape(-1, WINDOW):
            if self._step(float(np.abs(window).max()), float(np.sqrt(np.mean(window**2)))):
                triggered = True
        return triggered

    def _step(self, peak: float, rms: float) -> bool:
        self._time += WINDOW / self.rate
        triggered = False
        if self._candidate is not None:
            self._candidate_tail.append(rms)
            if len(self._candidate_tail) >= 8:
                onset_time, onset_rms = self._candidate
                decay = float(np.mean(self._candidate_tail[4:8]))
                if decay < 0.35 * onset_rms:
                    triggered = self._register_clap(onset_time)
                self._candidate = None
                self._candidate_tail = []
        elif peak >= self.threshold and rms > 6 * self._noise and rms > 3 * max(self._prev_rms, 1e-4):
            self._candidate = (self._time, rms)
        else:
            # Track background noise only from non-transient windows.
            self._noise = 0.995 * self._noise + 0.005 * rms if rms < 4 * self._noise else self._noise * 1.001
            self._noise = min(max(self._noise, 1e-4), 0.2)
        self._prev_rms = rms
        return triggered

    def _register_clap(self, when: float) -> bool:
        if when - self._last_trigger < self.cooldown:
            return False
        if self._last_clap is not None and self.min_gap <= when - self._last_clap <= self.max_gap:
            self._last_clap = None
            self._last_trigger = when
            return True
        self._last_clap = when
        return False
