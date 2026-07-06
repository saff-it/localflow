import tempfile
import unittest
import pathlib

from localflow.asst_memory import AssistantMemory


class AssistantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "mem.md"

    def test_append_and_reload_roundtrip(self):
        m = AssistantMemory(self.tmp)
        m.append("user", "come si dice cane in inglese")
        m.append("assistant", "Si dice dog.")
        m.append("user", "e gatto?")
        m.append("assistant", "Cat.")
        turns = AssistantMemory(self.tmp).recent_turns()  # fresh instance = survives restart
        self.assertEqual(turns[0], {"role": "user", "content": "come si dice cane in inglese"})
        self.assertEqual(turns[-1], {"role": "assistant", "content": "Cat."})
        self.assertEqual(len(turns), 4)

    def test_multiline_answer_preserved(self):
        m = AssistantMemory(self.tmp)
        m.append("assistant", "Prima riga.\nSeconda riga.")
        turns = m.recent_turns()
        self.assertEqual(turns[-1]["content"], "Prima riga.\nSeconda riga.")

    def test_recent_turns_bounded(self):
        m = AssistantMemory(self.tmp)
        for i in range(40):
            m.append("user", "domanda %d" % i)
            m.append("assistant", "risposta %d" % i)
        turns = m.recent_turns(max_turns=5)
        self.assertEqual(len(turns), 10)
        self.assertEqual(turns[-1]["content"], "risposta 39")


if __name__ == "__main__":
    unittest.main()
