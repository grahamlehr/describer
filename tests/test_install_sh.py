"""Static checks on deploy/install.sh: no Pi, no root, no network needed."""

import re
import shutil
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "deploy" / "install.sh"

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
