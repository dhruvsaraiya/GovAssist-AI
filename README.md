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
* Chat screen with text, image, and audio (multipart upload) integrated with backend
* Real-time WebSocket connection for streaming AI responses and form interactions
* Azure OpenAI Realtime API integration for voice-to-text transcription and AI responses
* Intelligent form activation based on keyword detection (Aadhaar, Income/Mudra loans)
* Step-by-step conversational form filling with field validation and progress tracking
* Draggable top-attached shutter (`FormWebView`) rendering forms with real-time field updates
* Voice input with PCM16 audio streaming for natural conversation
* Automatic form field highlighting and completion tracking
* Error handling with intelligent user intent interpretation
* Health check endpoint and platform-aware backend URL selection

### Completed Enhancements
* ✅ LLM-generated assistant responses via Azure OpenAI Realtime API
* ✅ Real-time WebSocket communication for streaming responses
* ✅ Voice input processing with Azure Realtime audio transcription
* ✅ Conversational form filling with intelligent field interpretation
* ✅ Form session management with progress tracking
* ✅ Field-by-field form completion with validation
* ✅ Intent extraction and entity mapping to form fields
* ✅ WebView form integration with automated field updates

## Backend (FastAPI)

### Prerequisites
* Python 3.11+
* Virtual environment tool (venv/pyenv)

### Setup & Run
```
cd backend
python -m venv .venv  (To use python 3.11 - py -3.11 -m venv .venv)
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
* `WebSocket /api/chat/ws` – Real-time chat with streaming AI responses
* `POST /api/chat` – Multipart form-data endpoint for fallback audio transcription
* `POST /api/chat/restart` – Clear all active form sessions

WebSocket Events:
* `user_message` – Send text input to AI
* Binary frames – Send audio chunks for real-time transcription
* `assistant_delta` – Streaming AI response chunks
* `assistant_message` – Complete AI responses with form activation
* `form_field_focus` – Highlight specific form fields
* `form_field_update` – Update form field values
* `form_completed` – Form submission completion

## Frontend <-> Backend Integration Notes
* WebSocket connection with automatic reconnection and keepalive
* Azure OpenAI Realtime API for streaming voice and text processing
* Real-time audio streaming with PCM16 format and WAV header stripping
* Form session management with field-by-field progression
* Intelligent error handling and user intent interpretation
* Speech detection feedback with real-time transcription display

## Development Notes
* Audio processing uses Azure Realtime API with 120ms minimum buffer threshold
* Form sessions track progress with field validation and completion
* Voice input requires 2-3 seconds minimum for reliable transcription
* WebSocket handles both text and binary (audio) frame types
* Form field updates are synchronized between chat and WebView

## Next Steps / Roadmap

### Phase 3 - Production Readiness (Current Priority)
1. **Security & Performance**
   - Implement robust authentication and user session management
   - Add rate limiting and abuse protection for voice/AI endpoints
   - Secure form URL validation with signed proxies
   - Add input sanitization and XSS protection for WebView content

2. **Enhanced User Experience**
   - Multi-language support for regional Indian languages
   - Offline mode with message queuing and sync
   - Voice commands for form navigation ("next field", "go back")
   - Smart form pre-filling from user profile/previous submissions

3. **Advanced Form Processing**
   - OCR integration for document scanning and auto-fill
   - Form validation with government database verification
   - Digital signature integration for official submissions
   - Progress saving and resume across sessions

### Phase 4 - Scale & Analytics
4. **Infrastructure & Monitoring**
   - Azure deployment with auto-scaling and load balancing
   - Comprehensive logging and error tracking
   - Performance monitoring and optimization
   - Database integration for user data and form history

5. **Advanced AI Features**
   - Context-aware form suggestions based on user profile
   - Predictive field completion using historical data
   - Multi-turn conversation memory across form sessions
   - Integration with government APIs for real-time validation

6. **Business Features**
   - Admin dashboard for form management and user analytics
   - Bulk form processing for organizations
   - Integration with government portals and services
   - Compliance reporting and audit trails

### Completed Items
* ✅ Real-time WebSocket communication with streaming responses
* ✅ Azure OpenAI Realtime API integration for voice and text
* ✅ Conversational form filling with step-by-step guidance
* ✅ Form session management with progress tracking
* ✅ Voice input with real-time transcription and response
* ✅ Field validation and intelligent error handling
* ✅ WebView form integration with automated updates

## License
TBD.

---
Generated initial scaffold (frontend Expo + backend FastAPI) via assisted automation.
