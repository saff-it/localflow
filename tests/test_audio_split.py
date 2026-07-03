import unittest

import numpy as np

from localflow.audio import split_on_silence

SR = 16000


def tone(seconds, level=0.5):
    t = np.arange(int(seconds * SR)) / SR
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds, level=0.005):
    return (level * np.random.randn(int(seconds * SR))).astype(np.float32)


class SplitOnSilenceTests(unittest.TestCase):
    def test_short_clip_untouched(self):
        clip = tone(10)
        parts = split_on_silence(clip, SR)
        self.assertEqual(len(parts), 1)
        self.assertEqual(len(parts[0]), len(clip))

    def test_long_clip_cut_in_the_quiet_gap(self):
        # 24s speech + 1s near-silence + 15s speech = 40s: the gap sits inside
        # the search window (20-28s), so the cut must land in it.
        clip = np.concatenate([tone(24), silence(1), tone(15)])
        parts = split_on_silence(clip, SR)
        self.assertEqual(len(parts), 2)
        cut = len(parts[0])
        gap_start, gap_end = 24 * SR, 25 * SR
        self.assertTrue(gap_start <= cut <= gap_end, "cut at %.1fs, gap is 24-25s" % (cut / SR))

    def test_no_samples_lost(self):
        clip = np.concatenate([tone(29), silence(0.5), tone(29), silence(0.5), tone(10)])
        parts = split_on_silence(clip, SR)
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual(sum(len(p) for p in parts), len(clip))


if __name__ == "__main__":
    unittest.main()
