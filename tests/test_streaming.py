import time
import unittest

import numpy as np

from localflow.audio import find_quiet_cut
from localflow.streaming import StreamingSession

SR = 16000


def tone(seconds, level=0.5):
    t = np.arange(int(seconds * SR)) / SR
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds, level=0.005):
    return (level * np.random.randn(int(seconds * SR))).astype(np.float32)


class FakeTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, sample_rate=16000, prompt=None):
        self.calls.append({"secs": len(audio) / sample_rate, "prompt": prompt or ""})
        return ("blocco%d." % len(self.calls), "it")


def wait_done(session, fake, n, timeout=5.0):
    deadline = time.time() + timeout
    while len(fake.calls) < n and time.time() < deadline:
        time.sleep(0.02)


class FindQuietCutTests(unittest.TestCase):
    def test_cut_lands_in_the_gap(self):
        clip = np.concatenate([tone(5), silence(0.6), tone(1.4)])
        cut = find_quiet_cut(clip, SR, search_seconds=3.0)
        self.assertTrue(5 * SR <= cut <= int(5.6 * SR), "cut at %.2fs" % (cut / SR))


class StreamingSessionTests(unittest.TestCase):
    def test_short_dictation_single_chunk(self):
        fake = FakeTranscriber()
        s = StreamingSession(fake, SR, chunk_seconds=7)
        s.feed(tone(3))
        texts, lang, _ = s.finish(tone(1))
        self.assertEqual(texts, ["blocco1."])
        self.assertEqual(lang, "it")
        self.assertAlmostEqual(s.total_seconds, 4.0, places=1)

    def test_long_dictation_commits_while_feeding(self):
        fake = FakeTranscriber()
        s = StreamingSession(fake, SR, chunk_seconds=6, base_prompt="Glossario: n8n.")
        # 5s speech + pause + 5s speech: the cut should happen once we pass 6s
        s.feed(tone(5))
        s.feed(silence(0.5))
        s.feed(tone(5))
        wait_done(s, fake, 1)
        self.assertEqual(len(fake.calls), 1, "first chunk should be committed mid-dictation")
        texts, _, _ = s.finish(tone(2))
        self.assertEqual(texts, ["blocco1.", "blocco2."])
        # rolling context: the second call must see the first chunk's text
        self.assertIn("blocco1.", fake.calls[1]["prompt"])
        self.assertIn("Glossario", fake.calls[1]["prompt"])
        # no audio lost across the cut
        self.assertAlmostEqual(sum(c["secs"] for c in fake.calls), s.total_seconds, places=1)

    def test_post_process_translates_but_asr_context_stays_raw(self):
        fake = FakeTranscriber()
        s = StreamingSession(fake, SR, chunk_seconds=6,
                             post_process=lambda text, done: text.upper())
        s.feed(tone(5))
        s.feed(silence(0.5))
        s.feed(tone(5))
        wait_done(s, fake, 1)
        texts, _, _ = s.finish(tone(2))
        self.assertEqual(texts, ["BLOCCO1.", "BLOCCO2."])  # output post-processed
        # ...but the rolling ASR context must stay in the RAW (spoken) language
        self.assertIn("blocco1.", fake.calls[1]["prompt"])
        self.assertNotIn("BLOCCO1.", fake.calls[1]["prompt"])

    def test_silence_only_is_never_transcribed(self):
        fake = FakeTranscriber()
        s = StreamingSession(fake, SR, chunk_seconds=5)
        s.feed(np.zeros(8 * SR, dtype=np.float32))
        texts, _, _ = s.finish(np.zeros(SR, dtype=np.float32))
        self.assertEqual(texts, [])
        self.assertEqual(fake.calls, [])


class QuietSpeechIsNeverLostSilentlyTests(unittest.TestCase):
    """A chunk of QUIET speech falls under the anti-hallucination gate and is
    dropped. Dropping it is fine; dropping it in silence is not: the caller
    must be told, so it can redo the dictation the classic way instead of
    pasting a quarter of what was said."""

    def test_quiet_chunk_is_reported_as_suspect(self):
        fake = FakeTranscriber()
        s = StreamingSession(fake, SR, chunk_seconds=6)
        s.feed(tone(6, level=0.5))          # normal voice
        wait_done(s, fake, 1)
        s.feed(tone(14, level=0.0056))      # same speech, spoken quietly: rms ~0.004
        wait_done(s, fake, 2, timeout=1.0)
        texts, _, _ = s.finish(tone(2, level=0.5))
        transcribed = sum(c["secs"] for c in fake.calls)
        self.assertLess(transcribed, s.total_seconds - 5,
                        "quiet-only chunks reached the model instead of being gated")
        self.assertGreaterEqual(s.suspect_drops, 1, "a quiet-speech chunk was dropped unreported")
        self.assertGreaterEqual(s.dropped_seconds, 6.0)

    def test_real_silence_is_not_suspect(self):
        """A long thinking pause must not trigger a pointless redo."""
        fake = FakeTranscriber()
        s = StreamingSession(fake, SR, chunk_seconds=5)
        s.feed(tone(5, level=0.5))
        wait_done(s, fake, 1)
        s.feed(silence(6, level=0.0009))    # the user's measured room noise floor
        texts, _, _ = s.finish(tone(1, level=0.5))
        self.assertEqual(s.suspect_drops, 0, "room noise must not look like lost speech")


if __name__ == "__main__":
    unittest.main()
