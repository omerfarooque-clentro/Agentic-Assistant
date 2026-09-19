from django.test import TestCase

from agent.cards.intent import get_presentation, is_question


class IntentClassificationTests(TestCase):
    def test_question_detection_question_mark(self):
        self.assertTrue(is_question("What is the weather today?"))
        self.assertTrue(is_question("Show me emails?"))
        self.assertTrue(is_question("are there any meetings?"))

    def test_question_detection_starter_words(self):
        self.assertTrue(is_question("who sent the last email"))
        self.assertTrue(is_question("where is my 3pm meeting"))
        self.assertTrue(is_question("can you check my unread messages"))
        self.assertTrue(is_question("did Alice reply"))
        self.assertTrue(is_question("how is the weather in Tokyo"))

    def test_command_detection(self):
        self.assertFalse(is_question("Show my recent emails"))
        self.assertFalse(is_question("List events for tomorrow"))
        self.assertFalse(is_question("Check weather in Paris"))
        self.assertFalse(is_question("Summarize the thread"))

    def test_presentation_parameters(self):
        q_pres = get_presentation("Is it raining today?")
        self.assertEqual(q_pres["order"], "text_first")
        self.assertEqual(q_pres["max_rows"], 3)

        cmd_pres = get_presentation("Show my latest 5 emails")
        self.assertEqual(cmd_pres["order"], "card_first")
        self.assertEqual(cmd_pres["max_rows"], 5)
