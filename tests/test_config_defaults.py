"""La soglia della voce vive in un posto solo.

Il 15 agosto 2026 la calibrazione porto' `audio.MIN_SPEECH_RMS` da 0.006 a 0.0035
sui clip veri, ma `config.py` teneva ancora 0.006 sia nel dataclass sia nel modello
di config.toml scritto al primo avvio. Siccome `app.py` fa
`audio.MIN_SPEECH_RMS = cfg.min_speech_rms`, il config vince sempre: la
ricalibrazione non arrivo' mai all'app e il 21 agosto le dettature sparivano di
nuovo con "(ignorato: nessuna voce rilevata)". Questi test tengono allineate le
due copie, cosi' la prossima taratura basta farla una volta.
"""
import re
import unittest

from localflow.audio import MIN_SPEECH_RMS
from localflow.config import DEFAULT_CONFIG, Config


class SpeechThresholdSingleSourceTests(unittest.TestCase):
    def test_dataclass_default_matches_calibrated_value(self):
        self.assertEqual(Config().min_speech_rms, MIN_SPEECH_RMS)

    def test_generated_config_file_matches_calibrated_value(self):
        match = re.search(r"^min_speech_rms\s*=\s*([0-9.]+)", DEFAULT_CONFIG, re.M)
        self.assertIsNotNone(match, "il modello di config.toml non dichiara min_speech_rms")
        self.assertEqual(float(match.group(1)), MIN_SPEECH_RMS)

    def test_threshold_sits_between_room_noise_and_quiet_voice(self):
        """Il margine che rende utile il cancello: sopra la stanza (~0.001 misurato
        sui clip del 21 ago), sotto la voce detta piano (0.003-0.006)."""
        self.assertGreater(MIN_SPEECH_RMS, 0.0015)
        self.assertLess(MIN_SPEECH_RMS, 0.006)
