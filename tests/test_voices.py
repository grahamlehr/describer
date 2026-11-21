"""Voices made on the board: the store, the job runner and the /api/voices routes.

No test needs ffmpeg or the network: recordings are WAVs at the pack's rate and
the cut runs in-process through the runner's inline executor.
"""

import array
import contextlib
import importlib.util
import io
import json
import wave
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from describer import main as main_module
from describer.announce import voices as voices_module
from describer.announce import workshop
from describer.announce.clips import RATE, VoicePack
from describer.announce.voices import SLUG, VoiceStore
from describer.config import Config, ProfileConfig, save_config
from describer.rail import sources as sources_module
from describer.rail.ldbws import parse_board

from .test_clips import silence, tone, write_pack


def inline(argv: list[str]) -> tuple[int, str]:
    """The job runner's subprocess, run in this process."""
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        code = workshop.run(argv)
    return code, errors.getvalue().strip()


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
        announcements={"voice_packs_dir": str(tmp_path / "voices")},
    )
    save_config(config, path)
    monkeypatch.setenv("DESCRIBER_CONFIG", str(path))

    with TestClient(main_module.app) as test_client:
        test_client.app.state.voices.jobs.inline = inline
        yield test_client


def recording_for(folder, number: str) -> bytes:
    items = json.loads(next((folder / "script").glob(f"{number}-*.json")).read_text())
    return recording_for_items(items)


