# User -> AI Assisted Form Filling Flow

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Frontend (Chat UI + Form View)
    participant BE as Backend API
    participant OR as Orchestrator (Prompt Builder)
    participant AI as Model (LLM)
    participant FS as Form State Store

    Note over U,AI: Initial request to fill a government form

    U->>FE: "Mujhe Mudra loan ka form bharna hai"
    FE->>BE: POST /chat { message, sessionId }
    BE->>OR: buildSystemPrompt(message, sessionContext)
    OR-->>BE: systemPrompt + context (prior answers, progress)
    BE->>AI: systemPrompt + userMessage
    activate AI
    AI-->>BE: { suggestedFormId, formSummary, firstFieldQuestion }
    deactivate AI
    BE-->>FE: { type: "form_suggestion", suggestedFormId, formSummary, nextQuestion }
    FE->>FS: initFormProgress(suggestedFormId)
    FE-->>U: Show summary + Ask first question

    loop For each field until complete
        U->>FE: Answer text / voice
        FE->>BE: POST /chat { answer, fieldId }
        BE->>AI: systemPrompt + priorAnswers + newAnswer
        activate AI
        AI-->>BE: { structuredFieldJSON, nextFieldQuestion? | done }
        deactivate AI
        BE-->>FE: { type: "field_update", fieldData, nextQuestion? , done }
        FE->>FS: updateField(fieldId, value)
        alt More fields remaining
            FE-->>U: Ask nextQuestion
        else All fields captured
            FE-->>U: Show completion + CTA (Review / Submit)
        end
    end

    opt User edits previous answer
        U->>FE: Edit field X
        FE->>FS: updateField(X, newValue)
        FE->>BE: PATCH /form/field { fieldId: X, value }
        BE->>AI: Re-evaluate dependencies (optional)
        AI-->>BE: { impactedFields? }
        BE-->>FE: { type: "impact", impactedFields }
        FE-->>U: Highlight impacted fields
    end

    opt Final submission
        U->>FE: Submit form
        FE->>BE: POST /form/submit { formData }
        BE-->>FE: { status: success, referenceId }
        FE-->>U: Confirmation + ReferenceId
    end
```

## Legend
- Frontend handles UI (chat + form prefill) and maintains a local reactive form state store.
- Backend orchestrates prompts, tracks conversation/session state, validates AI JSON.
- Model outputs: (a) initial form suggestion + summary + first question, (b) per-field structured JSON + next question, (c) done signal when no more fields.
- Structured field JSON example:
```json
{
  "fieldId": "applicant_name",
  "label": "Applicant Name",
  "value": "Kishan Patel",
  "confidence": 0.94,
  "meta": { "source": "user_input" }
}
```

