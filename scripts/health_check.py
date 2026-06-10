import sys
import requests
import time

def check_health(url, timeout=30):
    start_time = time.time()
    print(f"Starting health check for {url}...")
    while time.time() - start_time < timeout:
        try:
            # We assume the bot might have a simple health endpoint or we check if it responds
            # Since it's a Telegram bot, we might just check if the process is up or 
            # if we can hit a local metrics/status port if configured.
            # For this implementation, we'll check a placeholder status URL or 
            # simply verify requirements are met.
            response = requests.get(url)
            if response.status_code == 200:
                print("Health check passed!")
                return True
        except Exception as e:
            print(f"Check failed: {e}")
        
        time.sleep(5)
    
    print("Health check timed out.")
    return False

if __name__ == "__main__":
    # Default to localhost if not provided
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/health"
    if not check_health(target_url):
        sys.exit(1)
