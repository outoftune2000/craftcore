#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PID_FILE="$DIR/server.pid"
FIFO_FILE="$DIR/server.fifo"
LOG_FILE="$DIR/server.log"
GATEKEEPER_PID_FILE="$DIR/gatekeeper.pid"
GATEKEEPER_LOG_FILE="$DIR/gatekeeper.log"
PLAYIT_PID_FILE="$DIR/playit.pid"
PLAYIT_LOG_FILE="$DIR/playit.log"
GUI_PID_FILE="$DIR/dashboard.pid"
GUI_LOG_FILE="$DIR/dashboard.log"
GUI_PORT=$(python3 -c 'from installation import settings; print(settings()["gui_port"])')
PROXY_PORT=$(python3 -c 'from installation import settings; print(settings()["proxy_port"])')
SERVER_PORT=$(python3 -c 'from installation import settings; print(settings()["server_port"])')

is_running() {
    python3 "$DIR/installation.py" pid server > /dev/null
}

is_gatekeeper_running() {
    python3 "$DIR/installation.py" pid gatekeeper > /dev/null
}

is_playit_running() {
    python3 "$DIR/installation.py" pid playit > /dev/null
}

is_gui_running() {
    python3 "$DIR/installation.py" pid gui > /dev/null
}

start_gatekeeper() {
    if is_gatekeeper_running; then
        return 0
    fi
    echo "[*] Starting CraftCore Gatekeeper Proxy on port $PROXY_PORT..."
    python3 "$DIR/gatekeeper.py" --daemon
    sleep 1
    if is_gatekeeper_running; then
        local gpid
        gpid=$(cat "$GATEKEEPER_PID_FILE")
        echo "[✓] Gatekeeper Proxy online (PID: $gpid)."
    fi
}

stop_gatekeeper() {
    if ! is_gatekeeper_running; then
        return 0
    fi
    local gpid
    gpid=$(cat "$GATEKEEPER_PID_FILE" 2>/dev/null || pgrep -f "gatekeeper.py" || true)
    if [ -n "$gpid" ]; then
        kill -15 "$gpid" 2>/dev/null || true
    fi
    rm -f "$GATEKEEPER_PID_FILE"
    echo "[✓] Gatekeeper Proxy stopped."
}

start_server() {
    python3 "$DIR/installation.py" preflight
    start_gatekeeper

    if is_running; then
        echo "[!] Minecraft server is already running (PID: $(cat "$PID_FILE"))."
        return 0
    fi

    # Clean stale locks
    rm -f "$DIR/world*/session.lock" "$PID_FILE" "$FIFO_FILE"

    echo "[*] Starting configured Minecraft server..."
    python3 "$DIR/mc_daemon.py"
    
    sleep 3
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "[✓] Minecraft server is running (PID: $pid)."
        echo "[i] View live logs with: ./server.sh console"
    else
        echo "[!] Server started, checking initialization with: ./server.sh console"
    fi
}

stop_mc() {
    if ! is_running; then
        rm -f "$PID_FILE" "$FIFO_FILE"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    echo "[*] Gracefully stopping Minecraft server process (PID: $pid)..."
    
    if [ -p "$FIFO_FILE" ]; then
        echo "save-all flush" > "$FIFO_FILE" 2>/dev/null || true
        sleep 1
        echo "stop" > "$FIFO_FILE" 2>/dev/null || true
    fi

    local count=0
    while is_running; do
        sleep 1
        count=$((count + 1))
        if [ "$count" -ge 20 ]; then
            echo "[!] Server did not stop within 20s. Terminating..."
            kill -15 "$pid" 2>/dev/null || true
            sleep 2
            break
        fi
    done

    rm -f "$PID_FILE" "$FIFO_FILE" "$DIR/world*/session.lock"
    echo "[✓] Minecraft server process stopped cleanly."
}

stop_server() {
    stop_gatekeeper
    stop_mc
}

