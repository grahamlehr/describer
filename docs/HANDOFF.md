# Handoff
_Updated 2026-11-21 · branch `claude/voice-maker-on-pi`, stacked on PR 35 (`claude/train-announcements-audio-gen-bb01c5`) · uncommitted · not verified on the Pi_

## Goal
Make a recorded voice on the board itself (`/admin` → Announcements → Recorded voices), so an owner needs no Mac. Announcements stay Piper-backed for anything the voice cannot say.

## Done
- Voice maker (note `docs/design/24-voice-maker.md`, how-to `docs/voice-pack.md`): several named voices under `announcements.voice_packs_dir`; create, download script, upload audio, background cut, review page, build, use, free up space, delete, import a ready-made pack. `announcements.recorded_voice` holds the one in use.
- Code: `describer/announce/workshop.py`, `describer/announce/voices.py`, `/api/voices` routes in `main.py`, `web/static/voices.js`, `web/static/voice-review.html` (moved from `scripts/`), `tests/test_voices.py`. `scripts/voicepack.py` is a thin CLI over `workshop.py`. `ffmpeg` added to `deploy/install.sh`.
- Checked in a browser on the Mac with real ElevenLabs recordings: upload, cut, review, build, use, free, delete; phone width.
- The earlier pilot work (PR 35, note 23) is unchanged.

## Next
1. Try it on the Pi: update the board (so `ffmpeg` installs), make a voice, upload the pilot (`00`), and time the cut and watch memory (`top`) while the board keeps running.
2. Record and upload the remaining parts, check, build, use.
3. Tune `GAP`, `LIST_GAP`, `SENTENCE_GAP` in `clips.py` by ear once a full voice exists.
4. Commit, push and open the PR (public repo: needs Graham's OK). It stacks on PR 35, so merge that first or retarget.

## Open decisions (need Graham)
- OK to push the branch and open the PR (public repo).
- How far to record: core + local stations (11k characters), + reasons (33k), or the whole network (95k).

## Gotchas
- Not run on a Pi: cut time and memory there are unmeasured (a 24-minute part took about 10 s on the Mac).
- Queued jobs are lost on restart; the part shows "The cut did not finish" with a retry button.
- A review save made while the same voice is being cut could be lost (no lock).
- "Stop using" with an older `voicepack.zip` still in place falls back to that zip, not Piper.
- Never commit `voicepacks/`, `voicepack/` or a pack.

## Prompt for the next session
"On the branch claude/voice-maker-on-pi, I've updated the Pi. Help me try the voice maker on it: read docs/voice-pack.md and docs/design/24-voice-maker.md, check `/api/status` over HTTP for what is running, and tell me what to look for while a part cuts." Sonnet is enough.
