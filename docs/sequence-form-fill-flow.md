# End-to-End Form Filling Conversation Flow

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant FE as Frontend (App)
    participant BE as Backend (FastAPI)
    participant RT as Azure Realtime Model
    participant Form as HTML Form/WebView

    Note over U,RT: Initial discovery & form selection
    U->>FE: "mujhe mudra loan ka form bharna hai"
    FE->>BE: WebSocket user_message(content)
    BE->>RT: session.update (system prompt + tools description)
    BE->>RT: conversation.item.create (user message)
    BE->>RT: response.create (request form suggestion + first question)
    RT-->>BE: response.delta (stream: form suggestion + summary + Q1)
    BE-->>FE: assistant_delta (streamed text)
    RT-->>BE: response.completed (final text includes form id + first field question)
    BE-->>FE: assistant_message (final assembled assistant message)
    alt Form identified
        BE-->>FE: form_open (formUrl)
        FE->>Form: Open specified form (WebView)
    end

    Note over U,Form: Interactive field-by-field filling loop
    loop For each remaining form field
        U->>FE: Provides answer to current question
        FE->>BE: user_message(answer)
        BE->>RT: conversation.item.create (user answer)
        BE->>RT: response.create (ask model to extract value & ask next)
        RT-->>BE: response.delta (structured JSON + follow-up question)
        BE-->>FE: assistant_delta (stream tokens)
        RT-->>BE: response.completed
        BE-->>FE: assistant_message (final consolidated)
        BE->>FE: (optional) ack/form_open if switching forms
        FE->>Form: set_field_value (prefill from JSON)
        FE->>Form: get_next_field (advance cursor/highlight)
    end

    Note over FE,Form: Completion
    RT-->>BE: (Eventually) response.completed with summary / completion notice
    BE-->>FE: assistant_message ("Form completed" + summary)
    FE->>U: Renders confirmation & preview
```

## Legend
* `conversation.item.create` – Adds a user or assistant message item to the realtime conversation.
* `response.create` – Instructs the model to generate the next assistant response (streamed as `response.delta`).
* `assistant_delta` – Incremental text tokens forwarded from backend to frontend.
* `assistant_message` – Final assembled assistant response (end of turn).
* `form_open` – Backend instructs frontend to open/render a specific government form.
* Tool-like frontend actions (`set_field_value`, `get_next_field`) are triggered locally after model supplies structured JSON.

## High-Level Phases
1. Discovery & Form Selection – Identify correct form and load it.
2. Field Iteration – Repeated Q&A loop populating each form element.
3. Completion – Model or heuristic signals all required fields gathered; frontend presents confirmation.
