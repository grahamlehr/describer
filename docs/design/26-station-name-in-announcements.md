# 26. Naming the station when two stations announce

## Decision

When two stations are on the screen and both have announcements switched on,
an arrival says which station it is for:

- "The next train to arrive at Sydenham platform one will be the…"
- "The next train to arrive at Sydenham will be the…" (no platform known)

With one station on screen, or two with only one announcing, the wording is
what it always was ("…at platform one…", "…at this station…").

The name spoken is the board's own name (`Board.name`), the one on the screen.

## Reason

"Platform one" means two different places on a split board. With one station
announcing there is nothing to confuse it with, so the classic wording stays.

## What counts as "two stations announcing"

`AnnouncementScheduler.on_boards` counts the stations in the active (resolved)
config that have a board and `announce: true`. Two or more names the station.
It is counted on every poll, so a profile that switches one station's
announcements off goes back to the classic wording by itself. The global
announcements switch is checked before this, as before.

## Recorded voices

Two new fixed phrases in `catalogue.PHRASES`:

| Key | Words |
|---|---|
| `p.next_at` | "The next train to arrive at," |
| `p.platform` | "platform," |

The plan is `p.next_at`, the station's clip (list reading, falling back to the
other as any station does), then `p.platform` and the number when there is a
platform. A new voice records both phrases in its core part (145 items, was 143).

A voice made before this has neither clip. Per the standing rule, the whole
announcement falls back to Piper, never one word of it. So on a board with two
stations announcing, an older voice speaks delays and cancellations and Piper
speaks the arrivals, until the two clips are added: /admin lists them as missing, and "Add these to
the voice's script" makes a short top-up part to record.

## Trap found

A fixed phrase missing from a pack was remembered with no words, and a clip
with no words is left out of the top-up script. `build_plan` now gives a
missing `p.*` clip its words from `PHRASES`, so the top-up asks for it.

## Not done

Delay and cancellation apologies do not name the station. There is no natural
slot for it in "We are sorry to announce that the 14:32 … service to … is
delayed", and it was not asked for.

## How checked

Unit tests only: `test_phrasing.py`, `test_clips.py` (both voices say the same
words, with a platform, a lettered platform and none; an older pack asks for
the two phrases), `test_announce_scheduler.py` (two announcing, one of two
announcing, a delay). Not heard on a device.
