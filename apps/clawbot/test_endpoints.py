import os
import httpx
from dotenv import load_dotenv

# Load .env
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("KIMI_API_KEY", "").strip()

urls = [
    "https://api.moonshot.cn/v1",
    "https://api.moonshot.ai/v1"
]

print(f"Testing Key: {api_key[:10]}...")

for base_url in urls:
    print(f"\n--- Testing Endpoint: {base_url} ---")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "moonshot-v1-8k",
        "messages": [{"role": "user", "content": "hi"}]
    }
    
    try:
        with httpx.Client() as client:
            # First try model list (no body needed)
            models_res = client.get(f"{base_url}/models", headers=headers)
            print(f"Models GET status: {models_res.status_code}")
            if models_res.status_code != 200:
                print(f"Models error: {models_res.text}")
            
            # Then try chat completion
            chat_res = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            print(f"Chat POST status: {chat_res.status_code}")
            if chat_res.status_code == 200:
                print("Success!")
            else:
                print(f"Chat error: {chat_res.text}")
    except Exception as e:
        print(f"Connection error: {e}")
