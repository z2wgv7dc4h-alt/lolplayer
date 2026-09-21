from flask import Flask, send_file, request
from flask_socketio import SocketIO, emit
import html
import json
import os
from collections import defaultdict
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

APP_DIR = Path(__file__).resolve().parent
ENGINE_DIR = APP_DIR / "engine"
OUT_DIR = APP_DIR / "out"
CORPUS_ROOT = APP_DIR.parents[1] / "bulk" / "songs"


def _detect_dir(env_name, candidates, probe):
    override = os.environ.get(env_name)
    if override and (Path(override) / probe).exists():
        return Path(override)
    for cand in candidates:
        if (cand / probe).exists():
            return cand
    return candidates[0]


ASSETS_DIR = _detect_dir(
    "RIFFER_ASSETS",
    [APP_DIR / "assets", Path.home() / "Desktop" / "god-tier-metal" / "assets"],
    "di",
)
TOOLS_DIR = _detect_dir(
    "RIFFER_TOOLS",
    [APP_DIR / "tools", Path.home() / "Desktop" / "god-tier-metal" / "tools"],
    "fluidsynth",
)
os.environ.setdefault("RIFFER_ASSETS", str(ASSETS_DIR))
os.environ.setdefault("RIFFER_TOOLS", str(TOOLS_DIR))

sys.path.insert(0, str(ENGINE_DIR))
import amp as ampmod          # noqa: E402
import di                     # noqa: E402
import drums                  # noqa: E402
import fastsynth              # noqa: E402
import model                  # noqa: E402
import namamp                 # noqa: E402
import synth                  # noqa: E402

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
render_queue = queue.Queue()

DYNAMIC_VELOCITY = {
    "ppp": 20, "pp": 32, "p": 45, "mp": 58, "mf": 72, "f": 88, "ff": 104,
    "fff": 120,
}
DRUM_CHANNEL = 9


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
    global _songs_cache, _songs_cache_at
    with _songs_cache_lock:
        now = time.time()
        if force or _songs_cache is None or (now - _songs_cache_at) > _SONGS_TTL:
            _songs_cache = _discover_songs()
            _songs_cache_at = now
        return _songs_cache


def _safe_song_dir(artist, slug):
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


def _key_pc(sig):
    if not isinstance(sig, dict) or not isinstance(sig.get("fifths"), int):
        return None
    major = (int(sig["fifths"]) * 7) % 12
    return major if sig.get("mode") != "minor" else (major + 9) % 12


def load_song(artist, slug):
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
        tuning = track_data.get('tuning')
        low = min(tuning) if isinstance(tuning, list) and tuning else None
        tracks.append(model.Track(
            index=track_data.get('index', i),
            name=str(track_data.get('name') or f'track_{i}'),
            program=program,
            is_drums=is_drums,
            channel=channel,
            low_midi=low,
            tuning=tuning,
        ))

    events = []
    for e in data.get('events', []):
        pitch = e.get('pitch')
        if not isinstance(pitch, (int, float)):
            continue
        velocity = e.get('velocity')
        if not isinstance(velocity, (int, float)):
            velocity = DYNAMIC_VELOCITY.get((e.get('dynamic') or '').lower(), 90)
        events.append(model.Event(
            track=int(e.get('track', 0)),
            pitch=int(pitch),
            onset_beat=float(e.get('onset_beat') or 0.0),
            duration_beats=float(e.get('duration_beats') or 0.25),
            tempo=float(e.get('tempo_bpm') or 120.0),
            velocity=max(1, min(127, int(velocity))),
            string=e.get('string'),
            fret=e.get('fret'),
            measure=int(e.get('measure') or 0),
            palm_mute=bool(e.get('palm_mute')),
            dead=bool(e.get('dead')),
            ghost=bool(e.get('ghost')),
            hammer=bool(e.get('hammer')),
            bend=bool(e.get('bend')),
            slide=e.get('slide'),
            harmonic=e.get('harmonic'),
            tie=bool(e.get('tie')),
            key_pc=_key_pc(e.get('key_signature')),
        ))
    events.sort(key=lambda ev: ev.onset_beat)

    song = model.Song(
        slug=slug,
        title=str(data.get('title') or slug),
        artist=str(data.get('artist') or artist),
        tracks=tracks,
        events=events,
        source=str(notes_json),
    )
    _attach_mix(song, song_dir)
    _attach_expression(song, song_dir)
    return song