def recording_for_items(items) -> bytes:
    """A WAV with one burst of 'speech' per script item, a pause between each."""
    samples = array.array("h", bytes(2 * RATE // 5))
    for _, text in items:
        samples += tone(0.06 * len(text) + 0.15)
        samples += silence(0.45)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(samples.tobytes())
    return buffer.getvalue()


def make_voice(client, name="Station voice", **options) -> dict:
    body = {"name": name, "near": ["PAD"], "km": 1, **options}
    response = client.post("/api/voices", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# -- the whole journey --------------------------------------------------------


def test_a_voice_is_made_cut_reviewed_built_and_used(client, tmp_path):
    overview = client.get("/api/voices").json()
    assert overview["voices"] == [] and overview["in_use"] == ""
    assert overview["defaults"] == {"near": ["PAD"], "km": 20}
    assert overview["free_bytes"] > 0 and isinstance(overview["ffmpeg"], bool)

    voice = make_voice(client)
    slug = voice["slug"]
    folder = tmp_path / "voices" / slug
    assert slug == "station-voice" and voice["name"] == "Station voice"
    assert voice["parts_total"] == len(voice["parts"]) >= 3
    assert voice["parts_recorded"] == 0 and voice["built"] is False
    assert [p["state"] for p in voice["parts"]] == ["waiting"] * voice["parts_total"]
    assert voice["parts"][0]["label"] == "pilot" and voice["parts"][0]["number"] == "00"

    # The script, one part and all of them.
    text = client.get(f"/api/voices/{slug}/script/00")
    assert text.headers["content-type"] == "text/plain; charset=utf-8"
    assert 'filename="00-pilot.txt"' in text.headers["content-disposition"]
    assert text.text == (folder / "script" / "00-pilot.txt").read_text()
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/voices/{slug}/script.zip").content))
    assert "00-pilot.txt" in archive.namelist() and "INDEX.md" in archive.namelist()

    # Upload the pilot: it is cut at once (inline here).
    audio = recording_for(folder, "00")
    uploaded = client.put(f"/api/voices/{slug}/audio/00?name=take 1.wav", content=audio)
    assert uploaded.status_code == 202, uploaded.text
    detail = uploaded.json()
    pilot = detail["parts"][0]
    assert pilot["state"] == "cut" and pilot["clips"] == pilot["items"]
    assert pilot["audio"]["name"] == "take 1.wav" and pilot["audio"]["bytes"] == len(audio)
    assert pilot["audio"]["seconds"] > 1 and pilot["usual_pause_ms"] is not None
    assert detail["parts_recorded"] == 1 and detail["clips"] == pilot["clips"]
    assert (folder / "audio" / "00.wav").exists()

    # Review: the cuts, a clip, a window, then change two clips.
    cuts = client.get(f"/api/voices/{slug}/review/cuts").json()
    assert cuts["rate"] == RATE and len(cuts["clips"]) == pilot["items"]
    first, second = list(cuts["clips"])[:2]
    clip = client.get(f"/api/voices/{slug}/review/clip", params={"key": first})
    assert clip.headers["content-type"] == "audio/wav" and clip.content[:4] == b"RIFF"
    window = client.get(
        f"/api/voices/{slug}/review/window", params={"part": "00", "start": 0, "end": RATE}
    )
    assert len(window.content) == 44 + 2 * RATE
    moved = client.post(
        f"/api/voices/{slug}/review/clip", json={"key": first, "start": 1000, "end": 9000}
    ).json()
    assert (moved["start"], moved["end"], moved["manual"]) == (1000, 9000, True)
    rejected = client.post(
        f"/api/voices/{slug}/review/clip", json={"key": second, "rejected": True}
    ).json()
    assert rejected["rejected"] is True
    assert client.get(f"/api/voices/{slug}").json()["rejected"] == 1

    # Nothing rejected can be added twice; redo adds part 90.
    assert client.post(f"/api/voices/{slug}/extra", json={"kind": "topup"}).status_code == 409
    extra = client.post(f"/api/voices/{slug}/extra", json={"kind": "redo"})
    assert extra.status_code == 200
    assert extra.json()["parts"][-1]["number"] == "90"
    assert extra.json()["parts"][-1]["state"] == "waiting"

    # Build, then use.
    assert client.post(f"/api/voices/{slug}/use-nothing").status_code == 404
    built = client.post(f"/api/voices/{slug}/build")
    assert built.status_code == 202 and built.json()["built"] is True
    assert built.json()["build_error"] is None and built.json()["stale"] is False
    pack = VoicePack(folder / "pack.zip")
    assert pack.name == "Station voice" and not pack.has(second) and pack.has(first)

    used = client.post("/api/voices/use", json={"slug": slug}).json()
    assert used["in_use"] == slug and used["enabled"] is True
    assert [v["in_use"] for v in used["voices"]] == [True]
    status = client.get("/api/status").json()["voice_pack"]
    assert status["installed"] is True and status["name"] == "Station voice"
    saved = client.get("/api/config").json()["announcements"]
    assert saved["recorded_voice"] == slug and saved["voice_pack"] == "voicepack.zip"

    # Free the working files: the pack and the decisions stay.
    freed = client.delete(f"/api/voices/{slug}/working").json()
    assert freed["working_bytes"] == 0 and freed["pack_bytes"] > 0 and freed["built"] is True
    assert not (folder / "audio").exists() and (folder / "cuts.json").exists()
    assert client.post(f"/api/voices/{slug}/build").status_code == 409

    # Delete: the board goes back to Piper.
    gone = client.delete(f"/api/voices/{slug}").json()
    assert gone["voices"] == [] and gone["in_use"] == ""
    assert not folder.exists()
    assert client.get("/api/status").json()["voice_pack"]["installed"] is False
    assert client.get(f"/api/voices/{slug}").status_code == 404


def test_a_failed_cut_says_why_and_can_be_tried_again(client, tmp_path):
    slug = make_voice(client)["slug"]
    # A recording of silence matches nothing in the script.
    quiet = io.BytesIO()
    with wave.open(quiet, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(bytes(2 * RATE))
    detail = client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=quiet.getvalue()).json()
    assert detail["parts"][0]["state"] == "failed" and detail["parts"][0]["error"]

    folder = tmp_path / "voices" / slug
    client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=recording_for(folder, "00"))
    again = client.post(f"/api/voices/{slug}/cut/00")
    assert again.status_code == 202 and again.json()["parts"][0]["state"] == "cut"

    removed = client.delete(f"/api/voices/{slug}/audio/00").json()
    assert removed["parts"][0]["state"] == "waiting" and removed["clips"] == 0
    assert not list((folder / "audio").iterdir())


def test_a_second_recording_replaces_the_first(client, tmp_path):
    slug = make_voice(client)["slug"]
    folder = tmp_path / "voices" / slug
    audio = recording_for(folder, "00")
    client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=audio)
    client.put(f"/api/voices/{slug}/audio/00?name=b.wav", content=audio + bytes(2))
    assert [p.name for p in (folder / "audio").iterdir()] == ["00.wav"]


# -- refusals -----------------------------------------------------------------


def test_a_duplicate_name_and_a_script_with_no_stations_are_refused(client):
    make_voice(client)
    assert (
        client.post("/api/voices", json={"name": "Station  voice", "near": ["PAD"]}).status_code
        == 409
    )
    empty = client.post("/api/voices", json={"name": "Nothing"})
    assert empty.status_code == 422 and "station" in empty.json()["detail"]
    assert client.post("/api/voices", json={"name": "!!!", "near": ["PAD"]}).status_code == 422
    assert client.post("/api/voices", json={"name": "x", "near": ["../"]}).status_code == 422
    assert client.post("/api/voices", json={"name": "x", "paste_limit": 7}).status_code == 422
    assert client.post("/api/voices", json={"name": "x" * 61}).status_code == 422
    assert client.get("/api/voices").json()["voices"][0]["slug"] == "station-voice"


def test_the_board_stations_option_reads_the_boards_without_the_network(client):
    voice = make_voice(client, "Board voice", near=[], board_stations=True)
    assert voice["parts_total"] >= 3


@pytest.mark.parametrize("slug", ["..", "Upper", "a/b", "-x", "x" * 41, "%2e%2e", "no-such"])
def test_a_bad_slug_never_reaches_the_disk(client, slug):
    assert client.get(f"/api/voices/{slug}").status_code in (404, 405)
    assert client.get(f"/api/voices/{slug}/script/00").status_code == 404
    assert client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=b"x").status_code == 404
    assert client.delete(f"/api/voices/{slug}").status_code in (404, 405)
    assert client.post("/api/voices/use", json={"slug": slug}).status_code in (404, 409, 422)


