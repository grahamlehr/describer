"""Cuts one long recording into the clips it was scripted as.

A part of the recording script is a list of short items. The recording comes
back as one file with a pause after each item. This finds the items again.

Nobody can say how long those pauses are. The best voices take no
instruction about pausing, read "platform," and "one," a tenth of a second
apart, and pause nearly that long inside "Honor Oak Park". So the cut is not
made on silence alone. The script says what each item is, and speech runs at
a steady pace, so each item's length is known roughly before it is found. The
cutter lays the script against the recording and takes the set of cuts where
the pauses are longest *and* every piece is about as long as its words: a
short pause in the right place beats a longer one in the wrong place.

It cannot be certain, so it says where it is not: an item whose length does
not suit its words, or that was cut at a very short pause, comes back marked
doubtful, to be listened to before it is used (``voicepack.py review``). A
clip under the wrong name is a wrong station read out on a platform.

Pure Python over ``array('h')``; decoding the MP3 is the caller's business.
"""

from __future__ import annotations

import array
import math
import statistics
from dataclasses import dataclass

#: Analysis window. Short enough to place a cut, long enough to be quick.
FRAME_MS = 10
#: Quieter than this share of the recording's peak is silence.
SILENCE_RATIO = 0.02
#: ...but never call anything this quiet speech, however quiet the recording.
SILENCE_FLOOR = 90
#: A silence shorter than this is never a place to cut.
MIN_GAP_MS = 50
#: A sound shorter than this, alone in silence, is a click.
MIN_SOUND_MS = 30
#: Air kept before and after the speech, so soft starts and tails survive.
HEAD_PAD_MS = 40
TAIL_PAD_MS = 110
FADE_IN_MS = 5
FADE_OUT_MS = 20
#: Loudness every clip is brought to (RMS of the speech, of full scale), so
#: parts recorded on different days sit at one level.
TARGET_RMS = 0.10
PEAK_LIMIT = 0.95

#: Every item takes about this long before its first letter counts: the
#: shortest word still has an attack and a tail.
ITEM_BASE_SECONDS = 0.2
#: An item is never looked for across more sounds than this.
MAX_SOUNDS_PER_ITEM = 30
#: How the three things the cut weighs are traded: a piece the wrong length,
#: a pause left inside a piece, a cut made at a short pause.
FIT_WEIGHT = 1.5
INSIDE_WEIGHT = 2.0
SHORT_CUT_WEIGHT = 0.2
#: Doubtful: out by this factor from the length its words suggest...
DOUBT_LENGTH = 1.7
#: ...or cut at a pause shorter than this share of the usual one.
DOUBT_PAUSE = 0.4


class CutError(RuntimeError):
    """The recording does not match its script."""


@dataclass(frozen=True)
class Cut:
    #: (first sample, one past the last) of each item, pads included.
    segments: list[tuple[int, int]]
    #: Indexes of the items to listen to before trusting them.
    doubtful: list[int]
    #: Shortest pause that was cut at, and the usual one, in milliseconds.
    shortest_pause_ms: int
    usual_pause_ms: int


def _frame_peaks(samples: array.array, frame: int) -> list[int]:
    peaks = []
    for start in range(0, len(samples), frame):
        chunk = samples[start : start + frame]
        peaks.append(max(max(chunk), -min(chunk)))
    return peaks


