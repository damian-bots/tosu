#!/usr/bin/env bash

get_descendants() {
    local parent=$1
    local children=$(pgrep -P "$parent")

    for child in $children; do
        echo "$child"
        get_descendants "$child"
    done
}

echo "Starting monitor for all 'python3 -m AnonXMusic' instances..."

while true; do
    MAIN_PIDS=$(pgrep -f "python3 -m AnonXMusic")

    if [ -z "$MAIN_PIDS" ]; then
        echo "No 'python3 -m AnonXMusic' processes found. Retrying in 30 seconds..."
        sleep 30
        continue
    fi

    for MAIN_PID in $MAIN_PIDS; do
        DESCENDANTS=$(get_descendants "$MAIN_PID")

        for PID in $DESCENDANTS; do
            if [ "$PID" -eq "$MAIN_PID" ]; then
                continue
            fi

            CMD=$(ps -p "$PID" -o comm= 2>/dev/null)
            
            if [[ "$CMD" == *python* ]]; then
                STRACE_OUT=$(timeout 0.2 strace -p "$PID" 2>&1)
                
                if [[ "$STRACE_OUT" == *"FUTEX_WAIT_PRIVATE"* ]]; then
                    echo "[$(date +%T)] Force-killing stuck child ($CMD) PID: $PID (Parent: $MAIN_PID)"
                    kill -9 "$PID" 2>/dev/null
                fi
            fi
        done
    done

    sleep 30
done