def test_a_bad_part_number_or_file_type_is_refused(client, tmp_path):
    slug = make_voice(client)["slug"]
    for number in ("0", "000", "ab", "99", "0%2F0"):
        assert client.get(f"/api/voices/{slug}/script/{number}").status_code == 404
        put = client.put(f"/api/voices/{slug}/audio/{number}?name=a.wav", content=b"x")
        assert put.status_code == 404
    for name in ("a.exe", "a", "../../a.sh", "a.mp3.exe", ""):
        put = client.put(f"/api/voices/{slug}/audio/00?name={name}", content=b"x")
        assert put.status_code == 415
    assert (
        client.get(f"/api/voices/{slug}/review/window?part=../x&start=0&end=1").status_code == 404
    )
    unknown = client.post(f"/api/voices/{slug}/review/clip", json={"key": "../../etc/passwd"})
    assert unknown.status_code == 404
    assert client.get(f"/api/voices/{slug}/review/clip?key=../../x").status_code == 404


def test_an_empty_upload_and_a_missing_ffmpeg_are_explained(client, monkeypatch):
    slug = make_voice(client)["slug"]
    assert client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=b"").status_code == 422

    monkeypatch.setattr(workshop, "has_ffmpeg", lambda: False)
    refused = client.put(f"/api/voices/{slug}/audio/00?name=a.mp3", content=b"ID3 not wav")
    assert refused.status_code == 503 and "ffmpeg" in refused.json()["detail"]


def test_uploads_are_limited_by_size_and_by_free_disk(client, monkeypatch):
    slug = make_voice(client)["slug"]
    monkeypatch.setattr(voices_module, "MAX_AUDIO_BYTES", 10)
    monkeypatch.setattr(main_module, "MAX_AUDIO_BYTES", 10)
    big = client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=bytes(50))
    assert big.status_code == 413

    monkeypatch.setattr(VoiceStore, "free_bytes", lambda self: 100)
    full = client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=b"abc")
    assert full.status_code == 507


def test_a_voice_that_was_never_built_cannot_be_used(client):
    slug = make_voice(client)["slug"]
    assert client.post("/api/voices/use", json={"slug": slug}).status_code == 409
    assert client.post(f"/api/voices/{slug}/build").status_code == 409
    assert client.get("/api/voices").json()["in_use"] == ""


def test_a_voice_with_a_job_running_cannot_be_deleted(client):
    slug = make_voice(client)["slug"]
    runner = client.app.state.voices.jobs
    runner._queue.append(voices_module.Job(slug, "cut", "00", []))
    assert client.get(f"/api/voices/{slug}").json()["busy"] is True
    assert client.delete(f"/api/voices/{slug}").status_code == 409
    assert client.delete(f"/api/voices/{slug}/working").status_code == 409
    runner._queue.clear()
    assert client.delete(f"/api/voices/{slug}").status_code == 200


# -- importing ----------------------------------------------------------------


