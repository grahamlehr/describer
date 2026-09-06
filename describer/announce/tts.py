"""Piper text-to-speech: synthesis, on-disk cache, and playback.

Clips are cached by a hash of (text, voice, volume) so a repeated
announcement costs nothing but a file read. Playback is serialised through a
queue so two announcements never talk over each other.
"""

from __future__ import annotations

import array
import asyncio
import hashlib
import logging
import math
import shutil
import wave
from pathlib import Path

from ..config import AnnouncementsConfig

log = logging.getLogger(__name__)

#: ALSA device names for the Pi's two outputs. "default" leaves it to ALSA.
ALSA_DEVICES = {
    "hdmi": "plughw:CARD=vc4hdmi0,DEV=0",
    "jack": "plughw:CARD=Headphones,DEV=0",
    "default": "default",
}

CHIME_TONES = ((659.25, 0.45), (523.25, 0.75))  # E5 then C5: the two-tone "bing bong"
CHIME_RATE = 22050


class TtsError(RuntimeError):
    """Synthesis or playback failed."""


def cache_key(text: str, voice: str, volume: float) -> str:
    digest = hashlib.sha256(f"{voice}|{volume:.2f}|{text}".encode()).hexdigest()
    return digest[:32]


def _scale_wav(path: Path, volume: float) -> None:
    """Scale a 16-bit PCM WAV in place. Cheaper than pulling in an audio lib."""
    if volume >= 0.999:
        return
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        frames = source.readframes(params.nframes)
    if params.sampwidth != 2:
        log.debug("Not 16-bit audio; leaving volume alone")
        return
    samples = array.array("h")
    samples.frombytes(frames)
    for index, sample in enumerate(samples):
        samples[index] = int(sample * volume)
    with wave.open(str(path), "wb") as sink:
        sink.setparams(params)
        sink.writeframes(samples.tobytes())


def write_chime(path: Path, volume: float = 1.0) -> Path:
    """Generate the two-tone chime as a WAV. No external assets needed."""
    samples = array.array("h")
    for frequency, seconds in CHIME_TONES:
        count = int(CHIME_RATE * seconds)
        for index in range(count):
            # Exponential decay gives the tone a bell-like tail.
            envelope = math.exp(-3.0 * index / count)
            value = math.sin(2 * math.pi * frequency * index / CHIME_RATE)
            samples.append(int(0.4 * volume * envelope * value * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(CHIME_RATE)
        sink.writeframes(samples.tobytes())
    return path


class TtsEngine:
    """Synthesises with Piper and plays through ALSA, one clip at a time."""

    def __init__(self, config: AnnouncementsConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()

    def update_config(self, config: AnnouncementsConfig) -> None:
        self._config = config

    # -- availability ------------------------------------------------------

    @property
    def voice_path(self) -> Path:
        return Path(self._config.voices_dir) / f"{self._config.voice}.onnx"

    def player_command(self, path: Path) -> list[str] | None:
        """The playback command for this host, or None if nothing can play."""
        device = ALSA_DEVICES.get(self._config.audio_device, "default")
        if shutil.which("aplay"):
            command = ["aplay", "-q"]
            if device != "default":
                command += ["-D", device]
            return [*command, str(path)]
        if shutil.which("paplay"):
            return ["paplay", str(path)]
        if shutil.which("afplay"):  # macOS, for development
            return ["afplay", str(path)]
        return None

    def availability(self) -> dict[str, bool | str]:
        """Reported on the admin page so a silent Pi is diagnosable."""
        player = self.player_command(Path("x.wav"))
        return {
            "piper": bool(shutil.which(self._config.piper_binary)),
            "voice": self.voice_path.exists(),
            "player": player[0] if player else "",
        }

    # -- synthesis ---------------------------------------------------------

    async def synthesize(self, text: str) -> Path:
        """Return a WAV for ``text``, synthesising it only on a cache miss."""
        config = self._config
        cache_dir = Path(config.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{cache_key(text, config.voice, config.volume)}.wav"
        if target.exists():
            return target

        if not shutil.which(config.piper_binary):
            raise TtsError(f"Piper binary '{config.piper_binary}' not found")
        if not self.voice_path.exists():
            raise TtsError(f"Voice model not found at {self.voice_path}")

        partial = target.with_suffix(".partial.wav")
        process = await asyncio.create_subprocess_exec(
            config.piper_binary,
            "--model",
            str(self.voice_path),
            "--output_file",
            str(partial),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate(text.encode("utf-8"))
        if process.returncode or not partial.exists():
            partial.unlink(missing_ok=True)
            raise TtsError(f"Piper failed: {stderr.decode(errors='replace').strip()[:200]}")

        _scale_wav(partial, config.volume)
        partial.replace(target)
        return target

    # -- playback ----------------------------------------------------------

    async def play_file(self, path: Path) -> None:
        command = self.player_command(path)
        if command is None:
            raise TtsError("No audio player found (install alsa-utils)")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode:
            raise TtsError(f"Playback failed: {stderr.decode(errors='replace').strip()[:200]}")

    async def speak(self, text: str) -> None:
        """Chime (if enabled) then speak. Serialised against other calls."""
        async with self._lock:
            if self._config.chime:
                chime = Path(self._config.cache_dir) / f"chime-{self._config.volume:.2f}.wav"
                if not chime.exists():
                    write_chime(chime, self._config.volume)
                try:
                    await self.play_file(chime)
                except TtsError as exc:
                    log.warning("Chime failed: %s", exc)
            clip = await self.synthesize(text)
            await self.play_file(clip)
