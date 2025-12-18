import requests
import sys

def start_agent():
    try:
        response = requests.post("http://localhost:8000/agent/start")
        if response.status_code == 200:
            print("✅ Agent cycle started successfully.")
        else:
            print(f"❌ Failed to start agent: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error connecting to backend: {e}")

if __name__ == "__main__":
    start_agent()
