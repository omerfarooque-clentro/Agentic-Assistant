import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline
import os


df = pd.read_csv(os.path.join(os.path.dirname(__file__), "intent_data.CSV"))
 

x_train, x_test, y_train, y_test = train_test_split(df.text, df.intent, test_size=0.2, random_state=42, stratify=df.intent)
 
model = make_pipeline(TfidfVectorizer(), MultinomialNB())

model.fit(x_train, y_train)

predictions = model.predict(x_test)


def nlp_decider(message):
    prediction = model.predict([message])[0]
    print(f"Predicted intent: {prediction} for message: {message}")
    confidence = model.predict_proba([message]).max()
    print(f"Confidence: {confidence:.2f} for message: {message}")

    return {
        "intent": prediction,
        "confidence": confidence
    }

  