import subprocess
import time
import redis
import os
from core.path_util import get_bin_path

class RedisManager:
    def __init__(self, host="localhost", port=6379):
        self.host = host
        self.port = port
        self.process = None
        self.bin_path = get_bin_path(os.path.join("redis", "redis-server.exe"))

    def start(self, timeout=10):
        print(f"[RedisManager] Starting Redis from {self.bin_path}...")
        try:
            # We use a specific working directory or config if needed
            self.process = subprocess.Popen(
                [self.bin_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            # Wait for readiness
            start_time = time.time()
            client = redis.Redis(host=self.host, port=self.port)
            while True:
                try:
                    if client.ping():
                        print("[RedisManager] Redis is ready.")
                        return True
                except redis.exceptions.ConnectionError:
                    pass
                
                if time.time() - start_time > timeout:
                    print("[RedisManager] Timeout waiting for Redis.")
                    return False
                
                time.sleep(0.5)
        except Exception as e:
            print(f"[RedisManager] Failed to start: {e}")
            return False

    def stop(self):
        if self.process:
            print("[RedisManager] Stopping Redis...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            print("[RedisManager] Redis stopped.")

if __name__ == "__main__":
    # Test script
    manager = RedisManager()
    if manager.start():
        client = redis.Redis()
        client.set("health_check", "ok")
        print(f"Redis Health: {client.get('health_check')}")
        manager.stop()