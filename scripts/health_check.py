import os
import sys
import subprocess
import time

def check_process(process_name):
    """Check if the process is running using pgrep."""
    try:
        subprocess.check_output(["pgrep", "-f", process_name])
        return True
    except subprocess.CalledProcessError:
        return False

def check_tmux(session_name):
    """Check if a tmux session exists."""
    try:
        subprocess.check_output(["tmux", "has-session", "-t", session_name])
        return True
    except subprocess.CalledProcessError:
        return False

def run_health_check(timeout=30):
    start_time = time.time()
    print("Starting process-based health check...")
    
    while time.time() - start_time < timeout:
        # Check if the main script is running or the tmux session is active
        is_running = check_process("python main.py") or check_tmux("solbot")
        
        if is_running:
            print("Health check passed: Solbot process or session detected!")
            return True
        
        print("Bot process not found, retrying...")
        time.sleep(5)
    
    print("Health check timed out: Bot process not detected.")
    return False

if __name__ == "__main__":
    if not run_health_check():
        sys.exit(1)
