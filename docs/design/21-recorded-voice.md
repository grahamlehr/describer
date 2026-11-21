# Addendum 21 — recorded voice (voice packs)

## Decision

Announcements can be spoken from recorded clips instead of Piper. A *voice
pack* is one zip (`voicepack.zip`: `manifest.json` + `clips/<key>.wav`, 22,050
Hz mono 16-bit) installed from `/admin`. `announce/catalogue.py` lists every
clip and its wording; `announce/clips.py` turns a `Service` into a plan of clip
keys and pauses and joins them into one WAV; `announce/cutter.py` and
`scripts/voicepack.py` make a pack from long recordings on the Mac.
`docs/voice-pack.md` is the how-to.

## Why

- **Clips, not whole sentences.** Times, platforms, operators and calling
  lists multiply into millions of sentences; 143 fixed clips plus two per
  station say all of them. It is also how the real thing sounds.
- **Two recordings per station** (`.mid`, `.end`): the voice falls at the end
  of a sentence and not inside a list. One inflection is used for the other
  only when its twin is missing.
- **Fallback is the whole announcement.** One Piper word inside a recorded
  sentence is worse than either voice. Operator and reason are the exception:
  they are left out, because the sentence stands without them. Everything
  wanted and absent is kept (`voice_pack.missing`) for `script --topup`.
- **Keyed by spoken name, found by CRS too.** Destinations arrive as names
  only; calling points carry a CRS. The feed's spelling and NaPTAN's differ
  (ZLW is not in NaPTAN at all), so the pack's manifest maps CRS to slug and
  the planner tries both. Brackets are dropped: both Bramleys share a clip.
- **One zip, read in place.** No unpacking on the Pi, one file to upload,
  replace or delete; stored uncompressed (speech barely deflates).
- **Cut by count, not by threshold.** The script says how many items a
  recording holds; the cutter takes the N-1 longest silences and refuses when
  they are not clearly longer (x1.4) than the next one. A fixed "silence longer
  than X" cannot work when the voice chooses X.
- **Not committed.** The repo is public, the audio is licensed to whoever
  generated it, and a script written for `--near` says where the board is.

## Numbers

- Catalogue: 21 phrases, 60 numbers, 10 minute forms, 16 letters, 36
  operators, 484 reasons, 2,613 station names x 2 = 5,853 clips.
- Script: 95,209 characters for everything; 11,220 for core plus the 297
  stations within 20 km of two neighbouring stations; reasons add 21,675.
  Parts are capped at 4,500 characters and 200 items (ElevenLabs' web page
  takes 5,000).
- Stand-in run with macOS `say` (Daniel): 5 parts, 659 clips, 3.3 s to cut;
  shortest pause 200-210 ms; 33 clips compared with the same item recorded
  alone, none out by more than 25%. 641 clips = 35 MB.
- Pauses added on playback: 30 ms between clips, 140 ms in a list, 450 ms
  between sentences. Untuned against a real voice.

## Traps

- **Not yet heard in an ElevenLabs voice.** Everything was proved with `say`.
  Whether a comma-ended item gets a carrying inflection, and whether blank
  lines give pauses clearly longer than those inside a name, is what the pilot
  part (`00-pilot`) is for. `--breaks` is the fallback.
- Re-running `script` reorders or re-chunks the parts and silently mismatches
  any audio already recorded; it refuses without `--force`.
- `spell_number` used to raise on 60 and over, so a train an hour late crashed
  its own announcement. It now goes to 999; the clip plan says "is delayed"
  without a length from 60 up.
- A dev server with announcements on plays through the Mac's speakers.

## How checked

`tests/test_clips.py`: the plan's words equal `build_text`'s for twenty
service shapes; missing-clip behaviour; pack validation; cutter on synthetic
tone; upload, bad upload and removal through the API. End to end by hand with
the `say` stand-in, and the pack uploaded to a local server and seen on
`/admin` (local only; not on the Pi).
