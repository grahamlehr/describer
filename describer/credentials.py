"""The two API credentials, written from /admin and never read back to it.

The keys live in an env file, never in ``config.yaml``. On the Pi that is
``/etc/describer/describer.env``, which the backend unit reads *last* so that
nothing in the checkout can blank it (Addendum 1); a key written anywhere else
would be overridden on the next restart. On a dev machine it is the repo
``.env``. ``DESCRIBER_ENV_FILE`` points it somewhere else.

Nothing here returns a value. What /admin gets back is whether each key is
set, and when it was last changed from /admin.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

#: Source name -> the environment variable holding its credential.
ENV_NAMES = {"rdm": "RDM_API_KEY", "rtt": "RTT_TOKEN"}

#: Where install.sh puts the credentials, and what the backend unit reads last.
DEPLOYED_ENV_FILE = Path("/etc/describer/describer.env")

#: The dev machine's env file, beside config.yaml at the repo root.
REPO_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

#: The comment written above a key set from /admin, carrying when.
_STAMP = re.compile(r"^#\s*(RDM_API_KEY|RTT_TOKEN) set from /admin at (\S+)\s*$")


class CredentialError(ValueError):
    """A credential that cannot be written, or may not be written here."""


def env_file() -> Path:
    """The file the running backend's credentials belong in."""
    override = os.environ.get("DESCRIBER_ENV_FILE")
    if override:
        return Path(override)
    if DEPLOYED_ENV_FILE.parent.is_dir():
        return DEPLOYED_ENV_FILE
    return REPO_ENV_FILE


def rtt_allowed() -> bool:
    """False on a dev machine, where an RTT token spends the Pi's allowance.

    Addendum 4: local runs never call data.rtt.io, and leaving RTT_TOKEN unset
    is the primary guard. /admin must not be the easy way round it.
    """
    return DEPLOYED_ENV_FILE.parent.is_dir() or os.environ.get("DESCRIBER_ALLOW_RTT_TOKEN") == "1"


def validate(value: str) -> str:
    """The value as it will be written. Raises :class:`CredentialError`."""
    value = value.strip()
    if not value:
        raise CredentialError("A credential cannot be blank; use Clear to remove one")
    if "'" in value:
        # Written single-quoted, as install.sh does, so systemd and dotenv
        # both take it literally. A quote inside it cannot survive that.
        raise CredentialError("A credential cannot contain a single quote")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CredentialError("A credential cannot contain control characters or line breaks")
    return value


def _assigns(line: str, name: str) -> bool:
    return re.match(rf"^\s*(export\s+)?{name}\s*=", line) is not None


def changed_at(path: Path) -> dict[str, str | None]:
    """When each key was last set from /admin, from the stamps in the file."""
    stamps: dict[str, str | None] = dict.fromkeys(ENV_NAMES)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return stamps
    by_var = {var: source for source, var in ENV_NAMES.items()}
    for line in lines:
        match = _STAMP.match(line)
        if match:
            stamps[by_var[match.group(1)]] = match.group(2)
    return stamps


def status(path: Path | None = None) -> dict[str, object]:
    """What /admin may know: set or not, when, and where. Never a value."""
    path = path or env_file()
    stamps = changed_at(path)
    return {
        "path": str(path),
        "rtt_allowed": rtt_allowed(),
        "keys": {
            source: {
                "set": bool(os.environ.get(var, "").strip()),
                "changed_at": stamps[source],
            }
            for source, var in ENV_NAMES.items()
        },
    }


def write(
    updates: dict[str, str],
    clear: set[str] = frozenset(),  # type: ignore[assignment]
    path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Set and clear keys in the env file, then in this process's environment.

    ``updates`` and ``clear`` are keyed by source name. Lines the file holds
    that are not ours are kept as they are. A cleared key loses its line
    altogether: an empty assignment is still an assignment, and a bare
    ``RDM_API_KEY=`` is what once emptied the deployed key (Addendum 1).
    """
    path = path or env_file()
    unknown = (set(updates) | set(clear)) - set(ENV_NAMES)
    if unknown:
        raise CredentialError(f"Unknown source: {', '.join(sorted(unknown))}")
    values = {source: validate(value) for source, value in updates.items()}
    if "rtt" in values and not rtt_allowed():
        raise CredentialError(
            "This is not the Pi: an RTT token here would spend the live board's "
            "allowance. Set DESCRIBER_ALLOW_RTT_TOKEN=1 to override."
        )

    touched = {ENV_NAMES[source] for source in set(values) | set(clear)}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []

    kept = [
        line
        for line in lines
        if not any(_assigns(line, var) for var in touched)
        and not ((match := _STAMP.match(line)) and match.group(1) in touched)
    ]
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    for source, value in values.items():
        var = ENV_NAMES[source]
        kept += [f"# {var} set from /admin at {stamp}", f"{var}='{value}'"]

    # In place, not a temp file renamed over it: /etc/describer is root's, and
    # the backend may write the file but cannot create one beside it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write("\n".join(kept) + "\n" if kept else "")

    for source, value in values.items():
        os.environ[ENV_NAMES[source]] = value
        log.info("%s updated from /admin", ENV_NAMES[source])
    for source in clear:
        os.environ.pop(ENV_NAMES[source], None)
        log.info("%s cleared from /admin", ENV_NAMES[source])
