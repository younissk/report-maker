#!/usr/bin/env bash
#
# Put report-maker where a Mac expects to find it: the CLI on PATH, the app in
# /Applications. One command, from a cold checkout.
#
#   scripts/install-app.sh              install
#   scripts/install-app.sh --uninstall  take both of them away again
#
# The whole thing is `make install`. It is written as a script rather than a
# Makefile recipe because the interesting part is not the five commands — it is
# the refusals around them. This is the only thing in the repository that writes
# outside the repository, so every write is preceded by a check that says what it
# is about to touch and stops when it cannot identify it.
#
# Three rules hold throughout:
#
#   - Never sudo, and never suggest it. /Applications is writable by the logged-in
#     user on a normal macOS install; when it is not, ~/Applications is the answer,
#     not privilege escalation.
#   - Never rm -rf a path that has not been identified. A bundle at the
#     destination is only removed once its CFBundleIdentifier has been read and
#     found to be ours; anything else aborts and leaves it alone.
#   - Never edit a shell profile. If ~/.local/bin is not on PATH the script prints
#     the exact line to add and where to add it, and that is where its involvement
#     with your dotfiles ends.
#
# Re-running it is the normal case, not the recovery case: every step is a no-op
# when it is already true.

set -euo pipefail

APP_ID="com.younisskandah.reportmaker"   # what a bundle must claim to be ours
APP_NAME="report-maker.app"              # electron-builder's productName + .app

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_SRC="$ROOT/bin/report-maker"
BIN_DIR="$HOME/.local/bin"
BIN_LINK="$BIN_DIR/report-maker"

# ── output ───────────────────────────────────────────────────────────────────
#
# Steps are numbered because the slow ones (npm, electron-builder) look identical
# to a hang, and "3/5 building the app" is the difference between waiting and
# reaching for ctrl-C.

STEP=0
STEPS=5

if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'; else B=""; D=""; R=""; fi

step() { STEP=$((STEP + 1)); printf '\n%s%d/%d  %s%s\n' "$B" "$STEP" "$STEPS" "$1" "$R"; }
note() { printf '       %s\n' "$1"; }
dim()  { printf '       %s%s%s\n' "$D" "$1" "$R"; }
warn() { printf '\n  warning  %s\n' "$1"; }
# `${1+"$@"}` rather than `"$@"`: bash 3.2, which is what /bin/bash still is on
# macOS, treats an empty "$@" as an unbound variable under `set -u`.
die()  { printf '\n  error  %s\n' "$1" >&2; shift; for l in ${1+"$@"}; do printf '         %s\n' "$l" >&2; done; exit 1; }

# ── the destination, and proving what is already there ───────────────────────

# /Applications is the one people mean. It is group-writable by admin users on a
# stock macOS, so this normally succeeds without privilege; ~/Applications is the
# fallback because Spotlight and Launchpad index it just the same.
choose_dest() {
    if [ -w /Applications ]; then
        printf '%s' /Applications
    else
        mkdir -p "$HOME/Applications"
        printf '%s' "$HOME/Applications"
    fi
}

bundle_id() { /usr/bin/plutil -extract CFBundleIdentifier raw -o - "$1/Contents/Info.plist" 2>/dev/null; }

# The load-bearing check. `rm -rf` aimed at /Applications is a command that has
# to be right the first time, so the only bundle this script will ever delete is
# one that has told us, in its own Info.plist, that it is the one we installed.
# Unreadable, missing plist, wrong id, a symlink we cannot vouch for: all abort.
assert_ours_or_absent() {
    local app="$1" id
    [ -e "$app" ] || [ -L "$app" ] || return 0

    if [ -L "$app" ]; then
        die "$app is a symlink, not a bundle." \
            "Refusing to touch it — remove it yourself and re-run if it is not wanted."
    fi
    if [ ! -d "$app" ]; then
        die "$app exists but is not an app bundle." \
            "Refusing to touch it."
    fi

    id="$(bundle_id "$app" || true)"
    if [ -z "$id" ]; then
        die "$app has no readable CFBundleIdentifier." \
            "That is not something this script installed, so it will not remove it." \
            "Move it aside yourself and re-run."
    fi
    if [ "$id" != "$APP_ID" ]; then
        die "$app belongs to something else (id: $id)." \
            "Expected $APP_ID. Refusing to touch an app that is not ours."
    fi
}

# ── uninstall ────────────────────────────────────────────────────────────────

