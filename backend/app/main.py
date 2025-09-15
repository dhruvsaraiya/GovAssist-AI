from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import chat
import logging
import sys

# Configure root logging if not already configured by Uvicorn
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)

# Ensure our application logger levels
logging.getLogger("app.routers.chat").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = FastAPI(title="GovAssist AI Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api")

@app.get("/health")
async def health():
    return {"status": "ok"}
