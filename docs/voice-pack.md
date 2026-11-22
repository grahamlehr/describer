# Recorded voices: announcements in a recorded voice

Out of the box the board speaks with Piper. A **recorded voice** replaces
Piper's voice with a human or AI-made one (an ElevenLabs voice, say) by playing
short clips end to end, the way real station announcements are made. Piper
stays as the fallback: any announcement the recorded voice cannot say in full
is spoken by Piper, whole.

You make a voice on the board itself, in `/admin` → Announcements → Recorded
voices. You need no Mac and nothing installed: the board writes the script,
you record it in a voice service and upload the audio, and the board cuts it
into clips, lets you check them, and builds the voice. (A Mac command-line tool
that does the same is described [at the end](#from-the-command-line).)

Several voices can live side by side, each with its own name. One is in use at
a time.

## What gets recorded

| Group | Clips | What |
|---|---:|---|
| Phrases | 23 | "The next train to arrive at platform", "will be the", "calling at", the apologies, the test line; "The next train to arrive at" and "platform" on their own, for a board announcing two stations |
| Numbers | 60 + 10 | "zero" to "fifty nine" (hours, minutes, platforms, minutes late); "hundred hours", "oh one" to "oh nine" |
| Platform letters | 16 | A to P ("platform four B") |
| Operators | 36 | as the feeds name them |
| Stations | 2 each | once as a list item ("Reading,"), once as the end of a sentence ("Reading."); or once, if you tick "Record each station once" |
| Reasons | 484 | Darwin's delay and cancellation reasons |

The full list with its wording is `describer/announce/catalogue.py`. The whole
network is 2,613 distinct station names; your own lines are a few hundred.
A missing operator or reason is simply left out of the sentence. A missing
station, number or phrase hands that announcement to Piper.

## Making a voice on the board

Open `/admin`, go to the **Announcements** tab and find **Recorded voices**.

### 1. Name it and choose the stations

Under **New voice**:

- **Name**: anything, such as "Station voice". It becomes the voice's folder
  name, so two voices cannot share a name.
- **Stations**: the three-letter codes of your stations (PAD, RDG), separated
  by commas. It starts filled in with your configured stations.
- **Distance**: every station within this many kilometres of those is
  recorded too (20 by default).
- **Also every station on the trains showing now**: ticked by default. It adds
  every station named on the board's screen right now, spelled as the feed
  spells it, and uses the top train for the try-out.
- **Every station in Britain**: adds the rest of the network as later parts.
  A much longer script, so only tick it if you mean to record all of it.
- **Record each station once**: tick this if your voice says a name the same
  in a list and at the end of a sentence ("Reading," and "Reading."). ElevenLabs
  v4 does. It halves the station recording, and the board plays the one
  reading in both places. Record the try-out both ways and listen before
  deciding.
- **How much text the voice service takes at once**: *About 5,000 characters*
  suits most services. *100,000 characters* is for ElevenLabs v4, and gives far
  fewer, larger parts (up to 600 items each; a slip means recording that part
  again).

Press **Create voice**. The board writes the script and opens the voice. The
script stays on the board; it says which stations you serve, so it is never
sent anywhere except to you.

The audio is matched to the script line by line, and a voice's script is
written once. To add to a voice later, use the buttons under
[Topping up](#topping-up-and-redoing-clips); to start again with different
stations, make a new voice.

### 2. Record the try-out

The voice opens with its list of script parts, in order of usefulness: `00`
(a try-out of one whole announcement), the core phrases, numbers and train
companies, your stations, the reasons for delays, then the rest of Britain.
Stop wherever your character allowance does.

1. In ElevenLabs open Text to Speech. Pick the model and voice you like the
   sound of, and keep the model, voice and settings the same for every part.
   **Eleven v4 sounds best and is what this was proved with.** It ignores
   pause tags, which is fine: the script does not use them.
2. Press **Download** beside part `00` and paste the text in. Generate, then
   download the audio.
3. Press **Upload recording** beside part `00` and choose the file. It takes
   `.mp3`, `.wav`, `.m4a`, `.flac` and `.ogg`, up to 300 MB. The board refuses
   a file that would leave less than 500 MB free on its disk.
4. The board starts cutting at once. When it says how many clips it made,
   press **Check clips** (or **Review all clips**) and listen: see below.

The try-out costs about 300 characters and shows whether the voice and the
pacing are right before you spend the rest.

If a name is said wrongly, respell it in the downloaded text and generate that
part again. Only the order of the lines matters.

**Download all parts (.zip)** gives every part in one file if you would rather
have them together.

### 3. Record the rest

For each remaining part: download, paste, generate, upload. Name the file
anything; **Replace recording** swaps it for a new take. The board cuts one
part at a time in the background, at low priority, so the announcements and the
screen carry on as normal. The state beside each part shows *Queued*,
*Cutting…* or *N clips, M to check*. A part that failed says why and has a
**Try again** button. **Remove recording** takes the audio and that part's
clips away.

Restarting the board while a part is waiting or being cut loses it. The part
then reads "The cut did not finish. Press Cut again." with a **Try again**
button.

### 4. Check the doubtful clips

**Check N clips** opens the review page in a new tab
(`/admin/voice-review?voice=<name>`). The voice does not pause evenly (a tenth
of a second between "platform," and "one,", nearly as long inside "Honor Oak
Park"), so the cutter marks the cuts it is unsure of as **to check** rather
than pretending. The page lists the clips, the doubtful ones first:

- **Play** a clip (Space), or Play through to hear them one after another.
- **Move a cut**: the waveform shows the clip in its recording with the
  neighbouring clips shaded. Drag the *in* and *out* markers, listen, Save cut.
  The clip is written again from the recording, and a hand cut is kept for as
  long as the recording is the same file.
- **Sounds right** (Enter) clears the mark. **Reject** (X) leaves the clip out
  of the voice.

Do not save changes in the review page while that voice is being cut. Wait for
*Cutting…* to finish first; the page does not stop you.

### 5. Build it, then use it

Press **Build voice**. It zips the clips into the voice (a few seconds to a
minute) and the button then reads **Built**. If you change a clip or add a
recording afterwards the voice shows *changed since it was built*; press
**Build voice** again to include the change, or use it as it is.

Press **Use this voice**. It takes effect at the next announcement, and the
voice in the list shows *In use*. **Test announcement** (with the box empty)
plays the voice's own test line. **Stop using** (or the **Use** button on
*Piper*) returns the board to Piper.

If you have unticked "Speak with the recorded voice" higher
up, the page warns that Piper is still speaking. **Use this voice** ticks it
again for you.

### Topping up and redoing clips

- **Re-record rejected clips (N)**: adds a short extra part holding the clips
  you rejected. Download it, record, upload, check, build again.
- **Add these to the voice's script**: the board keeps a list of clips it
  wanted and did not have (shown under the tick box, and as
  `voice_pack.missing` in `/api/status`). This adds them to the voice in use
  as a new part.

Extra parts are numbered from 90, so they never disturb the parts already
recorded.

### Freeing up space

Recordings and loose clips are big (a voice for the whole network is about
320 MB of clips; your own lines, about 40 MB; the recordings come on top).
**Free up space** removes the recordings and the loose clips and keeps the
built voice, so it still speaks. The catch: to adjust a cut later you must
upload the recordings again. **Delete** removes a voice entirely.

### A ready-made pack

If someone has already made a pack (a `voicepack.zip`), press **Install a
ready-made voice pack (.zip)** and choose it. It becomes a voice of its own,
named from the pack. There is nothing to record, so **Use** it straight away.

### Updating the board for MP3 and other formats

The board needs `ffmpeg` to read anything but WAV. The installer adds it, and
the board picks it up the next time it updates itself (Status tab, then
Update). Until then the page says "MP3 files cannot be read until the board has
been updated", and WAV uploads still work.

## Notes

- A voice for the whole network is about 320 MB; your own lines, about 40 MB.
- Voices are kept under `announcements.voice_packs_dir` (default `voicepacks/`),
  one folder each: `voice.json`, `script/`, `audio/`, `clips/`, `cuts.json` and
  the built `pack.zip`. The choice of voice in use is `announcements.recorded_voice`
  (a folder name; empty means use the older `announcements.voice_pack` zip, or
  Piper if there is none). Neither is a profile setting, and you do not edit
  them by hand: the buttons write them.
- Nothing from a voice is ever committed: `voicepacks/`, `voicepack/` and
  `voicepack.zip` are gitignored, and the repo is public. Check your
  ElevenLabs plan's terms before sharing a voice (the free plan is
  non-commercial and wants attribution).
- Wording lives in two places, `phrasing.py` (Piper) and `catalogue.py` +
  `clips.py` (clips); `tests/test_clips.py` holds them together.
- Not yet tried on the Pi. On the Mac a 24-minute part took about 10 seconds
  to cut. Decoding holds about two bytes per sample in memory, so a very long
  part may be slow on a Pi 4B; cut time and memory there are unmeasured.

## From the command line

The same work can be done on a Mac with `scripts/voicepack.py`, which shares
its code with the board (`describer/announce/workshop.py`). It keeps a single
working folder, `voicepack/` (gitignored), and the finished `voicepack.zip` is
installed on the board with **Install a ready-made voice pack**. Use it if you
want to cut on something faster than a Pi.

### Write the script

```bash
.venv/bin/python scripts/voicepack.py script --near PAD --km 20 --board http://describer.local:8080
```

- `--near` takes your stations' CRS codes (comma-separated); every station
  within `--km` of them is recorded.
- `--board` also takes every station on a running board's screen, spelled as
  the feed spells it, and uses its top train for the pilot.
- `--all-stations` adds the rest of the network as later parts.
- `--one-reading` records each station once instead of twice (the same as
  "Record each station once" above).
- `--chars` and `--items` set the most characters and items in a part. For
  Eleven v4, which takes 100,000 characters, use
  `script --chars 100000 --items 600`.
- `--breaks 1.0` writes `<break time="1.0s" />` after every item, for models
  that obey it (Multilingual v2, Flash). It roughly triples the characters and
  is not needed.

It writes `voicepack/script/NN-*.txt` (the text to paste), a `.json` beside
each (which clip each line is) and `INDEX.md` (sizes). It refuses to run again
over an existing script without `--force`; do not force it once you have
started recording.

Record each part as in step 2 above, saving it as `voicepack/audio/<number>.mp3`
(`03 take 2.mp3` is fine; the newest name wins).

### Cut, then check

```bash
.venv/bin/python scripts/voicepack.py cut
.venv/bin/python scripts/voicepack.py review
```

`cut` needs `ffmpeg`. It writes one levelled 22,050 Hz WAV per item to
`voicepack/clips/`. `review` serves the same review page the board uses, at
`http://localhost:8091` (Claude: `preview_start` `voicepack-review`), for the
working folder; it listens on 127.0.0.1 only, because it rewrites clips.

Rejected clips are recorded again as a short extra part:

```bash
.venv/bin/python scripts/voicepack.py script --redo
```

### Build, listen, install

```bash
.venv/bin/python scripts/voicepack.py build --name "Station voice"
.venv/bin/python scripts/voicepack.py demo --kind arriving delayed cancelled
```

`demo` plays the pilot's train from the pack. `demo --board
http://describer.local:8080` plays the trains on the board now.

Then on the board: `/admin` → Announcements → Recorded voices → **Install a
ready-made voice pack (.zip)** → choose `voicepack/voicepack.zip`, then **Use**.

To record just what the board has found missing:

```bash
.venv/bin/python scripts/voicepack.py script --topup http://describer.local:8080
```

It adds parts numbered from 90, as `--redo` does. Record, `cut`, `review`,
`build`, install again.
