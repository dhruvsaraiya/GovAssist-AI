Azure Realtime Integration
===========================

This backend integrates with an Azure OpenAI `gpt-realtime` deployment for low-latency text responses.

Prerequisites
-------------
1. Azure OpenAI resource with a deployment named (or aliased to) `gpt-realtime`.
2. Your user/service principal has the `Cognitive Services User` role (for keyless auth).
3. Installed dependencies (see `backend/requirements.txt`).

Environment Variables
---------------------
Set these before starting the FastAPI server:

```
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-realtime   # if different from default
OPENAI_API_VERSION=2025-08-28               # optional; defaults internally
```

Authentication
--------------
Uses `DefaultAzureCredential` (azure-identity). On local dev run:

```
az login
```
Ensure no `AZURE_OPENAI_API_KEY` is set for keyless auth.

Runtime Behavior
----------------
`/chat` endpoint attempts a realtime model response for text messages unless a form URL is detected. If model configuration is missing or errors occur, it falls back to an echo.

Extending
--------
To add audio streaming or token-level UI updates, refactor `generate_assistant_reply` into an async generator yielding deltas from the event stream in `azure_realtime.py`.
