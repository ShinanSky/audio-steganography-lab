#!/bin/bash
# macOS / Bash 3.2 compatible. All installs stay in this application's folder.
set -e
task_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$task_dir"
task_mode="${1:-generator}"
case "$task_mode" in
    generator|reader|install) ;;
    *) printf 'Unknown mode: %s\n' "$task_mode"; exit 1 ;;
esac

fail() {
    printf '\n%s\n' "$1" >&2
    if [ -t 0 ]; then
        read -r -p 'Press Return to close. ' task_reply
    fi
    exit 1
}

task_venv="$task_dir/.venv-macos"
task_env_python="$task_venv/bin/python3"
task_ready='import sys; assert sys.version_info >= (3,10); import tkinter, numpy, sounddevice'

if [ "$task_mode" != install ] && [ -x "$task_env_python" ] &&
    "$task_env_python" -c "$task_ready" >/dev/null 2>&1; then
    exec "$task_env_python" "$task_dir/launcher.py" "$task_mode"
fi

# Finder does not necessarily load a user's shell profile or Homebrew PATH.
task_python=""
task_path_python="$(command -v python3 || true)"
for task_candidate in "${AUDIO_MORSE_PYTHON:-}" \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /usr/local/bin/python3 /opt/homebrew/bin/python3 "$task_path_python"; do
    [ -n "$task_candidate" ] || continue
    # Avoid invoking Apple's Xcode placeholder / older system Python.
    [ "$task_candidate" != /usr/bin/python3 ] || continue
    if [ -x "$task_candidate" ] && "$task_candidate" -c \
        'import sys; assert sys.version_info >= (3,10); import tkinter, venv' >/dev/null 2>&1; then
        task_python="$task_candidate"
        break
    fi
done

[ -n "$task_python" ] || fail 'Please install Python 3.10+ with Tkinter from https://www.python.org/downloads/macos/ and open this file again.'

if [ ! -x "$task_env_python" ] || ! "$task_env_python" -c \
    'import sys,tkinter; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
    printf 'Creating local Python environment...\n'
    "$task_python" -m venv "$task_venv" || fail 'Could not create .venv-macos. Check folder permissions and your Python installation.'
fi

if [ "$task_mode" = install ] || ! "$task_env_python" -c "$task_ready" >/dev/null 2>&1; then
    printf 'Installing audio dependencies (first launch needs Internet access)...\n'
    "$task_env_python" -m pip install -r "$task_dir/requirements.txt" || \
        fail 'Installation failed. Check your Internet connection and run the installer again.'
fi

if [ "$task_mode" = install ]; then
    printf 'Ready. Open the generator or reader .command file.\n'
    if [ -t 0 ]; then
        read -r -p 'Press Return to close. ' task_reply
    fi
    exit 0
fi
exec "$task_env_python" "$task_dir/launcher.py" "$task_mode"