def _attach_mix(song, song_dir):
    raw = song_dir / "raw" / "song.json"
    if not raw.exists():
        return
    try:
        data = json.loads(raw.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    raw_tracks = data.get("tracks") or []
    if len(raw_tracks) != len(song.tracks):
        return
    for track, rt in zip(song.tracks, raw_tracks):
        volume = rt.get("volume")
        balance = rt.get("balance")
        if isinstance(volume, (int, float)):
            track.volume = max(0.0, min(1.5, float(volume)))
        if isinstance(balance, (int, float)):
            track.balance = max(-1.0, min(1.0, float(balance)))


def _attach_expression(song, song_dir):
    raw = song_dir / "raw" / "song.json"
    if not raw.exists():
        return
    try:
        data = json.loads(raw.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    raw_tracks = data.get("tracks") or []
    if len(raw_tracks) != len(song.tracks):
        return
    queues = defaultdict(list)
    for e in song.events:
        queues[(e.track, e.measure, e.string, e.fret)].append(e)
    for pos, tr in enumerate(raw_tracks):
        for mi, measure in enumerate(tr.get("measures") or []):
            for voice in measure.get("voices") or []:
                for beat in voice.get("beats") or []:
                    for note in beat.get("notes") or []:
                        bucket = queues.get((pos, mi, note.get("string"), note.get("fret")))
                        if not bucket:
                            continue
                        event = bucket.pop(0)
                        bend = note.get("bend")
                        if isinstance(bend, dict) and bend.get("points"):
                            event.bends = [
                                (float(p.get("position", 0.0)), float(p.get("tone", 0.0)))
                                for p in bend["points"]
                            ]
                        if note.get("staccato"):
                            event.staccato = True
                        if note.get("accentuated"):
                            event.accentuated = True


def _guitar_bus(di_audio, rate, use_nam, amp_name, cab_name, drive):
    mono = di_audio.mean(axis=1) if getattr(di_audio, "ndim", 1) > 1 else di_audio
    proc = None
    if use_nam and namamp.available():
        try:
            proc = namamp.process(mono, rate, name=amp_name, cab_name=cab_name)
        except Exception:
            proc = None
    if proc is None:
        cab = None
        try:
            ir = namamp.cab(cab_name)
            if ir is not None:
                cab = namamp._to_rate(ir, namamp._ir_rate(cab_name), rate)
        except Exception:
            cab = None
        if cab is None:
            cab = ampmod.cabinet_ir(rate)
        proc = ampmod.amp(mono, rate, drive=drive, cab=cab)
    delay = int(0.008 * rate)
    right = np.concatenate([np.zeros(delay, dtype=np.float32), proc])[:len(proc)]
    return np.stack([proc, right], axis=1).astype(np.float32)


def _bass_bus(src, rate):
    mono = src.mean(axis=1) if getattr(src, "ndim", 1) > 1 else src
    proc = ampmod.bass_amp(mono, rate, drive=5.0, cab=ampmod.bass_cabinet_ir(rate))
    delay = int(0.004 * rate)
    right = np.concatenate([np.zeros(delay, dtype=np.float32), proc])[:len(proc)]
    return np.stack([proc, right], axis=1).astype(np.float32)


def _placeholder_tone(rate=44100):
    duration = 5
    t = np.linspace(0, duration, int(duration * rate), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def render_song(song, rate=44100, use_nam=False, amp_name=None, cab_name=None,
                drive=14.0, gain=0.5):
    if not song or not song.events:
        return _placeholder_tone(rate), rate

    sf2 = synth.find_soundfont()
    can_synth = bool(fastsynth.available() and sf2)
    dtracks, gtracks, otracks = synth._stem_tracks(song)
    bass_idx = {t.index for t in otracks if synth.category_of(t) == "bass"}
    bass = [t for t in otracks if t.index in bass_idx]
    other = [t for t in otracks if t.index not in bass_idx]
    buses = []

    if other and can_synth:
        try:
            buses.append(fastsynth.render_array(
                synth._subsong(song, other), sf2, mix=False, rate=rate,
                gain=gain, normalize=False))
        except Exception:
            pass

    if bass and can_synth:
        try:
            bbus = fastsynth.render_array(
                synth._subsong(song, bass), sf2, mix=False, rate=rate,
                gain=gain, normalize=False)
            if bbus is not None and len(bbus):
                buses.append(_bass_bus(bbus, rate))
        except Exception:
            pass

    if gtracks:
        di_bus = None
        if di.available():
            try:
                di_bus = di.render(song, rate=rate, gain=gain,
                                   track_indices={t.index for t in gtracks})
            except Exception:
                di_bus = None
        if di_bus is None and can_synth:
            try:
                di_bus = fastsynth.render_array(
                    synth._subsong(song, gtracks), sf2, mix=False, rate=rate,
                    gain=gain, guitar_program=27, normalize=False)
            except Exception:
                di_bus = None
        if di_bus is not None and len(di_bus):
            buses.append(_guitar_bus(di_bus, rate, use_nam, amp_name, cab_name, drive))

    if dtracks and drums.available():
        try:
            dbus = drums.render(song, rate=rate, gain=0.9)
            if dbus is not None and len(dbus):
                buses.append(dbus)
        except Exception:
            pass

    buses = [b for b in buses if b is not None and len(b)]
    if not buses:
        return _placeholder_tone(rate), rate
    return synth._sum_buses(buses), rate


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
            use_nam = bool(params.get("use_nam"))
            amp_name = params.get("amp_name")
            cab_name = params.get("cab_name")
            drive = float(params.get("drive") or 14.0)
            gain = float(params.get("gain") or 0.5)
            preview = float(params.get("preview") or 0.0)

            song = load_song(artist, slug)
            if not song:
                socketio.emit('render_error', {'error': 'Song not found'}, to=sid)
            else:
                if preview > 0:
                    song = synth.trim_song(song, preview)
                audio, rate = render_song(song, rate=44100, use_nam=use_nam,
                                          amp_name=amp_name, cab_name=cab_name,
                                          drive=drive, gain=gain)
                filename = f"{_sanitize_filename(slug)[:60]}_{uuid.uuid4().hex[:8]}.wav"
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                sf.write(str(OUT_DIR / filename), audio, rate, subtype="PCM_16")
                socketio.emit('render_complete', {'filename': filename}, to=sid)
        except Exception as e:
            socketio.emit('render_error', {'error': str(e) + '\n' + traceback.format_exc()}, to=sid)
        finally:
            render_queue.task_done()


def _engine_status():
    amps = namamp.amps() if namamp.available() else []
    cabs = namamp.cabs()
    return {
        'nam': bool(amps),
        'amps': amps,
        'cabs': cabs,
        'di': di.available(),
        'drums': drums.available(),
        'fastsynth': bool(fastsynth.available() and synth.find_soundfont()),
    }


@app.route('/')
def index():
    songs = load_songs()
    eng = _engine_status()
    song_opts = ''.join(
        '<option value="{a}::{s}">{a} - {t}</option>'.format(
            a=html.escape(s['artist'], quote=True),
            s=html.escape(s['slug'], quote=True),
            t=html.escape(s['title']),
        )
        for s in songs
    )
    amp_opts = ''.join(
        '<option value="{v}">{v}</option>'.format(v=html.escape(a, quote=True))
        for a in eng['amps']
    )
    cab_opts = ''.join(
        '<option value="{v}">{v}</option>'.format(v=html.escape(c, quote=True))
        for c in eng['cabs']
    )
    status = (
        f"engine: DI={'on' if eng['di'] else 'off'} "
        f"drums={'on' if eng['drums'] else 'off'} "
        f"NAM={'on' if eng['nam'] else 'off'} "
        f"synth={'on' if eng['fastsynth'] else 'off'}"
    )
    page = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Riffer Renderer</title>
        <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
        <script src="https://unpkg.com/wavesurfer.js@7"></script>
        <style>
            body { font-family: monospace; padding: 20px; max-width: 820px; }
            #waveform { height: 180px; border: 1px solid #ccc; margin: 20px 0; }
            input, select { width: 100%; margin: 8px 0; padding: 5px; }
            button { padding: 8px 16px; margin: 5px; cursor: pointer; }
            button:disabled { opacity: 0.5; cursor: not-allowed; }
            #status { margin-top: 16px; font-weight: bold; }
            #engine { color: #666; font-size: 12px; margin-top: 4px; }
            label.inline { display: inline-flex; align-items: center; gap: 6px; }
            label.inline input { width: auto; margin: 0; }
        </style>
    </head>
    <body>
        <h1>Riffer Renderer</h1>
        <div id="waveform"></div>
        <div>
            <label>Song:</label>
            <select id="songSelect">__SONG_OPTS__</select>
        </div>
        <div>
            <label class="inline"><input type="checkbox" id="useNam"> NAM amp (slow)</label>
            <select id="ampSelect">__AMP_OPTS__</select>
            <select id="cabSelect">__CAB_OPTS__</select>
        </div>
        <div>
            <label>Amp Drive: <span id="driveVal">14</span></label>
            <input type="range" id="drive" min="1" max="30" step="1" value="14"
                   oninput="document.getElementById('driveVal').textContent = this.value">
            <label>Gain: <span id="gainVal">0.50</span></label>
            <input type="range" id="gain" min="0.1" max="1" step="0.05" value="0.5"
                   oninput="document.getElementById('gainVal').textContent = this.value">
        </div>
        <div>
            <label>Length:</label>
            <select id="previewSelect">
                <option value="0">Full song</option>
                <option value="30">First 30s</option>
                <option value="45">First 45s</option>
                <option value="60">First 60s</option>
            </select>
        </div>
        <button id="renderBtn" onclick="render()">Render</button>
        <button onclick="wavesurfer.playPause()">Play</button>
        <div id="engine">__ENGINE__</div>
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
                renderBtn.disabled = true;
                statusEl.textContent = 'Rendering...';
                socket.emit('render_request', {
                    artist: value.slice(0, idx),
                    slug: value.slice(idx + 2),
                    use_nam: document.getElementById('useNam').checked,
                    amp_name: document.getElementById('ampSelect').value || null,
                    cab_name: document.getElementById('cabSelect').value || null,
                    drive: parseFloat(document.getElementById('drive').value),
                    gain: parseFloat(document.getElementById('gain').value),
                    preview: parseFloat(document.getElementById('previewSelect').value)
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
    '''
    return (page
            .replace('__SONG_OPTS__', song_opts)
            .replace('__AMP_OPTS__', amp_opts)
            .replace('__CAB_OPTS__', cab_opts)
            .replace('__ENGINE__', status))


@app.route('/audio/<filename>')
def get_audio(filename):
    name = Path(filename).name
    if name != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or ".." in name:
        return 'Not found', 404
    path = (OUT_DIR / name).resolve()
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
        'use_nam': bool(data.get('use_nam')),
        'amp_name': data.get('amp_name') if isinstance(data.get('amp_name'), str) else None,
        'cab_name': data.get('cab_name') if isinstance(data.get('cab_name'), str) else None,
        'drive': data.get('drive'),
        'gain': data.get('gain'),
        'preview': data.get('preview'),
        '_sid': request.sid,
    }
    render_queue.put(job)
    emit('render_started', {})


OUT_DIR.mkdir(parents=True, exist_ok=True)

if __name__ == '__main__':
    threading.Thread(target=worker_render, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
