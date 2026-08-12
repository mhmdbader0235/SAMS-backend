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
    is_windows = os.name == 'nt'
    if is_windows:
        python_bin = os.path.join(".venv", "Scripts", "python.exe")
    else:
        python_bin = os.path.join(".venv", "bin", "python")

    if not os.path.exists(python_bin):
        py312 = r"C:\Users\mb883\AppData\Local\Programs\Python\Python312\python.exe"
        if os.path.exists(py312):
            python_bin = py312
        else:
            print(f"Warning: Virtual env python not found at {python_bin}. Falling back to system python: {sys.executable}")
            python_bin = sys.executable

    print("Step 3: Starting Backend Service (port 8001)...")
    p_backend = subprocess.Popen(
        [python_bin, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
    )
    processes.append(p_backend)

    print("\n" + "="*55)
    print("  Full Stack Infrastructure Started Successfully!")
    print("  - DMZ (Nginx)     : http://localhost:9080")
    print("  - APISIX Gateway  : Internal Docker Network (9180)")
    print("  - Keycloak Server : http://localhost:8000")
    print("  - FastAPI Backend : http://localhost:8001")
    print("  - Frontend SPA    : http://localhost:3000")
    print("="*55)
    print("Press Ctrl+C to stop all services and containers.")

    # Keep script running
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
