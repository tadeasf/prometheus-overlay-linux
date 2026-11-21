#!/bin/sh
# Steam launch options for The Isle:  /path/to/linux/steam-launch.sh %command%
# Starts the overlay next to the game and stops it when the game exits.
# The overlay runs as a systemd user unit: a clean desktop environment, without Steam's LD_PRELOAD / runtime libraries.
here=$(dirname "$(readlink -f "$0")")
running() {  # the pid the overlay wrote at start, and it is still the overlay
    pid=$(sed -n 's/.*"pid": *\([0-9]*\).*/\1/p' "${XDG_RUNTIME_DIR:-/tmp}/prometheus-overlay.json" 2>/dev/null)
    [ -n "$pid" ] && grep -q prometheus_overlay "/proc/$pid/cmdline" 2>/dev/null
}
if ! running; then
    envs=""
    for v in WAYLAND_DISPLAY NIRI_SOCKET DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP XDG_RUNTIME_DIR; do
        eval "val=\${$v:-}"
        [ -n "$val" ] && envs="$envs --setenv=$v=$val"
    done
    # shellcheck disable=SC2086
    systemd-run --user --quiet --collect --unit=prometheus-overlay $envs \
        /usr/bin/python3 "$here/prometheus_overlay.py" || echo "prometheus overlay: could not start" >&2
    started=1
fi
"$@"
status=$?
[ -n "${started:-}" ] && systemctl --user stop prometheus-overlay 2>/dev/null
exit $status
