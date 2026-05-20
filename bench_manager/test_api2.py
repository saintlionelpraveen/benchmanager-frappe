from bench_manager.api import create_site_on_bench
import json

def execute():
    bp = "/home/praveen/k1"
    # To test synchronously, we will just use subprocess locally here:
    import subprocess, os
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    r = subprocess.run(
        ["bench", "new-site", "test2.k1.local", "--admin-password", "admin"],
        cwd=bp, capture_output=True, text=True, timeout=60, env=env
    )
    print("Return:", r.returncode)
    print("STDOUT:", r.stdout)
    print("STDERR:", r.stderr)
