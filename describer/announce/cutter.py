"""Cuts one long recording into the clips it was scripted as.

A part of the recording script is a list of short items, read out with a
silence after each. The recording comes back as one file. This finds the
items again.

It does not look for "a silence longer than X", because nobody knows X: the
voice decides how long to pause, and it pauses inside "Stratford-upon-Avon"
too. It is told how many items the script had, takes the longest silences
until it has that many pieces, and reports how clear the choice was (the
shortest silence it cut at, against the longest it left alone). A recording
with too few silences, or an unclear choice, is refused rather than guessed
at: a clip under the wrong name is a wrong station read out on a platform.

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
#: A silence shorter than this is a consonant, not a pause.
MIN_GAP_MS = 80
#: A sound shorter than this, alone in silence, is a click.
MIN_SOUND_MS = 30
#: Air kept before and after the speech, so soft starts and tails survive.
HEAD_PAD_MS = 40
TAIL_PAD_MS = 110
FADE_IN_MS = 5
FADE_OUT_MS = 20
#: The cut is trusted when the pauses it used are this much longer than the
#: longest it ignored.
CLEAR_MARGIN = 1.4
#: Loudness every clip is brought to (RMS of the speech, of full scale), so
#: parts recorded on different days sit at one level.
TARGET_RMS = 0.10
PEAK_LIMIT = 0.95


class CutError(RuntimeError):
    """The recording does not match its script."""


@dataclass(frozen=True)
class Cut:
    #: (first sample, one past the last) of each item, pads included.
    segments: list[tuple[int, int]]
    #: Shortest pause cut at, over longest pause left alone. inf when no pause
    #: was left alone.
    margin: float
    #: Shortest pause that was cut at, in milliseconds.
    shortest_pause_ms: int


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


def find_items(samples: array.array, rate: int, count: int) -> Cut:
    """Find ``count`` items in a recording by its ``count - 1`` longest pauses."""
    if count < 1:
        raise CutError("The script has no items")
    if not samples:
        raise CutError("The recording is empty")
    frame = max(1, rate * FRAME_MS // 1000)
    peaks = _frame_peaks(samples, frame)
    threshold = max(max(peaks) * SILENCE_RATIO, SILENCE_FLOOR)
    runs = _sound_runs([peak >= threshold for peak in peaks])
    # Drop clicks, then close the gaps too short to be pauses.
    runs = [run for run in runs if (run[1] - run[0]) * FRAME_MS >= MIN_SOUND_MS]
    if not runs:
        raise CutError("The recording is silent")
    words: list[list[int]] = [list(runs[0])]
    for start, end in runs[1:]:
        if (start - words[-1][1]) * FRAME_MS < MIN_GAP_MS:
            words[-1][1] = end
        else:
            words.append([start, end])

    if len(words) < count:
        raise CutError(
            f"The script has {count} items but only {len(words)} can be told apart: "
            "the voice ran some together. Record this part again with longer pauses."
        )
    # (pause length, index of the word it follows), longest first.
    pauses = sorted(
        ((words[i + 1][0] - words[i][1], i) for i in range(len(words) - 1)), reverse=True
    )
    chosen = pauses[: count - 1]
    ignored = pauses[count - 1 :]
    shortest = min((length for length, _ in chosen), default=0)
    longest_ignored = max((length for length, _ in ignored), default=0)
    margin = shortest / longest_ignored if longest_ignored else math.inf

    breaks = sorted(index for _, index in chosen)
    head = HEAD_PAD_MS // FRAME_MS
    tail = TAIL_PAD_MS // FRAME_MS
    segments: list[tuple[int, int]] = []
    first = 0
    for position in range(count):
        last = breaks[position] if position < len(breaks) else len(words) - 1
        start_frame, end_frame = words[first][0], words[last][1]
        # A pad never reaches more than half way into the pause it sits in.
        before = start_frame - (words[first - 1][1] if first else 0)
        after = (words[last + 1][0] if last + 1 < len(words) else len(peaks)) - end_frame
        start = max(0, start_frame - min(head, before // 2)) * frame
        end = min(len(samples), (end_frame + min(tail, after // 2)) * frame)
        segments.append((start, end))
        first = last + 1
    return Cut(segments, margin, shortest * FRAME_MS)


def odd_ones_out(cut: Cut, texts: list[str], rate: int) -> list[int]:
    """Items whose length does not suit their words: likely a slip in the reading.

    Speech runs at a fairly steady number of letters a second, so an item far
    longer or shorter than its text suggests is where two were joined or one
    was split. Returns their indexes; an empty list is a good sign, not proof.
    """
    if len(texts) != len(cut.segments) or len(texts) < 5:
        return []
    padding = (HEAD_PAD_MS + TAIL_PAD_MS) / 1000
    paces = []
    for (start, end), text in zip(cut.segments, texts, strict=True):
        letters = max(3, sum(1 for char in text if char.isalnum()))
        paces.append(max(0.05, (end - start) / rate - padding) / letters)
    typical = statistics.median(paces)
    return [i for i, pace in enumerate(paces) if not typical / 2.6 <= pace <= typical * 2.6]


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
