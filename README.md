# GovAssist-AI
An AI-powered assistant that will guide users step-by-step through filling out application forms for major government schemes in India. The aim is to make the process accessible for users unfamiliar with technology or paperwork.

## Monorepo Structure

```
frontend/         # React Native (Expo) app with chat UI, media inputs, webview for forms
	App.tsx
	src/
		screens/      # Chat screen(s)
		components/   # Reusable UI components (MessageBubble, etc)
		services/     # API client stubs
		types/        # Shared TypeScript types
backend/
	app/
		main.py       # FastAPI app entrypoint
		routers/      # Chat endpoint router
		schemas/      # Pydantic models
	tests/          # Pytest tests for API
prompt.md         # High-level implementation prompt
```

## Frontend (React Native / Expo)

### Prerequisites
* Node.js LTS (>=18)
* Expo CLI (npx is fine – no global install required)
* Android Studio / Xcode (for device emulators) or Expo Go app on a physical device

### Install Dependencies
```
cd frontend
npm install
```

### Run (Development)
```
npm start   # Opens Expo dev tools (press a for Android, i for iOS, w for web)
```

### Current Features
* Chat screen with text, image, and audio (multipart upload) integrated with backend echo API
* Backend FastAPI `/api/chat` endpoint accepts text/image/audio and returns structured messages
* Automatic detection of certain trigger phrases (e.g. "tax form", "passport form", "visa form") that returns a `form_url` in the assistant message
* Phase 1 (implemented): Basic assistant-provided `form_url` contract (Pydantic `ChatMessage.form_url`) with allow‑listed government domains
* Phase 2 (implemented): Draggable top-attached shutter (`TopFormShutter`) rendering the form inside a WebView while keeping the input bar always accessible
* Health check endpoint and platform-aware (Android emulator) backend URL selection in frontend config

### Planned Enhancements
* LLM-generated assistant responses instead of simple echo
* Persist conversation context & multi-turn session management
* Enhanced URL validation (strict allowlist, signature or server-side retrieval proxy)
* Automated form field extraction & auto-fill within the WebView (accessibility-first design)
* Offline caching / retry queue for messages & form progress
* Security hardening (origin isolation, CSP headers for in-app browser) 

## Backend (FastAPI)

### Prerequisites
* Python 3.11+
* Virtual environment tool (venv/pyenv)

### Setup & Run
```
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Visit: http://127.0.0.1:8000/docs for interactive API docs.

### Run Tests
```
pytest -q
```

### API Summary
`POST /api/chat` – Multipart form-data endpoint:
* text: `text` + `media_type=text`
* image/audio upload: `file` + `media_type=image|audio`

Returns array `[user_msg, assistant_msg]` where `assistant_msg` may include:
* `form_url` – If a trigger phrase or explicit `form:` URL (allow‑listed host) was detected.
* `media_uri` – Placeholder reference for uploaded file (future: real storage location).

## Frontend <-> Backend Integration Notes
* See `frontend/src/services/api.ts` for multipart implementation.
* Android emulator uses `10.0.2.2` automatically (see `config.ts`). Override via `EXPO_PUBLIC_BACKEND_HOST` env var if needed.
* When a `form_url` arrives, `ChatScreen` sets `activeFormUrl` and renders a draggable top shutter (`TopFormShutter`) WebView. Closing the shutter does not remove the historical message containing the URL.
* Multiple future `form_url` messages will currently replace the active sheet with the newest form (simple heuristic).

## Development Notes
* CORS is currently wide open (`*`) – restrict origins before production.
* File upload handling is placeholder (no persistence or virus scanning yet).
* `form_url` allowlist is naive substring-based; replace with robust domain & path validation before production.
* Media URIs in responses are mock references – implement storage layer later.
* Top shutter uses a minimal animated snap implementation; consider `@gorhom/bottom-sheet` (adapted for top usage) or a custom Reanimated solution for production-grade performance & accessibility.

## Next Steps / Roadmap
1. Conversation/session IDs & persistence.
2. Integrate LLM for contextual assistant responses.
3. Strong URL sanitization & signed proxy retrieval for forms.
4. Intent extraction + entity mapping to form fields.
5. WebView automation: DOM injection / accessibility tree parsing to populate fields.
6. Form progress tracking & resume.
7. Add secure auth / rate limiting & audit logging.
8. Add lightweight analytics (anonymized) for flow optimization.

## License
TBD.

---
Generated initial scaffold (frontend Expo + backend FastAPI) via assisted automation.
