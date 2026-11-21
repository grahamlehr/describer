# Handoff
_Updated 2026-11-21 · branch `claude/train-announcements-audio-gen-bb01c5` · PR not opened yet (public repo: needs Graham's OK to push)_

## Goal
Announcements in an ElevenLabs voice: recorded clips joined on the Pi, Piper as fallback.

## Done
- Clip catalogue, clip plan, voice pack, cutter, `scripts/voicepack.py` (script / cut / build / demo), `/admin` install and remove, docs (`docs/voice-pack.md`, `docs/design/21`).
- Proved end to end with macOS `say` standing in for ElevenLabs. Local only.
- Graham's recording script is written to `~/Projects/describer/voicepack/script/` (gitignored, 35 parts).

## Next
1. Graham records `00-pilot.txt` in ElevenLabs and saves it as `~/Projects/describer/voicepack/audio/00.mp3`.
2. Cut, build and demo the pilot; judge inflection and pacing by ear. Tune `GAP`, `LIST_GAP`, `SENTENCE_GAP` in `clips.py` if joins sound rushed or slow.
3. If the pilot will not cut, re-run `script --force --breaks 1.0` (same `--near`/`--board`) and re-record the pilot.
4. Record the remaining parts, cut, build, install on the Pi from `/admin`.

## Open decisions (need Graham)
- OK to push the branch and open the PR (public repo).
- How far to record: core + local stations (11k characters), + reasons (33k), or the whole network (95k).

## Gotchas
- Do not re-run `voicepack.py script` without `--topup` once recording has started: audio is matched to the script by line.
- Never commit `voicepack/` or a pack.
- Not yet heard in a real ElevenLabs voice, and not run on the Pi.

## Prompt for the next session
"I've saved the ElevenLabs pilot as ~/Projects/describer/voicepack/audio/00.mp3. Run `scripts/voicepack.py --dir ~/Projects/describer/voicepack cut`, `build` and `demo`, tell me what the cutter reported, and fix anything that sounds wrong (see docs/voice-pack.md and docs/design/21)." Sonnet is enough unless the cutter needs redesigning.
