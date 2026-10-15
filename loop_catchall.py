#!/usr/bin/env python3
"""Endless catch-all reg chainer: waits for any running treg_catchall.py wave,
then loops waves of N forever (pace P). Logs to loop_catchall.log."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
N = os.environ.get("N", "50")
PACE = os.environ.get("PACE", "40")


def wave_running():
    try:
        import psutil
        me = os.getpid()
        for p in psutil.process_iter(["pid", "cmdline"]):
            if p.info["pid"] == me:
                continue
            try:
                cl = " ".join(p.info["cmdline"] or [])
                if "treg_catchall.py" in cl:
                    return True
            except Exception:
                pass
    except ImportError:
        pass
    return False


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(os.path.join(HERE, "loop_catchall.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log("chainer start (N=%s PACE=%s)" % (N, PACE))
    while True:
        if wave_running():
            log("wave already running — waiting 60s")
            time.sleep(60)
            continue
        log("launching wave N=%s pace=%s" % (N, PACE))
        env = dict(os.environ)
        env.update({"PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"})
        rc = subprocess.call([PY, "-u", "treg_catchall.py", N, "--pace", PACE],
                             cwd=HERE, env=env)
        log("wave finished rc=%s — cooldown 90s" % rc)
        time.sleep(90)


if __name__ == "__main__":
    main()