sleep_server() {
    echo "[*] Putting Minecraft server into Sleep Mode (hibernation)..."
    stop_mc
    start_gatekeeper
    echo "[✓] Minecraft server is now SLEEPING (Standby active on port $PROXY_PORT - will auto-wake on join)."
}

send_cmd() {
    if ! is_running; then
        echo "[!] Minecraft server is not running."
        return 1
    fi

    local cmd="$*"
    if [ -z "$cmd" ]; then
        echo "Usage: ./server.sh cmd <command>"
        echo "Example: ./server.sh cmd op YourUsername"
        return 1
    fi

    if [ -p "$FIFO_FILE" ]; then
        echo "$cmd" > "$FIFO_FILE"
        echo "[✓] Sent command to console: $cmd"
    else
        echo "[!] Server pipe not found."
        return 1
    fi
}

start_gui() {
    if is_gui_running; then
        echo "[!] Web GUI dashboard is already running (PID: $(cat "$GUI_PID_FILE"))."
        echo "[*] Open in browser: http://localhost:$GUI_PORT"
        return 0
    fi

    echo "[*] Starting Minecraft Web GUI Dashboard on port $GUI_PORT..."
    python3 "$DIR/installation.py" token
    python3 "$DIR/dashboard/server.py" --daemon
    sleep 1
    if is_gui_running; then
        local gpid
        gpid=$(cat "$GUI_PID_FILE")
        echo "[✓] GUI Dashboard online: http://localhost:$GUI_PORT (PID: $gpid)"
    fi
}

stop_gui() {
    if ! is_gui_running; then
        echo "[!] Web GUI dashboard is not running."
        rm -f "$GUI_PID_FILE"
        return 0
    fi

    local gpid
    gpid=$(cat "$GUI_PID_FILE" 2>/dev/null || pgrep -f "dashboard/server.py" || true)
    echo "[*] Stopping Web GUI dashboard (PID: $gpid)..."
    kill -15 "$gpid" 2>/dev/null || true
    rm -f "$GUI_PID_FILE"
    echo "[✓] Web GUI dashboard stopped."
}