uninstall() {
    STEPS=2
    printf '%sremoving report-maker%s\n' "$B" "$R"

    step "the app"
    local removed=0 dest app
    for dest in /Applications "$HOME/Applications"; do
        app="$dest/$APP_NAME"
        [ -e "$app" ] || [ -L "$app" ] || continue
        assert_ours_or_absent "$app"     # identified before anything is deleted
        rm -rf "$app"
        note "removed $app"
        removed=1
    done
    [ "$removed" = 1 ] || dim "no bundle in /Applications or ~/Applications"

    step "the cli"
    if [ -L "$BIN_LINK" ] && [ "$(readlink "$BIN_LINK")" = "$CLI_SRC" ]; then
        rm -f "$BIN_LINK"
        note "removed $BIN_LINK"
    elif [ -e "$BIN_LINK" ] || [ -L "$BIN_LINK" ]; then
        warn "$BIN_LINK does not point at this checkout — leaving it alone."
    else
        dim "no symlink at $BIN_LINK"
    fi

    printf '\nGone. Your vaults are untouched — they are just folders on disk.\n'
}

# ── 1. prerequisites ─────────────────────────────────────────────────────────
#
# Reported, never installed. A script that reaches for brew on your behalf is a
# script that installs things you did not ask for on a machine you have opinions
# about; naming the one command to run is the whole job.

check_prereqs() {
    step "checking prerequisites"

    # Newline-separated rather than an array: macOS still ships bash 3.2, where
    # an empty array under `set -u` is an unbound variable.
    local missing=""

    if command -v python3 >/dev/null 2>&1; then
        local pyv
        pyv="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
        if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            note "python3   $pyv"
        else
            note "python3   $pyv  — too old"
            missing="$missing"$'\n'"python3 3.11 or newer — the engine is Python:  brew install python"
        fi
    else
        missing="$missing"$'\n'"python3 — the engine is Python:  brew install python"
    fi

    if command -v typst >/dev/null 2>&1; then
        note "typst     $(typst --version 2>/dev/null | head -1)"
    else
        missing="$missing"$'\n'"typst — reports are Typst, and nothing builds without it:  brew install typst"
    fi

    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        note "node      $(node --version)"
    else
        missing="$missing"$'\n'"node and npm — the desktop app is Electron:  brew install node"
    fi

    if [ -n "$missing" ]; then
        printf '\n  missing:\n' >&2
        printf '%s\n' "$missing" | sed '/^$/d; s/^/    /' >&2
        printf '\n  Install those and run `make install` again. Nothing has been changed.\n' >&2
        exit 1
    fi

    # Whether the destination is safe to write is knowable now, and finding out
    # after a two-minute build is finding out too late. Checked again at step 5,
    # since something could arrive in between.
    assert_ours_or_absent "$(choose_dest)/$APP_NAME"
    dim "$(choose_dest) is clear"
}

# ── 2. the CLI on PATH ───────────────────────────────────────────────────────

link_cli() {
    step "linking the cli onto PATH"
    mkdir -p "$BIN_DIR"

    if [ -L "$BIN_LINK" ]; then
        local target
        target="$(readlink "$BIN_LINK")"
        if [ "$target" = "$CLI_SRC" ]; then
            dim "$BIN_LINK → $CLI_SRC (already)"
        else
            # A symlink we can see through is safe to repoint; it carries no data.
            ln -sfn "$CLI_SRC" "$BIN_LINK"
            note "$BIN_LINK → $CLI_SRC"
            warn "that link used to point at $target"
        fi
    elif [ -e "$BIN_LINK" ]; then
        die "$BIN_LINK exists and is not a symlink." \
            "Refusing to overwrite a real file. Move it aside and re-run."
    else
        ln -s "$CLI_SRC" "$BIN_LINK"
        note "$BIN_LINK → $CLI_SRC"
    fi

    # PATH is the user's business. Say the exact line and where it goes; do not
    # go near their profile — a script that edits dotfiles is a script you have
    # to audit before you run it.
    case ":$PATH:" in
        *":$BIN_DIR:"*) dim "$BIN_DIR is on PATH" ;;
        *)
            local profile
            case "$(basename "${SHELL:-}")" in
                zsh)  profile="~/.zshrc" ;;
                bash) profile="~/.bash_profile" ;;
                fish) profile="~/.config/fish/config.fish" ;;
                *)    profile="your shell profile" ;;
            esac
            warn "$BIN_DIR is not on your PATH, so \`report-maker\` will not be found."
            printf '           Add this to %s, then open a new terminal:\n\n' "$profile"
            printf '             export PATH="$HOME/.local/bin:$PATH"\n\n'
            printf '           (The app finds the engine on its own, so it works either way.)\n'
            ;;
    esac
}

# ── 3. build the renderer and the main process ───────────────────────────────

