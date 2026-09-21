# Corpus Layout and `notes.json` Schema

The renderer reads a local tab corpus bundled inside this project. By default it looks at
`corpus/songs` under the project root (override with the `RIFFER_CORPUS` env var).

## Directory layout

```
corpus/songs/
  <Artist>/
    <slug>/
      notes.json          # canonical flattened event stream (what the renderer reads)
      manifest.json
      timeline.json
      raw/
        song.json         # raw Songsterr model (measures, mix, effects)
        video_points.json
        parts/
          0.json
          1.json
          ...
    (old)/
      <slug>/             # superseded copies — ignored by discovery
```

Discovery is `CORPUS_ROOT/*/*/notes.json`, so a song is visible only if it has a
`notes.json` directly inside `<Artist>/<slug>/`. Any path containing a `(old)` segment
is skipped.

In practice the corpus also contains a root-level sibling layout used by other tools;
only `<Artist>/<slug>/notes.json` is relevant here.

## `notes.json`

Top-level keys:

| Key | Type | Notes |
| --- | --- | --- |
| `format` | string | corpus format marker |
| `id` | string | source id (Songsterr song id) |
| `title` | string | display title |
| `artist` | string | display artist |
| `tracks` | array | see below |
| `event_count` | int | number of events |
| `events` | array | flattened note events, sorted by `onset_beat` |

### Track object

| Field | Type | Notes |
| --- | --- | --- |
| `index` | int | track index |
| `part_id` | int | source part id |
| `name` | string | track name (often empty) |
| `instrument_id` | int | GM program; `1024` means "unset" and maps to 0 |
| `instrument_name` | string | e.g. `Distortion Guitar` |
| `category` | string | `guitar`, `bass`, `drums`, ... |
| `tuning` | int[] | MIDI open-string values (low to high) |
| `capo` | int | capo fret |
| `strings` | int | string count |
| `is_percussion` | bool | true for the drum track |

Drums are detected from `is_percussion` or `category == "drums"` and are assigned MIDI
channel 9. Other tracks are assigned channels 0-8, 10+, skipping 9.

### Event object

| Field | Type | Used by renderer |
| --- | --- | --- |
| `track` | int | yes |
| `part_id` | int | no |
| `track_name`, `instrument_id`, `instrument_name`, `category`, `is_percussion` | mixed | no |
| `measure` | int | no |
| `voice` | int | no |
| `beat` | float | no |
| `onset_beat` | float | yes (fallback timing / sort) |
| `onset_ms` | float | yes (preferred absolute timing) |
| `duration_beats` | float | yes (fallback duration) |
| `duration_ms` | float | yes (preferred absolute duration) |
| `tempo_bpm` | float | yes (tempo map) |
| `time_signature` | string | no |
| `key_signature` | object / null | no |
| `pitch` | int | yes (MIDI note) |
| `string` | int | no (kept for future fretboard work) |
| `fret` | int | no |
| `capo` | int | no |
| `velocity` | int / null | yes (falls back to `dynamic`) |
| `dynamic` | string | yes (`ppp`..`fff` -> velocity) |
| `palm_mute` | bool | carried, not yet applied |
| `dead` | bool | carried, not yet applied |
| `ghost`, `hammer`, `bend`, `tie` | bool | no |
| `slide`, `harmonic` | string / null | no |

Events with a non-numeric `pitch` are dropped. Velocity resolution:

1. Use `velocity` if it is a number.
2. Otherwise map `dynamic` through `DYNAMIC_VELOCITY`
   (`ppp`:20, `pp`:32, `p`:45, `mp`:58, `mf`:72, `f`:88, `ff`:104, `fff`:120).
3. Otherwise default to 90.

Velocity is clamped to `1..127`.

## Timing model

`notes.json` is generated from a tempo map upstream, so `onset_ms`/`duration_ms` are
already correct absolute times even when the tempo changes mid-song. The renderer uses
them directly. If a file lacks the ms fields, `render_song` builds a cumulative
beat-to-seconds map from the per-event `tempo_bpm` values instead.

`raw/song.json` is the unflattened Songsterr model (measures, per-track `volume`/
`balance`, effects). The renderer reads only its per-track `volume`/`balance`
(`_attach_mix`) to seed the mix; the note data comes entirely from `notes.json`.
