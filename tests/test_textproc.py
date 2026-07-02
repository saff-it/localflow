import unittest

from localflow import textproc


class TidyTests(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(textproc.tidy("  ciao   come  va  "), "Ciao come va")

    def test_capitalizes_first_letter(self):
        self.assertEqual(textproc.tidy("hello world"), "Hello world")

    def test_keeps_existing_capitalization(self):
        self.assertEqual(textproc.tidy("Già maiuscola"), "Già maiuscola")

    def test_empty(self):
        self.assertEqual(textproc.tidy("   "), "")


class DictionaryTests(unittest.TestCase):
    def test_case_insensitive_replacement(self):
        out = textproc.apply_dictionary("parliamo di local mind oggi", {"local mind": "LocalMind"})
        self.assertEqual(out, "parliamo di LocalMind oggi")

    def test_word_boundaries(self):
        out = textproc.apply_dictionary("un trafficone con traefik", {"traefik": "Traefik"})
        self.assertEqual(out, "un trafficone con Traefik")

    def test_no_partial_word_match(self):
        out = textproc.apply_dictionary("scaffale", {"caffa": "X"})
        self.assertEqual(out, "scaffale")

    def test_multiple_rules(self):
        out = textproc.apply_dictionary(
            "wisper flow e enne otto enne", {"wisper flow": "Wispr Flow", "enne otto enne": "n8n"}
        )
        self.assertEqual(out, "Wispr Flow e n8n")


if __name__ == "__main__":
    unittest.main()
