import unittest

from kkoma_slack.commands import parse_command


class ParseCommandTest(unittest.TestCase):
    def test_guess_without_keyword(self):
        parsed = parse_command("사과")
        self.assertEqual(parsed.action, "guess")
        self.assertEqual(parsed.word, "사과")

    def test_guess_with_keyword(self):
        parsed = parse_command("guess  사과")
        self.assertEqual(parsed.action, "guess")
        self.assertEqual(parsed.word, "사과")

    def test_korean_aliases(self):
        self.assertEqual(parse_command("시작").action, "start")
        self.assertEqual(parse_command("순위").action, "top")
        self.assertEqual(parse_command("포기").action, "giveup")


if __name__ == "__main__":
    unittest.main()
