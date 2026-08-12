import unittest

from localflow import structure

SPESA = ("Ciao amore, come stai? Sto andando a fare la spesa. Mi sono segnato le cose "
         "che ci servono, ti do qui la lista. latte, detersivo, spazzolino dentifricio "
         "e i profilattici. Fammi sapere se manca qualcosa.")


class ListTests(unittest.TestCase):
    def test_dictated_shopping_list_becomes_bullets(self):
        out = structure.apply(SPESA)
        self.assertIn("\n- latte", out)
        self.assertIn("\n- detersivo", out)
        self.assertIn("\n- spazzolino dentifricio", out)
        self.assertIn("\n- i profilattici", out)  # nessuna parola tolta, solo la 'e'
        self.assertIn("ti do qui la lista:", out)

    def test_the_sentence_after_the_list_starts_on_its_own_line(self):
        out = structure.apply(SPESA)
        self.assertIn("- i profilattici\n", out)
        self.assertNotIn("profilattici Fammi", out)

    def test_commas_without_an_announcement_are_left_alone(self):
        text = ("Sono andato al mare con Marco, poi abbiamo mangiato un panino al bar "
                "del porto, e alla fine siamo tornati a casa tardi.")
        self.assertEqual(structure.apply(text), text)

    def test_two_items_are_not_a_list(self):
        text = "Mi servono latte e detersivo."
        self.assertEqual(structure.apply(text), text)

    def test_long_items_are_not_a_list(self):
        text = ("Mi servono le cose che avevamo detto ieri sera al telefono, quelle che "
                "non abbiamo comprato quando siamo usciti insieme, e anche le altre.")
        self.assertEqual(structure.apply(text), text)


class ParagraphTests(unittest.TestCase):
    LONG = ("Allora per il sito ho guardato la pagina dei servizi e mi sembra che il "
            "testo sia troppo lungo e poco chiaro per chi arriva la prima volta dal "
            "telefono, quindi va accorciato parecchio. "
            "Poi c'e' la questione delle immagini, che pesano troppo e rallentano "
            "tutto quanto anche sulla rete di casa che va benissimo. "
            "Per quanto riguarda il blog invece direi che possiamo lasciarlo cosi' "
            "com'e' almeno fino a settembre, non e' una priorita' adesso.")

    def test_discourse_markers_open_a_paragraph(self):
        out = structure.apply(self.LONG)
        self.assertIn("\n\nPoi c'e' la questione", out)
        self.assertIn("\n\nPer quanto riguarda il blog", out)

    def test_short_text_is_never_reflowed(self):
        text = "Ho controllato il log. Poi ti dico."
        self.assertEqual(structure.apply(text), text)

    def test_text_that_already_has_newlines_is_untouched(self):
        text = self.LONG.replace(". Poi", ".\nPoi")
        self.assertEqual(structure.apply(text), text)

    def test_never_starts_or_ends_with_blank_lines(self):
        out = structure.apply(self.LONG)
        self.assertEqual(out, out.strip())
        self.assertNotIn("\n\n\n", out)


class SafetyTests(unittest.TestCase):
    def test_disabled_returns_the_very_same_text(self):
        self.assertEqual(structure.apply(SPESA, enabled=False), SPESA)

    def test_only_whitespace_and_bullets_are_ever_added(self):
        """The words that come out must be the words that went in: the list
        connector 'e' is the single exception, and nothing else may vanish."""
        for text in (SPESA, ParagraphTests.LONG):
            self.assertTrue(structure._words_preserved(text, structure.apply(text)))

    def test_the_check_refuses_a_dropped_word_that_is_not_the_connector(self):
        self.assertTrue(structure._words_preserved("latte e detersivo", "- latte\n- detersivo"))
        self.assertFalse(structure._words_preserved("latte e detersivo", "- latte"))
        self.assertFalse(structure._words_preserved("latte", "- latte fresco"))

    def test_a_tampered_result_is_rejected(self):
        """The guarantee is a check that runs, not a comment: if the rules ever
        produced different words, the original text must win."""
        out = structure.apply(SPESA, _rules=lambda t: t.replace("latte", "vino"))
        self.assertEqual(out, SPESA)

    def test_empty_and_tiny_inputs_survive(self):
        for text in ("", "   ", "Ciao.", "Si."):
            self.assertEqual(structure.apply(text), text)


if __name__ == "__main__":
    unittest.main()
