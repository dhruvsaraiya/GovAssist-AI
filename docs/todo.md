# Implementation Plan: Guided AI Form Filling Flow

This document captures the high‑level tasks required to implement the iterative, AI‑assisted form filling loop described in `userflow.md` and the sequence diagram (`sequence-user-form-flow.md`).

## 0. Guiding Principles
* Deterministic contract between backend and model — always request/expect strict JSON for field updates.
* Stateless model, stateful backend: backend is source of truth for session, progress, and schema.
* Frontend holds a reactive mirror of backend state; optimistic UI only after validation.
* Incremental delivery: ship thin vertical slices (intent → suggestion → first field) before full loop.

## 1. Backend Enhancements

### 1.1 Session & State Management
| Task | Description | Notes |
|------|-------------|-------|
| Create `app/state/session_manager.py` | In‑memory dict keyed by `session_id` storing: active_form, answers, field_index, status, transcript refs | Add TTL / cleanup later |
| Session ID propagation | Accept `session_id` via WS message / HTTP header, generate UUID if missing | Return to client on first response |

### 1.2 Form Schemas
| Task | Description | Notes |
|------|-------------|-------|
| Backend schema source | Add JSON schemas under `backend/form_schemas/` (authoritative order & metadata) | Reuse names: `aadhaar`, `income` |
| Loader module | `app/form_schemas/loader.py` with `get_form_schema(form_id)` | Cache on first load |
| Field metadata | Each field: id, label, required, type, dependencies? | Minimal now; extensible |

### 1.3 Orchestrator / Prompt Builder
| Task | Description |
|------|-------------|
| `app/services/orchestrator.py` | Build system prompts for (a) initial form suggestion (b) per-field capture |
| Templates | Add `app/prompt_templates/initial.txt`, `field.txt` for maintainable prompt text |

### 1.4 Model Response Parsing
| Task | Description |
|------|-------------|
| `parsers.py` | Extract first valid JSON block; validate keys: `field`, `next`, `done` |
| Error handling | On malformed JSON -> send `error` event + fallback reprompt option |
| Confidence defaulting | If model omits confidence, default 1.0 |

### 1.5 WebSocket Protocol Extension
Current events: `assistant_delta`, `assistant_message`, `form_open`.
Add standardized events:
```
form_suggestion { form:{id,title,summary}, next_question:{fieldId,question} }
field_update     { field:{id,label,value,confidence} }
next_question    { fieldId, question }
done             { }
impact           { impacted:[fieldIds...] }
error            { code, detail }
```

### 1.6 Message Handling Logic
| Scenario | Action |
|----------|--------|
| First user intent | Detect intent → choose form (keyword or embedding future) → call model for summary/first field |
| User answers field | Build prompt with current answers → model → parse JSON → update session → emit `field_update` + `next_question` or `done` |
| Edit previous field | Replace value, mark dependent fields stale, optionally re-query model for next field if dependencies changed |
| Completion | On no further required fields → emit `done` |

### 1.7 Tool Calls (Optional Phase 2)
Map realtime tool invocations to same event stream (e.g. `set_field_value` → `field_update`). For MVP rely on plain text + JSON parsing.

### 1.8 HTTP Fallback Endpoints (Optional but Recommended)
| Endpoint | Purpose |
|----------|---------|
| `POST /api/form/field` | Non-WS answer submission |
| `PATCH /api/form/field` | Edit field |
| `POST /api/form/submit` | Final submission, returns referenceId |

### 1.9 Validation & Security
* Enforce max length per field (e.g. 512 chars).
* Reject unknown field IDs with `error` event.
* Sanitize model text (strip control chars) before parsing.

### 1.10 Logging & Metrics
| Event | Log Fields |
|-------|-----------|
| form_chosen | session_id, form_id |
| field_captured | session_id, field_id, latency_ms |
| parse_error | session_id, snippet |
| session_complete | session_id, total_fields, duration_s |