def _sound_runs(loud: list[bool]) -> list[tuple[int, int]]:
    """(first frame, one past the last) of each run of sound."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate(loud):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(loud)))
    return runs


def _sounds(peaks: list[int]) -> list[tuple[int, int]]:
    """The stretches of sound between silences long enough to cut at, in frames."""
    threshold = max(max(peaks) * SILENCE_RATIO, SILENCE_FLOOR)
    runs = _sound_runs([peak >= threshold for peak in peaks])
    runs = [run for run in runs if (run[1] - run[0]) * FRAME_MS >= MIN_SOUND_MS]
    if not runs:
        raise CutError("The recording is silent")
    sounds: list[list[int]] = [list(runs[0])]
    for start, end in runs[1:]:
        if (start - sounds[-1][1]) * FRAME_MS < MIN_GAP_MS:
            sounds[-1][1] = end
        else:
            sounds.append([start, end])
    return [(start, end) for start, end in sounds]


def _letters(text: str) -> int:
    return max(1, sum(1 for char in text if char.isalnum()))


def find_items(samples: array.array, rate: int, texts: list[str]) -> Cut:
    """Find the items of a script in its recording, one segment per text."""
    count = len(texts)
    if count < 1:
        raise CutError("The script has no items")
    if not samples:
        raise CutError("The recording is empty")
    frame = max(1, rate * FRAME_MS // 1000)
    peaks = _frame_peaks(samples, frame)
    sounds = _sounds(peaks)
    total = len(sounds)
    if total < count:
        raise CutError(
            f"The script has {count} items but only {total} sounds can be told apart: "
            "the voice ran some together. Record this part again."
        )
    gaps = [sounds[i + 1][0] - sounds[i][1] for i in range(total - 1)]
    # The usual pause between items: the middle one of the longest count-1.
    longest = sorted(gaps, reverse=True)[: count - 1]
    usual = statistics.median(longest) if longest else 1
    seconds = FRAME_MS / 1000
    letters = [_letters(text) for text in texts]
    inside = [INSIDE_WEIGHT * (gap / usual) ** 2 for gap in gaps]
    short = [SHORT_CUT_WEIGHT * max(0.0, math.log(usual / gap)) ** 2 for gap in gaps]

    def lay(pace: float) -> list[tuple[int, int]]:
        """The cheapest way to share the sounds among the items at this pace."""
        expected = [ITEM_BASE_SECONDS + pace * n for n in letters]
        infinity = float("inf")
        # best[i][j]: cheapest way to make the first i items of the first j sounds.
        best = [[infinity] * (total + 1) for _ in range(count + 1)]
        came = [[0] * (total + 1) for _ in range(count + 1)]
        best[0][0] = 0.0
        for item in range(1, count + 1):
            want = expected[item - 1]
            # Leave at least one sound for every item still to come.
            for last in range(item, total - (count - item) + 1):
                kept = 0.0
                for first in range(last - 1, max(item - 2, last - 1 - MAX_SOUNDS_PER_ITEM), -1):
                    if first < last - 1:
                        kept += inside[first]
                    before = best[item - 1][first]
                    if before == infinity:
                        continue
                    length = (sounds[last - 1][1] - sounds[first][0]) * seconds
                    cost = before + kept + FIT_WEIGHT * math.log(length / want) ** 2
                    if first:
                        cost += short[first - 1]
                    if cost < best[item][last]:
                        best[item][last] = cost
                        came[item][last] = first
        if best[count][total] == infinity:
            raise CutError("The recording could not be matched to its script")
        spans: list[tuple[int, int]] = []
        last = total
        for item in range(count, 0, -1):
            first = came[item][last]
            spans.append((first, last - 1))
            last = first
        spans.reverse()
        return spans

    def pace_of(spans: list[tuple[int, int]]) -> float:
        """Seconds a letter, from the middle item: one slip must not move it."""
        paces = [
            ((sounds[last][1] - sounds[first][0]) * seconds - ITEM_BASE_SECONDS) / n
            for (first, last), n in zip(spans, letters, strict=True)
        ]
        return max(0.02, statistics.median(paces))

    # A first guess at the pace from the whole recording, then a better one
    # from the items that guess found.
    speech = sum(end - start for start, end in sounds) * seconds
    spans = lay(max(0.02, (speech - ITEM_BASE_SECONDS * count) / sum(letters)))
    spans = lay(pace_of(spans))
    pace = pace_of(spans)
    expected = [ITEM_BASE_SECONDS + pace * n for n in letters]

    head, tail = HEAD_PAD_MS // FRAME_MS, TAIL_PAD_MS // FRAME_MS
    segments: list[tuple[int, int]] = []
    doubtful: list[int] = []
    cut_at: list[int] = []
    for index, (first, last) in enumerate(spans):
        start_frame, end_frame = sounds[first][0], sounds[last][1]
        # A pad never reaches more than half way into the pause it sits in.
        before = start_frame - (sounds[first - 1][1] if first else 0)
        after = (sounds[last + 1][0] if last + 1 < total else len(peaks)) - end_frame
        start = max(0, start_frame - min(head, before // 2)) * frame
        end = min(len(samples), (end_frame + min(tail, after // 2)) * frame)
        segments.append((start, end))
        length = (end_frame - start_frame) * seconds
        off = max(length / expected[index], expected[index] / length)
        pauses = ([gaps[first - 1]] if first else []) + ([gaps[last]] if last + 1 < total else [])
        if first:
            cut_at.append(gaps[first - 1])
        if off > DOUBT_LENGTH or any(pause < usual * DOUBT_PAUSE for pause in pauses):
            doubtful.append(index)
    return Cut(
        segments,
        doubtful,
        min(cut_at, default=0) * FRAME_MS,
        int(usual * FRAME_MS) if cut_at else 0,
    )


def gain_for(samples: array.array, rate: int) -> float:
    """The gain that brings this recording's speech to the house level."""
    frame = max(1, rate * FRAME_MS // 1000)
    peak = max(max(samples), -min(samples)) or 1
    threshold = max(peak * SILENCE_RATIO, SILENCE_FLOOR)
    total, counted = 0, 0
    for start in range(0, len(samples), frame):
        chunk = samples[start : start + frame]
        if max(max(chunk), -min(chunk)) >= threshold:
            total += sum(sample * sample for sample in chunk)
            counted += len(chunk)
    if not counted:
        return 1.0
    rms = math.sqrt(total / counted) / 32768
    return min(TARGET_RMS / rms, PEAK_LIMIT * 32768 / peak)


def extract(samples: array.array, segment: tuple[int, int], rate: int, gain: float) -> array.array:
    """One item, levelled, with its ends faded so a join never clicks."""
    start, end = segment
    clip = array.array("h", (max(-32768, min(32767, int(s * gain))) for s in samples[start:end]))
    fade_in = min(len(clip) // 2, rate * FADE_IN_MS // 1000)
    fade_out = min(len(clip) // 2, rate * FADE_OUT_MS // 1000)
    for index in range(fade_in):
        clip[index] = clip[index] * index // fade_in
    for index in range(fade_out):
        clip[-1 - index] = clip[-1 - index] * index // fade_out
    return clip
