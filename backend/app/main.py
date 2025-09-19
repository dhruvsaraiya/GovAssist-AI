from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import chat
from fastapi.staticfiles import StaticFiles
import logging
import sys
import os

# Configure root logging if not already configured by Uvicorn
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)

# Ensure our application logger levels
logging.getLogger("app.routers.chat").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = FastAPI(title="FormAssist AI Backend", version="0.1.0")

FRONTEND_ORIGINS = [
    "http://localhost:8081",  # expo web dev
    "http://127.0.0.1:8081",
    "http://localhost:19006", # expo classic web port
    "http://127.0.0.1:19006",
]

allow_all = os.getenv("BACKEND_ALLOW_ALL_ORIGINS", "0").lower() in {"1", "true", "yes"}
if allow_all:
    logging.getLogger(__name__).warning("CORS: Allowing ALL origins (BACKEND_ALLOW_ALL_ORIGINS=1) - dev only!")
    origins = ["*"]
else:
    origins = FRONTEND_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api")

# Serve demo static forms
forms_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'static', 'forms'))
root_static = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'static'))
candidates = [forms_dir, root_static]
mounted = False
for d in candidates:
    try:
        exists = os.path.isdir(d) and any(f.lower().endswith('.html') for f in os.listdir(d))
    except Exception:
        exists = False
    logging.getLogger(__name__).info("Resolved candidate forms dir: %s (has_html=%s)", d, exists)
    if exists:
        app.mount('/forms', StaticFiles(directory=d), name='forms')
        logging.getLogger(__name__).info("Mounted forms from %s at /forms", d)
        mounted = True
        break
if not mounted:
    logging.getLogger(__name__).warning("No demo forms found; skipping /forms mount. Tried: %s", candidates)

# Mount static directory for audio files and other static content
static_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'static'))
if os.path.isdir(static_dir):
    app.mount('/static', StaticFiles(directory=static_dir), name='static')
    logging.getLogger(__name__).info("Mounted static files from %s at /static", static_dir)

@app.get("/health")
async def health():
    return {"status": "ok"}
