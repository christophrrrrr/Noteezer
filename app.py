import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from noteezer.main import create_app

if __name__ == "__main__":
    # A .env file is optional. Variables already set in the environment win.
    load_dotenv(Path(__file__).with_name(".env"))
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
