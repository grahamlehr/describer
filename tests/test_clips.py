"""The recorded voice: the clip catalogue, the plan, the pack and the cutter."""

import array
import importlib.util
import json
import math
import re
import wave
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from describer import main as main_module
from describer.announce import catalogue, cutter
from describer.announce.clips import (
    FORMAT,
    MANIFEST,
    RATE,
    EveryClip,
    RecordedVoice,
    VoicePack,
    VoicePackError,
    build_plan,
    member,
)
from describer.announce.phrasing import AnnouncementKind, build_text
from describer.announce.scheduler import TEST_TEXT, Announcement, AnnouncementScheduler
from describer.config import AnnouncementsConfig, Config, save_config
from describer.rail import sources as sources_module
from describer.rail.ldbws import parse_board
from describer.rail.models import CallingPoint, Service, ServiceStatus

ROOT = Path(__file__).parent.parent


def service(**overrides) -> Service:
    defaults = {
        "id": "svc",
        "scheduled_time": "14:32",
        "destination": "London Victoria via Crystal Palace",
        "origin": "London Bridge",
        "operator": "Southern",
        "platform": "2",
        "status": ServiceStatus.ON_TIME,
        "calling_points": [
            CallingPoint(name="Crystal Palace", crs="CYP"),
            CallingPoint(name="Hayes (Kent)", crs="HYS"),
            CallingPoint(name="Highbury & Islington", crs="HHY"),
            CallingPoint(name="London Victoria", crs="VIC"),
        ],
    }
    return Service(**{**defaults, **overrides})