def test_a_ready_made_pack_is_imported_and_not_switched_to(client, tmp_path):
    pack = write_pack(tmp_path / "ready.zip", ["p.test", "p.and"]).read_bytes()
    first = client.put("/api/voices/import", content=pack)
    assert first.status_code == 201
    assert first.json()["imported"] is True and first.json()["slug"] == "test-voice"
    assert first.json()["built"] is True and first.json()["parts_total"] == 0

    second = client.put("/api/voices/import", content=pack).json()
    assert second["slug"] == "test-voice-2"
    assert client.get("/api/voices").json()["in_use"] == ""
    assert not [p for p in (tmp_path / "voices").iterdir() if p.name.startswith(".")]

    used = client.post("/api/voices/use", json={"slug": "test-voice-2"})
    assert used.status_code == 200
    assert client.get("/api/status").json()["voice_pack"]["clips"] == 2


def test_a_file_that_is_not_a_pack_is_not_kept(client, tmp_path):
    assert client.put("/api/voices/import", content=b"not a zip").status_code == 422
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": 99}))
    assert client.put("/api/voices/import", content=bad.read_bytes()).status_code == 422
    assert client.get("/api/voices").json()["voices"] == []
    assert list((tmp_path / "voices").iterdir()) == []


# -- the config ----------------------------------------------------------------


def test_the_made_voice_is_not_something_a_profile_can_change():
    for name in ("recorded_voice", "voice_packs_dir", "voice_pack"):
        assert (
            name
            not in ProfileConfig.model_fields["announcements"].annotation.__args__[0].model_fields
        )

    config = Config(
        stations=[{"crs": "PAD"}],
        announcements={"recorded_voice": "kate"},
        profiles={
            "enabled": True,
            "entries": [{"name": "Night", "announcements": {"recorded_voice": "other"}}],
        },
    )
    from describer.config import merge_profile

    merged = merge_profile(config, config.profiles.entries[0])
    assert merged.announcements.recorded_voice == "kate"


def test_every_slug_the_store_makes_matches_the_pattern(tmp_path):
    store = VoiceStore(tmp_path)
    slug = store.create(
        "Ünï Kate's Voice — the very long one that goes on and on and on", near=["PAD"], km=1
    )
    assert SLUG.fullmatch(slug) and len(slug) <= 40


# -- the real job runner and the Mac's review server ---------------------------


def test_the_cut_and_the_build_run_as_subprocesses_when_nothing_is_injected(client, tmp_path):
    import time

    client.app.state.voices.jobs.inline = None
    slug = make_voice(client)["slug"]
    folder = tmp_path / "voices" / slug

    def settled(check) -> dict:
        deadline = time.time() + 60
        while time.time() < deadline:
            detail = client.get(f"/api/voices/{slug}").json()
            if check(detail):
                return detail
            time.sleep(0.1)
        raise AssertionError(detail)

    client.put(f"/api/voices/{slug}/audio/00?name=a.wav", content=recording_for(folder, "00"))
    detail = settled(lambda d: d["parts"][0]["state"] in ("cut", "failed"))
    assert detail["parts"][0]["state"] == "cut", detail["parts"][0]

    client.post(f"/api/voices/{slug}/build")
    detail = settled(lambda d: not d["building"])
    assert detail["built"] is True and detail["build_error"] is None


def test_the_local_review_server_answers_the_boards_routes(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    spec = importlib.util.spec_from_file_location(
        "voicepack", Path(__file__).parent.parent / "scripts" / "voicepack.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    items = [["s.reading.mid", "Reading,"], ["s.slough.mid", "Slough,"]]
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "01-core.json").write_text(json.dumps(items))
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "01.wav").write_bytes(recording_for_items(items))
    assert workshop.cut_folder(tmp_path).cut == 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), tool.review_handler(workshop.Review(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/api/voices/local/review"
        cuts = json.load(urllib.request.urlopen(f"{base}/cuts"))
        assert cuts["rate"] == RATE and set(cuts["clips"]) == {"s.reading.mid", "s.slough.mid"}
        clip = urllib.request.urlopen(f"{base}/clip?key=s.reading.mid").read()
        assert clip[:4] == b"RIFF"
        window = urllib.request.urlopen(f"{base}/window?part=01&start=0&end=100").read()
        assert len(window) == 44 + 200
        request = urllib.request.Request(
            f"{base}/clip",
            data=json.dumps({"key": "s.slough.mid", "rejected": True}).encode(),
            method="POST",
        )
        assert json.load(urllib.request.urlopen(request))["rejected"] is True
    finally:
        server.shutdown()
