"""
wss://dhruv-mfkz9obl-eastus2.cognitiveservices.azure.com/openai/realtime?api-version=2024-10-01-preview&deployment=gpt-realtime&api-key=<KEY>
    azure_endpoint="wss://dhruv-mfkz9obl-eastus2.cognitiveservices.azure.com/openai/realtime/",
    api_key="<KEY>",
    # azure_ad_token_provider=token_provider,
    azure_deployment="gpt-realtime",
    api_version="2025-08-28",
"""

import os
import asyncio
import websockets
import json

# Replace with your resource info
AZURE_OPENAI_KEY = "<KEY>"
AZURE_OPENAI_RESOURCE = "dhruv-mfkz9obl-eastus2"    # e.g., my-aoai-instance
DEPLOYMENT_NAME = "gpt-realtime"
API_VERSION = "2025-04-01-preview"

async def connect_realtime():
    # Construct websocket URL
    ws_url = (
        f"wss://{AZURE_OPENAI_RESOURCE}.openai.azure.com/openai/realtime"
        f"?api-version={API_VERSION}&deployment={DEPLOYMENT_NAME}"
    )

    headers = {
        "api-key": AZURE_OPENAI_KEY,
    }

    async with websockets.connect(ws_url, additional_headers=headers) as ws:
        print("Connected to Azure OpenAI Realtime")

        # Example: send a simple instruction
        await ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "instructions": "Say hello from Azure GPT Realtime!"
            }
        }))

        # Listen for events
        async for message in ws:
            data = json.loads(message)
            print("Received:", json.dumps(data, indent=2))

asyncio.run(connect_realtime())
