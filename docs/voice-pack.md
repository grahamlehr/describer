# Voice pack: announcements in a recorded voice

Out of the box the board speaks with Piper. A **voice pack** replaces Piper's
voice with a recorded one (an ElevenLabs voice, or a person) by playing short
clips end to end, the way real station announcements are made. Piper stays as
the fallback: any announcement the pack cannot say in full is spoken by Piper,
whole.

Everything here runs on the Mac. The Pi only receives the finished
`voicepack.zip`, uploaded from `/admin`.

## What gets recorded

| Group | Clips | What |
|---|---:|---|
| Phrases | 21 | "The next train to arrive at platform", "will be the", "calling at", the apologies, the test line |
| Numbers | 60 + 10 | "zero" to "fifty nine" (hours, minutes, platforms, minutes late); "hundred hours", "oh one" to "oh nine" |
| Platform letters | 16 | A to P ("platform four B") |
| Operators | 36 | as the feeds name them |
| Stations | 2 each | once as a list item ("Reading,"), once as the end of a sentence ("Reading.") |
| Reasons | 484 | Darwin's delay and cancellation reasons |

The full list with its wording is `describer/announce/catalogue.py`. The whole
network is 2,613 distinct station names; your own lines are a few hundred.
A missing operator or reason is simply left out of the sentence. A missing
station, number or phrase hands that announcement to Piper.

## 1. Write the script

```bash
.venv/bin/python scripts/voicepack.py script --near PAD --km 20 --board http://describer.local:8080
```

- `--near` takes your stations' CRS codes (comma-separated); every station
  within `--km` of them is recorded.
- `--board` also takes every station on a running board's screen, spelled as
  the feed spells it, and uses its top train for the pilot.
- `--all-stations` adds the rest of the network as later parts.

It writes `voicepack/script/NN-*.txt` (the text to paste), a `.json` beside
each (which clip each line is) and `INDEX.md` (sizes). Parts come in order of
usefulness: `00-pilot`, core, your stations, reasons, the rest of the network.
Stop wherever your character allowance does. **The audio is matched to the
script line by line, so do not re-run `script` once you have started
recording**; `--topup` (below) adds parts without disturbing it.

## 2. Record it (ElevenLabs)

1. Text to Speech, model **Eleven Multilingual v2**. Pick the voice. Set
   stability high (about 75%) so it reads every item the same way, speed 1.0,
   and do not change voice or settings between parts.
2. Paste **`00-pilot.txt`** only. Generate, download, save as
   `voicepack/audio/00.mp3`.
3. Run steps 3 to 5 below on the pilot alone and listen. This costs about 300
   characters and tells you whether the voice, the pacing and the cutting work
   before you spend the rest.
4. Then each remaining part: paste, generate, save as `audio/<number>.mp3`
   (`03 take 2.mp3` is fine; the newest name wins).

Each part fits the web page's 5,000-character limit. If a name is said wrongly,
respell it in the `.txt` and generate that part again; only the order of the
lines matters.

**If the pilot will not cut** (the voice runs items together), write the
script again with explicit pauses and start over from the pilot:

```bash
.venv/bin/python scripts/voicepack.py script --force --breaks 1.0 --near ... 
```

That puts `<break time="1.0s" />` after every item. It works on Multilingual
v2 and Flash, not on Eleven v3. It roughly triples the characters pasted, and
ElevenLabs warns that many breaks in one generation can upset the voice, which
is why it is not the default.

## 3. Cut

```bash
.venv/bin/python scripts/voicepack.py cut
```

Needs `ffmpeg`. Each recording is cut at its longest silences into exactly as
many clips as its script has lines, levelled, and written to
`voicepack/clips/` as 22,050 Hz mono WAV. A part whose pauses are ambiguous is
refused, not guessed. `listen to …` lines name clips whose length does not suit
their words; play those before trusting the part.

## 4. Build and listen

```bash
.venv/bin/python scripts/voicepack.py build --name "Station voice"
.venv/bin/python scripts/voicepack.py demo --kind arriving delayed cancelled
```

`demo` plays the pilot's train from the pack. `demo --board
http://describer.local:8080` plays the trains on the board now.

## 5. Install

`/admin` → Announcements → Recorded voice → choose `voicepack/voicepack.zip` →
Install voice pack. It takes effect at the next announcement. Test
announcement (with the box empty) plays the pack's own test line. Remove
returns the board to Piper; so does unticking "Use the recorded voice".

## Topping up

The board keeps a list of clips it wanted and did not have (`/admin`, same
panel; `voice_pack.missing` in `/api/status`). To record just those:

```bash
.venv/bin/python scripts/voicepack.py script --topup http://describer.local:8080
```

It adds parts numbered from 90. Record, `cut`, `build`, install again.

## Notes

- A pack for the whole network is about 320 MB; your own lines, about 40 MB.
- The pack is never committed: `voicepack/` and `voicepack.zip` are
  gitignored, and the repo is public. Check your ElevenLabs plan's terms
  before sharing a pack (the free plan is non-commercial and wants
  attribution).
- Wording lives in two places, `phrasing.py` (Piper) and `catalogue.py` +
  `clips.py` (clips); `tests/test_clips.py` holds them together.
