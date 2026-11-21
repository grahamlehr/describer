# Handoff
_Updated 2026-11-21 · branch `claude/train-announcements-audio-gen-bb01c5` · PR not opened yet (public repo: needs Graham's OK to push)_

## Goal
Announcements in an ElevenLabs voice: recorded clips joined on the Pi, Piper as fallback.

## Done
- Clip catalogue, clip plan, voice pack, `/admin` install and remove, docs (`docs/voice-pack.md`, `docs/design/21`).
- `scripts/voicepack.py`: script / cut / review / build / demo. The cutter aligns the script to the recording (no pause tags needed); `review` is a local page with waveform and draggable in/out markers.
- Graham's script (no tags, 35 parts) is in `~/Projects/describer/voicepack/script/`. His Eleven v4 pilot (`audio/00.mp3`) is cut: 18 clips, 2 marked to check.

## Next
1. Graham checks the pilot's two doubtful clips in `review` (`preview_start` `voicepack-review`; in a new worktree first `ln -s ~/Projects/describer/voicepack voicepack`).
2. He records parts 01 onwards in Eleven v4 as `audio/NN.mp3`. Then `cut`, `review`, `build`, install from `/admin` on the Pi.
3. Tune `GAP`, `LIST_GAP`, `SENTENCE_GAP` in `clips.py` by ear once a full pack exists.

## Open decisions (need Graham)
- OK to push the branch and open the PR (public repo).
- How far to record: core + local stations (11k characters), + reasons (33k), or the whole network (95k).

## Gotchas
- Do not re-run `voicepack.py script` without `--topup` once recording has started: audio is matched to the script by line.
- Never commit `voicepack/` or a pack.
- Only the 18-item pilot has been cut from real ElevenLabs audio; not run on the Pi.

## Prompt for the next session
"I've saved more ElevenLabs parts in ~/Projects/describer/voicepack/audio/. Run `scripts/voicepack.py --dir ~/Projects/describer/voicepack cut`, tell me what it reported, open the review page for me, then `build` (see docs/voice-pack.md and docs/design/21)." Sonnet is enough unless the cutter needs redesigning.
