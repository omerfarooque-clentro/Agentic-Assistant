import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SERVICES_DIR = Path(__file__).resolve().parent
TOKEN_FILE = SERVICES_DIR / "token.json"
CREDENTIALS_FILE = SERVICES_DIR / "credentials.json"

# Master list of all required Google API scopes
# Master list of all required Google API scopes
SCOPES = [
    # Gmail Scopes
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",  # Required for drafting, trashing, & updating threads
    "https://www.googleapis.com/auth/gmail.labels",  # Required for list_labels & label/unlabel operations
    # Workspace Scopes
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

def get_google_credentials():
    """Retrieve or authorize single credential object with all required scopes."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # Trigger fresh login if refresh token was revoked or scope changed
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save updated token state to disk
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds