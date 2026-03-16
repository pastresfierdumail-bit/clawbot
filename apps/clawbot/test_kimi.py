import os
from openai import OpenAI
from dotenv import load_dotenv

# Load .env
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("KIMI_API_KEY", "").strip()

print(f"Testing KIMI API Key: {api_key[:5]}...{api_key[-5:]}")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.moonshot.cn/v1",
)

try:
    completion = client.chat.completions.create(
        model="moonshot-v1-8k",
        messages=[
            {"role": "user", "content": "Hello, respond with 'OK' if you can hear me."}
        ],
    )
    print("Success! Response:", completion.choices[0].message.content)
except Exception as e:
    print("\n--- ERROR ---")
    print(e)
    print("\nCommon fixes for 401:")
    print("1. Check if you have topped up your account (credits required).")
    print("2. Ensure the key is not expired or deleted in Moonshot console.")
