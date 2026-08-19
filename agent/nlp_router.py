import os

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline


DATA_FILE = os.path.join(os.path.dirname(__file__), "intent_data.CSV")
training_data = pd.read_csv(DATA_FILE).dropna(subset=["text", "intent"])
model = make_pipeline(TfidfVectorizer(), MultinomialNB())
model.fit(training_data["text"], training_data["intent"])


def nlp_decider(message, available_domains=None):
    """Predict an intent with Naive Bayes and enforce user tool availability."""
    prediction = model.predict([message])[0]
    confidence = float(model.predict_proba([message]).max())
    available_domains = set(available_domains or ())

    print(available_domains)

    if available_domains and prediction not in available_domains:
        prediction = "general"
        confidence = 0.0

    print(
        f"NLU Decider: message='{message}', intent='{prediction}', "
        f"confidence={confidence:.2f}"
    )
    return {
        "intent": prediction,
        "confidence": confidence,
    }

  