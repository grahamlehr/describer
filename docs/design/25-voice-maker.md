# Addendum 25 — the voice maker (recorded voices made on the board)

## Decision

A recorded voice (note 23) is made on the board, in `/admin` → Announcements →
Recorded voices, not on a Mac. The owner names a voice and chooses stations;
the board writes the script; the owner records it in a voice service and
uploads the audio for each part; the board cuts each in a background job; the
owner checks the doubtful clips on a review page, builds, and presses "Use this
voice". `docs/voice-pack.md` is the how-to.

- Code: `announce/workshop.py` (the working-folder logic, moved out of
  `scripts/voicepack.py`, also runnable as `python -m describer.announce.workshop
  cut|build`), `announce/voices.py` (`VoiceStore`, `JobRunner`), the
  `/api/voices` routes in `main.py`, `web/static/voices.js`,
  `web/static/voice-review.html`. `scripts/voicepack.py` is now a thin CLI over
  `workshop.py` and works as before.
- Several named voices live side by side under `announcements.voice_packs_dir`
  (`voicepacks/`, gitignored), one folder each: `voice.json`, `script/`,
  `audio/`, `clips/`, `cuts.json`, `pack.zip`.
- `announcements.recorded_voice` holds the folder name in use. Empty means the
  older `announcements.voice_pack` zip, else Piper.
- A ready-made pack zip is still accepted (`PUT /api/voices/import`); it becomes
  a voice with nothing to record. This replaces the old `PUT`/`DELETE
  /api/voicepack`.

## Why

- **On the board, so no Mac is needed.** The people who own a board are not the
  people who run `uv` and `ffmpeg`. The whole job is a web form, a file upload
  and a review page.
- **Several named voices.** One slot meant a bad take replaced a good voice.
  Now a new voice is built beside the old one and switched to when it is ready;
  Delete is a separate button.
- **Jobs are a subprocess, not a thread.** Cutting is CPU-bound Python and numpy
  work. The poller, the SSE streams and the announcements share one event loop
  and one GIL; a thread would stall them all for the length of a cut. A child
  process at `nice 10` (`python -m describer.announce.workshop`) leaves the board
  running, and a crash or memory kill takes only the job. `JobRunner` runs one
  job at a time, in the order asked, and keeps its state in memory.
- **One review page for the board and the CLI.** `web/static/voice-review.html`
  (moved from `scripts/`) asks `/api/voices/<voice>/review/*`; the board serves
  those from `main.py`, the CLI's local server serves the same shapes under the
  voice `local`. The page reads `?voice=` and defaults to `local`.
- **ffmpeg from apt.** The only reliable way to read MP3, M4A and the rest.
  `deploy/install.sh` lists it in `install_packages`; `provision` runs on every
  update, so an existing board gets it on its next update. Without it only WAV
  uploads work, and the page says so rather than failing late.
- **Recordings are kept until freed.** Moving a cut later needs the original
  audio. They are big, so "Free up space" removes recordings and loose clips and
  keeps the built `pack.zip`; the page shows free space and sizes. The price is
  stated before it is paid: to adjust a cut afterwards you upload again.
- **`recorded_voice` and `voice_packs_dir` are not profile-overridable.** They
  are machine state like the other `announcements` paths: a schedule profile
  must not switch the voice or point at another folder. `_set_recorded_voice`
  writes the raw file (`ConfigStore.get()`), never `active()`.
- **The script never leaves the board.** It says which stations the board
  serves, so it is downloaded only by the owner, from the board, to paste
  into the voice service by hand. Voices are gitignored (`voicepacks/`).

## Numbers

- Upload limit 300 MB a file; refused with a 507 when the free disk would fall
  under 500 MB (checked while streaming, so a half-sent file is dropped).
  An imported pack is limited to 1 GB.
- Paste sizes: 5,000 characters gives parts of at most 4,500 characters and 200
  items; 100,000 gives 100,000 and 600 items. Extra parts (rejected clips,
  clips the board has needed) are numbered from 90.
- Cutting a 24-minute part took about 10 s on the Mac. Decoding holds about
  two bytes per sample in memory, so a 24-minute 22,050 Hz part is about 64 MB
  as samples. Not measured on a Pi.

## Traps

- **Not verified on a Pi.** Cutting time and memory there are unmeasured. The
  background job's `nice` and a single job at a time are the safeguards, not a
  measurement.
- **Queued jobs are lost on restart.** The state is in memory, so a part that was
  queued or cutting then reads "The cut did not finish" with a retry button.
- **A review save during a cut can be lost.** There is no lock on `cuts.json`;
  the page keeps the two apart in practice (it asks the owner to wait) and does
  not enforce it.
- **Inputs in the voices section have no `name`.** `admin.js` saves every named
  field in the config form, so a field with a `name` would be written to config
  (note 13 and `traps.md`). `voices.js` also stops `input` and `change` at the
  container so the form is not marked unsaved, and swallows Enter so it does
  not submit the form. The two paste-size radios are grouped by hand for the
  same reason.
- **The review routes have two homes**: `main.py` and the local server in
  `scripts/voicepack.py`. Change one and the other drifts (`traps.md`).
- **A pack file put in place by hand still speaks when no voice is chosen**,
  because an empty `recorded_voice` means the older `announcements.voice_pack`
  path. The list says so ("Voice pack file on the board") instead of showing
  Piper as in use (`legacy_pack` in `GET /api/voices`); unticking the box
  silences it.
- **Use needs a built voice**: `POST /api/voices/use` answers 409 until
  `pack.zip` exists, and the button is disabled with a hint.

## How checked

`tests/test_voices.py`: the whole flow (create, upload, cut, review, build, use)
through `JobRunner` with an inline executor; a failed cut and its retry;
replacing a recording; duplicate names and a script with no stations; bad
slugs, part numbers and file types; an empty upload and missing ffmpeg; size
and free-disk limits; use before build; delete during a job; import of a good
and a bad pack; profiles unable to change the voice; slugs matching the pattern;
the real subprocess path; and the CLI's local review server answering the
board's routes. By hand on a Mac in a browser against real
ElevenLabs recordings: create, upload, cut, review, build, use, free up space,
delete; the page checked at phone width. Not on a Pi.
