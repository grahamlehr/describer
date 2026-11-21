#!/usr/bin/env python3
"""Make a voice pack: announcements in a recorded voice instead of Piper's.

Four steps, run on the Mac, in a working folder (default ``voicepack/``):

    script   write the recording script, in parts small enough to paste
    cut      cut the audio that came back into one clip per item
    review   listen to the clips in a browser; move a cut, or reject a clip
    build    zip the clips into voicepack.zip for the board
    demo     play a sample announcement from the pack

The recording itself happens elsewhere (ElevenLabs, or anyone reading the
script aloud): paste each ``script/NN-*.txt`` in, save what comes back as
``audio/NN.mp3``. See ``docs/voice-pack.md``.

Needs ``ffmpeg`` on the PATH for ``cut``; nothing else beyond the checkout.
"""

from __future__ import annotations

import argparse
import array
import io
import json
import math
import re
import shutil
import subprocess
import sys
import urllib.request
import wave
import zipfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contextlib

from describer.announce import catalogue, cutter  # noqa: E402
from describer.announce.clips import (  # noqa: E402
    FORMAT,
    MANIFEST,
    RATE,
    EveryClip,
    VoicePack,
    build_plan,
    member,
)
from describer.announce.phrasing import AnnouncementKind, build_text  # noqa: E402
from describer.rail.models import CallingPoint, Service, ServiceStatus  # noqa: E402

AUDIO_TYPES = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aiff")
SAMPLE = "sample-service.json"
#: Where every clip was cut from, and what has been decided about it.
CUTS = "cuts.json"
REVIEW_PAGE = Path(__file__).with_name("voicepack_review.html")


# -- script ------------------------------------------------------------------


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


def nearby(codes: list[str], km: float) -> list[str]:
    """Station names within ``km`` of any of ``codes``, nearest first."""
    places = {crs: (lat, lon) for crs, _, lat, lon in catalogue.stations() if lat is not None}
    centres = []
    for code in codes:
        if code not in places:
            raise SystemExit(f"{code}: not a station with a known position")
        centres.append(places[code])
    found = []
    for crs, name, _, _ in catalogue.stations():
        if crs in places:
            distance = min(_km(places[crs], centre) for centre in centres)
            if distance <= km:
                found.append((distance, name))
    return [name for _, name in sorted(found)]


def _fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def board_services(base: str) -> list[tuple[str, Service]]:
    """(mode, service) for everything on a running board's screen right now."""
    state = _fetch(base.rstrip("/") + "/api/state")
    found = []
    for board in state.get("boards", []):
        for raw in board.get("services", []):
            found.append((board.get("mode", "departures"), Service.model_validate(raw)))
    return found


def service_stations(service: Service) -> list[str]:
    names = [point.name for point in service.calling_points]
    for where in (service.destination, service.origin):
        names += re.split(r"\s+via\s+", where) if where else []
    return [name for name in names if name]


def generic_sample(names: list[str]) -> Service:
    """A plausible train for the pilot when there is no board to copy one from."""
    stops = names[1:5] or names[:1]
    return Service(
        id="sample",
        scheduled_time="14:32",
        destination=stops[-1],
        platform="2",
        status=ServiceStatus.ON_TIME,
        calling_points=[CallingPoint(name=name) for name in stops],
    )


def _parts(clips: list[catalogue.Clip], label: str, chars: int, items: int, separator: str):
    """Split clips into pasteable parts: under ``chars`` characters and ``items`` items."""
    part: list[catalogue.Clip] = []
    size = 0
    for clip in clips:
        cost = len(clip.text) + len(separator)
        if part and (size + cost > chars or len(part) >= items):
            yield label, part
            part, size = [], 0
        part.append(clip)
        size += cost
    if part:
        yield label, part


