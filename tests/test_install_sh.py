"""Static checks on deploy/install.sh: no Pi, no root, no network needed."""

import re
import shutil
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "deploy" / "install.sh"
KIOSK_UNIT = INSTALL_SH.parent / "kiosk.service"

#: provision() must be chroot-safe: no live systemd assumed. migrate_old_layout
#: is the one deliberate exception -- it only ever runs against a real
#: per-user install (which cannot exist inside the pi-gen chroot), and
#: disabling that install's *user* unit has no system-scope equivalent.
CHROOT_UNSAFE = (
    "systemctl start",
    "systemctl restart",
    "systemctl is-active",
    "systemctl daemon-reload",
)
EXEMPT_FROM_CHROOT_CHECK = {"migrate_old_layout"}


def function_body(name: str) -> str:
    text = INSTALL_SH.read_text()
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"no {name}() function found in install.sh"
    return match.group(1)


def called_functions(body: str) -> list[str]:
    """Bare-word lines: a call to a helper with no arguments."""
    return re.findall(r"^\s*(\w+)\s*$", body, re.MULTILINE)


def test_syntax_is_valid():
    subprocess.run(["bash", "-n", str(INSTALL_SH)], check=True)


def test_shellcheck_if_available():
    if shutil.which("shellcheck") is None:
        return
    subprocess.run(["shellcheck", str(INSTALL_SH)], check=True)


def test_provision_and_its_helpers_are_chroot_safe():
    provision = function_body("provision")
    assert "hostname -I" not in provision
    for helper in CHROOT_UNSAFE:
        assert helper not in provision

    for name in called_functions(provision):
        if name in EXEMPT_FROM_CHROOT_CHECK:
            continue
        body = function_body(name)
        assert "hostname -I" not in body, name
        for helper in CHROOT_UNSAFE:
            assert helper not in body, (name, helper)


def test_systemctl_user_scope_is_only_the_migration():
    text = INSTALL_SH.read_text()
    migrate_body = function_body("migrate_old_layout")
    for line in text.splitlines():
        if "systemctl --user" in line:
            assert line.strip() in migrate_body, line


def test_a_config_yaml_in_the_checkout_is_refused():
    text = INSTALL_SH.read_text()
    assert "config.yaml exists" in text


def test_helpers_never_end_a_function_on_a_bare_and_list():
    """CLAUDE.md trap: `[ ... ] && cmd` as a bare statement dies under set -e."""
    text = INSTALL_SH.read_text()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("if ") or stripped.startswith("elif "):
            continue
        assert not re.search(r"^\[\s.*\]\s*&&\s*\S", stripped), f"line {lineno}: {line!r}"


def test_the_kiosk_does_not_depend_on_the_default_target():
    """Pi OS's first-boot user setup resets the default to multi-user.target.

    On a flashed image that runs after install.sh, so a kiosk wanted by
    graphical.target never started (docs/traps.md).
    """
    install_section = KIOSK_UNIT.read_text().split("[Install]", 1)[1]
    wanted_by = [
        line.split("=", 1)[1].strip()
        for line in install_section.splitlines()
        if line.startswith("WantedBy=")
    ]
    assert wanted_by == ["multi-user.target"]
    assert "set-default" not in INSTALL_SH.read_text()


def test_install_units_keeps_tty1_for_the_kiosk():
    """tty1's getty is switched off by a condition, never by a mask or a conflict.

    Pi OS's first-boot user setup ends with `systemctl enable getty@tty1` and
    `systemctl start getty@tty1`. Masked, both fail and the user setup loops;
    with a Conflicts= the start stops the kiosk (docs/traps.md).
    """
    body = function_body("install_units")
    assert "systemctl mask" not in INSTALL_SH.read_text()
    assert "systemctl unmask getty@tty1.service" in body
    assert "/etc/systemd/system/getty@tty1.service.d/10-describer-kiosk.conf" in body
    # An install from before the change still has the old link.
    assert "rm -f /etc/systemd/system/graphical.target.wants/kiosk.service" in body

    drop_in = (KIOSK_UNIT.parent / "getty-tty1-kiosk.conf").read_text()
    assert (
        "ConditionPathExists=!/etc/systemd/system/multi-user.target.wants/kiosk.service" in drop_in
    )
    unit_lines = [line for line in KIOSK_UNIT.read_text().splitlines() if not line.startswith("#")]
    assert not any(line.startswith("Conflicts=") for line in unit_lines)


#: Every CSS cursor keyword but `auto` and `none`: what Chromium and
#: cursor-shape-v1 ask for. The blank theme must answer to each (design note 24).
CSS_CURSORS = [
    "default",
    "context-menu",
    "help",
    "pointer",
    "progress",
    "wait",
    "cell",
    "crosshair",
    "text",
    "vertical-text",
    "alias",
    "copy",
    "move",
    "no-drop",
    "not-allowed",
    "grab",
    "grabbing",
    "all-scroll",
    "col-resize",
    "row-resize",
    "n-resize",
    "e-resize",
    "s-resize",
    "w-resize",
    "ne-resize",
    "nw-resize",
    "se-resize",
    "sw-resize",
    "ew-resize",
    "ns-resize",
    "nesw-resize",
    "nwse-resize",
    "zoom-in",
    "zoom-out",
]

#: The X names cage and older Chromium builds ask for.
X_CURSORS = ["left_ptr", "left_ptr_watch", "watch", "xterm", "hand2", "fleur"]


def test_the_blank_cursor_theme_answers_to_every_cursor_name(tmp_path):
    # install(1) differs between GNU and BSD, and the real one would want root
    # for /usr/local, so the function runs against a temp dir with a stand-in.
    body = function_body("install_cursor_theme").replace(
        "/usr/local/share/describer-cursors", str(tmp_path)
    )
    script = (
        "set -euo pipefail\n"
        "say() { :; }\n"
        'install() { if [ "$1" = -d ]; then shift 3; mkdir -p "$@";'
        ' else cat > "$4"; fi; }\n'
        f"install_cursor_theme() {{\n{body}}}\n"
        "install_cursor_theme\n"
    )
    subprocess.run(["bash", "-c", script], check=True)

    cursors = tmp_path / "default" / "cursors"
    blank = (cursors / "default").read_bytes()
    assert blank.startswith(b"Xcur")
    assert set(blank[-24 * 24 * 4 :]) == {0}, "the cursor image must be transparent"
    for name in CSS_CURSORS + X_CURSORS:
        assert (cursors / name).resolve() == (cursors / "default").resolve(), name

    theme = (tmp_path / "default" / "index.theme").read_text()
    assert "Inherits" not in theme
    assert (tmp_path / "describer-blank").resolve() == (tmp_path / "default").resolve()


def test_the_kiosk_finds_no_cursor_theme_but_the_blank_one():
    """Any other directory on the path lends its visible cursors (note 24)."""
    paths = re.findall(r"^Environment=XCURSOR_PATH=(.*)$", KIOSK_UNIT.read_text(), re.MULTILINE)
    assert paths == ["/usr/local/share/describer-cursors"]
