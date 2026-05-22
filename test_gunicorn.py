import os, sys, time

pid = os.fork()
if pid == 0:
    print("Child sys.path contains drive?", any("drive" in p for p in sys.path))
    sys.exit(0)
else:
    os.wait()
