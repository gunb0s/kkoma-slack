from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kkoma_slack.semantle_engine import GuessResult, TopScore
from kkoma_slack.slack_app import ensure_signing_configured, handle_slash_command
from kkoma_slack.storage import StateStore


class FakeEngine:
    def today(self):
        return 10

    def answer(self, day=None):
        return "사과"

    def guess(self, word, day=None):
        if word == "사과":
            similarity, rank, is_answer = 1.0, "정답!", True
        elif word.startswith("멀"):
            similarity, rank, is_answer = 0.1, "1000위 이상", False
        else:
            similarity, rank, is_answer = 0.42, "1000위 이상", False
        return GuessResult(day=10, guess=word, similarity=similarity, rank=rank, is_answer=is_answer)

    def top_scores(self, day=None):
        return [
            TopScore(rank=rank, word=f"힌트{rank}", similarity=0.9 - rank / 2000)
            for rank in range(1, 1001)
        ]


class SlackAppTest(unittest.TestCase):
    def test_guess_records_and_renders_public_response(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            response = handle_slash_command(
                {
                    "team_id": "T1",
                    "channel_id": "C1",
                    "user_id": "U1",
                    "user_name": "보성",
                    "text": "바나나",
                },
                FakeEngine(),
                store,
            )

            self.assertEqual(response["response_type"], "in_channel")
            self.assertIn("바나나", response["text"])
            self.assertIn("TOP 1", response["text"])
            self.assertIn("*보성*", response["text"])
            self.assertNotIn("<@", response["text"])
            self.assertEqual(store.guess_count("T1", "C1", 10), 1)

    def test_answer_marks_solved(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            response = handle_slash_command(
                {
                    "team_id": "T1",
                    "channel_id": "C1",
                    "user_id": "U1",
                    "user_name": "보성",
                    "text": "사과",
                },
                FakeEngine(),
                store,
            )

            self.assertIn("정답입니다!", response["text"])
            self.assertIn("축하", response["text"])
            self.assertIn("<@U1>", response["text"])
            self.assertEqual(store.guess_count("T1", "C1", 10), 1)

    def test_top_renders_twenty_entries(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            engine = FakeEngine()
            for index in range(25):
                handle_slash_command(
                    {
                        "team_id": "T1",
                        "channel_id": "C1",
                        "user_id": f"U{index}",
                        "text": f"단어{index}",
                    },
                    engine,
                    store,
                )

            response = handle_slash_command(
                {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "top"},
                engine,
                store,
            )

            self.assertIn("TOP 20", response["text"])
            self.assertIn("총 25개 추측", response["text"])
            self.assertIn("20. `단어19`", response["text"])
            self.assertNotIn("21. `단어20`", response["text"])

    def test_hint_is_stored_per_level(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            engine = FakeEngine()
            first = handle_slash_command(
                {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "hint strong"},
                engine,
                store,
            )
            second = handle_slash_command(
                {"team_id": "T1", "channel_id": "C1", "user_id": "U2", "text": "hint strong"},
                engine,
                store,
            )

            self.assertIn("strong", first["text"])
            self.assertIn("공개했습니다", first["text"])
            self.assertIn("이미 공개됐습니다", second["text"])
            stored = store.hint("T1", "C1", 10, "strong")
            self.assertIn(f"`{stored.word}`", first["text"])
            self.assertIn(f"`{stored.word}`", second["text"])

    def test_unranked_high_similarity_shows_near_label(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            response = handle_slash_command(
                {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "호러"},
                FakeEngine(),
                store,
            )

            self.assertIn("후보엔 없지만 가까워요", response["text"])
            self.assertNotIn("1000위 이상", response["text"])

    def test_unranked_low_similarity_keeps_default(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            response = handle_slash_command(
                {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "멀고먼단어"},
                FakeEngine(),
                store,
            )

            self.assertIn("1000위 이상", response["text"])
            self.assertNotIn("후보엔 없지만 가까워요", response["text"])

    def test_hint_defaults_to_medium(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            response = handle_slash_command(
                {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "hint"},
                FakeEngine(),
                store,
            )

            stored = store.hint("T1", "C1", 10, "medium")
            self.assertIsNotNone(stored)
            self.assertIn("medium", response["text"])


class SigningConfigTest(unittest.TestCase):
    def test_missing_secret_raises(self):
        with self.assertRaises(RuntimeError):
            ensure_signing_configured("", allow_unsigned=False)

    def test_secret_set_passes(self):
        ensure_signing_configured("real-secret", allow_unsigned=False)

    def test_allow_unsigned_bypasses(self):
        ensure_signing_configured("", allow_unsigned=True)


if __name__ == "__main__":
    unittest.main()