show_status() {
    echo "=========================================="
    echo "       MINECRAFT SERVER STATUS   "
    echo "=========================================="
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "Status       : RUNNING (PID: $pid)"
        local mem
        mem=$(ps -p "$pid" -o rss= 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')
        echo "Memory Usage : $mem"
    else
        echo "Status       : STOPPED"
    fi

    if is_gatekeeper_running; then
        local gpid
        gpid=$(cat "$GATEKEEPER_PID_FILE")
        echo "Gatekeeper   : RUNNING (PID: $gpid, Port $PROXY_PORT -> $SERVER_PORT)"
    else
        echo "Gatekeeper   : STOPPED"
    fi

    echo "------------------------------------------"
    echo "          PLAYIT.GG TUNNEL STATUS         "
    echo "------------------------------------------"
    if is_playit_running; then
        local ppid
        ppid=$(cat "$PLAYIT_PID_FILE")
        echo "Tunnel Status: RUNNING (PID: $ppid)"
        if [ -f "$PLAYIT_LOG_FILE" ]; then
            echo "Recent logs  :"
            tail -n 6 "$PLAYIT_LOG_FILE"
        fi
    else
        echo "Tunnel Status: STOPPED"
    fi

    echo "------------------------------------------"
    echo "          WEB GUI DASHBOARD (PORT $GUI_PORT)   "
    echo "------------------------------------------"
    if is_gui_running; then
        local gpid
        gpid=$(cat "$GUI_PID_FILE")
        echo "GUI Status   : RUNNING (PID: $gpid)"
        echo "URL          : http://localhost:$GUI_PORT"
    else
        echo "GUI Status   : STOPPED"
    fi
    echo "=========================================="
}

start_tunnel() {
    if is_playit_running; then
        echo "[!] Playit tunnel is already running (PID: $(cat "$PLAYIT_PID_FILE"))."
        return 0
    fi

    if [ ! -x "./playitd" ]; then
        echo "[!] playitd binary not found in $DIR."
        return 1
    fi

    python3 "$DIR/runtime_config.py"
    if [ ! -f "$DIR/playit.toml" ]; then
        echo "[!] playit.toml not found. Please run one-time setup first:"
        echo "    ./server.sh tunnel-setup"
        return 1
    fi

    echo "[*] Starting playit.gg tunnel in background..."
    nohup ./playitd --secret-path "$DIR/playit.toml" --socket-path "$DIR/playit.sock" --log-path "$PLAYIT_LOG_FILE" > /dev/null 2>&1 &
    local ppid=$!
    echo "$ppid" > "$PLAYIT_PID_FILE"

    echo "[✓] Playit tunnel started (PID: $ppid)."
    sleep 2
    if [ -f "$PLAYIT_LOG_FILE" ]; then
        tail -n 6 "$PLAYIT_LOG_FILE"
    fi
}

stop_tunnel() {
    if ! is_playit_running; then
        echo "[!] Playit tunnel is not running."
        rm -f "$PLAYIT_PID_FILE"
        return 0
    fi

    local ppid
    ppid=$(cat "$PLAYIT_PID_FILE")
    echo "[*] Stopping playit tunnel (PID: $ppid)..."
    kill -15 "$ppid" 2>/dev/null || true
    rm -f "$PLAYIT_PID_FILE" "$DIR/playit.sock"
    echo "[✓] Playit tunnel stopped."
}

create_backup() {
    mkdir -p "$DIR/backups"
    echo "[*] Creating world backup (holding up to 3 copies)..."
    if is_running; then
        send_cmd "save-off"
        send_cmd "save-all flush"
        sleep 1
    fi
    local ts
    ts=$(date +"%Y%m%d_%H%M%S")
    local bfile="$DIR/backups/world_backup_${ts}.zip"
    (cd "$DIR" && zip -r -q "$bfile" world -x "world/session.lock")
    if is_running; then
        send_cmd "save-on"
    fi
    local bsize
    bsize=$(du -h "$bfile" | awk '{print $1}')
    echo "[✓] Backup created successfully: world_backup_${ts}.zip ($bsize)"
    
    # Enforce max 3 copies retention (remove oldest backups)
    local count
    count=$(ls -1t "$DIR/backups"/world_backup_*.zip 2>/dev/null | wc -l)
    if [ "$count" -gt 3 ]; then
        ls -1t "$DIR/backups"/world_backup_*.zip | tail -n +"4" | while read -r old_backup; do
            rm -f "$old_backup"
            echo "[i] Pruned older backup beyond 3-copy limit: $(basename "$old_backup")"
        done
    fi
}

list_backups() {
    echo "=========================================="
    echo "   WORLD BACKUPS ARCHIVE (MAX 3 COPIES)   "
    echo "=========================================="
    if [ ! -d "$DIR/backups" ] || [ -z "$(ls -A "$DIR/backups"/world_backup_*.zip 2>/dev/null)" ]; then
        echo "No backups found in $DIR/backups."
        return 0
    fi
    printf "%-35s %-12s %-20s\n" "Backup Filename" "Size" "Date & Time"
    echo "------------------------------------------------------------------"
    for f in $(ls -1t "$DIR/backups"/world_backup_*.zip 2>/dev/null); do
        local fname fsize fdate
        fname=$(basename "$f")
        fsize=$(du -h "$f" | awk '{print $1}')
        fdate=$(date -r "$f" +"%Y-%m-%d %H:%M:%S")
        printf "%-35s %-12s %-20s\n" "$fname" "$fsize" "$fdate"
    done
    echo "=========================================="
}

restore_backup() {
    local target="$1"
    if [ -z "$target" ]; then
        echo "[!] Error: Specify a backup filename to restore."
        echo "Usage: ./server.sh restore <filename>"
        echo "Available backups:"
        list_backups
        return 1
    fi
    local bpath="$DIR/backups/$target"
    if [ ! -f "$bpath" ]; then
        bpath="$target"
    fi
    if [ ! -f "$bpath" ]; then
        echo "[!] Error: Backup file '$target' not found in $DIR/backups/"
        return 1
    fi
    echo "[!] Restoring world from $(basename "$bpath")..."
    local was_running=0
    if is_running; then
        was_running=1
        echo "[*] Stopping Minecraft server for safe world restore..."
        stop_server
    fi
    # Safety snapshot of current world
    if [ -d "$DIR/world" ]; then
        rm -rf "$DIR/world_prev_restore"
        cp -r "$DIR/world" "$DIR/world_prev_restore"
    fi
    rm -rf "$DIR/world"
    unzip -q -o "$bpath" -d "$DIR"
    rm -f "$DIR/world/session.lock"
    echo "[✓] World restored successfully from $(basename "$bpath")."
    if [ "$was_running" -eq 1 ]; then
        echo "[*] Restarting Minecraft server with restored world..."
        start_server
    fi
}

case "$1" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    stop-mc)
        stop_mc
        ;;
    sleep)
        sleep_server
        ;;
    wake)
        start_server
        ;;
    restart)
        stop_server
        sleep 2
        start_server
        ;;
    status)
        show_status
        ;;
    console)
        if [ ! -f "$LOG_FILE" ]; then
            touch "$LOG_FILE"
        fi
        echo "[i] Streaming server logs (Ctrl+C to exit)..."
        tail -f "$LOG_FILE"
        ;;
    cmd)
        shift
        send_cmd "$@"
        ;;
    backup)
        create_backup
        ;;
    backups)
        list_backups
        ;;
    restore)
        shift
        restore_backup "$@"
        ;;
    gui)
        start_gui
        ;;
    gui-stop)
        stop_gui
        ;;
    tunnel-setup)
        ./tunnel_setup.sh
        ;;
    tunnel-start)
        start_tunnel
        ;;
    tunnel-stop)
        stop_tunnel
        ;;
    tunnel-status)
        if is_playit_running; then
            echo "[*] Playit is running. Logs:"
            cat "$PLAYIT_LOG_FILE"
        else
            echo "[!] Playit is not running."
        fi
        ;;
    all-start)
        start_gui
        if python3 "$DIR/installation.py" ready; then
            start_server
        else
            echo "Complete setup at http://127.0.0.1:$GUI_PORT/setup"
        fi
        ;;
    all-stop)
        stop_server
        stop_tunnel
        stop_gui
        ;;
    *)
        echo "=========================================="
        echo "   Minecraft Dedicated Server Manager    "
        echo "=========================================="
        echo ""
        echo "Usage: ./server.sh [command]"
        echo ""
        echo "Server Controls:"
        echo "  start             Start Minecraft server & Gatekeeper in background"
        echo "  stop              Gracefully stop Minecraft server & Gatekeeper"
        echo "  restart           Restart Minecraft server & Gatekeeper"
        echo "  status            Check server, gatekeeper, tunnel, and GUI status"
        echo "  console           View live console log stream (Ctrl+C to exit)"
        echo "  cmd <command>     Send command to console (e.g. ./server.sh cmd op Username)"
        echo ""
        echo "World & Backup Controls:"
        echo "  backup            Create an instant world backup (retaining up to 3 copies)"
        echo "  backups           List all stored world backups"
        echo "  restore <file>    Safely restore world from a backup archive"
        echo ""
        echo "Web GUI Dashboard:"
        echo "  gui               Start web dashboard on http://localhost:$GUI_PORT"
        echo "  gui-stop          Stop web dashboard"
        echo ""
        echo "Tunnel Controls (playit.gg):"
        echo "  tunnel-setup      One-time web browser claim setup"
        echo "  tunnel-start      Start tunnel in background"
        echo "  tunnel-stop       Stop tunnel"
        echo "  tunnel-status     Show tunnel logs and connection info"
        echo ""
        echo "Combined Controls:"
        echo "  all-start         Start Minecraft server, gatekeeper, tunnel & web GUI"
        echo "  all-stop          Stop Minecraft server, gatekeeper, tunnel & web GUI"
        ;;
esac
