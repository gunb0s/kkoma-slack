from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from kkoma_slack.semantle_engine import today_puzzle


class DateRuleTest(unittest.TestCase):
    def test_first_day_is_zero(self):
        dt = datetime(2022, 4, 1, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertEqual(today_puzzle(dt), 0)

    def test_next_day_is_one(self):
        dt = datetime(2022, 4, 2, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertEqual(today_puzzle(dt), 1)


if __name__ == "__main__":
    unittest.main()