build_app() {
    step "building the app"
    if [ -d "$ROOT/app/node_modules" ]; then
        dim "node_modules present — skipping npm install"
    else
        note "npm install (first run — this takes a minute)"
        ( cd "$ROOT/app" && npm install --no-audit --no-fund )
    fi
    note "npm run build"
    ( cd "$ROOT/app" && npm run build )
}

# ── 4. package it ────────────────────────────────────────────────────────────
#
# The `dir` target, not `dmg`. A dmg exists to be handed to somebody else — it
# adds compression, a mounted volume and a code-signing step to a job whose whole
# output is one folder we are about to copy locally. `make app-dist` is still
# there for the real thing.

package_app() {
    step "packaging (dir target — no dmg, no signing)"

    local arch_flag
    case "$(uname -m)" in
        arm64) arch_flag="--arm64" ;;
        *)     arch_flag="--x64" ;;
    esac

    # Explicit arch keeps the output directory predictable: electron-builder
    # names it dist/mac-arm64 or dist/mac depending on the slice it built, and a
    # config-driven multi-arch build would leave two to choose between.
    ( cd "$ROOT/app" && npx --no-install electron-builder --mac dir "$arch_flag" )

    local found
    found="$(find "$ROOT/app/dist" -maxdepth 2 -type d -name '*.app' 2>/dev/null | head -2)"
    [ -n "$found" ] || die "electron-builder produced no .app under app/dist." \
                           "Run it by hand to see why:  cd app && npx electron-builder --mac dir"
    BUILT_APP="$(printf '%s\n' "$found" | head -1)"
    note "built $BUILT_APP"
}

# ── 5. install it ────────────────────────────────────────────────────────────

install_app() {
    local dest app staging
    dest="$(choose_dest)"
    app="$dest/$APP_NAME"

    step "installing into $dest"

    if [ "$dest" != "/Applications" ]; then
        warn "/Applications is not writable by you, so this is going to ~/Applications instead."
        dim "Launchpad and Spotlight index it the same way. Do not use sudo to force the other one."
    fi

    # The bundle we just built has to claim the identity we are about to check
    # for. If it does not, the check in every future run would abort — better to
    # find that out now than to leave an unremovable bundle behind.
    local built_id
    built_id="$(bundle_id "$BUILT_APP" || true)"
    [ "$built_id" = "$APP_ID" ] || die \
        "the bundle we just built has id '${built_id:-<unreadable>}', not $APP_ID." \
        "appId in app/electron-builder.yml and this script have drifted apart."

    assert_ours_or_absent "$app"

    # Copy first, swap second. ditto merges into an existing directory rather
    # than replacing it, so installing over the top would leave files from the
    # previous version inside the bundle; staging beside the destination keeps
    # the window where neither exists down to one mv.
    staging="$dest/.report-maker-install-$$"
    rm -rf "$staging"
    trap 'rm -rf "$staging"' EXIT

    note "ditto → $app"
    /usr/bin/ditto "$BUILT_APP" "$staging"

    [ "$(bundle_id "$staging" || true)" = "$APP_ID" ] || die \
        "the staged copy did not come out with the expected identifier." \
        "Nothing at $app was touched."

    rm -rf "$app"
    mv "$staging" "$app"
    trap - EXIT

    INSTALLED_AT="$app"
}

# ── main ─────────────────────────────────────────────────────────────────────

main() {
    case "${1:-}" in
        --uninstall|uninstall) uninstall; return ;;
        -h|--help)
            printf 'usage: %s [--uninstall]\n' "$0"
            return ;;
        "") ;;
        *) die "unknown argument: $1" "usage: $0 [--uninstall]" ;;
    esac

    printf '%sinstalling report-maker%s\n' "$B" "$R"
    dim "from $ROOT"

    check_prereqs
    link_cli
    build_app
    package_app
    install_app

    printf '\n%sDone.%s\n\n' "$B" "$R"
    printf '  app  %s\n' "$INSTALLED_AT"
    printf '  cli  %s\n\n' "$BIN_LINK"
    printf '  Open it:   open -a report-maker\n'
    printf '             …or find it in Applications, or ⌘-space and type report-maker.\n\n'
    printf '  It opens with no vault. Point it at a folder, or make one:\n'
    printf '    mkdir -p ~/Documents/Reports && cd ~/Documents/Reports && report-maker init\n\n'
    printf '  Unsigned and un-notarised. Built here, so it is not quarantined and\n'
    printf '  opens normally — see INSTALL.md. Undo all of it with `make uninstall`.\n'
}

main ${1+"$@"}
