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
  only when its twin is missing. That makes a pack with one reading per
  station a whole pack: ElevenLabs v4 read the comma and full-stop scripts so
  alike that the first pack holds one (`script --one-reading`). The two keys
  stay, for a voice that does tell them apart.
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
- **Cut by aligning the script, not by a silence threshold.** The model that
  sounds best (Eleven v4) ignores `<break>` tags, and its pauses between items
  ran from 100 to 430 ms in the first real take, against 90 ms inside a phrase.
  "The N-1 longest silences" was right on that take by 10 ms. So the cutter
  knows what each item says, estimates its length from its letters (0.2 s plus
  a pace taken from the recording's median item), and a dynamic programme
  picks the cuts that best trade three costs: a piece the wrong length, a
  pause left inside a piece, a cut at a short pause.
- **Doubt is reported, not refused.** An item 1.7x off its expected length, or
  cut at under 0.4 of the usual pause, is marked to check. `voicepack.py
  review` is a local page (127.0.0.1 only: it rewrites clips) that plays each
  clip and shows its waveform in the recording with draggable in and out
  markers; `cuts.json` holds every clip's place, hand cuts and rejections.
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

- **One real take so far**: the 18-item pilot in Eleven v4, cut correctly with
  two items marked to check (either side of the 100 ms pause). The 200-item
  parts are proved only with `say`, whose pauses are even. Two items read with
  no gap at all cannot be separated by anything here; they show up as doubtful
  and are fixed by hand or re-recorded.
- The weights (`FIT_WEIGHT` 1.5, `INSIDE_WEIGHT` 2, `SHORT_CUT_WEIGHT` 0.2)
  were set on that take and one synthetic case. With the short-cut penalty at
  0.5 the cutter preferred splitting "Honor Oak Park" to cutting at 100 ms.
- Re-running `script` reorders or re-chunks the parts and silently mismatches
  any audio already recorded; it refuses without `--force`.
- `spell_number` used to raise on 60 and over, so a train an hour late crashed
  its own announcement. It now goes to 999; the clip plan says "is delayed"
  without a length from 60 up.
- A dev server with announcements on plays through the Mac's speakers.

## How checked

`tests/test_clips.py`: the plan's words equal `build_text`'s for twenty
service shapes; missing-clip behaviour; pack validation; cutter on synthetic
tone, including a short pause in the right place against a longer one in the
wrong place; cut, hand cut, reject, redo and build through the tool; upload, bad upload and removal through the API. End to end by hand with
the `say` stand-in, and the pack uploaded to a local server and seen on
`/admin` (local only; not on the Pi).
