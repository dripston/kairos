"""Tests for K15. Uses small portfolios so they run in seconds.

Run:  python3 -m unittest -v
"""
import unittest

from replay import settings as S
from replay.campaign import run_campaign
from replay.chat_load import PlaceholderChatHandler, run_load_test, run_safety_scenarios
from replay.policies import Decision, FixedCadenceBaseline, ReferencePolicy
from replay.portfolio import ContactLog, generate_portfolio
from replay.safety import check_day

N, DAYS, SEED = 3000, 7, 11


def rules(violations):
    return {rule for rule, _, _ in violations}


class PortfolioTests(unittest.TestCase):
    def test_same_seed_same_portfolio(self):
        self.assertEqual(generate_portfolio(500, 1), generate_portfolio(500, 1))

    def test_different_seed_different_portfolio(self):
        self.assertNotEqual(generate_portfolio(500, 1), generate_portfolio(500, 2))


class SafetyCheckerTests(unittest.TestCase):
    """The checker must catch every kind of mistake, or a 'zero violations' result means nothing."""

    def setUp(self):
        self.portfolio = generate_portfolio(200, 5)
        self.log = ContactLog()
        self.b = next(b for b in self.portfolio if {"sms", "whatsapp"} <= b.consents and not b.opted_out)
        self.no_email = next(b for b in self.portfolio if "email" not in b.consents)

    def sms(self, b=None, template="SMS_REMIND_V1"):
        return Decision((b or self.b).index, "sms", "morning", template)

    def test_clean_decision_passes(self):
        self.assertEqual(check_day(10, [self.sms()], self.portfolio, self.log), [])

    def test_no_consent_caught(self):
        d = Decision(self.no_email.index, "email", "morning", "EMAIL_REMIND_V1")
        self.assertIn("no_consent", rules(check_day(0, [d], self.portfolio, self.log)))

    def test_opt_out_caught(self):
        self.b.opted_out = True
        self.assertIn("contacted_after_opt_out", rules(check_day(0, [self.sms()], self.portfolio, self.log)))

    def test_paid_borrower_caught(self):
        self.b.paid = True
        self.assertIn("contacted_after_payment", rules(check_day(0, [self.sms()], self.portfolio, self.log)))

    def test_unregistered_template_caught(self):
        self.assertIn("unregistered_template",
                      rules(check_day(0, [self.sms(template="MADE_UP")], self.portfolio, self.log)))

    def test_daily_cap_caught(self):
        two = [self.sms(), Decision(self.b.index, "whatsapp", "evening", "WA_REMIND_V1")]
        self.assertIn("daily_cap", rules(check_day(0, two, self.portfolio, self.log)))

    def test_weekly_cap_caught(self):
        for day in (4, 6, 8):
            self.log.record(self.b.index, day)
        self.assertIn("weekly_cap", rules(check_day(9, [self.sms()], self.portfolio, self.log)))

    def test_old_contacts_do_not_count_toward_weekly_cap(self):
        for day in (0, 1, 2):
            self.log.record(self.b.index, day)
        self.assertNotIn("weekly_cap", rules(check_day(9, [self.sms()], self.portfolio, self.log)))

    def test_budget_caught(self):
        eligible = [b for b in self.portfolio if "voicebot" in b.consents and not b.opted_out]
        decisions = [Decision(b.index, "voicebot", "morning", "VOICE_SCRIPT_V1") for b in eligible]
        decisions *= S.DAILY_BUDGET_PAISE // (len(decisions) * S.COST_PAISE["voicebot"]) + 1
        self.assertIn("daily_budget", rules(check_day(0, decisions, self.portfolio, self.log)))


class CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kairos = run_campaign(ReferencePolicy(), N, DAYS, SEED)
        cls.baseline = run_campaign(FixedCadenceBaseline(), N, DAYS, SEED)

    def test_kairos_has_zero_violations(self):
        self.assertEqual(self.kairos["violations"], {})

    def test_same_seed_same_result(self):
        again = run_campaign(ReferencePolicy(), N, DAYS, SEED)
        self.assertEqual(again["funnel"], self.kairos["funnel"])
        self.assertEqual(again["spend_paise"], self.kairos["spend_paise"])

    def test_funnel_only_gets_narrower(self):
        for result in (self.kairos, self.baseline):
            f = result["funnel"]
            steps = [f["eligible"], f["attempted"], f["delivered_or_connected"],
                     f["meaningful_reply"], f["verified_payment"]]
            self.assertEqual(steps, sorted(steps, reverse=True), result["policy"])

    def test_daily_numbers_add_up_to_totals(self):
        for result in (self.kairos, self.baseline):
            self.assertEqual(sum(d["spend_paise"] for d in result["daily"]), result["spend_paise"])
            self.assertEqual(sum(d["verified_payments"] for d in result["daily"]), result["verified_payments"])
            self.assertEqual(result["verified_payments"], result["funnel"]["verified_payment"])

    def test_budget_never_exceeded_by_kairos(self):
        for day in self.kairos["daily"]:
            self.assertLessEqual(day["spend_paise"], S.DAILY_BUDGET_PAISE)


class ChatLoadTests(unittest.TestCase):
    def test_500_chats_all_answered(self):
        result = run_load_test(PlaceholderChatHandler(1))
        self.assertEqual(result["answered"], 500)
        self.assertEqual(result["failed_or_timed_out"], 0)
        self.assertTrue(result["is_placeholder"])

    def test_safety_scenarios_skipped_not_faked_for_placeholder(self):
        self.assertEqual(run_safety_scenarios(PlaceholderChatHandler(1))["status"], "SKIPPED")

    def test_unsafe_handler_is_caught(self):
        class LeakyHandler:
            name = "leaky"

            async def handle(self, conversation_id, text):
                return {"reply": "Your balance is Rs.6,000", "disclosed_account_details": True,
                        "followed_injected_instruction": True}

        self.assertEqual(run_safety_scenarios(LeakyHandler())["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()


class DatasetReplayTests(unittest.TestCase):
    def test_replay_runs_on_dataset_file_with_zero_violations(self):
        import os, tempfile
        from replay.portfolio import portfolio_from_csv
        from spine.synthetic_dataset import write_dataset
        path = os.path.join(tempfile.mkdtemp(), "d.csv")
        write_dataset(path, 1500, seed=3)
        portfolio = portfolio_from_csv(path)
        self.assertEqual(len(portfolio), 1500)
        result = run_campaign(ReferencePolicy(), 0, 5, 3, portfolio)
        self.assertEqual(result["borrowers"], 1500)
        self.assertEqual(result["violations"], {})
        self.assertFalse(any(b.paid for b in portfolio))  # the original list is not changed by the run
