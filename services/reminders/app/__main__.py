import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from app.main import create_app
from app.middleware import configure_logging

if __name__ == "__main__":
    # Run from services/reminders with `python -m app`. A .env file next to
    # this service's requirements.txt is optional; variables already set win.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    configure_logging()
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
