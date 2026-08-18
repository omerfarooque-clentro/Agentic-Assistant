import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from services.auth_services import get_google_credentials

def get_sheets_service():
    return build("sheets", "v4", credentials=get_google_credentials())