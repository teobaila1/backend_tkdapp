import os
import requests
import pytest
from pathlib import Path
from dotenv import load_dotenv

# Load frontend env to obtain public backend url
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "frontend" / ".env")

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL must be set in frontend/.env"


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s
