#!/usr/bin/env bash
# Install the deploy/ files (units, polkit rules, the shutdown button) for the
# release /admin has just updated the checkout to.
#
# Installed root-owned as /usr/local/sbin/describer-apply-deploy and run by
# describer-apply-deploy.service (Type=oneshot, User=root). The unprivileged
# describer user is allowed by polkit to *start* that unit and nothing else.
#
# THREAT MODEL. /admin has no login, so anyone on the LAN can press "Update",
# and the app runs as `describer`, which owns /opt/describer. So everything in
# /opt/describer, including its .git and every file under deploy/, is
# attacker-writable, and this script runs as root. It therefore trusts exactly
# one thing from there, a 40-hex commit id, and checks that against GitHub:
#
#   * it never runs, sources, installs or copies anything from /opt/describer;
#   * it keeps its own root-owned clone of the public repository, at a URL
#     written below rather than read from the checkout's remote;
#   * it refuses unless the commit is in that clone AND is `main` or an
#     ancestor of it, and then runs `install.sh provision` from the clone,
#     checked out at that commit, so every root action is code that came
#     straight from GitHub main.
#
# What that buys: a LAN attacker can only make Describer install code that is
# already on GitHub main, never their own. Someone who already runs code as
# `describer` still cannot reach root through this path, but can pick an
# *older* main commit, i.e. a downgrade. Nothing here defends against GitHub
# main itself being bad; that is the repository owner's to keep. See
# docs/design/13-credentials-and-updates.md.
#
# Nothing in the environment is read: the unit gives this script a clean one,
# and it sets its own below, so no variable can move the URL or the paths.
set -euo pipefail

CHECKOUT=/opt/describer
DEPLOY_CLONE=/var/lib/describer-deploy
REPO_URL=https://github.com/grahamlehr/describer.git
BRANCH=main

log() { echo "apply-deploy: $*"; }

die() {
  log "REFUSING: $*" >&2
  exit 1
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "apply-deploy: must run as root" >&2
    exit 1
  fi
}

restart_backend() {
  # However this ends, the checkout is already on the new release and the
  # updater smoke-tested it, so the backend restarts into it: leaving the
  # board on the old process would only hide that the files were not applied.
  # Nothing here is a privilege question; the describer user could do it too.
  log "restarting describer.service"
  systemctl restart --no-block describer.service || true
}

checkout_commit() {
  # The one thing read from the untrusted tree. git runs as root against a
  # directory another user owns, hence safe.directory (honoured on the command
  # line). `rev-parse HEAD` reads a ref and runs no hook and no config command.
  local hash
  hash="$(GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    timeout 30 git -C "$CHECKOUT" -c "safe.directory=$CHECKOUT" rev-parse HEAD)"
  if [[ ! "$hash" =~ ^[0-9a-f]{40}$ ]]; then
    die "$CHECKOUT did not name a commit"
  fi
  printf '%s\n' "$hash"
}

refresh_clone() {
  export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 LC_ALL=C
  if [ ! -d "$DEPLOY_CLONE/.git" ]; then
    log "cloning $REPO_URL into $DEPLOY_CLONE"
    rm -rf "$DEPLOY_CLONE"
    timeout 600 git clone --quiet --branch "$BRANCH" "$REPO_URL" "$DEPLOY_CLONE"
  fi
  # The URL is ours, not whatever an earlier state of the clone held.
  git -C "$DEPLOY_CLONE" remote set-url origin "$REPO_URL"
  log "fetching $REPO_URL"
  timeout 300 git -C "$DEPLOY_CLONE" fetch --quiet origin \
    "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
}

verify_commit() {  # verify_commit <hash>
  local hash="$1"
  if ! git -C "$DEPLOY_CLONE" cat-file -e "$hash^{commit}" 2>/dev/null; then
    die "commit $hash is not in $REPO_URL"
  fi
  if ! git -C "$DEPLOY_CLONE" merge-base --is-ancestor "$hash" "origin/$BRANCH"; then
    die "commit $hash is not on $BRANCH of $REPO_URL"
  fi
  log "commit $hash is on $BRANCH of $REPO_URL"
}

fingerprint() {
  # What the running services were installed from, to see afterwards which of
  # them need restarting. A missing file is its own answer.
  local file
  for file in \
    /etc/systemd/system/kiosk.service \
    /etc/systemd/system/shutdown-button.service \
    /usr/local/bin/shutdown-button; do
    if [ -f "$file" ]; then
      sha256sum "$file"
    else
      echo "missing  $file"
    fi
  done
}

restart_if_changed() {  # restart_if_changed <unit> <before> <after> <file...>
  local unit="$1" before="$2" after="$3" file line_before line_after
  shift 3
  for file in "$@"; do
    line_before="$(grep -F " $file" <<<"$before" || true)"
    line_after="$(grep -F " $file" <<<"$after" || true)"
    if [ "$line_before" != "$line_after" ]; then
      log "$file changed; restarting $unit"
      systemctl restart "$unit" || log "$unit did not restart"
      return
    fi
  done
  log "$unit unchanged"
}

main() {
  require_root
  umask 022
  export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  trap restart_backend EXIT

  log "started"
  local hash
  hash="$(checkout_commit)"
  log "$CHECKOUT is at $hash"

  refresh_clone
  verify_commit "$hash"
  git -C "$DEPLOY_CLONE" checkout --quiet --detach --force "$hash"
  git -C "$DEPLOY_CLONE" clean --quiet -fdx
  if [ "$(git -C "$DEPLOY_CLONE" rev-parse HEAD)" != "$hash" ]; then
    die "$DEPLOY_CLONE did not check out $hash"
  fi

  local before after
  before="$(fingerprint)"

  log "running install.sh provision from $DEPLOY_CLONE at $hash"
  # A clean environment: nothing from ours steers provision, and DESCRIBER_REF /
  # DESCRIBER_REPO_URL, which only matter to a fresh install, are unset.
  env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive \
    "$DEPLOY_CLONE/deploy/install.sh" provision

  log "reloading systemd"
  systemctl daemon-reload
  after="$(fingerprint)"
  restart_if_changed kiosk.service "$before" "$after" \
    /etc/systemd/system/kiosk.service
  restart_if_changed shutdown-button.service "$before" "$after" \
    /etc/systemd/system/shutdown-button.service /usr/local/bin/shutdown-button

  log "done"
}

main "$@"
# Stop here: this file may be replaced by the install it just ran, and bash
# reads a script as it goes.
exit 0
