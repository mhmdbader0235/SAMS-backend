import subprocess
import sys
import os
import time
import signal

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
    subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "down"], check=False)
    print("All services stopped.")
    sys.exit(0)

# Register signals for clean exit
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def main():
    print("Step 1: Creating Docker volume (if not exists)...")
    subprocess.run(["docker", "volume", "create", "doumind-backend_pgdata"], check=False)

    print("Step 2: Starting Docker Compose (PostgreSQL database)...")
    subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "up", "-d", "db"], check=True)

    # Detect the correct python/uvicorn path
    is_windows = os.name == 'nt'
    if is_windows:
        python_bin = os.path.join(".venv", "Scripts", "python.exe")
    else:
        python_bin = os.path.join(".venv", "bin", "python")

    if not os.path.exists(python_bin):
        print(f"Warning: Virtual env python not found at {python_bin}. Falling back to system python: {sys.executable}")
        python_bin = sys.executable

    print("Step 3: Starting Backend Service (port 8001)...")
    p_backend = subprocess.Popen(
        [python_bin, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
    )
    processes.append(p_backend)

    print("Step 4: Starting API Gateway (port 8000)...")
    p_gateway = subprocess.Popen(
        [python_bin, "-m", "uvicorn", "api_gateway:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd="gateway"
    )
    processes.append(p_gateway)

    print("\n" + "="*50)
    print("All backend services started successfully!")
    print(" - API Gateway:   http://localhost:8000")
    print(" - Backend API:   http://localhost:8001")
    print("="*50)
    print("Press Ctrl+C to stop all services and containers.")

    # Keep script running
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
