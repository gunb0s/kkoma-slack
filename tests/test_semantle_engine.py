import unittest

from kkoma_slack.semantle_engine import RemoteSemantleEngine


class RemoteSemantleEngineTest(unittest.TestCase):
    def test_answer_uses_top_scores_key(self):
        engine = RemoteSemantleEngine("https://example.test")
        requested_paths = []

        def read_json(path):
            requested_paths.append(path)
            return {"key": "정답"}

        engine._read_json = read_json

        self.assertEqual(engine.answer(123), "정답")
        self.assertEqual(requested_paths, ["/top_scores/123"])


if __name__ == "__main__":
    unittest.main()
