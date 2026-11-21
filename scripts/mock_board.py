#!/usr/bin/env python3
"""A board with made-up trains, for checking a theme without any rail API.

Serves the real ``index.html``, ``board.js`` and ``/static`` from the checkout
and feeds them an invented state over ``/api/stream``. Nothing here imports
the app, reads a credential or opens an outbound connection, so it is safe to
leave running and costs no RDM or RTT allowance.

    python3 scripts/mock_board.py --port 8090 --theme swiss --boards 2

Change what is on screen while it runs (every open board follows at once):

    /mock?theme=linemap          any theme name
    /mock?boards=1               1 or 2
    /mock?rows=8                 configured rows per board
    /mock?mode=arrivals          mode of the second board (first stays departures)
    /mock?source=rtt             an RTT board: no position, formation or journey
    /mock?stale=1                the stale flag, with an error
    /mock?empty=1                no services at all
    /mock?times=1                display.show_calling_times
    /mock?messages=1             an NRCC message on the first board
    /mock?name=London+St+Pancras+International   the first board's station name
    /mock?clock=0                display.clock off
    /mock?both=1                 display.clock_both
    /mock?opt.ground=dark        an option for the current theme

The trains roll: one every few minutes, for ever, with the names that break
layouts (``London Charing Cross``, ``Ashford International``,
``Abbey Wood via Whitechapel``), a late one, a "Delayed" one and a cancelled
one with its reason always somewhere on the board.
"""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

STATIC = Path(__file__).resolve().parent.parent / "describer" / "web" / "static"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
}
#: Minutes between one invented train and the next.
HEADWAY = 4
#: How often every open board is sent the state again.
PUSH_SECONDS = 5.0

# (destination, operator, code, platform, coaches, stops before here, stops after)
# A stop is (name, minutes from this station); the last "after" is the terminus.
ROUTES_A = [
    (
        "London Charing Cross",
        "Southeastern",
        "SE",
        "6",
        8,
        [("Hastings", -92), ("Tunbridge Wells", -52), ("Sevenoaks", -31), ("Orpington", -17)],
        [("London Waterloo East", 5), ("London Charing Cross", 9)],
    ),
    (
        "Ashford International",
        "Southeastern",
        "SE",
        "2",
        12,
        [("London Cannon Street", -5)],
        [
            ("Orpington", 16),
            ("Sevenoaks", 26),
            ("Tonbridge", 36),
            ("Paddock Wood", 44),
            ("Marden", 49),
            ("Staplehurst", 54),
            ("Headcorn", 60),
            ("Pluckley", 67),
            ("Ashford International", 76),
        ],
    ),
    (
        "Abbey Wood via Whitechapel",
        "Elizabeth line",
        "XR",
        "5",
        9,
        [("London Paddington", -14), ("Bond Street", -9), ("Farringdon", -4)],
        [
            ("Whitechapel", 3),
            ("Canary Wharf", 7),
            ("Custom House", 11),
            ("Woolwich", 15),
            ("Abbey Wood", 19),
        ],
    ),
    (
        "Brighton",
        "Thameslink",
        "TL",
        "5",
        12,
        [
            ("Bedford", -84),
            ("Luton", -60),
            ("St Albans City", -45),
            ("London St Pancras International", -17),
            ("Farringdon", -12),
            ("City Thameslink", -9),
            ("London Blackfriars", -6),
        ],
        [
            ("East Croydon", 14),
            ("Gatwick Airport", 30),
            ("Three Bridges", 35),
            ("Balcombe", 41),
            ("Haywards Heath", 46),
            ("Wivelsfield", 50),
            ("Burgess Hill", 52),
            ("Hassocks", 56),
            ("Preston Park", 62),
            ("Brighton", 67),
        ],
    ),
    (
        "Dartford",
        "Southeastern",
        "SE",
        "1",
        10,
        [("London Cannon Street", -4)],
        [
            ("Deptford", 6),
            ("Greenwich", 8),
            ("Maze Hill", 10),
            ("Westcombe Park", 12),
            ("Charlton", 15),
            ("Woolwich Dockyard", 18),
            ("Woolwich Arsenal", 21),
            ("Plumstead", 23),
            ("Abbey Wood", 27),
            ("Belvedere", 30),
            ("Erith", 33),
            ("Slade Green", 36),
            ("Dartford", 42),
        ],
    ),
    (
        "Bedford",
        "Thameslink",
        "TL",
        "4",
        12,
        [("Brighton", -66), ("Gatwick Airport", -29), ("East Croydon", -14)],
        [
            ("London Blackfriars", 6),
            ("City Thameslink", 8),
            ("Farringdon", 11),
            ("London St Pancras International", 16),
            ("St Albans City", 37),
            ("Harpenden", 43),
            ("Luton Airport Parkway", 49),
            ("Luton", 53),
            ("Flitwick", 63),
            ("Bedford", 75),
        ],
    ),
    (
        "Gillingham (Kent)",
        "Thameslink",
        "TL",
        "3",
        8,
        [("Luton", -58), ("London Blackfriars", -6)],
        [
            ("Greenwich", 9),
            ("Woolwich Arsenal", 20),
            ("Dartford", 38),
            ("Gravesend", 50),
            ("Strood", 61),
            ("Rochester", 65),
            ("Chatham", 68),
            ("Gillingham (Kent)", 73),
        ],
    ),
    (
        "East Croydon",
        "Southern",
        "SN",
        None,
        8,
        [],
        [("Norwood Junction", 9), ("East Croydon", 14)],
    ),
]
ROUTES_B = [
    (
        "London Paddington",
        "Great Western Railway",
        "GW",
        "10",
        9,
        [
            ("Bristol Temple Meads", -78),
            ("Bath Spa", -65),
            ("Swindon", -30),
            ("Didcot Parkway", -14),
        ],
        [("London Paddington", 25)],
    ),
    (
        "Penzance",
        "Great Western Railway",
        "GW",
        "7",
        10,
        [("London Paddington", -26)],
        [
            ("Taunton", 78),
            ("Exeter St Davids", 104),
            ("Newton Abbot", 124),
            ("Plymouth", 163),
            ("Truro", 240),
            ("Penzance", 282),
        ],
    ),
    (
        "Oxford",
        "Great Western Railway",
        "GW",
        "8",
        5,
        [("London Paddington", -27), ("Slough", -12)],
        [("Didcot Parkway", 14), ("Oxford", 27)],
    ),
    (
        "Manchester Piccadilly",
        "CrossCountry",
        "XC",
        "3",
        4,
        [("Bournemouth", -98), ("Southampton Central", -65), ("Basingstoke", -22)],
        [
            ("Oxford", 24),
            ("Banbury", 43),
            ("Leamington Spa", 61),
            ("Coventry", 75),
            ("Birmingham New Street", 100),
            ("Stoke-on-Trent", 150),
            ("Manchester Piccadilly", 194),
        ],
    ),
    (
        "Abbey Wood via Whitechapel",
        "Elizabeth line",
        "XR",
        "14",
        9,
        [],
        [
            ("Twyford", 6),
            ("Maidenhead", 13),
            ("Slough", 22),
            ("Hayes & Harlington", 35),
            ("Ealing Broadway", 44),
            ("London Paddington", 53),
            ("Whitechapel", 66),
            ("Abbey Wood", 82),
        ],
    ),
    (
        "Basingstoke",
        "Great Western Railway",
        "GW",
        "2",
        3,
        [],
        [("Reading West", 3), ("Mortimer", 11), ("Bramley (Hants)", 17), ("Basingstoke", 25)],
    ),
]
STATIONS = [("LBG", "London Bridge", ROUTES_A), ("RDG", "Reading", ROUTES_B)]

