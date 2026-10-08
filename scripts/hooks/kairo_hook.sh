#!/bin/sh
# Runs kairo_hook.py (every Kairo hook, see hooks/hooks.json) with the first real
# Python found: `python`, then `python3` (macOS and many Linux distributions ship
# only `python3`). The Microsoft Store aliases in WindowsApps are not Pythons.
#
# With no Python at all the hooks cannot run, and the guards they hold
# (send: never, isolated agents, papers kept out of the main session) would be
# off without a word. So this says so on stderr every time, and refuses a
# PreToolUse read inside a Kairo vault (exit 2 blocks the tool) — outside a vault
# it lets the session go on.
here=${0%/*}                       # shell builtins only: this must run on any PATH
[ "$here" = "$0" ] && here=${0%\\*}                   # a Windows path
[ "$here" = "$0" ] && here=.
for name in python python3; do
    py=$(command -v "$name" 2>/dev/null) || continue
    case "$py" in
        *WindowsApps*) continue ;;
    esac
    exec "$py" "$here/kairo_hook.py" "$@"
done

echo "Kairo: no se encontró Python (python / python3) en el PATH: los hooks de Kairo no se ejecutan" \
     "(send_guard, aislamiento, lectura de papers). Instala Python 3.10+." >&2
if [ "$1" = "pre-tool" ]; then
    for d in "$CLAUDE_PROJECT_DIR" "$PWD" "$KAIRO_VAULT"; do
        [ -n "$d" ] || continue
        for v in "$d" "$d/vault"; do
            if [ -d "$v/Papers" ] && [ -d "$v/Projects" ]; then
                echo "Kairo: bloqueado — sin Python no se puede comprobar send_guard en el vault $v" >&2
                exit 2
            fi
        done
    done
fi
exit 0