def cmd_script(args: argparse.Namespace) -> int:
    out: Path = args.dir / "script"
    adding = bool(args.topup or args.redo)
    if out.exists() and any(out.glob("*.json")) and not args.force and not adding:
        raise SystemExit(
            f"{out} already holds a script. Audio is matched to it line by line, so it is "
            "not overwritten; use --force to replace it (and record everything again), "
            "or --topup / --redo to add to it."
        )
    separator = f' <break time="{args.breaks}s" />\n' if args.breaks else "\n\n"

    if adding:
        label = "topup" if args.topup else "redo"
        if args.topup:
            status = _fetch(args.topup.rstrip("/") + "/api/status")
            wanted = (status.get("voice_pack") or {}).get("missing") or []
            clips = [catalogue.Clip(item["key"], item["text"], label) for item in wanted]
        else:
            rejected = [c for c in load_cuts(args.dir)["clips"].values() if c.get("rejected")]
            clips = [catalogue.Clip(c["key"], c["text"], label) for c in rejected]
        # A station is worth having both ways once it is being recorded at all.
        extra = []
        for clip in clips:
            if args.topup and clip.key.startswith("s."):
                stem, other = clip.key.rsplit(".", 1)[0], clip.text.rstrip(",.")
                extra += [
                    catalogue.Clip(f"{stem}.mid", f"{other},", "topup"),
                    catalogue.Clip(f"{stem}.end", f"{other}.", "topup"),
                ]
        clips = list({clip.key: clip for clip in [*clips, *extra]}.values())
        if not clips:
            print("Nothing to record: no clip is missing or rejected.")
            return 0
        existing = [int(path.name[:2]) for path in out.glob("[0-9][0-9]-*.json")]
        number = max([89, *existing]) + 1
        groups = list(
            _parts(sorted(clips, key=lambda c: c.key), label, args.chars, args.items, separator)
        )
        sample = None
    else:
        local = nearby([code.upper() for code in args.near], args.km) if args.near else []
        seen = board_services(args.board) if args.board else []
        for _, service in seen:
            local = service_stations(service) + local
        everywhere = [name for _, name, _, _ in catalogue.stations()] if args.all_stations else []
        local_clips = catalogue.station_clips(local)
        local_keys = {clip.key for clip in local_clips}
        rest_clips = [c for c in catalogue.station_clips(everywhere) if c.key not in local_keys]

        with_stops = [pair for pair in seen if pair[1].calling_points]
        mode, sample = with_stops[0] if with_stops else ("departures", generic_sample(local))
        if not local and not everywhere:
            raise SystemExit("Name some stations: --near CRS[,CRS], --board URL or --all-stations")
        known = {
            clip.key: clip
            for clip in [
                *catalogue.core_clips(),
                *catalogue.station_clips(service_stations(sample)),
            ]
        }
        plan, _ = build_plan(AnnouncementKind.ARRIVING, sample, mode, EveryClip())
        pilot = list({key: known[key] for key in plan.keys if key in known}.values())

        core = catalogue.core_clips()
        groups = [("pilot", pilot)]
        groups += _parts(core, "core", args.chars, args.items, separator)
        for inflection in ("mid", "end"):
            chosen = [c for c in local_clips if c.key.endswith(inflection)]
            groups += _parts(chosen, f"local-{inflection}", args.chars, args.items, separator)
        groups += _parts(catalogue.reason_clips(), "reasons", args.chars, args.items, separator)
        for inflection in ("mid", "end"):
            chosen = [c for c in rest_clips if c.key.endswith(inflection)]
            groups += _parts(chosen, f"network-{inflection}", args.chars, args.items, separator)
        number = 0
        if out.exists():
            shutil.rmtree(out)

    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, clips in groups:
        stem = f"{number:02d}-{label}"
        if args.breaks:
            text = "".join(clip.text + separator for clip in clips)
        else:
            text = separator.join(clip.text for clip in clips) + "\n"
        (out / f"{stem}.txt").write_text(text, encoding="utf-8")
        (out / f"{stem}.json").write_text(
            json.dumps([[clip.key, clip.text] for clip in clips], ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
        rows.append((stem, len(clips), len(text)))
        number += 1
    if sample is not None:
        (out / SAMPLE).write_text(
            json.dumps({"mode": mode, "service": sample.model_dump(mode="json")}), encoding="utf-8"
        )

    lines = [
        "# Recording script",
        "",
        f"Written {date.today().isoformat()}. Paste each part into the voice, save what comes",
        "back as `audio/<number>.mp3` (part 03 is `audio/03.mp3`), then run `voicepack.py cut`.",
        "Do part 00 first and listen to it before recording the rest.",
        "",
        "| Part | Items | Characters | Running total |",
        "|---|---:|---:|---:|",
    ]
    total = 0
    for stem, count, size in rows:
        total += size
        lines.append(f"| `{stem}.txt` | {count} | {size:,} | {total:,} |")
    (out / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[6:]))
    print(f"\n{len(rows)} parts, {total:,} characters, in {out}")
    return 0


# -- cut ---------------------------------------------------------------------


def decode(path: Path) -> array.array:
    """Any audio file as 16-bit mono samples at the pack's rate."""
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is needed to read the audio (brew install ffmpeg)")
    command = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE)]
    result = subprocess.run([*command, "-f", "s16le", "-"], capture_output=True, check=False)
    if result.returncode:
        raise cutter.CutError(result.stderr.decode(errors="replace").strip()[:200])
    samples = array.array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def write_wav(path: Path, samples: array.array) -> None:
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(samples.tobytes())


