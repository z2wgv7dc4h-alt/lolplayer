from flask import Flask, send_file, request
from flask_socketio import SocketIO, emit
import html
import json
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import soundfile as sf

APP_DIR = Path(__file__).resolve().parent
ENGINE_DIR = APP_DIR / "engine"
OUT_DIR = APP_DIR / "out"
CORPUS_ROOT = APP_DIR.parents[1] / "bulk" / "songs"

sys.path.insert(0, str(ENGINE_DIR))
try:
    import namamp_integrated as namamp
    namamp.NAM_DIR = APP_DIR / "assets" / "nam"
    namamp.CAB_DIR = APP_DIR / "assets" / "cab"
except Exception:
    namamp = None

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
render_queue = queue.Queue()

DYNAMIC_VELOCITY = {
    "ppp": 20, "pp": 32, "p": 45, "mp": 58, "mf": 72, "f": 88, "ff": 104,
    "fff": 120,
}
DRUM_CHANNEL = 9


@dataclass
class Event:
    onset_beat: float
    pitch: int
    velocity: int
    duration_beats: float
    track: int
    tempo: float = 120.0
    dead: bool = False
    palm_mute: bool = False
    onset_sec: Optional[float] = None
    duration_sec: Optional[float] = None


@dataclass
class Track:
    index: int
    channel: int
    name: str = "track"
    program: int = 0
    is_drums: bool = False


@dataclass
class Song:
    slug: str
    title: str
    artist: str
    tracks: List[Track]
    events: List[Event]


_songs_cache = None
_songs_cache_at = 0.0
_songs_cache_lock = threading.Lock()
_SONGS_TTL = 5.0


