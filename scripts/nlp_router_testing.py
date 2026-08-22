import os

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline



DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "agent", "routing", "data", "intent_data.CSV")
training_data = pd.read_csv(DATA_FILE).dropna(subset=["text", "intent"])
print(training_data.count(), "training examples loaded from", DATA_FILE) #209,209 varified
model = make_pipeline(TfidfVectorizer(), MultinomialNB())
model.fit(training_data["text"], training_data["intent"])


def nlp_decider(message, available_domains=None):
    """Predict an intent with Naive Bayes and enforce user tool availability."""
    prediction = model.predict([message])[0]
    confidence = float(model.predict_proba([message]).max())
    available_domains = set(available_domains or ())

    base_domain = prediction.split(".")[0]

    if available_domains and base_domain not in available_domains:
        prediction = "general"
        confidence = 0.0

    print(f"Predicted intent: {prediction} (confidence: {confidence:.2f}) for message: {message}")
    print(f"Available domains: {available_domains}")
    return {
        "intent": prediction,
        "domain": base_domain if prediction != "general" else "general",
        "confidence": confidence,
    }



ALL_DOMAINS = ["email", "calendar", "docs", "sheets", "slack"]

test_cases = [
    # --- Group 1: Baseline Intent Checks (All Domains Active) ---
    ("schedule a meeting with john at 10:00 AM", ALL_DOMAINS),
    ("send a message to john on slack", ALL_DOMAINS),
    ("create a new document for the project", ALL_DOMAINS),
    ("update the spreadsheet with the latest data", ALL_DOMAINS),
    ("is there an email from john?", ALL_DOMAINS),
    ("email john about the meeting", ALL_DOMAINS),

    # --- Group 2: Permission Restrictiveness & Fallbacks ---
    # Calendar query, but 'calendar' domain is missing -> Should return 'general'
    ("schedule a meeting with john at 10:00 AM", ["email", "docs", "sheets", "slack"]),
    # Email query, but 'email' domain is missing -> Should return 'general'
    ("send an email to sarah regarding the invoice", ["calendar", "slack", "sheets"]),
    # Slack query, but 'slack' domain is missing -> Should return 'general'
    ("post an update in the #announcements channel", ["email", "calendar", "docs"]),

    # --- Group 3: Specific CRUD Operations per Domain ---
    # Calendar Actions (Search / Create / Update / Delete)
    ("is there a meeting scheduled for today?", ["calendar"]),
    ("cancel my 4 PM meeting with alex", ["calendar"]),
    ("move my dentist appointment to Friday at 10 AM", ["calendar"]),

    # Google Docs Actions (Create / Update / Read)
    ("draft a project roadmap document", ["docs"]),
    ("append meeting notes to the Q3 design doc", ["docs"]),
    ("read the summary in the project pitch file", ["docs"]),

    # Google Sheets Actions (Update / Read)
    ("add a new row for quarterly expenses in the ledger", ["sheets"]),
    ("calculate the total revenue in column B", ["sheets"]),

    # Slack Actions (Send / React / Search)
    ("add a thumbs up emoji to the last message in general", ["slack"]),
    ("search slack history for the deployment key", ["slack"]),

    # Email Actions (Search / Send)
    ("find emails from HR sent last week", ["email"]),
    ("send a follow-up email to client@example.com", ["email"]),

    # --- Group 4: Web Search, Research & General Queries ---
    ("what is the weather like today?", ALL_DOMAINS),
    ("look up the latest research on AI", ALL_DOMAINS),
    ("search the web for competitor pricing analysis", ALL_DOMAINS),
    ("hello, how can you help me today?", ALL_DOMAINS),

    # --- Group 5: Cross-Domain Ambiguity & Edge Cases ---
    ("email john to ask if he scheduled the slack call", ALL_DOMAINS),
    ("post the google sheet link to the marketing channel on slack", ALL_DOMAINS),
    ("sched a call w/ mike tmrw morning", ALL_DOMAINS),
    ("dm sam on slck about the bug", ALL_DOMAINS),
]

# ---------------------------------------------------------------------------
# 4. Run Test Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== Running Intent Classifier Tests ===\n")
    for message, domains in test_cases:
        nlp_decider(message, available_domains=domains)