### 1.11 Testing
| Test | Description |
|------|-------------|
| `test_form_flow_initial` | Intent → suggestion + first question |
| `test_form_flow_sequence` | Sequential answers produce ordered updates |
| `test_parse_malformed` | Model bad JSON triggers error event |
| `test_edit_field` | Edit triggers state update & impact (stub) |

## 2. Frontend Enhancements

### 2.1 State Management Hook
File: `src/hooks/useFormConversation.ts`
State: `activeFormId`, `currentFieldId`, `answers`, `status`, `summary`.
Actions: `start(formSuggestion)`, `applyFieldUpdate(field)`, `setNextQuestion(fieldId, question)`, `markDone()`, `editField(fieldId)`.

### 2.2 WebSocket Extensions
Update `ws.ts`:
* Add types & handlers for new events (`form_suggestion`, `field_update`, `next_question`, `done`, `impact`).
* Outbound user message shape extended: `{ type:'user_message', content, fieldId?, session_id }`.

### 2.3 ChatScreen Integration
* Display current question (sticky prompt area above input).
* Prefill input when editing existing answer.
* Disable submission while awaiting model (optimistic allowed optionally).

### 2.4 Form Injection Incremental Updates
Enhance `FormWebView`:
* Expose imperative `applyFieldValue(fieldId, value)` (via `ref`).
* On each `field_update` call injection script for that single field (no full re-fill).

### 2.5 Edit Flow UI
* Long-press / context menu on answered user bubble: “Edit”.
* On confirm: set `currentFieldId` to edited field, show previous value in input.
* Send as message type `edit_field` (or reuse `user_message` with `editOf: fieldId`).

### 2.6 Completion & Submission
* When `done` event received: show bar with “Review & Submit”.
* On submit: POST collected answers; show confirmation bubble with referenceId.

### 2.7 Persistence
* Store `session_id` + partial answers in `AsyncStorage` keyed by date + form.
* On mount, if active session incomplete, prompt user to resume.

### 2.8 Error Handling
* Map `error.code` to user-friendly toasts.
* Provide “Retry last step” action for parse errors.

### 2.9 Types Updates
Extend `ChatMessage` with optional: `fieldId`, `question`, `eventType`.

### 2.10 Frontend Testing
| Test | Goal |
|------|------|
| hook_initialization | Starts idle state |
| apply_field_update | Adds answer and updates current field |
| next_question_transition | Moves to next question correctly |
| done_event | Shows submission UI |

## 3. Prompt Design (Draft Responsibilities)
* Initial prompt: map intent → form id + summary + first question.
* Field prompt: Provide JSON of current answers + NEXT target field; instruct: “Respond ONLY with JSON { field:{...}, next:{...} } or { field:{...}, done:true }”.
* Guardrails: no additional commentary; if user off-topic ask to refocus.

## 4. Phased Delivery Roadmap
| Phase | Scope | Exit Criteria |
|-------|-------|---------------|
| 1 | Session + form suggestion + first field Q | Receives first `field_update` + `next_question` |
| 2 | Full sequential field loop | Can complete all fields & emit `done` |
| 3 | Submission endpoint + UI | ReferenceId returned |
| 4 | Edit previous answer | Edited answer persists & re-prompts next |
| 5 | Impact/dependency propagation | Impact event produced for dependent field |
| 6 | Persistence & resilience | Resume mid-session after reload |

## 5. Open Questions / Future Enhancements
* Dependency graph format for fields (YAML? inside schema JSON?).
* Confidence-based user confirmation (“I guessed X, correct?”) threshold.
* Multi-modal (voice answers transcription) integration into field pipeline.
* Form versioning & migration strategy.
* Analytics dashboard (completion rate, time per field).

## 6. Acceptance Criteria (MVP)
1. User intent triggers single `form_suggestion` event with summary + first question.
2. Each user answer yields exactly one `field_update` and either `next_question` or `done`.
3. Frontend injects each captured field into visible form without reload.
4. Edit path updates stored answer and (if not last) resumes Q&A from next unanswered field.
5. Submission returns stable `referenceId` and locks session state.

---
Owner: (assign)
Last Updated: (fill when modified)

