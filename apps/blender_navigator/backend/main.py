import asyncio
import os
from fastapi import FastAPI, WebSocket
from dotenv import load_dotenv

# Load environment variables (API keys)
load_dotenv(dotenv_path="../../../.env")

app = FastAPI()

# Store active websocket connections
active_connections = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            # Receive message from the local client
            data = await websocket.receive_json()
            print(f"Received from client: {data}")
            
            # TODO: Add Gemini Multimodal logic here
            # Pass the received image/screen and goal to the Gemini model
            
    except Exception as e:
        print(f"Websocket disconnected: {e}")
    finally:
        active_connections.remove(websocket)

@app.get("/")
def read_root():
    return {"status": "Backend is running"}

# In subsequent steps, we will add Telegram Webhook endpoints here.
