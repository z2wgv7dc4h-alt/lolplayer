from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

DRUM_CHANNEL = 9

DYNAMIC_VELOCITY = {
    "ppp": 20, "pp": 32, "p": 45, "mp": 58, "mf": 72, "f": 88, "ff": 104,
    "fff": 120,
}


@dataclass
class Event:
    track: int
    pitch: int
    onset_beat: float
    duration_beats: float
    tempo: float = 120.0
    velocity: int = 90
    string: Optional[float] = None
    fret: Optional[float] = None
    measure: int = 0
    palm_mute: bool = False
    dead: bool = False
    ghost: bool = False
    hammer: bool = False
    bend: bool = False
    slide: Optional[str] = None
    harmonic: Optional[str] = None
    tie: bool = False
    key_pc: Optional[int] = None
    bends: List = field(default_factory=list)
    staccato: bool = False
    accentuated: bool = False

    def end_beat(self) -> float:
        return self.onset_beat + max(0.05, self.duration_beats)


@dataclass
class Track:
    index: int
    name: str
    program: int
    is_drums: bool
    channel: int = 0
    low_midi: Optional[int] = None
    tuning: Optional[List[int]] = None
    volume: float = 1.0
    balance: float = 0.0


@dataclass
class Song:
    slug: str
    title: str
    artist: str
    tracks: List[Track]
    events: List[Event]
    source: Optional[str] = None

    def duration_beats(self) -> float:
        return max((e.end_beat() for e in self.events), default=0.0)

    def track(self, index: int) -> Optional[Track]:
        for t in self.tracks:
            if t.index == index:
                return t
        return None

    def guitars(self) -> List[Track]:
        return [t for t in self.tracks if not t.is_drums and t.low_midi is not None]

    def drum_track(self) -> Optional[Track]:
        for t in self.tracks:
            if t.is_drums:
                return t
        return None
