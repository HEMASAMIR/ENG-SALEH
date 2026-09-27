import os
import subprocess

mypid = os.getpid()
try:
    # Run tasklist and filter for python.exe
    output = subprocess.check_output('tasklist /NH /FI "IMAGENAME eq python.exe"', shell=True).decode('utf-8', errors='ignore')
    for line in output.strip().split('\n'):
        parts = line.split()
        if len(parts) > 1:
            try:
                pid = int(parts[1])
                if pid != mypid:
                    os.system(f"taskkill /F /PID {pid}")
                    print(f"Killed orphaned python process: {pid}")
            except ValueError:
                pass
except Exception as e:
    print(f"Error: {e}")