def _discover_songs():
    if not CORPUS_ROOT.exists():
        return []
    available = []
    for notes_json in CORPUS_ROOT.glob("*/*/notes.json"):
        if "(old)" in notes_json.parts:
            continue
        song_dir = notes_json.parent
        try:
            data = json.loads(notes_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        available.append({
            'slug': song_dir.name,
            'artist': song_dir.parent.name,
            'title': str(data.get('title') or song_dir.name),
            'path': str(song_dir),
        })
    return sorted(available, key=lambda s: (s['artist'].lower(), s['slug'].lower()))


def load_songs(force=False):
    """Scan the shared corpus, cached briefly to avoid re-reading every page load."""
    global _songs_cache, _songs_cache_at
    with _songs_cache_lock:
        now = time.time()
        if force or _songs_cache is None or (now - _songs_cache_at) > _SONGS_TTL:
            _songs_cache = _discover_songs()
            _songs_cache_at = now
        return _songs_cache


def _safe_song_dir(artist, slug):
    """Resolve a song directory, rejecting anything that escapes the corpus."""
    if not isinstance(artist, str) or not isinstance(slug, str):
        return None
    if not artist or not slug:
        return None
    if any(sep in artist or sep in slug for sep in ("/", "\\", "\x00")):
        return None
    candidate = (CORPUS_ROOT / artist / slug).resolve()
    try:
        candidate.relative_to(CORPUS_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def load_song(artist, slug):
    """Load a Song from the corpus by artist/slug."""
    song_dir = _safe_song_dir(artist, slug)
    if song_dir is None:
        return None
    notes_json = song_dir / "notes.json"
    if not notes_json.exists():
        return None

    try:
        data = json.loads(notes_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    tracks = []
    slot = 0
    for i, track_data in enumerate(data.get('tracks', [])):
        is_drums = bool(track_data.get('is_percussion')) or track_data.get('category') == 'drums'
        if is_drums:
            channel = DRUM_CHANNEL
        else:
            channel = slot if slot < DRUM_CHANNEL else slot + 1
            slot += 1
        program = track_data.get('instrument_id')
        program = int(program) if isinstance(program, (int, float)) else 0
        program = 0 if program == 1024 else max(0, min(127, program))
        tracks.append(Track(
            index=track_data.get('index', i),
            channel=channel,
            name=str(track_data.get('name') or f'track_{i}'),
            program=program,
            is_drums=is_drums,
        ))

    events = []
    for e in data.get('events', []):
        pitch = e.get('pitch')
        if not isinstance(pitch, (int, float)):
            continue
        velocity = e.get('velocity')
        if not isinstance(velocity, (int, float)):
            velocity = DYNAMIC_VELOCITY.get((e.get('dynamic') or '').lower(), 90)
        onset_ms = e.get('onset_ms')
        duration_ms = e.get('duration_ms')
        events.append(Event(
            onset_beat=float(e.get('onset_beat') or 0.0),
            pitch=int(pitch),
            velocity=max(1, min(127, int(velocity))),
            duration_beats=float(e.get('duration_beats') or 0.25),
            track=int(e.get('track', 0)),
            tempo=float(e.get('tempo_bpm') or 120.0),
            dead=bool(e.get('dead')),
            palm_mute=bool(e.get('palm_mute')),
            onset_sec=(float(onset_ms) / 1000.0) if isinstance(onset_ms, (int, float)) else None,
            duration_sec=(float(duration_ms) / 1000.0) if isinstance(duration_ms, (int, float)) else None,
        ))
    events.sort(key=lambda ev: ev.onset_beat)

    return Song(
        slug=slug,
        title=str(data.get('title') or slug),
        artist=str(data.get('artist') or artist),
        tracks=tracks,
        events=events,
    )


def _tempo_map(events):
    """Cumulative beat -> seconds map built from per-event tempo changes."""
    segs = []
    for e in sorted(events, key=lambda x: x.onset_beat):
        bpm = e.tempo if e.tempo and e.tempo > 0 else 120.0
        if not segs or segs[-1][1] != bpm:
            if segs and segs[-1][0] == e.onset_beat:
                segs[-1] = (e.onset_beat, bpm)
            else:
                segs.append((e.onset_beat, bpm))
    if not segs:
        segs = [(0.0, 120.0)]
    if segs[0][0] > 0:
        segs.insert(0, (0.0, segs[0][1]))
    out = []
    t = 0.0
    for i, (beat, bpm) in enumerate(segs):
        if i:
            pb, pbpm = segs[i - 1]
            t += (beat - pb) * 60.0 / pbpm
        out.append((beat, t, bpm))
    return out


def _beat_to_sec(beat, tm):
    seg = tm[0]
    for s in tm:
        if s[0] <= beat:
            seg = s
        else:
            break
    return seg[1] + (beat - seg[0]) * 60.0 / seg[2]


def render_song(song, amp_name=None, cab_name=None):
    """Render a Song to mono float32 audio."""
    rate = 44100
    if not song or not song.events:
        duration = 5
        t = np.linspace(0, duration, int(duration * rate), endpoint=False)
        return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), rate

    tm = _tempo_map(song.events)

    def onset_seconds(ev):
        return ev.onset_sec if ev.onset_sec is not None else _beat_to_sec(ev.onset_beat, tm)

    def dur_seconds(ev):
        if ev.duration_sec is not None:
            return ev.duration_sec
        bpm = ev.tempo if ev.tempo and ev.tempo > 0 else 120.0
        return ev.duration_beats * 60.0 / bpm

    duration_sec = max(onset_seconds(e) + dur_seconds(e) for e in song.events) + 0.25
    out = np.zeros(int(duration_sec * rate), dtype=np.float32)

    for event in song.events:
        onset = onset_seconds(event)
        start_sample = int(onset * rate)
        if start_sample >= len(out):
            continue
        end_sample = min(int((onset + dur_seconds(event)) * rate), len(out))
        if end_sample <= start_sample:
            continue
        amp = (event.velocity / 127.0) * 0.3
        t = np.arange(end_sample - start_sample) / rate
        freq = 440 * (2 ** ((event.pitch - 69) / 12.0))
        out[start_sample:end_sample] += (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    peak = float(np.max(np.abs(out))) + 1e-9
    out = (out / peak * 0.9).astype(np.float32)

    if namamp is not None and amp_name and namamp.available():
        out = namamp.process(out, rate, name=amp_name, cab_name=cab_name)

    return out, rate


def _sanitize_filename(name):
    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", name or "render")
    return clean[:80] or "render"


def worker_render():
    while True:
        params = render_queue.get()
        sid = params.pop("_sid", None)
        try:
            artist = params.get("artist")
            slug = params.get("slug")
            amp_name = params.get("amp_name")
            cab_name = params.get("cab_name")

            song = load_song(artist, slug)
            if not song:
                socketio.emit('render_error', {'error': 'Song not found'}, to=sid)
            else:
                audio, rate = render_song(song, amp_name, cab_name)
                filename = f"{_sanitize_filename(slug)}_{uuid.uuid4().hex[:8]}.wav"
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                sf.write(str(OUT_DIR / filename), audio, rate)
                socketio.emit('render_complete', {'filename': filename}, to=sid)
        except Exception as e:
            socketio.emit('render_error', {'error': str(e) + '\n' + traceback.format_exc()}, to=sid)
        finally:
            render_queue.task_done()


@app.route('/')
def index():
    songs = load_songs()
    song_opts = ''.join(
        '<option value="{a}::{s}">{a} - {t}</option>'.format(
            a=html.escape(s['artist'], quote=True),
            s=html.escape(s['slug'], quote=True),
            t=html.escape(s['title']),
        )
        for s in songs
    )
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Riffer Renderer</title>
        <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
        <script src="https://unpkg.com/wavesurfer.js@7"></script>
        <style>
            body { font-family: monospace; padding: 20px; max-width: 800px; }
            #waveform { height: 200px; border: 1px solid #ccc; margin: 20px 0; }
            input, select { width: 100%; margin: 10px 0; padding: 5px; }
            button { padding: 8px 16px; margin: 5px; cursor: pointer; }
            button:disabled { opacity: 0.5; cursor: not-allowed; }
            #status { margin-top: 20px; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>Riffer Renderer</h1>
        <div id="waveform"></div>
        <div>
            <label>Song:</label>
            <select id="songSelect">
                __SONG_OPTS__
            </select>
        </div>
        <div>
            <label>Amp Drive: <span id="ampVal">0.50</span></label>
            <input type="range" id="ampDrive" min="0" max="1" step="0.01" value="0.5"
                   oninput="document.getElementById('ampVal').textContent = this.value">
        </div>
        <button id="renderBtn" onclick="render()">Render</button>
        <button onclick="wavesurfer.playPause()">Play</button>
        <div id="status"></div>

        <script>
            const socket = io();
            const statusEl = document.getElementById('status');
            const renderBtn = document.getElementById('renderBtn');
            const wavesurfer = WaveSurfer.create({
                container: '#waveform',
                waveColor: '#4F4A85',
                progressColor: '#383351'
            });

            function render() {
                const value = document.getElementById('songSelect').value;
                if (!value) {
                    statusEl.textContent = 'No song selected';
                    return;
                }
                const idx = value.indexOf('::');
                const artist = value.slice(0, idx);
                const slug = value.slice(idx + 2);
                renderBtn.disabled = true;
                statusEl.textContent = 'Rendering...';
                socket.emit('render_request', {
                    artist: artist,
                    slug: slug,
                    amp_name: 'wavenet_a1_standard',
                    cab_name: null
                });
            }

            socket.on('render_started', () => {
                statusEl.textContent = 'Rendering...';
            });

            socket.on('render_complete', (data) => {
                renderBtn.disabled = false;
                wavesurfer.load('/audio/' + encodeURIComponent(data.filename));
                statusEl.textContent = 'Done!';
            });

            socket.on('render_error', (data) => {
                renderBtn.disabled = false;
                statusEl.textContent = 'Error: ' + data.error;
            });

            wavesurfer.on('error', (err) => {
                statusEl.textContent = 'Playback error: ' + err;
            });
        </script>
    </body>
    </html>
    '''.replace('__SONG_OPTS__', song_opts)


@app.route('/audio/<filename>')
def get_audio(filename):
    safe = _sanitize_filename(Path(filename).stem) + Path(filename).suffix
    path = (OUT_DIR / safe).resolve()
    try:
        path.relative_to(OUT_DIR.resolve())
    except ValueError:
        return 'Not found', 404
    if not path.is_file():
        return 'Not found', 404
    return send_file(str(path), mimetype='audio/wav', conditional=True)


@socketio.on('render_request')
def on_render(data):
    data = data if isinstance(data, dict) else {}
    artist = data.get('artist')
    slug = data.get('slug')
    if not isinstance(artist, str) or not isinstance(slug, str):
        emit('render_error', {'error': 'Invalid request'})
        return
    job = {
        'artist': artist,
        'slug': slug,
        'amp_name': data.get('amp_name') if isinstance(data.get('amp_name'), str) else None,
        'cab_name': data.get('cab_name') if isinstance(data.get('cab_name'), str) else None,
        '_sid': request.sid,
    }
    render_queue.put(job)
    emit('render_started', {})


OUT_DIR.mkdir(parents=True, exist_ok=True)

if __name__ == '__main__':
    threading.Thread(target=worker_render, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
