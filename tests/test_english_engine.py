from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from kkoma_slack.semantle_engine import EnglishSemantleEngine, UnknownWordError


def make_engine(tmpdir, secrets=("apple", "banana", "cherry")):
    data_dir = Path(tmpdir)
    (data_dir / "near").mkdir(parents=True, exist_ok=True)
    (data_dir / "secrets.txt").write_text("\n".join(secrets) + "\n", encoding="utf-8")
    return EnglishSemantleEngine(data_dir, base_url="https://example.test")


SECRET_VEC = [1.0, 0.0, 0.0] + [0.0] * 297
NEAR_VEC = [0.8, 0.6, 0.0] + [0.0] * 297
FAR_VEC = [0.0, 0.0, 1.0] + [0.0] * 297


class EnglishSemantleEngineTest(unittest.TestCase):
    def test_guess_uses_percentile_for_rank(self):
        with TemporaryDirectory() as tmpdir:
            engine = make_engine(tmpdir)

            def fake_read(path):
                if path == "/model2/apple/apple":
                    return '{"percentile":1000,"vec":%s}' % SECRET_VEC
                if path == "/model2/apple/banana":
                    return '{"percentile":818,"vec":%s}' % NEAR_VEC
                raise AssertionError(path)

            engine._read_remote = fake_read
            result = engine.guess("banana", day=0)
            self.assertEqual(result.rank, "182위")  # 1000 - 818
            self.assertFalse(result.is_answer)
            self.assertAlmostEqual(result.similarity, 0.8, places=5)

    def test_guess_far_word_out_of_rank(self):
        with TemporaryDirectory() as tmpdir:
            engine = make_engine(tmpdir)

            def fake_read(path):
                if path == "/model2/apple/apple":
                    return '{"percentile":1000,"vec":%s}' % SECRET_VEC
                if path == "/model2/apple/the":
                    return '{"vec":%s}' % FAR_VEC  # no percentile key
                raise AssertionError(path)

            engine._read_remote = fake_read
            result = engine.guess("the", day=0)
            self.assertEqual(result.rank, "1000위 이상")

    def test_guess_answer_short_circuits(self):
        with TemporaryDirectory() as tmpdir:
            engine = make_engine(tmpdir)
            engine._read_remote = lambda path: (_ for _ in ()).throw(AssertionError("no HTTP"))
            result = engine.guess("apple", day=0)
            self.assertTrue(result.is_answer)
            self.assertEqual(result.rank, "정답!")
            self.assertEqual(result.similarity, 1.0)

    def test_unknown_word_raises(self):
        with TemporaryDirectory() as tmpdir:
            engine = make_engine(tmpdir)

            def fake_read(path):
                if path == "/model2/apple/apple":
                    return '{"percentile":1000,"vec":%s}' % SECRET_VEC
                if path == "/model2/apple/zzqqxx":
                    return ""  # empty body = unknown
                raise AssertionError(path)

            engine._read_remote = fake_read
            with self.assertRaises(UnknownWordError):
                engine.guess("zzqqxx", day=0)

    def test_top_scores_parses_nearby_html_and_caches(self):
        with TemporaryDirectory() as tmpdir:
            engine = make_engine(tmpdir)
            html = (
                "<table>"
                "<tr><td> 999</td><td> apples</td><td> 79.02</td></tr>"
                "<tr><td> 818</td><td> banana</td><td> 39.43</td></tr>"
                "<tr><td> 1</td><td> couvert</td><td> 30.23</td></tr>"
                "</table>"
            )
            calls = []

            def fake_read(path):
                calls.append(path)
                return html

            engine._read_remote = fake_read
            scores = engine.top_scores(day=0)
            self.assertEqual([s.rank for s in scores], [1, 182, 999])
            self.assertEqual(scores[0].word, "apples")
            self.assertAlmostEqual(scores[0].similarity, 0.7902, places=4)

            # cached in memory + file: second call does not hit HTTP
            scores2 = engine.top_scores(day=0)
            self.assertEqual(len(calls), 1)
            self.assertEqual([s.rank for s in scores2], [1, 182, 999])

    def test_today_is_within_range(self):
        with TemporaryDirectory() as tmpdir:
            engine = make_engine(tmpdir)
            self.assertIn(engine.today(), range(len(engine.secrets)))


if __name__ == "__main__":
    unittest.main()
