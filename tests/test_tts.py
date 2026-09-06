"""Clip cache, volume scaling and the generated chime."""

import wave

from describer.announce.tts import TtsEngine, cache_key, write_chime
from describer.config import AnnouncementsConfig


def test_cache_key_depends_on_text_voice_and_volume():
    base = cache_key("hello", "en_GB-alan-medium", 0.8)

    assert base == cache_key("hello", "en_GB-alan-medium", 0.8)
    assert base != cache_key("hello!", "en_GB-alan-medium", 0.8)
    assert base != cache_key("hello", "en_GB-southern_english_female-low", 0.8)
    assert base != cache_key("hello", "en_GB-alan-medium", 0.5)


def test_chime_is_a_playable_wav(tmp_path):
    path = write_chime(tmp_path / "chime.wav")

    with wave.open(str(path), "rb") as clip:
        assert clip.getnchannels() == 1
        assert clip.getsampwidth() == 2
        assert clip.getnframes() > 0


def test_chime_volume_scales_the_samples(tmp_path):
    loud = write_chime(tmp_path / "loud.wav", 1.0).read_bytes()
    quiet = write_chime(tmp_path / "quiet.wav", 0.25).read_bytes()

    assert len(loud) == len(quiet)
    assert max(quiet) <= max(loud)


def test_availability_reports_missing_pieces(tmp_path):
    engine = TtsEngine(
        AnnouncementsConfig(piper_binary="definitely-not-piper", voices_dir=str(tmp_path))
    )
    availability = engine.availability()

    assert availability["piper"] is False
    assert availability["voice"] is False


def test_player_command_targets_the_configured_output(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/aplay" if name == "aplay" else None)
    engine = TtsEngine(AnnouncementsConfig(audio_device="jack"))

    command = engine.player_command(tmp_path / "clip.wav")

    assert command[0] == "aplay"
    assert "plughw:CARD=Headphones,DEV=0" in command


def test_player_command_omits_the_device_for_default_output(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/aplay" if name == "aplay" else None)
    engine = TtsEngine(AnnouncementsConfig(audio_device="default"))

    assert "-D" not in engine.player_command(tmp_path / "clip.wav")


def test_no_player_available(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    engine = TtsEngine(AnnouncementsConfig())

    assert engine.player_command(tmp_path / "clip.wav") is None