def audio_for(audio_dir: Path, number: str) -> Path | None:
    """The recording of part ``number``: ``03.mp3``, ``03 take 2.mp3``, ``3.wav``."""
    pattern = re.compile(rf"^0*{int(number)}(?!\d)")
    found = sorted(
        path
        for path in audio_dir.iterdir()
        if path.suffix.lower() in AUDIO_TYPES and pattern.match(path.stem)
    )
    return found[-1] if found else None


def load_cuts(folder: Path) -> dict:
    """``cuts.json``: each part's recording, and each clip's place in it."""
    try:
        return json.loads((folder / CUTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"parts": {}, "clips": {}}


def save_cuts(folder: Path, cuts: dict) -> None:
    partial = folder / (CUTS + ".partial")
    partial.write_text(json.dumps(cuts, ensure_ascii=False, indent=1), encoding="utf-8")
    partial.replace(folder / CUTS)


def cmd_cut(args: argparse.Namespace) -> int:
    script, audio, clips_dir = args.dir / "script", args.dir / "audio", args.dir / "clips"
    audio.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    parts = sorted(script.glob("[0-9][0-9]-*.json"))
    if not parts:
        raise SystemExit(f"No script in {script}. Run `voicepack.py script` first.")
    cuts = load_cuts(args.dir)
    cut_count = waiting = failed = doubts = 0
    for part in parts:
        number = part.name[:2]
        if args.only and number not in args.only:
            continue
        items = json.loads(part.read_text(encoding="utf-8"))
        source = audio_for(audio, number)
        if source is None:
            waiting += 1
            continue
        stamp = source.stat().st_mtime
        same_take = (cuts["parts"].get(number) or {}).get("mtime") == stamp
        try:
            samples = decode(source)
            cut = cutter.find_items(samples, RATE, [text for _, text in items])
        except cutter.CutError as exc:
            failed += 1
            print(f"✗ {part.stem}  {source.name}: {exc}")
            continue
        gain = cutter.gain_for(samples, RATE)
        cuts["parts"][number] = {
            "name": part.stem,
            "source": source.name,
            "mtime": stamp,
            "gain": gain,
            "seconds": len(samples) / RATE,
        }
        kept = 0
        for index, ((key, text), segment) in enumerate(zip(items, cut.segments, strict=True)):
            old = cuts["clips"].get(key) or {}
            # A cut moved by hand is kept for as long as the recording is the same one.
            by_hand = same_take and old.get("part") == number and old.get("manual")
            if by_hand:
                segment = (old["start"], old["end"])
                kept += 1
            cuts["clips"][key] = {
                "key": key,
                "text": text,
                "part": number,
                "index": index,
                "start": segment[0],
                "end": segment[1],
                "doubtful": index in cut.doubtful and not by_hand,
                "manual": bool(by_hand),
                "rejected": bool(same_take and old.get("part") == number and old.get("rejected")),
            }
            write_wav(clips_dir / f"{key}.wav", cutter.extract(samples, segment, RATE, gain))
        cut_count += 1
        doubts += len(cut.doubtful)
        print(
            f"✓ {part.stem}  {len(items)} clips from {source.name}: usual pause "
            f"{cut.usual_pause_ms} ms, shortest {cut.shortest_pause_ms} ms, "
            f"{len(cut.doubtful)} to check" + (f", {kept} hand cuts kept" if kept else "")
        )
    save_cuts(args.dir, cuts)
    print(f"\n{cut_count} cut, {failed} refused, {waiting} still to record. Clips in {clips_dir}")
    if doubts:
        print(f"{doubts} clips to check: run `voicepack.py review`.")
    return 1 if failed else 0


# -- review ------------------------------------------------------------------


class Review:
    """The working folder as the review page sees it."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._decoded: dict[str, tuple[float, array.array]] = {}

    def samples(self, part: str) -> array.array:
        """A part's recording, decoded once and again only if the file changes."""
        info = load_cuts(self.folder)["parts"][part]
        source = self.folder / "audio" / info["source"]
        stamp = source.stat().st_mtime
        held = self._decoded.get(part)
        if held is None or held[0] != stamp:
            held = (stamp, decode(source))
            self._decoded = {part: held}
        return held[1]

    def window(self, part: str, start: int, end: int) -> bytes:
        """Part of a recording as a WAV, for the page to draw and play."""
        samples = self.samples(part)
        start, end = max(0, start), min(len(samples), end)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(RATE)
            sink.writeframes(samples[start:end].tobytes())
        return buffer.getvalue()

    def update(self, key: str, change: dict) -> dict:
        """Reject or restore a clip, or move its cut and write the clip again."""
        cuts = load_cuts(self.folder)
        clip = cuts["clips"][key]
        if "rejected" in change:
            clip["rejected"] = bool(change["rejected"])
        if "start" in change and "end" in change:
            samples = self.samples(clip["part"])
            start = max(0, min(int(change["start"]), len(samples) - 1))
            end = max(start + 1, min(int(change["end"]), len(samples)))
            clip.update(start=start, end=end, manual=True, doubtful=False, rejected=False)
            gain = cuts["parts"][clip["part"]]["gain"]
            write_wav(
                self.folder / "clips" / f"{key}.wav",
                cutter.extract(samples, (start, end), RATE, gain),
            )
        if change.get("checked"):
            clip["doubtful"] = False
        save_cuts(self.folder, cuts)
        return clip


def review_handler(review: Review) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, kind: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: object, status: int = 200) -> None:
            self._send(json.dumps(data).encode(), "application/json", status)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/":
                    self._send(REVIEW_PAGE.read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/cuts":
                    self._json({**load_cuts(review.folder), "rate": RATE})
                elif url.path == "/clip":
                    key = query["key"][0]
                    if key not in load_cuts(review.folder)["clips"]:
                        raise KeyError(key)
                    self._send((review.folder / "clips" / f"{key}.wav").read_bytes(), "audio/wav")
                elif url.path == "/window":
                    part, start, end = (
                        query["part"][0],
                        int(query["start"][0]),
                        int(query["end"][0]),
                    )
                    self._send(review.window(part, start, end), "audio/wav")
                else:
                    self._json({"error": "not found"}, 404)
            except (KeyError, ValueError, OSError) as exc:
                self._json({"error": str(exc)}, 404)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length") or 0)
                change = json.loads(self.rfile.read(length) or b"{}")
                self._json(review.update(change.pop("key"), change))
            except (KeyError, ValueError, OSError) as exc:
                self._json({"error": str(exc)}, 400)

        def log_message(self, *_args: object) -> None:
            pass

    return Handler


def cmd_review(args: argparse.Namespace) -> int:
    if not load_cuts(args.dir)["clips"]:
        raise SystemExit("Nothing has been cut yet. Run `voicepack.py cut` first.")
    # This machine only: the page can rewrite clips.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), review_handler(Review(args.dir)))
    print(f"Review the clips at http://localhost:{args.port}  (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


# -- build -------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    clips_dir = args.dir / "clips"
    known = load_cuts(args.dir)["clips"]
    rejected = {key for key, clip in known.items() if clip.get("rejected")}
    unchecked = sum(1 for clip in known.values() if clip.get("doubtful"))
    files = sorted(path for path in clips_dir.glob("*.wav") if path.stem not in rejected)
    if not files:
        raise SystemExit(f"No clips in {clips_dir}. Run `voicepack.py cut` first.")
    have = {path.stem for path in files}
    core_missing = [clip for clip in catalogue.core_clips() if clip.key not in have]
    manifest = {
        "format": FORMAT,
        "name": args.name,
        "rate": RATE,
        "created": date.today().isoformat(),
        "crs": {
            crs: slug for crs, slug in catalogue.crs_index().items() if f"s.{slug}.mid" in have
        },
    }
    target: Path = args.out or args.dir / "voicepack.zip"
    # Stored, not deflated: speech barely compresses, and the Pi reads clips in place.
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
        for path in files:
            archive.write(path, member(path.stem))
    pack = VoicePack(target)
    stations = sum(1 for key in have if key.startswith("s.") and key.endswith(".mid"))
    reasons = sum(1 for key in have if key.startswith("r."))
    print(f"{target}: {len(pack)} clips, {target.stat().st_size / 1e6:.1f} MB")
    print(f"  {stations} stations, {reasons} reasons")
    if core_missing:
        print(f"  {len(core_missing)} core clips missing; announcements needing them use Piper:")
        print("   ", ", ".join(clip.key for clip in core_missing[:20]))
    if rejected:
        print(f"  {len(rejected)} rejected clips left out; `script --redo` lists them to record")
    if unchecked:
        print(f"  {unchecked} clips are still marked to check (`voicepack.py review`)")
    print("Install it from /admin → Announcements → Recorded voice.")
    return 0


# -- demo --------------------------------------------------------------------


def cmd_demo(args: argparse.Namespace) -> int:
    pack = VoicePack(args.pack or args.dir / "voicepack.zip")
    if args.board:
        chosen = board_services(args.board)[: args.count]
    else:
        saved = json.loads((args.dir / "script" / SAMPLE).read_text(encoding="utf-8"))
        chosen = [(saved["mode"], Service.model_validate(saved["service"]))]
    kinds = [AnnouncementKind(kind) for kind in args.kind]
    for index, (mode, service) in enumerate(chosen):
        for kind in kinds:
            if kind is AnnouncementKind.DELAYED and not service.delay_minutes:
                service = service.model_copy(update={"delay_minutes": 7})
            plan, optional = build_plan(kind, service, mode, pack)
            print(f"\n{build_text(kind, service, mode)}")
            if optional:
                print("  left out, no clip:", ", ".join(optional))
            if not plan.playable:
                print("  Piper would speak this one; no clip for:", ", ".join(plan.missing))
                continue
            target = pack.render(plan.parts, args.dir / f"demo-{index}-{kind.value}.wav")
            print(f"  {target}")
            if shutil.which("afplay") and not args.quiet:
                subprocess.run(["afplay", str(target)], check=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", type=Path, default=Path("voicepack"), help="working folder")
    commands = parser.add_subparsers(dest="command", required=True)

    script = commands.add_parser("script", help="write the recording script")
    script.add_argument("--near", type=lambda v: v.split(","), help="CRS codes of your stations")
    script.add_argument("--km", type=float, default=20, help="how far around them to record")
    script.add_argument("--board", help="a running board, e.g. http://describer.local:8080")
    script.add_argument("--all-stations", action="store_true", help="every station in Britain")
    script.add_argument("--chars", type=int, default=4500, help="most characters in one part")
    script.add_argument("--items", type=int, default=200, help="most items in one part")
    script.add_argument(
        "--breaks", type=float, default=0, help="seconds of <break> after each item"
    )
    script.add_argument("--topup", metavar="URL", help="only what a running board found missing")
    script.add_argument("--redo", action="store_true", help="only the clips rejected in review")
    script.add_argument("--force", action="store_true", help="replace an existing script")
    script.set_defaults(run=cmd_script)

    cut = commands.add_parser("cut", help="cut recorded audio into clips")
    cut.add_argument("--only", nargs="*", default=[], help="part numbers to cut (default: all)")
    cut.set_defaults(run=cmd_cut)

    review = commands.add_parser("review", help="check the clips in a browser")
    review.add_argument("--port", type=int, default=8091)
    review.set_defaults(run=cmd_review)

    build = commands.add_parser("build", help="zip the clips into a voice pack")
    build.add_argument("--name", default="Recorded voice")
    build.add_argument("--out", type=Path)
    build.set_defaults(run=cmd_build)

    demo = commands.add_parser("demo", help="play sample announcements from the pack")
    demo.add_argument("--pack", type=Path)
    demo.add_argument("--board", help="use the trains on a running board")
    demo.add_argument("--count", type=int, default=2)
    demo.add_argument(
        "--kind", nargs="*", default=["arriving"], choices=[k.value for k in AnnouncementKind]
    )
    demo.add_argument("--quiet", action="store_true", help="write the files without playing them")
    demo.set_defaults(run=cmd_demo)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
