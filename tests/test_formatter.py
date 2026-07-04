import unittest

from localflow.formatter import _words_match, needs_punctuation


class NeedsPunctuationTests(unittest.TestCase):
    def test_short_text_never_triggers(self):
        self.assertFalse(needs_punctuation("ciao come va"))

    def test_medium_sentence_not_worth_the_latency(self):
        text = "e come vedi alcune parole me le ha tagliate dal testo che ti sto mandando adesso"
        self.assertFalse(needs_punctuation(text))  # ~85 chars: rescue would cost more than it gives

    def test_long_flat_text_triggers(self):
        text = "vediamo mi sembra che stia funzionando molto meglio anche se questa frase " \
               "la sto dicendo velocissima senza respirare quindi mancano le pause del tutto"
        self.assertTrue(needs_punctuation(text))

    def test_normally_punctuated_text_skips(self):
        text = "Vediamo, mi sembra che stia funzionando molto meglio. Anche se questa frase, " \
               "detta velocissima, non respira: mancano le pause, capito?"
        self.assertFalse(needs_punctuation(text))


class WordsMatchTests(unittest.TestCase):
    def test_punctuation_and_case_are_ignored(self):
        self.assertTrue(_words_match(
            "il preventivo per milano e tremila euro iva esclusa",
            "Il preventivo per Milano, è tremila euro: IVA esclusa.",
        ))

    def test_accent_fixes_are_allowed(self):
        self.assertTrue(_words_match("perche non vieni", "Perché non vieni?"))

    def test_changed_word_is_rejected(self):
        self.assertFalse(_words_match(
            "il preventivo e di tremila euro iva esclusa",
            "Il preventivo è di trentamila euro, IVA esclusa.",
        ))

    def test_dropped_word_is_rejected(self):
        self.assertFalse(_words_match("ciao marco come stai", "Ciao, come stai?"))

    def test_merging_split_words_is_allowed(self):
        self.assertTrue(_words_match(
            "qual è lo step success ivo per arrivare al live llo giusto",
            "Qual è lo step successivo per arrivare al livello giusto?",
        ))


if __name__ == "__main__":
    unittest.main()
