"""One-time Gmail OAuth (Desktop client). Opens a browser; prints GOOGLE_REFRESH_TOKEN for .env.

    uv run python -m scripts.gmail_auth
Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env. Sign in as the CS inbox account (a test user).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import Settings
from app.providers.real.gmail import SCOPES

s = Settings()
if not (s.google_client_id and s.google_client_secret):
    raise SystemExit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first")
flow = InstalledAppFlow.from_client_config(
    {"installed": {"client_id": s.google_client_id, "client_secret": s.google_client_secret,
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
                   "redirect_uris": ["http://localhost"]}}, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
print("\nAdd this line to .env (keep it secret):\nGOOGLE_REFRESH_TOKEN=" + creds.refresh_token)
