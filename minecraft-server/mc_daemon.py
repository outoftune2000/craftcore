#!/usr/bin/env python3
import os
import sys
import subprocess
import signal
import time
from runtime_config import prepare_properties
from installation import preflight, operation_lock

DIR = os.path.dirname(os.path.abspath(__file__))
FIFO_PATH = os.path.join(DIR, "server.fifo")
LOG_PATH = os.path.join(DIR, "server.log")
PID_PATH = os.path.join(DIR, "server.pid")

def daemonize():
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

    os.setsid()
    os.umask(0o077)

    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

def main():
    state = preflight()
    daemonize()

    with open(LOG_PATH, "a", buffering=1) as log_file:
        with operation_lock():
            from installation import running, update_properties
            if running(): return
            state = preflight()
            update_properties({'server-port': state['server_port'], 'server-ip': '127.0.0.1'})
            prepare_properties()
            if os.path.exists(FIFO_PATH): os.remove(FIFO_PATH)
            os.mkfifo(FIFO_PATH, 0o600)
            fifo_fd = os.open(FIFO_PATH, os.O_RDWR)
            fifo_reader = open(fifo_fd, "r")
            cmd = [state['java'], '-Xms512M', f"-Xmx{state['memory_mb']}M", '-jar', 'server.jar', 'nogui']
            proc = subprocess.Popen(cmd, cwd=DIR, stdin=fifo_reader, stdout=log_file, stderr=subprocess.STDOUT)
            with open(PID_PATH, 'w') as pf: pf.write(str(proc.pid))

        def handle_term(signum, frame):
            try:
                with open(FIFO_PATH, "w") as f:
                    f.write("stop\n")
            except Exception:
                pass

        signal.signal(signal.SIGTERM, handle_term)
        signal.signal(signal.SIGINT, handle_term)

        proc.wait()

    try:
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)
        if os.path.exists(FIFO_PATH):
            os.remove(FIFO_PATH)
    except Exception:
        pass

if __name__ == "__main__":
    main()