def tone(seconds: float, level: int = 9000) -> array.array:
    count = int(RATE * seconds)
    return array.array(
        "h", (int(level * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(count))
    )


def silence(seconds: float) -> array.array:
    return array.array("h", bytes(2 * int(RATE * seconds)))


def write_pack(path: Path, keys: list[str], *, crs: dict[str, str] | None = None) -> Path:
    """A pack whose every clip is a tenth of a second of tone."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            MANIFEST,
            json.dumps({"format": FORMAT, "rate": RATE, "name": "Test voice", "crs": crs or {}}),
        )
        for key in keys:
            with archive.open(member(key), "w") as handle, wave.open(handle, "wb") as clip:
                clip.setnchannels(1)
                clip.setsampwidth(2)
                clip.setframerate(RATE)
                clip.writeframes(tone(0.1).tobytes())
    return path


def words(text: str) -> str:
    """Wording with the punctuation, case and brackets taken out."""
    text = re.sub(r"\([^)]*\)", " ", text).replace("&", " and ").lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def spoken(plan_keys: list[str], stations: list[str]) -> str:
    """What a plan says, read back from the catalogue's recording texts."""
    known = {
        clip.key: clip.text
        for clip in [
            *catalogue.core_clips(),
            *catalogue.station_clips(stations),
            *catalogue.reason_clips(),
        ]
    }
    return words(" ".join(known[key] for key in plan_keys))


# -- the catalogue ------------------------------------------------------------


def test_every_clip_has_one_key_and_some_words():
    names = [name for _, name, _, _ in catalogue.stations()]
    clips = [*catalogue.core_clips(), *catalogue.station_clips(names), *catalogue.reason_clips()]

    assert len({clip.key for clip in clips}) == len(clips)
    assert all(clip.text.strip() and re.fullmatch(r"[a-z0-9.\-_]+", clip.key) for clip in clips)
    assert len(clips) > 5000


def test_a_county_in_brackets_is_not_part_of_the_name():
    assert catalogue.slug("Hayes (Kent)") == "hayes"
    assert catalogue.slug("Highbury & Islington") == "highbury-and-islington"
    assert catalogue.spoken_station("Hayes (Kent)") == "Hayes"


@pytest.mark.parametrize(
    "raw",
    [
        "This is due to a signalling fault",
        "This service has been delayed by a signalling fault",
        "This train has been cancelled because of a signalling fault.",
        "a signalling fault",
    ],
)
def test_a_reason_is_keyed_without_its_lead_in(raw):
    assert catalogue.reason_key(raw) == "r.a-signalling-fault"


# -- the plan -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["departures", "arrivals"])
@pytest.mark.parametrize(
    "kind, overrides",
    [
        (AnnouncementKind.ARRIVING, {}),
        (AnnouncementKind.ARRIVING, {"platform": None, "scheduled_time": "09:05"}),
        (AnnouncementKind.ARRIVING, {"platform": "12B", "scheduled_time": "00:00"}),
        (AnnouncementKind.ARRIVING, {"calling_points": [CallingPoint(name="Penge West")]}),
        (
            AnnouncementKind.ARRIVING,
            {"status": ServiceStatus.EXPECTED, "expected_time": "14:33", "delay_minutes": 1},
        ),
        (AnnouncementKind.DELAYED, {"delay_minutes": 12}),
        (AnnouncementKind.DELAYED, {"status": ServiceStatus.DELAYED}),
        (
            AnnouncementKind.DELAYED,
            {"delay_minutes": 3, "delay_reason": "a fault with the signalling system"},
        ),
        (AnnouncementKind.CANCELLED, {"cancel_reason": "This is due to a shortage of train crew"}),
        (AnnouncementKind.CANCELLED, {"destination": "Penge West"}),
    ],
)
def test_the_recorded_voice_says_what_piper_says(kind, overrides, mode):
    """The wording has two homes, ``phrasing.py`` and the clip plan; hold them together."""
    train = service(**overrides)

    plan, optional = build_plan(kind, train, mode, EveryClip())

    stations = [point.name for point in train.calling_points]
    stations += ["London Victoria", "Crystal Palace", "London Bridge", "Penge West"]
    expected = words(build_text(kind, train, mode))
    # Piper is handed "platform 12B" and the times as they stand; the clips say them out.
    expected = expected.replace("platform 12b", "platform twelve b")
    assert plan.playable and not optional
    assert spoken(plan.keys, stations) == expected


def test_a_stop_is_found_by_its_code_when_the_feed_spells_it_differently(tmp_path):
    keys = [clip.key for clip in catalogue.core_clips()]
    keys += ["s.london-victoria.mid", "s.london-victoria.end", "s.whitechapel.mid"]
    pack = VoicePack(write_pack(tmp_path / "p.zip", keys, crs={"ZLW": "whitechapel"}))
    train = service(
        destination="London Victoria",
        calling_points=[
            CallingPoint(name="Whitechapel (Elizabeth line)", crs="ZLW"),
            CallingPoint(name="London Victoria", crs="VIC"),
        ],
    )

    plan, _ = build_plan(AnnouncementKind.ARRIVING, train, "departures", pack)

    assert plan.playable
    assert "s.whitechapel.mid" in plan.keys
    assert plan.keys[-1] == "s.london-victoria.end"


def test_a_missing_station_makes_the_plan_unplayable_and_says_what_to_record(tmp_path):
    keys = [clip.key for clip in catalogue.core_clips()] + ["s.london-victoria.mid"]
    pack = VoicePack(write_pack(tmp_path / "p.zip", keys))
    train = service(destination="London Victoria", calling_points=[CallingPoint(name="Anerley")])

    plan, _ = build_plan(AnnouncementKind.ARRIVING, train, "departures", pack)

    assert not plan.playable
    assert plan.missing == {"s.anerley.end": "Anerley."}


def test_an_operator_or_reason_with_no_clip_is_left_out_not_handed_to_piper(tmp_path):
    keys = [clip.key for clip in catalogue.core_clips() if not clip.key.startswith("o.")]
    pack = VoicePack(write_pack(tmp_path / "p.zip", [*keys, "s.penge-west.mid"]))
    train = service(destination="Penge West", cancel_reason="This is due to a swan on the line")

    plan, optional = build_plan(AnnouncementKind.CANCELLED, train, "departures", pack)

    assert plan.playable
    assert "p.due_to" not in plan.keys
    assert optional == {"o.southern": "Southern,", "r.a-swan-on-the-line": "a swan on the line."}


def test_a_delay_of_an_hour_or_more_is_announced_without_its_length():
    train = service(delay_minutes=75)

    plan, _ = build_plan(AnnouncementKind.DELAYED, train, "departures", EveryClip())

    assert "p.is_delayed" in plan.keys
    assert "seventy five" in build_text(AnnouncementKind.DELAYED, train)


# -- the pack -----------------------------------------------------------------


def test_a_plan_is_rendered_as_one_wav_of_clips_and_pauses(tmp_path):
    pack = VoicePack(write_pack(tmp_path / "p.zip", ["p.and", "n.1"]))

    target = pack.render(["p.and", 500, "n.1"], tmp_path / "out.wav", volume=0.5)

    with wave.open(str(target), "rb") as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (RATE, 1, 2)
        assert clip.getnframes() == pytest.approx(RATE * 0.7, abs=4)
        samples = array.array("h", clip.readframes(clip.getnframes()))
    assert 4000 < max(samples) <= 4500


@pytest.mark.parametrize("manifest", [{"format": 99, "rate": RATE}, {"format": 1, "rate": 8000}])
def test_a_pack_this_board_cannot_play_is_refused(tmp_path, manifest):
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(MANIFEST, json.dumps(manifest))
        archive.writestr(member("p.and"), b"")

    with pytest.raises(VoicePackError):
        VoicePack(path)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    path = tmp_path / "p.zip"
    path.write_bytes(b"not a zip")

    with pytest.raises(VoicePackError):
        VoicePack(path)


def test_the_voice_remembers_what_it_lacked_and_follows_the_file(tmp_path):
    path = tmp_path / "voicepack.zip"
    voice = RecordedVoice(AnnouncementsConfig(voice_pack=str(path)))
    assert voice.pack() is None
    assert voice.status()["installed"] is False

    write_pack(path, [clip.key for clip in catalogue.core_clips()])
    train = service(destination="Anerley", calling_points=[])

    assert voice.plan(AnnouncementKind.CANCELLED, train, "departures", 8) is None
    assert voice.status()["missing"] == [{"key": "s.anerley.mid", "text": "Anerley,"}]

    voice.update_config(AnnouncementsConfig(voice_pack=str(path), use_voice_pack=False))
    assert voice.pack() is None


# -- the scheduler ------------------------------------------------------------


class TwoVoices:
    """Stands in for the engine: records which voice spoke."""

    def __init__(self, recorded: RecordedVoice) -> None:
        self.recorded = recorded
        self.piper: list[str] = []
        self.clips: list[list] = []

    async def speak(self, text, audio_device=None):
        self.piper.append(text)

    async def speak_clips(self, parts, audio_device=None):
        self.clips.append(parts)


async def test_the_recorded_voice_speaks_when_it_can_and_piper_when_it_cannot(tmp_path):
    keys = [clip.key for clip in catalogue.core_clips()] + ["s.penge-west.mid", "s.penge-west.end"]
    path = write_pack(tmp_path / "voicepack.zip", keys)
    engine = TwoVoices(RecordedVoice(AnnouncementsConfig(voice_pack=str(path))))
    scheduler = AnnouncementScheduler(engine)
    known = service(destination="Penge West", calling_points=[])
    unknown = service(destination="Anerley", calling_points=[])

    for train in (known, unknown):
        text = build_text(AnnouncementKind.ARRIVING, train)
        await scheduler.say(Announcement(text, AnnouncementKind.ARRIVING, train))
    await scheduler.say(TEST_TEXT)
    await scheduler.say("Something nobody recorded.")

    assert [parts[-1] for parts in engine.clips] == ["s.penge-west.end", "p.test"]
    assert engine.piper == [
        build_text(AnnouncementKind.ARRIVING, unknown),
        "Something nobody recorded.",
    ]


# -- the cutter ---------------------------------------------------------------


def recording(*pieces: array.array) -> array.array:
    joined = array.array("h")
    for piece in pieces:
        joined.extend(piece)
    return joined


def test_items_are_found_by_the_longest_pauses_not_a_fixed_one():
    # The second item pauses inside itself for longer than "a consonant".
    two_words = recording(tone(0.3), silence(0.15), tone(0.3))
    audio = recording(
        silence(0.2), tone(0.4), silence(0.5), two_words, silence(0.45), tone(0.6), silence(0.3)
    )

    cut = cutter.find_items(audio, RATE, 3)

    lengths = [(end - start) / RATE for start, end in cut.segments]
    assert lengths == pytest.approx([0.55, 0.9, 0.75], abs=0.05)
    assert cut.margin == pytest.approx(3.0, abs=0.3)
    assert cut.shortest_pause_ms == pytest.approx(450, abs=20)


def test_a_recording_with_too_few_items_is_refused():
    audio = recording(tone(0.4), silence(0.5), tone(0.4))

    with pytest.raises(cutter.CutError, match="only 2"):
        cutter.find_items(audio, RATE, 3)


def test_an_item_far_too_long_for_its_words_is_pointed_out():
    audio = recording()
    for seconds in (0.5, 0.5, 0.5, 0.5, 3.0, 0.5):
        audio.extend(tone(seconds))
        audio.extend(silence(0.5))
    cut = cutter.find_items(audio, RATE, 6)

    assert cutter.odd_ones_out(cut, ["Reading,"] * 6, RATE) == [4]


def test_a_clip_is_levelled_and_faded_at_both_ends():
    audio = recording(silence(0.2), tone(0.5, level=2000), silence(0.2))
    cut = cutter.find_items(audio, RATE, 1)

    clip = cutter.extract(audio, cut.segments[0], RATE, cutter.gain_for(audio, RATE))

    assert clip[0] == 0 and clip[-1] == 0
    rms = math.sqrt(sum(s * s for s in clip) / len(clip)) / 32768
    assert 0.06 < rms < cutter.TARGET_RMS * 1.05


# -- the tool -----------------------------------------------------------------


def test_the_script_is_written_in_parts_that_fit_a_paste(tmp_path):
    spec = importlib.util.spec_from_file_location("voicepack", ROOT / "scripts" / "voicepack.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    assert tool.main(["--dir", str(tmp_path), "script", "--near", "PAD", "--km", "3"]) == 0

    parts = sorted((tmp_path / "script").glob("[0-9][0-9]-*.json"))
    assert parts[0].name == "00-pilot.json"
    for part in parts:
        items = json.loads(part.read_text())
        text = part.with_suffix(".txt").read_text()
        assert len(text) <= 4500
        assert [line for line in text.split("\n") if line] == [words for _, words in items]
    with pytest.raises(SystemExit, match="already holds a script"):
        tool.main(["--dir", str(tmp_path), "script", "--near", "PAD"])


# -- the API ------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path, rdm_credentials, departures_payload):
    board = parse_board(departures_payload, "PAD", "departures")

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def fetch_board(self, crs, mode="departures"):
            return board.model_copy(deep=True)

        async def aclose(self):
            pass

    monkeypatch.setattr(sources_module, "LdbwsClient", FakeClient)
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    path = tmp_path / "config.yaml"
    config = Config(
        stations=[{"crs": "PAD", "announce": False}],
        announcements={"voice_pack": str(tmp_path / "pack" / "voicepack.zip")},
    )
    save_config(config, path)
    monkeypatch.setenv("DESCRIBER_CONFIG", str(path))

    with TestClient(main_module.app) as test_client:
        yield test_client


def test_a_voice_pack_is_installed_reported_and_removed(client, tmp_path):
    assert client.get("/api/status").json()["voice_pack"]["installed"] is False
    upload = write_pack(tmp_path / "upload.zip", ["p.test", "p.and"]).read_bytes()

    installed = client.put("/api/voicepack", content=upload)

    assert installed.status_code == 200
    assert installed.json()["clips"] == 2
    status = client.get("/api/status").json()["voice_pack"]
    assert (status["installed"], status["name"]) == (True, "Test voice")

    assert client.delete("/api/voicepack").json()["installed"] is False
    assert not (tmp_path / "pack" / "voicepack.zip").exists()


def test_a_bad_upload_leaves_the_installed_pack_alone(client, tmp_path):
    client.put("/api/voicepack", content=write_pack(tmp_path / "a.zip", ["p.test"]).read_bytes())

    refused = client.put("/api/voicepack", content=b"this is not a zip")

    assert refused.status_code == 422
    assert client.get("/api/status").json()["voice_pack"]["clips"] == 1
    assert list((tmp_path / "pack").iterdir()) == [tmp_path / "pack" / "voicepack.zip"]