settings: dict[str, object] = {
    "theme": "modern",
    "boards": 2,
    "rows": 8,
    "mode": "departures",
    "source": "rdm",
    "stale": False,
    "empty": False,
    "times": False,
    "messages": False,
    "name": "",
    "clock": True,
    "both": False,
    "options": {},
}
changed = threading.Condition()


def hhmm(when: datetime) -> str:
    return when.strftime("%H:%M")


def stop(name: str, when: datetime, late: int, left: bool) -> dict:
    expected = hhmm(when + timedelta(minutes=late)) if late else "On time"
    return {
        "name": name,
        "crs": None,
        "scheduled_time": hhmm(when),
        "expected_time": None if left else expected,
        "actual_time": hhmm(when + timedelta(minutes=late)) if left else None,
        "cancelled": False,
        "here": False,
    }


def service(slot: int, routes: list, here: str, mode: str, now: datetime, top: bool) -> dict:
    """The train in ``slot``: slots are HEADWAY minutes apart since midnight."""
    destination, operator, code, platform, coaches, before, after = routes[slot % len(routes)]
    due = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=slot * HEADWAY)
    # One late, one "Delayed" and one cancelled train in every eleven.
    kind = {3: "late", 7: "delayed", 9: "cancelled"}.get(slot % 11, "on_time")
    late = 4 + slot % 5 if kind == "late" else 0
    rdm = settings["source"] == "rdm"

    past = [
        stop(n, due + timedelta(minutes=m), late, due + timedelta(minutes=m + late) <= now)
        for n, m in before
    ]
    ahead = [stop(n, due + timedelta(minutes=m), late, False) for n, m in after]
    this = stop(here, due, late, False) | {"here": True}
    origin = before[0][0] if before else here

    position = None
    left = [p for p in past if p["actual_time"]]
    if rdm and past and kind != "cancelled":
        if not left:
            position = {
                "state": "not_started",
                "last": None,
                "last_time": None,
                "next": past[0]["name"],
                "stops_away": len(past),
            }
        else:
            away = len(past) - len(left)
            position = {
                "state": "approaching" if away == 0 else "between",
                "last": left[-1]["name"],
                "last_time": left[-1]["actual_time"],
                "next": past[len(left)]["name"] if away else None,
                "stops_away": away,
            }

    formation = None
    if rdm and top:
        formation = {
            "coaches": [
                {
                    "number": str(i + 1),
                    "first_class": i == 0,
                    "accessible_toilet": i in (2, coaches - 2),
                    "toilet_in_service": i != 2,
                    "loading": (20 + 11 * ((slot + i * 3) % 8)),
                }
                for i in range(coaches)
            ],
            "average_loading": 50,
        }

    reasons = {
        "late": (None, "This is due to a fault with the signalling system"),
        "delayed": (None, "This is due to a shortage of train crew"),
        "cancelled": ("This is due to a points failure", None),
    }
    cancel_reason, delay_reason = reasons.get(kind, (None, None))
    return {
        "id": f"{slot}-{code}",
        "scheduled_time": hhmm(due),
        "expected_time": {
            "late": hhmm(due + timedelta(minutes=late)),
            "delayed": "Delayed",
            "cancelled": "Cancelled",
        }.get(kind, "On time"),
        "destination": destination,
        "origin": origin,
        "operator": operator,
        "operator_code": code,
        "platform": None if kind == "cancelled" else platform,
        "status": {"late": "expected"}.get(kind, kind),
        "delay_minutes": late,
        "cancel_reason": cancel_reason,
        "delay_reason": delay_reason,
        "calling_points": past if mode == "arrivals" else ahead,
        "journey": [*past, this, *ahead] if rdm and top else [],
        "length": coaches,
        "position": position,
        "formation": formation,
    }


