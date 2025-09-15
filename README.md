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
* Chat screen with text messaging (local echo)
* Image picker (gallery) stub adds image messages locally
* Audio recording stub adds audio message placeholder
* WebView screen ready for future form automation integration

### Planned Enhancements
* Connect to backend API for real responses
* Persist conversation context & multi-turn state
* Automated form filling in WebView based on chat intent & extracted entities

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
`POST /api/chat` – Accepts form-data with either:
* text (field: text, media_type=text)
* file (field: file, media_type=image|audio) – returns echo placeholder

Response contains an array of two messages (user + assistant echo) for now.

## Connecting Frontend & Backend (Future)
Update `src/services/api.ts` to call the backend:
```
export async function sendChatMessage(payload) {
	const form = new FormData();
	if (payload.type === 'text') { form.append('text', payload.content); form.append('media_type', 'text'); }
	// handle image/audio with file blobs later
	const res = await fetch('http://127.0.0.1:8000/api/chat', { method: 'POST', body: form });
	return res.json();
}
```

## Development Notes
* CORS is currently wide open (`*`) – restrict origins before production.
* File upload handling is placeholder (no persistence or validation yet).
* Media URIs in responses are mock references – implement storage layer later.

## Next Steps / Roadmap
1. Hook frontend chat to backend endpoint.
2. Introduce conversation/session IDs.
3. Integrate LLM (e.g., OpenAI / local model) for assistant responses.
4. Intent extraction + entity mapping for government form fields.
5. WebView automation layer to populate forms.
6. Add secure auth / rate limiting.

## License
TBD.

---
Generated initial scaffold (frontend Expo + backend FastAPI) via assisted automation.
