from langchain_core.tools import tool
from services.gmail_service import get_gmail_service
from services.sheet_services import get_sheets_service
from services.docs_service import get_docs_service, get_drive_service

@tool
def search_gmail(query: str) -> str:
    """
    Search the user's Gmail inbox.

    The query must use valid Gmail search syntax.
    Examples:
    - from:someone
    - newer_than:7d
    - from:someone newer_than:7d
    """
    service = get_gmail_service()
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    if not messages:
        return f"Search completed successfully. Gmail results for '{query}': No matching emails found."

    summaries = []
    for message in messages:
        msg = service.users().messages().get(userId='me', id=message['id']).execute()
        subject = next((header['value'] for header in msg['payload']['headers'] if header['name'] == 'Subject'), 'No Subject')
        sender = next((header['value'] for header in msg['payload']['headers'] if header['name'] == 'From'), 'Unknown Sender')
        summaries.append(f"Email ID: {message['id']}, Subject: {subject}, Sender: {sender}")
    return "\n".join(summaries)

 
SPREADSHEET_ID = "14s_n4oOSYXk0jEKrAmbevvgnhE7XulIQk2H2CXbo_Pg"
SHEET_RANGE = "Sheet1!A:B"


@tool
def add_to_sheet(data: str) -> str:
    """
    Add an expense to the user's Google Sheets expense tracker.

    data should contain the expense information, such as:
    "$50 groceries"
    """

    service = get_sheets_service()

    values = [[data]]

    body = {
        "values": values
    }

    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=SPREADSHEET_ID,
            range=SHEET_RANGE,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )

    return (
        f"Successfully added expense to Google Sheets. "
        f"Updated range: {result.get('updates', {}).get('updatedRange')}"
    )

@tool
def read_doc(document_name: str) -> str:
    """Read the plain text contents of a Google Doc by its name."""
    try:
        drive_service = get_drive_service()
        docs_service = get_docs_service()

        # 1. Search for the Google Doc by filename using Drive API
        query = (
            f"name = '{document_name}' and "
            "mimeType = 'application/vnd.google-apps.document' and "
            "trashed = false"
        )
        results = drive_service.files().list(
            q=query, fields="files(id, name)"
        ).execute()
        files = results.get("files", [])

        if not files:
            return f"Error: No Google Doc found with the name '{document_name}'."

        doc_id = files[0]["id"]

        # 2. Retrieve document content using Docs API
        doc = docs_service.documents().get(documentId=doc_id).execute()

        # 3. Extract plain text from document structural elements
        text_content = []
        for element in doc.get("body", {}).get("content", []):
            if "paragraph" in element:
                for param_element in element["paragraph"].get("elements", []):
                    text_run = param_element.get("textRun")
                    if text_run:
                        text_content.append(text_run.get("content", ""))

        return "".join(text_content).strip()

    except Exception as e:
        return f"Error reading document '{document_name}': {str(e)}"


@tool 
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email using the user's Gmail account.

    Parameters:
    - to: Recipient's email address
    - subject: Subject of the email
    - body: Body content of the email
    """
    
    try:
        service = get_gmail_service()

        # Create the email message
        from email.mime.text import MIMEText
        import base64

        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject

        # Encode the message in base64
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        # Send the email
        sent_message = service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()

        return f"Email sent successfully! Message ID: {sent_message['id']}"

    except Exception as e:
        return f"Error sending email: {str(e)}"