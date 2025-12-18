import requests
import sys

def stop_agent():
    try:
        response = requests.post("http://localhost:8000/agent/stop")
        if response.status_code == 200:
            print("🛑 Agent cycle stopped successfully.")
        else:
            print(f"❌ Failed to stop agent: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error connecting to backend: {e}")

if __name__ == "__main__":
    stop_agent()
