from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kkoma_slack.semantle_engine import GuessResult, TopScore
from kkoma_slack.slack_app import Game, ensure_signing_configured, handle_slash_command
from kkoma_slack.storage import StateStore


class FakeEngine:
    def __init__(self, today=10, answer="사과"):
        self._today = today
        self._answer = answer

    def today(self):
        return self._today

    def answer(self, day=None):
        return self._answer

    def guess(self, word, day=None):
        if word == self._answer:
            similarity, rank, is_answer = 1.0, "정답!", True
        elif word.startswith("멀"):
            similarity, rank, is_answer = 0.1, "1000위 이상", False
        else:
            similarity, rank, is_answer = 0.42, "1000위 이상", False
        return GuessResult(day=self._today, guess=word, similarity=similarity, rank=rank, is_answer=is_answer)

    def top_scores(self, day=None):
        return [
            TopScore(rank=rank, word=f"힌트{rank}", similarity=0.9 - rank / 2000)
            for rank in range(1, 1001)
        ]


def kkoma_games(engine=None):
    engine = engine or FakeEngine()
    return {"kkoma": Game(key="kkoma", command="kkoma", display_name="꼬맨틀", engine=engine)}


def dispatch(games, store, text, command="/kkoma", **form):
    payload = {
        "command": command,
        "team_id": "T1",
        "channel_id": "C1",
        "user_id": "U1",
        "text": text,
        **form,
    }
    return handle_slash_command(payload, games, store)


def started_store(tmpdir, games):
    store = StateStore(Path(tmpdir) / "state.db")
    dispatch(games, store, "start")
    return store