def board(index: int, now: datetime) -> dict:
    crs, name, routes = STATIONS[index]
    if index == 0 and settings["name"]:
        name = str(settings["name"])
    mode = str(settings["mode"]) if index == 1 else "departures"
    if mode == "arrivals":
        # A train that starts here never arrives here.
        routes = [route for route in routes if route[5]]
    first = (now.hour * 60 + now.minute) // HEADWAY + 1
    services = []
    if not settings["empty"]:
        services = [
            service(first + n, routes, name, mode, now, n == 0)
            for n in range(int(settings["rows"]) + 2)
        ]
    stale = bool(settings["stale"])
    return {
        "crs": crs,
        "name": name,
        "mode": mode,
        "generated_at": now.isoformat(),
        "fetched_at": now.isoformat(),
        "services": services,
        "messages": ["Lifts at this station are out of order until further notice."]
        if settings["messages"] and index == 0
        else [],
        "source": settings["source"],
        "stale": stale,
        "error": "upstream timed out" if stale else None,
    }


def state() -> dict:
    now = datetime.now().astimezone()
    count = int(settings["boards"])
    theme = str(settings["theme"])
    return {
        "type": "state",
        "version": "mock",
        "server_time": now.isoformat(),
        "display_on": True,
        "active_profile": None,
        "display": {
            "theme": theme,
            "clock": bool(settings["clock"]),
            "clock_both": bool(settings["both"]),
            "show_calling_points": True,
            "show_position": True,
            "show_formation": True,
            "show_calling_times": bool(settings["times"]),
            "show_weather": False,
            "resolution": "auto",
            "themes": {theme: settings["options"]},
        },
        "stations": [
            {
                "crs": STATIONS[i][0],
                "name": None,
                "mode": "departures",
                "rows": int(settings["rows"]),
            }
            for i in range(count)
        ],
        "boards": [board(i, now) for i in range(count)],
        "weather": [None] * count,
        "setup": None,
    }


def coerce(value: str) -> object:
    if value in ("true", "false"):
        return value == "true"
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def apply(query: list[tuple[str, str]]) -> None:
    for key, value in query:
        if key.startswith("opt."):
            settings["options"] = {**settings["options"], key[4:]: coerce(value)}  # type: ignore[dict-item]
        elif key == "theme":
            settings["theme"] = value
            settings["options"] = {}
        elif key in ("boards", "rows"):
            settings[key] = int(value)
        elif key in ("mode", "source", "name"):
            settings[key] = value
        elif key in ("stale", "empty", "times", "messages", "clock", "both"):
            settings[key] = value not in ("0", "false", "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def send(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        if url.path == "/":
            self.send((STATIC / "index.html").read_bytes(), TYPES[".html"])
        elif url.path == "/mock":
            with changed:
                apply(parse_qsl(url.query))
                changed.notify_all()
            self.send(json.dumps(settings).encode(), TYPES[".json"])
        elif url.path == "/api/state":
            self.send(json.dumps(state()).encode(), TYPES[".json"])
        elif url.path == "/api/stream":
            self.stream()
        elif url.path.startswith("/static/"):
            target = (STATIC / url.path.removeprefix("/static/")).resolve()
            if STATIC not in target.parents or not target.is_file():
                self.send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
                return
            self.send(target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))
        else:
            self.send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

    def stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                self.wfile.write(f"data: {json.dumps(state())}\n\n".encode())
                self.wfile.flush()
                with changed:
                    changed.wait(PUSH_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--theme", default="modern")
    parser.add_argument("--boards", type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    settings["theme"] = args.theme
    settings["boards"] = args.boards
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"Mock board on http://localhost:{args.port}/  (theme {args.theme})")
    server.serve_forever()


if __name__ == "__main__":
    main()
