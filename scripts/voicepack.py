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
import contextlib
import json
import shutil
import subprocess
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from describer.announce import workshop  # noqa: E402
from describer.announce.clips import (
    VoicePack,  # noqa: E402
    build_plan,  # noqa: E402
)
from describer.announce.cutter import CutError  # noqa: E402
from describer.announce.phrasing import AnnouncementKind, build_text, calling_points  # noqa: E402,F401
from describer.announce.workshop import (  # noqa: E402,F401
    CUTS,
    REVIEW_PAGE,
    SAMPLE,
    Review,
    decode,
    load_cuts,
    nearby,
    save_cuts,
    write_wav,
)
from describer.rail.models import Service  # noqa: E402

#: The review page asks for the same routes the board serves, under this voice.
LOCAL = "/api/voices/local/review"


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


# -- script ------------------------------------------------------------------


def cmd_script(args: argparse.Namespace) -> int:
    topup = None
    if args.topup:
        status = _fetch(args.topup.rstrip("/") + "/api/status")
        topup = (status.get("voice_pack") or {}).get("missing") or []
    try:
        result = workshop.write_script(
            args.dir,
            near=args.near or (),
            km=args.km,
            seen=board_services(args.board)
            if args.board and not (topup is not None or args.redo)
            else (),
            all_stations=args.all_stations,
            one_reading=args.one_reading,
            chars=args.chars,
            items=args.items,
            breaks=args.breaks,
            force=args.force,
            topup=topup,
            redo=args.redo,
        )
    except workshop.NothingToRecord as exc:
        print(exc)
        return 0
    print("\n".join(result.table()))
    print(f"\n{len(result.rows)} parts, {result.total:,} characters, in {result.folder}")
    return 0


# -- cut ---------------------------------------------------------------------


def cmd_cut(args: argparse.Namespace) -> int:
    def show(part: workshop.PartResult) -> None:
        if part.ok:
            print(
                f"✓ {part.stem}  {part.items} clips from {part.source}: usual pause "
                f"{part.usual_pause_ms} ms, shortest {part.shortest_pause_ms} ms, "
                f"{part.doubtful} to check" + (f", {part.kept} hand cuts kept" if part.kept else "")
            )
        else:
            print(f"✗ {part.stem}  {part.source}: {part.error}")

    summary = workshop.cut_folder(args.dir, args.only, show)
    print(
        f"\n{summary.cut} cut, {summary.failed} refused, {summary.waiting} still to record. "
        f"Clips in {summary.clips_dir}"
    )
    if summary.doubts:
        print(f"{summary.doubts} clips to check: run `voicepack.py review`.")
    return 1 if summary.failed else 0


# -- review ------------------------------------------------------------------


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
                if url.path in ("/", "/admin/voice-review"):
                    self._send(REVIEW_PAGE.read_bytes(), "text/html; charset=utf-8")
                elif url.path == f"{LOCAL}/cuts":
                    self._json(review.cuts())
                elif url.path == f"{LOCAL}/clip":
                    self._send(review.clip(query["key"][0]), "audio/wav")
                elif url.path == f"{LOCAL}/window":
                    part, start, end = (
                        query["part"][0],
                        int(query["start"][0]),
                        int(query["end"][0]),
                    )
                    self._send(review.window(part, start, end), "audio/wav")
                else:
                    self._json({"error": "not found"}, 404)
            except (KeyError, ValueError, OSError, workshop.WorkshopError) as exc:
                self._json({"error": str(exc)}, 404)

        def do_POST(self) -> None:  # noqa: N802
            try:
                if urlparse(self.path).path != f"{LOCAL}/clip":
                    self._json({"error": "not found"}, 404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                change = json.loads(self.rfile.read(length) or b"{}")
                self._json(review.update(change.pop("key"), change))
            except (KeyError, ValueError, OSError, workshop.WorkshopError) as exc:
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
    result = workshop.build_pack(args.dir, args.name, args.out or args.dir / "voicepack.zip")
    print(f"{result.target}: {result.clips} clips, {result.size / 1e6:.1f} MB")
    print(f"  {result.stations} stations, {result.reasons} reasons")
    if result.core_missing:
        print(
            f"  {len(result.core_missing)} core clips missing; "
            "announcements needing them use Piper:"
        )
        print("   ", ", ".join(result.core_missing[:20]))
    if result.rejected:
        print(f"  {result.rejected} rejected clips left out; `script --redo` lists them to record")
    if result.unchecked:
        print(f"  {result.unchecked} clips are still marked to check (`voicepack.py review`)")
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
    script.add_argument(
        "--one-reading",
        action="store_true",
        help="record each station once, for a voice that reads a name the same in a list "
        "and at the end of a sentence",
    )
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
    try:
        return args.run(args)
    except (workshop.WorkshopError, CutError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    sys.exit(main())