class SlackAppTest(unittest.TestCase):
    def test_guess_before_start_is_refused(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = StateStore(Path(tmpdir) / "state.db")
            response = dispatch(games, store, "바나나")
            self.assertIn("먼저 `/kkoma start`", response["text"])
            self.assertEqual(response["response_type"], "ephemeral")
            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 10), 0)

    def test_welcome_is_shared_and_ephemeral(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = StateStore(Path(tmpdir) / "state.db")
            kk = dispatch(games, store, "welcome")
            sema = dispatch(games, store, "안내")
            self.assertEqual(kk["response_type"], "ephemeral")
            self.assertEqual(kk["text"], sema["text"])
            self.assertIn("start", kk["text"])
            self.assertIn("동시에", kk["text"])
            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 10), 0)

    def test_guess_records_and_renders_public_response(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            response = dispatch(games, store, "바나나", user_name="보성")

            self.assertEqual(response["response_type"], "in_channel")
            self.assertIn("바나나", response["text"])
            self.assertIn("TOP 1", response["text"])
            self.assertIn("*보성*", response["text"])
            self.assertNotIn("<@", response["text"])
            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 10), 1)

    def test_answer_marks_solved(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            response = dispatch(games, store, "사과", user_name="보성")

            self.assertIn("정답입니다!", response["text"])
            self.assertIn("축하", response["text"])
            self.assertIn("<@U1>", response["text"])
            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 10), 1)

    def test_top_renders_ten_entries(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            for index in range(25):
                dispatch(games, store, f"단어{index}", user_id=f"U{index}")

            response = dispatch(games, store, "top")
            self.assertIn("TOP 10", response["text"])
            self.assertIn("총 25개 추측", response["text"])
            self.assertIn("10. `단어9`", response["text"])
            self.assertNotIn("11. `단어10`", response["text"])

    def test_hint_is_stored_per_level(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            first = dispatch(games, store, "hint strong")
            second = dispatch(games, store, "hint strong", user_id="U2")

            self.assertIn("strong", first["text"])
            self.assertIn("공개했습니다", first["text"])
            self.assertIn("이미 공개됐습니다", second["text"])
            stored = store.hint("kkoma", "T1", "C1", 10, "strong")
            self.assertIn(f"`{stored.word}`", first["text"])
            self.assertIn(f"`{stored.word}`", second["text"])

    def test_unranked_high_similarity_shows_near_label(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            response = dispatch(games, store, "호러")
            self.assertIn("후보엔 없지만 가까워요", response["text"])
            self.assertNotIn("1000위 이상", response["text"])

    def test_unranked_low_similarity_keeps_default(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            response = dispatch(games, store, "멀고먼단어")
            self.assertIn("1000위 이상", response["text"])
            self.assertNotIn("후보엔 없지만 가까워요", response["text"])

    def test_hint_defaults_to_medium(self):
        with TemporaryDirectory() as tmpdir:
            games = kkoma_games()
            store = started_store(tmpdir, games)
            response = dispatch(games, store, "hint")
            stored = store.hint("kkoma", "T1", "C1", 10, "medium")
            self.assertIsNotNone(stored)
            self.assertIn("medium", response["text"])


class DispatchRoutingTest(unittest.TestCase):
    def _games(self):
        return {
            "kkoma": Game("kkoma", "kkoma", "꼬맨틀", FakeEngine(today=10, answer="사과")),
            "sema": Game("sema", "sema", "semantle", FakeEngine(today=99, answer="apple")),
        }

    def test_routes_to_engine_and_namespace_by_command(self):
        with TemporaryDirectory() as tmpdir:
            games = self._games()
            store = StateStore(Path(tmpdir) / "state.db")
            kk = handle_slash_command(
                {"command": "/kkoma", "team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "start"},
                games, store,
            )
            self.assertIn("꼬맨틀 #10", kk["text"])

            sema_start = handle_slash_command(
                {"command": "/sema", "team_id": "T1", "channel_id": "C2", "user_id": "U1", "text": "start"},
                games, store,
            )
            self.assertIn("semantle #99", sema_start["text"])

            handle_slash_command(
                {"command": "/sema", "team_id": "T1", "channel_id": "C2", "user_id": "U1", "text": "banana"},
                games, store,
            )
            self.assertEqual(store.guess_count("sema", "T1", "C2", 99), 1)
            self.assertEqual(store.guess_count("kkoma", "T1", "C2", 99), 0)


class ConcurrencyLockTest(unittest.TestCase):
    def _games(self):
        return {
            "kkoma": Game("kkoma", "kkoma", "꼬맨틀", FakeEngine(today=10, answer="사과")),
            "sema": Game("sema", "sema", "semantle", FakeEngine(today=99, answer="apple")),
        }

    def _start(self, games, store, command, channel="C1"):
        return handle_slash_command(
            {"command": command, "team_id": "T1", "channel_id": channel, "user_id": "U1", "text": "start"},
            games, store,
        )

    def test_start_sema_refused_while_kkoma_active(self):
        with TemporaryDirectory() as tmpdir:
            games = self._games()
            store = StateStore(Path(tmpdir) / "state.db")
            self._start(games, store, "/kkoma")
            resp = self._start(games, store, "/sema")
            self.assertIn("진행 중", resp["text"])
            self.assertIn("꼬맨틀", resp["text"])
            self.assertFalse(store.has_game("sema", "T1", "C1", 99))

    def test_start_kkoma_refused_while_sema_active(self):
        with TemporaryDirectory() as tmpdir:
            games = self._games()
            store = StateStore(Path(tmpdir) / "state.db")
            self._start(games, store, "/sema")
            resp = self._start(games, store, "/kkoma")
            self.assertIn("진행 중", resp["text"])
            self.assertIn("semantle", resp["text"])

    def test_solving_releases_lock(self):
        with TemporaryDirectory() as tmpdir:
            games = self._games()
            store = StateStore(Path(tmpdir) / "state.db")
            self._start(games, store, "/kkoma")
            handle_slash_command(
                {"command": "/kkoma", "team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "사과"},
                games, store,
            )
            resp = self._start(games, store, "/sema")
            self.assertIn("semantle #99 시작", resp["text"])

    def test_giveup_releases_lock(self):
        with TemporaryDirectory() as tmpdir:
            games = self._games()
            store = StateStore(Path(tmpdir) / "state.db")
            self._start(games, store, "/sema")
            handle_slash_command(
                {"command": "/sema", "team_id": "T1", "channel_id": "C1", "user_id": "U1", "text": "giveup"},
                games, store,
            )
            resp = self._start(games, store, "/kkoma")
            self.assertIn("꼬맨틀 #10 시작", resp["text"])

    def test_day_rollover_releases_lock(self):
        with TemporaryDirectory() as tmpdir:
            games = self._games()
            store = StateStore(Path(tmpdir) / "state.db")
            # kkoma active on yesterday's day (9), but engine.today() is 10 now
            store.ensure_game("kkoma", "T1", "C1", 9, "U1")
            resp = self._start(games, store, "/sema")
            self.assertIn("semantle #99 시작", resp["text"])


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
