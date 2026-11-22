# 27. The Announcements tab, rearranged

## Decision

The Announcements tab of `/admin` is grouped by what the user is deciding,
in this order:

1. **What is announced**: lead time, "Stations read out"
   (`announcements.max_calling_points`), delays, cancellations.
2. **Sound**: volume, output, chime, and the test button beside them.
3. **Voice**: what is speaking now, "Speak with the recorded voice"
   (`use_voice_pack`), the list of voices, installing a ready-made pack, and
   "Make a new voice" folded away until wanted.
4. **Built-in voice**: the Piper voice name, with a line saying when it speaks.
5. **File locations** (folded): the Piper program, its voices folder, the
   cache folder and the hand-placed voice pack file.

## Reason

The tab grew from "Piper settings" into a voice maker, and still read as the
former: the Piper voice name came first, and the limit on stations read out sat
under "Piper paths and limits", where nobody looking for it would open. It
applies to both voices and is the reason a long train's announcement skips
stops, so it now sits with the other choices about what is said.

## Why there is a limit at all

Not a technical one for the recorded voice, which joins clips in well under a
second. It keeps an announcement short enough that the next one is not late:
announcements are spoken one at a time, and a six-stop arrival in the recorded
voice already runs to 20 seconds (measured on a sample; a 15-stop list was not
timed). Piper also takes longer to make a longer sentence on the Pi. The
range stays 1 to 30 and the default 8.

## Unchanged

No config key, input `name`, element id or API changed, so the form saves
exactly what it saved before. Only markup order, headings and labels moved.

## How checked

Local, in a browser: the tab at desktop width and at 375 px (no sideways
scroll), "Make a new voice" opening, no console errors. Not checked on the Pi.
