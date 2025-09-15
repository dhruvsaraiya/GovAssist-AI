# Project Setup Guide

This document walks you through configuring and running both the backend (FastAPI) and frontend (Expo React Native) for GovAssist-AI.

## 1. Clone & Enter Repo
```
git clone <your-fork-or-origin>
cd GovAssist-AI
```

## 2. Environment Variables
Env files are now per-package:

Backend:
```
cd backend
cp .env.sample .env
```
Fill `AZURE_OPENAI_ENDPOINT` (and optionally override deployment/api version).

Frontend (Expo):
```
cd frontend
cp .env.sample .env
```
Adjust `EXPO_PUBLIC_BACKEND_HOST` to a LAN IP for physical device testing or `10.0.2.2` for Android emulator.

The root `.env` is only a pointer now. Do not commit secrets; samples are safe placeholders.

## 3. Backend (FastAPI)
```
cd backend
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux equivalent:
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Visit: http://127.0.0.1:8000/docs

### Realtime Model (Azure OpenAI)
Keyless auth (recommended):
```
az login
```
Ensure your user has the `Cognitive Services User` role for the Azure OpenAI resource.

The chat endpoint will attempt to call the realtime deployment for text responses. If not configured properly it will fall back to a simple echo.

## 4. Frontend (Expo)
Open a new terminal:
```
cd frontend
npm install
npm start
```
Press `a` (Android), `i` (iOS), or scan QR for a real device. Ensure `EXPO_PUBLIC_BACKEND_HOST` in `.env` matches where the backend is reachable.

## 5. Running Tests (Backend)
```
cd backend
pytest -q
```

## 6. Directory Overview
```
backend/   # FastAPI app, realtime integration service, tests
frontend/  # Expo React Native application
.env       # Local environment variables (not committed)
env.sample # Template for .env
setup.md   # This file
```

## 7. Common Issues
| Issue | Cause | Fix |
|-------|-------|-----|
| Empty assistant replies | Realtime env vars unset | Set `AZURE_OPENAI_ENDPOINT` & run `az login` |
| Mobile app can’t reach backend | Wrong host/IP | Use LAN IP or `10.0.2.2` for Android emulator |
| SSL errors | Incorrect endpoint format | Must include `https://` and no trailing path |

## 8. Next Steps
- Add conversation/session persistence.
- Introduce schema-driven form field orchestration.
- Extend realtime to include audio output or streaming token updates.

## 9. Cleanup
Deactivate virtual environment:
```
deactivate
```
Stop Expo with `Ctrl+C`.

---
Generated initial setup instructions; extend as the project evolves.
