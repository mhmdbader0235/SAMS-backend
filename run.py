import os
import signal
import subprocess
import sys
import time

# Change to the directory where run.py is located so docker-compose and relative paths work
os.chdir(os.path.dirname(os.path.abspath(__file__)))

processes = []

def cleanup(sig=None, frame=None):
    print("\nShutting down all services...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    print("Stopping docker containers...")
    compose_file = os.path.join("..", "docker-compose.yml") if os.path.exists(os.path.join("..", "docker-compose.yml")) else "docker-compose.yml"
    subprocess.run(["docker", "compose", "-f", compose_file, "down"], check=False)
    print("All services stopped.")
    sys.exit(0)

# Register signals for clean exit
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def main():
    compose_file = os.path.join("..", "docker-compose.yml") if os.path.exists(os.path.join("..", "docker-compose.yml")) else "docker-compose.yml"
    print("Step 1: Creating Docker volumes (if not exist)...")
    subprocess.run(["docker", "volume", "create", "doumind-backend_pgdata"], check=False)
    subprocess.run(["docker", "volume", "create", "keycloak_data"], check=False)

    print("Step 2: Starting Docker Compose (PostgreSQL, Keycloak, Gateway)...")
    subprocess.run(["docker", "compose", "-f", compose_file, "up", "-d"], check=True)

    # Detect the correct python/uvicorn path
    python_bin = sys.executable
    if os.path.exists(os.path.join(".venv", "Scripts", "python.exe")):
        python_bin = os.path.join(".venv", "Scripts", "python.exe")
    elif os.path.exists(os.path.join(".venv", "bin", "python")):
        python_bin = os.path.join(".venv", "bin", "python")

    print(f"Step 3: Starting Backend Service with Python: {python_bin} (port 8001)...")
    p_backend = subprocess.Popen(
        [python_bin, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
    )
    processes.append(p_backend)

    print("\n" + "="*55)
    print("  Full Stack Infrastructure Started Successfully!")
    print("  - DMZ (Nginx)     : http://localhost:9080")
    print("  - APISIX Gateway  : Internal Docker Network (9180)")
    print("  - Keycloak Server : http://localhost:8000")
    print("  - OPA Policy Eng. : http://localhost:8181")
    print("  - FastAPI Backend : http://localhost:8001")
    print("  - Frontend SPA    : http://localhost:3000")
    print("="*55)

    print("Press Ctrl+C to stop all services and containers.")

    # Keep script running
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
