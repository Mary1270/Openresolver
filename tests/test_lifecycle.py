import json
import unittest

from helpers import *  # noqa: F401,F403
from helpers import Base, Stack, W, Rollback, REG, BANK, ENG, GEN, eoa
from helpers import OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE
from helpers import URL_P, URL_D, URL_T, QUOTE_YES, QUOTE_NO, TEXT_YES, TEXT_NO, llm_const

PB = REG.PROPOSER_BOND
DB = REG.DISPUTE_BOND
AB = REG.APPEAL_BOND
SHARE = REG.WINNER_SHARE_PCT
BOUNTY = 2 * REG.MIN_BOUNTY


def share(amount):
    return amount * SHARE // 100


class OptimisticPath(Base):
    def test_unchallenged_proposal_becomes_final_and_pays_bounty(self):
        st = self.st
        qid = st.create()
        self.assertEqual(st.status(qid), "OPEN")
        self.assertEqual(st.bv("get_bounty", qid), BOUNTY)
        self.assertEqual(st.propose(qid), "PROPOSED")
        self.assertEqual(st.bv("get_bond", qid, "P"), PB)
        self.assertEqual(st.accounting()["locked_total"], BOUNTY + PB)
        with self.assertRaises(Rollback):
            st.finalize(qid)
        st.wait_challenge(qid)
        self.assertEqual(st.finalize(qid), "FINAL_OPTIMISTIC")
        res = st.resolution(qid)
        self.assertEqual((res["status"], res["final"], res["answer"], res["path"]),
                         ("FINAL_OPTIMISTIC", True, "YES", "OPTIMISTIC"))
        self.assertEqual(st.claimable(ALICE), PB + BOUNTY)
        self.assertEqual(st.rep(ALICE), 110)
        before = W.balance_of(ALICE)
        st.withdraw(ALICE)
        self.assertEqual(W.balance_of(ALICE), before + PB + BOUNTY)
        self.assertEqual(st.accounting()["locked_total"], 0)
        self.assertEqual(st.accounting()["claimable_total"], 0)

    def test_not_final_before_window_and_double_finalize_rejected(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        self.assertFalse(st.resolution(qid)["final"])
        self.assertEqual(st.resolution(qid)["answer"], "")
        st.wait_challenge(qid)
        st.finalize(qid)
        with self.assertRaises(Rollback):
            st.finalize(qid)

    def test_second_proposal_rejected_and_refunded(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        res = st.propose(qid, who=DAVE)
        self.assertTrue(res.startswith("REJECTED:"))
        self.assertEqual(st.claimable(DAVE), PB)
        st.withdraw(DAVE)
        self.assertEqual(st.claimable(DAVE), 0)

    def test_too_early_and_too_late_proposals(self):
        st = self.st
        qid = st.create()
        res = W.tx(ALICE, st.reg, "propose", qid, "YES", json.dumps([URL_P]), value=PB)
        self.assertIn("too early", res)
        self.assertEqual(st.claimable(ALICE), PB)
        q = st.question(qid)
        W.time = q["response_deadline"] + 1
        res = W.tx(BOB, st.reg, "propose", qid, "YES", json.dumps([URL_P]), value=PB)
        self.assertIn("deadline", res)

    def test_creator_cannot_propose_or_dispute(self):
        st = self.st
        qid = st.create()
        res = st.propose(qid, who=CREATOR)
        self.assertIn("creator", res)
        st.propose(qid)
        res = st.dispute(qid, who=CREATOR)
        self.assertIn("creator", res)

    def test_bond_below_required_is_refunded(self):
        st = self.st
        qid = st.create()
        res = st.propose(qid, value=PB - 1)
        self.assertIn("bond below", res)
        self.assertEqual(st.claimable(ALICE), PB - 1)
        self.assertEqual(st.status(qid), "OPEN")

    def test_overpaying_bond_is_fully_locked_and_returned(self):
        st = self.st
        qid = st.create()
        st.propose(qid, value=3 * PB)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.claimable(ALICE), 3 * PB + BOUNTY)


class DisputedPath(Base):
    def _disputed(self, atype="BINARY", options=None, pa="YES", da="NO"):
        st = self.st
        qid = st.create(atype=atype, options=options)
        self.assertEqual(st.propose(qid, answer=pa), "PROPOSED")
        self.assertEqual(st.dispute(qid, answer=da), "DISPUTED")
        return qid

    def test_dispute_upheld_disputer_wins(self):
        st = self.st
        qid = self._disputed()
        self.assertEqual(st.status(qid), "DISPUTED")
        self.assertEqual(st.engine_first_round(qid, "NO", QUOTE_NO), "NO")
        self.assertEqual(st.status(qid), "JUDGED")
        self.assertFalse(st.resolution(qid)["final"])
        with self.assertRaises(Rollback):
            st.finalize(qid)
        st.wait_challenge(qid)
        st.finalize(qid)
        res = st.resolution(qid)
        self.assertEqual((res["status"], res["final"], res["answer"], res["path"]),
                         ("FINAL_CONSENSUS", True, "NO", "CONSENSUS"))
        self.assertEqual(st.claimable(BOB), DB + share(PB) + BOUNTY)
        self.assertEqual(st.claimable(ALICE), 0)
        self.assertEqual(st.claimable(CREATOR), PB - share(PB))
        self.assertEqual(st.rep(BOB), 110)
        self.assertEqual(st.rep(ALICE), 80)
        self.assertEqual(st.accounting()["locked_total"], 0)

    def test_dispute_rejected_proposer_wins(self):
        st = self.st
        qid = self._disputed()
        st.engine_first_round(qid, "YES", QUOTE_YES)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.resolution(qid)["answer"], "YES")
        self.assertEqual(st.claimable(ALICE), PB + share(DB) + BOUNTY)
        self.assertEqual(st.claimable(BOB), 0)
        self.assertEqual(st.claimable(CREATOR), DB - share(DB))
        self.assertEqual(st.rep(ALICE), 110)
        self.assertEqual(st.rep(BOB), 80)

    def test_invalid_returns_both_bonds_and_refunds_bounty(self):
        st = self.st
        qid = self._disputed()
        st.engine_first_round(qid, "INVALID", "")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.resolution(qid)["answer"], "INVALID")
        self.assertTrue(st.resolution(qid)["final"])
        self.assertEqual(st.claimable(ALICE), PB)
        self.assertEqual(st.claimable(BOB), DB)
        self.assertEqual(st.claimable(CREATOR), BOUNTY)
        self.assertEqual(st.rep(ALICE), 100)
        self.assertEqual(st.rep(BOB), 100)

    def test_missing_quote_backstop_forces_invalid(self):
        st = self.st
        qid = self._disputed()
        st.engine_first_round(qid, "NO", "this sentence is not in any evidence at all")
        case = json.loads(W.view(st.eng, "get_case", qid))
        self.assertEqual(case["answer_1"], "INVALID")
        self.assertEqual(case["quote_1"], "")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.resolution(qid)["answer"], "INVALID")
        self.assertEqual(st.claimable(CREATOR), BOUNTY)

    def test_empty_or_short_quote_backstop(self):
        for quote in ("", "short", None, 5, "x" * 500):
            st = Stack()
            qid = st.create()
            st.propose(qid)
            st.dispute(qid)
            st.engine_first_round(qid, "NO", quote)
            case = json.loads(W.view(st.eng, "get_case", qid))
            self.assertEqual(case["answer_1"], "INVALID", quote)

    def test_third_answer_neither_side_returns_bonds(self):
        st = self.st
        qid = st.create(atype="CATEGORICAL", options=["red", "green", "blue"])
        st.propose(qid, answer="red")
        st.dispute(qid, answer="green")
        st.engine_first_round(qid, "blue", QUOTE_YES)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.resolution(qid)["answer"], "blue")
        self.assertEqual(st.claimable(ALICE), PB)
        self.assertEqual(st.claimable(BOB), DB)
        self.assertEqual(st.claimable(CREATOR), BOUNTY)

    def test_dispute_window_and_participants(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        self.assertIn("proposer", st.dispute(qid, who=ALICE))
        self.assertIn("different", st.dispute(qid, answer="YES"))
        self.assertIn("different", st.dispute(qid, answer="INVALID"))
        self.assertIn("different", st.dispute(qid, answer="maybe"))
        W.advance(st.question(qid)["challenge_window"] + 1)
        self.assertIn("closed", st.dispute(qid))

    def test_bonds_are_exact_and_excess_is_refunded_so_a_whale_cannot_price_out_disputers(self):
        st = self.st
        qid = st.create()
        st.propose(qid, value=5 * DB)
        self.assertEqual(st.question(qid)["proposer_bond"], PB)
        self.assertEqual(st.bv("get_bond", qid, "P"), PB)
        self.assertEqual(st.claimable(ALICE), 5 * DB - PB)
        self.assertIn("bond below", st.dispute(qid, value=DB - 1))
        self.assertEqual(st.dispute(qid, value=DB + 7), "DISPUTED")
        self.assertEqual(st.bv("get_bond", qid, "D"), DB)
        self.assertEqual(st.claimable(BOB), DB - 1 + 7)


class AppealPath(Base):
    def _to_judged(self, first_answer, first_quote):
        st = self.st
        qid = st.create()
        st.propose(qid, answer="YES")
        st.dispute(qid, answer="NO")
        st.engine_first_round(qid, first_answer, first_quote)
        self.assertEqual(st.status(qid), "JUDGED")
        return qid

    def test_appeal_flips_verdict_refunds_appeal_bond(self):
        st = self.st
        qid = self._to_judged("YES", QUOTE_YES)
        self.assertEqual(st.appeal(qid, who=BOB), "APPEALED")
        self.assertEqual(st.status(qid), "APPEALED")
        self.assertEqual(st.engine_appeal_round(qid, "NO", QUOTE_NO), "NO")
        res = st.resolution(qid)
        self.assertEqual((res["status"], res["final"], res["answer"]), ("FINAL_CONSENSUS", True, "NO"))
        self.assertEqual(st.claimable(BOB), AB + DB + share(PB) + BOUNTY)
        self.assertEqual(st.claimable(ALICE), 0)
        self.assertEqual(st.rep(BOB), 110)
        self.assertEqual(st.rep(ALICE), 80)

    def test_appeal_upheld_appellant_loses_bond(self):
        st = self.st
        qid = self._to_judged("YES", QUOTE_YES)
        st.appeal(qid, who=BOB)
        st.engine_appeal_round(qid, "YES", QUOTE_YES)
        self.assertEqual(st.resolution(qid)["answer"], "YES")
        self.assertEqual(st.claimable(ALICE), PB + share(DB) + BOUNTY + share(AB))
        self.assertEqual(st.claimable(BOB), 0)
        self.assertEqual(st.claimable(CREATOR), (DB - share(DB)) + (AB - share(AB)))
        self.assertEqual(st.rep(BOB), 60)
        self.assertEqual(st.rep(ALICE), 110)

    def test_winner_cannot_appeal(self):
        st = self.st
        qid = self._to_judged("YES", QUOTE_YES)
        self.assertIn("lost", st.appeal(qid, who=ALICE, value=AB))
        self.assertIn("lost", st.appeal(qid, who=DAVE, value=AB))
        self.assertEqual(st.status(qid), "JUDGED")

    def test_appeal_bond_and_window(self):
        st = self.st
        qid = self._to_judged("YES", QUOTE_YES)
        self.assertIn("bond below", st.appeal(qid, who=BOB, value=AB - 1))
        W.advance(st.question(qid)["challenge_window"] + 1)
        self.assertIn("closed", st.appeal(qid, who=BOB))

    def test_second_appeal_rejected(self):
        st = self.st
        qid = self._to_judged("YES", QUOTE_YES)
        st.appeal(qid, who=BOB)
        self.assertIn("JUDGED", st.appeal(qid, who=BOB))
        st.engine_appeal_round(qid, "NO", QUOTE_NO)
        self.assertIn("JUDGED", st.appeal(qid, who=ALICE))

    def test_appeal_after_invalid_verdict_either_side(self):
        st = self.st
        qid = self._to_judged("INVALID", "")
        self.assertEqual(st.appeal(qid, who=ALICE), "APPEALED")
        st.engine_appeal_round(qid, "YES", QUOTE_YES)
        self.assertEqual(st.resolution(qid)["answer"], "YES")
        self.assertEqual(st.claimable(ALICE), AB + PB + share(DB) + BOUNTY)

    def test_appeal_confirms_invalid_bond_goes_to_beneficiary_not_the_other_loser(self):
        st = self.st
        qid = self._to_judged("INVALID", "")
        st.appeal(qid, who=ALICE)
        st.engine_appeal_round(qid, "INVALID", "")
        self.assertEqual(st.claimable(BOB), DB)
        self.assertEqual(st.claimable(ALICE), PB)
        self.assertEqual(st.claimable(CREATOR), BOUNTY + AB)
        self.assertEqual(st.rep(ALICE), 80)

    def test_engine_needs_registry_appeal_before_round_two(self):
        st = self.st
        qid = self._to_judged("YES", QUOTE_YES)
        with self.assertRaises(Rollback):
            W.tx(CAROL, st.eng, "open_appeal", qid)
        with self.assertRaises(Rollback):
            W.tx(CAROL, st.eng, "judge", qid)


class TimeoutAndCancel(Base):
    def _expired(self):
        st = self.st
        qid = st.create()
        W.time = st.question(qid)["response_deadline"] + 1
        return qid

    def test_status_reports_unresolved_timeout(self):
        st = self.st
        qid = self._expired()
        res = st.resolution(qid)
        self.assertEqual(res["status"], "UNRESOLVED_TIMEOUT")
        self.assertFalse(res["final"])

    def test_timeout_resolution_pays_trigger_caller_after_the_appeal_window(self):
        st = self.st
        qid = self._expired()
        self.assertEqual(st.trigger_timeout(qid, CAROL, json.dumps([URL_T])), "TIMEOUT_PENDING")
        W.llm = llm_const("YES", QUOTE_YES)
        st.run_evidence(qid, who=DAVE)
        st.judge_publish(qid, 1, who=DAVE)
        res = st.resolution(qid)
        self.assertEqual((res["status"], res["final"], res["path"]), ("JUDGED", False, "TIMEOUT"))
        self.assertEqual(st.claimable(CAROL), 0)
        with self.assertRaises(Rollback):
            st.finalize(qid)
        st.wait_challenge(qid)
        st.finalize(qid)
        res = st.resolution(qid)
        self.assertEqual((res["status"], res["final"], res["answer"], res["path"]),
                         ("FINAL_CONSENSUS", True, "YES", "TIMEOUT"))
        self.assertEqual(st.claimable(CAROL), BOUNTY + PB)
        self.assertEqual(st.rep(CAROL), 110)

    def test_timeout_invalid_refunds_creator(self):
        st = self.st
        qid = self._expired()
        st.trigger_timeout(qid, CAROL, json.dumps([URL_T]))
        W.llm = llm_const("INVALID", "")
        st.run_evidence(qid)
        st.judge_publish(qid)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.resolution(qid)["answer"], "INVALID")
        self.assertEqual(st.claimable(CREATOR), BOUNTY)
        self.assertEqual(st.claimable(CAROL), PB)
        self.assertEqual(st.rep(CAROL), 100)

    def test_timeout_guards(self):
        st = self.st
        qid = st.create()
        self.assertIn("deadline not reached", st.trigger_timeout(qid, CAROL, json.dumps([URL_T])))
        W.time = st.question(qid)["response_deadline"] + 1
        self.assertIn("creator", st.trigger_timeout(qid, CREATOR, json.dumps([URL_T])))
        self.assertIn("domain", st.trigger_timeout(qid, CAROL, json.dumps(["https://evil.org/x"])))
        self.assertIn("evidence", st.trigger_timeout(qid, CAROL, "[]"))
        self.assertIn("bond below", st.trigger_timeout(qid, CAROL, json.dumps([URL_T]), value=PB - 1))
        self.assertEqual(st.trigger_timeout(qid, CAROL, json.dumps([URL_T])), "TIMEOUT_PENDING")
        self.assertIn("not open", st.trigger_timeout(qid, DAVE, json.dumps([URL_T])))
        with self.assertRaises(Rollback):
            W.tx(CREATOR, st.reg, "cancel_question", qid)
        refunds = PB - 1 + 5 * PB
        self.assertEqual(st.claimable(CAROL) + st.claimable(CREATOR) + st.claimable(DAVE), refunds)
        self.assertEqual(st.bv("get_bond", qid, "T"), PB)
        for who in (CAROL, CREATOR, DAVE):
            st.withdraw(who)

    def _timeout_judged(self, answer="YES", quote=QUOTE_YES):
        st = self.st
        qid = self._expired()
        st.trigger_timeout(qid, CAROL, json.dumps([URL_T]))
        W.llm = llm_const(answer, quote)
        st.run_evidence(qid)
        st.judge_publish(qid)
        return qid

    def test_timeout_verdict_can_be_appealed_and_flipped(self):
        st = self.st
        qid = self._timeout_judged()
        self.assertEqual(st.appeal(qid, who=DAVE), "APPEALED")
        st.engine_appeal_round(qid, "NO", QUOTE_YES)
        res = st.resolution(qid)
        self.assertEqual((res["status"], res["answer"], res["path"]), ("FINAL_CONSENSUS", "NO", "TIMEOUT"))
        self.assertEqual(st.claimable(DAVE), AB)
        self.assertEqual(st.claimable(CAROL), BOUNTY + PB)

    def test_timeout_appeal_upheld_pays_the_caller(self):
        st = self.st
        qid = self._timeout_judged()
        st.appeal(qid, who=DAVE)
        st.engine_appeal_round(qid, "YES", QUOTE_YES)
        self.assertEqual(st.claimable(DAVE), 0)
        self.assertEqual(st.claimable(CAROL), BOUNTY + PB + share(AB))
        self.assertEqual(st.claimable(CREATOR), AB - share(AB))
        self.assertEqual(st.rep(DAVE), 80)

    def test_timeout_caller_appealing_their_own_result_forfeits_to_the_beneficiary(self):
        st = self.st
        qid = self._timeout_judged()
        st.appeal(qid, who=CAROL)
        st.engine_appeal_round(qid, "YES", QUOTE_YES)
        self.assertEqual(st.claimable(CREATOR), AB)
        self.assertEqual(st.claimable(CAROL), BOUNTY + PB)

    def test_creator_cannot_appeal_a_timeout_verdict(self):
        st = self.st
        qid = self._timeout_judged()
        self.assertIn("lost", st.appeal(qid, who=CREATOR))
        self.assertEqual(st.status(qid), "JUDGED")

    def test_creator_cancel_refunds_bounty(self):
        st = self.st
        qid = st.create()
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.reg, "cancel_question", qid)
        self.assertEqual(W.tx(CREATOR, st.reg, "cancel_question", qid), "CANCELLED")
        self.assertEqual(st.status(qid), "CANCELLED")
        self.assertEqual(st.claimable(CREATOR), BOUNTY)
        res = st.propose(qid)
        self.assertTrue(res.startswith("REJECTED"))
        with self.assertRaises(Rollback):
            W.tx(CREATOR, st.reg, "cancel_question", qid)

    def test_cancel_after_proposal_rejected(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        with self.assertRaises(Rollback):
            W.tx(CREATOR, st.reg, "cancel_question", qid)

    def test_creator_can_cancel_after_deadline_if_nobody_triggered(self):
        st = self.st
        qid = self._expired()
        W.tx(CREATOR, st.reg, "cancel_question", qid)
        self.assertEqual(st.claimable(CREATOR), BOUNTY)


class AnswerTypes(Base):
    CASES = [
        ("BINARY", [], "YES", "NO"),
        ("CATEGORICAL", ["red", "green", "blue"], "red", "green"),
        ("BUCKETED_RANGE", [10, 20, 30], "1", "2"),
        ("SPLIT", [eoa(21), eoa(22), eoa(23)], "5000,3000,2000", "4000,3000,3000"),
    ]

    def test_optimistic_for_every_type(self):
        for atype, options, pa, da in self.CASES:
            st = Stack()
            qid = st.create(atype=atype, options=options)
            self.assertEqual(st.propose(qid, answer=pa), "PROPOSED", atype)
            st.wait_challenge(qid)
            st.finalize(qid)
            self.assertEqual(st.resolution(qid)["answer"], pa)
            st.check_invariants(self)

    def test_disputed_both_directions_for_every_type(self):
        for atype, options, pa, da in self.CASES:
            for winner in ("P", "D"):
                st = Stack()
                qid = st.create(atype=atype, options=options)
                st.propose(qid, answer=pa)
                st.dispute(qid, answer=da)
                if winner == "P":
                    st.engine_first_round(qid, pa, QUOTE_YES)
                else:
                    st.engine_first_round(qid, da, QUOTE_NO)
                st.wait_challenge(qid)
                st.finalize(qid)
                self.assertEqual(st.resolution(qid)["answer"], pa if winner == "P" else da)
                self.assertEqual(st.accounting()["locked_total"], 0)
                st.check_invariants(self)

    def test_illegal_answers_rejected_with_refund(self):
        bad = {
            "BINARY": ["maybe", "", "INVALID", 5],
            "CATEGORICAL": ["purple", "RED", "red,green"],
            "BUCKETED_RANGE": ["4", "-1", "01", "1.0", "x", "²"],
            "SPLIT": ["5000,5000", "5500,2500,2000", "5000,3000,3000", "5000,3000,2000,0", "5000, 3000, 2000"],
        }
        for atype, options, pa, da in self.CASES:
            for ans in bad[atype]:
                st = Stack()
                qid = st.create(atype=atype, options=options)
                st.open_window(qid)
                try:
                    res = W.tx(ALICE, st.reg, "propose", qid, ans, json.dumps([URL_P]), value=PB)
                except Rollback:
                    res = "ROLLBACK"
                self.assertTrue(res.startswith("REJECTED") or res == "ROLLBACK", (atype, ans, res))
                self.assertEqual(st.status(qid), "OPEN")
                st.check_invariants(self)

    def test_binary_case_insensitive_normalised(self):
        st = self.st
        qid = st.create()
        st.propose(qid, answer=" yes ")
        self.assertEqual(st.question(qid)["proposer_answer"], "YES")

    def test_bucketed_boundaries(self):
        st = self.st
        qid = st.create(atype="BUCKETED_RANGE", options=[10, 20, 30])
        self.assertEqual(st.propose(qid, answer="3"), "PROPOSED")
        st2 = Stack()
        qid2 = st2.create(atype="BUCKETED_RANGE", options=[10, 20, 30])
        self.assertEqual(st2.propose(qid2, answer="0"), "PROPOSED")
        st2.check_invariants(self)


class Reputation(Base):
    def test_bond_multiplier_tiers_and_max_bounty(self):
        st = self.st
        reg = st.reg._addr
        self.assertEqual(st.bv("get_bond_multiplier_pct", DAVE), 100)
        for _ in range(3):
            W.tx(reg, st.bank, "rep_slash", DAVE)
        self.assertEqual(st.rep(DAVE), 40)
        self.assertEqual(st.bv("get_bond_multiplier_pct", DAVE), 200)
        W.tx(reg, st.bank, "rep_win", DAVE)
        W.tx(reg, st.bank, "rep_win", DAVE)
        self.assertEqual(st.rep(DAVE), 60)
        self.assertEqual(st.bv("get_bond_multiplier_pct", DAVE), 150)
        for _ in range(4):
            W.tx(reg, st.bank, "rep_win", DAVE)
        self.assertEqual(st.bv("get_bond_multiplier_pct", DAVE), 100)
        self.assertEqual(st.bv("get_max_bounty", DAVE), BANK.MAX_BOUNTY_BASE * (st.rep(DAVE) // 50 + 1))

    def test_reputation_bounds(self):
        st = self.st
        reg = st.reg._addr
        for _ in range(20):
            W.tx(reg, st.bank, "rep_slash", DAVE)
        self.assertEqual(st.rep(DAVE), 0)
        for _ in range(200):
            W.tx(reg, st.bank, "rep_win", EVE)
        self.assertEqual(st.rep(EVE), BANK.REP_MAX)

    def test_low_reputation_pays_higher_bond(self):
        st = self.st
        for _ in range(3):
            W.tx(st.reg._addr, st.bank, "rep_slash", ALICE)
        qid = st.create()
        st.open_window(qid)
        res = W.tx(ALICE, st.reg, "propose", qid, "YES", json.dumps([URL_P]), value=PB)
        self.assertIn("bond below required", res)
        self.assertEqual(st.propose(qid), "PROPOSED")
        self.assertEqual(st.bv("get_bond", qid, "P"), 2 * PB)

    def test_bounty_above_reputation_limit_blocks_proposal(self):
        st = self.st
        limit = st.bv("get_max_bounty", ALICE)
        qid = st.create(bounty=limit + 1)
        res = st.propose(qid)
        self.assertIn("reputation limit", res)
        self.assertEqual(st.claimable(ALICE), PB)

    def test_leaderboard(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        st.wait_challenge(qid)
        st.finalize(qid)
        board = json.loads(st.bv("list_resolvers"))
        self.assertEqual(board[0]["address"], ALICE)
        self.assertEqual(board[0]["reputation"], 110)
        self.assertEqual(board[0]["wins"], 1)
        one = json.loads(st.bv("get_resolver", ALICE))
        self.assertEqual(one["reputation"], 110)


class RefundOnReject(Base):
    def test_every_rejected_payable_is_recoverable_and_conserving(self):
        st = self.st
        qid = st.create()
        # create_question rejections
        bad_creates = [
            ("", "BINARY", "[]", json.dumps(["example.com"]), 1, 10 ** 6, 3600),
        ]
        now = W.time
        before = st.claimable(DAVE)
        res = W.tx(DAVE, st.reg, "create_question", "x" * 601, "BINARY", "[]", json.dumps(DOMAINS),
                   now + 100, now + 8000, 3600, "", "", value=BOUNTY)
        self.assertTrue(res.startswith("REJECTED"))
        res = W.tx(DAVE, st.reg, "create_question", "ok?", "BINARY", "[]", json.dumps(DOMAINS),
                   now + 100, now + 8000, 3600, "", "", value=REG.MIN_BOUNTY - 1)
        self.assertIn("minimum", res)
        self.assertEqual(st.claimable(DAVE), before + BOUNTY + REG.MIN_BOUNTY - 1)
        st.withdraw(DAVE)
        self.assertEqual(st.claimable(DAVE), 0)

    def test_create_validation_matrix(self):
        st = self.st
        now = W.time
        ok = dict(text="Q?", atype="BINARY", options=[], domains=DOMAINS, ra=now + 100, rd=now + 100 + 7200, cw=3600)
        cases = {
            "unknown type": dict(ok, atype="SCALAR"),
            "binary with options": dict(ok, options=["a", "b"]),
            "categorical too few": dict(ok, atype="CATEGORICAL", options=["a"]),
            "categorical too many": dict(ok, atype="CATEGORICAL", options=list("abcdefghi")),
            "categorical dup": dict(ok, atype="CATEGORICAL", options=["a", "a"]),
            "categorical bad id": dict(ok, atype="CATEGORICAL", options=["A!", "b"]),
            "categorical upper": dict(ok, atype="CATEGORICAL", options=["Aa", "b"]),
            "edges not increasing": dict(ok, atype="BUCKETED_RANGE", options=[5, 5]),
            "edges decreasing": dict(ok, atype="BUCKETED_RANGE", options=[9, 3]),
            "edges float": dict(ok, atype="BUCKETED_RANGE", options=[1.5, 3]),
            "edges bool": dict(ok, atype="BUCKETED_RANGE", options=[True, 3]),
            "edges none": dict(ok, atype="BUCKETED_RANGE", options=[]),
            "split one party": dict(ok, atype="SPLIT", options=[eoa(30)]),
            "split dup party": dict(ok, atype="SPLIT", options=[eoa(30), eoa(30)]),
            "split bad addr": dict(ok, atype="SPLIT", options=[eoa(30), "0x12"]),
            "no domains": dict(ok, domains=[]),
            "bad domain": dict(ok, domains=["not a domain"]),
            "dup domain": dict(ok, domains=["example.com", "example.com"]),
            "too many domains": dict(ok, domains=["a%d.com" % i for i in range(6)]),
            "resolve in past": dict(ok, ra=now - 1, rd=now - 1 + 7200),
            "response window too short": dict(ok, rd=ok["ra"] + 10),
            "response window too long": dict(ok, rd=ok["ra"] + REG.MAX_RESPONSE_WINDOW + 1),
            "challenge too short": dict(ok, cw=REG.MIN_CHALLENGE_WINDOW - 1),
            "challenge too long": dict(ok, cw=REG.MAX_CHALLENGE_WINDOW + 1),
            "empty text": dict(ok, text="   "),
        }
        for name, c in cases.items():
            res = W.tx(DAVE, st.reg, "create_question", c["text"], c["atype"], json.dumps(c["options"]),
                       json.dumps(c["domains"]), c["ra"], c["rd"], c["cw"], "", "", value=BOUNTY)
            self.assertTrue(res.startswith("REJECTED"), (name, res))
        self.assertEqual(st.claimable(DAVE), BOUNTY * len(cases))
        self.assertEqual(json.loads(st.rv("list_questions")), [])

    def test_malformed_json_inputs_rejected(self):
        st = self.st
        now = W.time
        res = W.tx(DAVE, st.reg, "create_question", "Q?", "BINARY", "{", json.dumps(DOMAINS),
                   now + 100, now + 8000, 3600, "", "", value=BOUNTY)
        self.assertIn("JSON", res)
        qid = st.create()
        st.open_window(qid)
        res = W.tx(ALICE, st.reg, "propose", qid, "YES", "not json", value=PB)
        self.assertTrue(res.startswith("REJECTED"))
        res = W.tx(ALICE, st.reg, "propose", "q999", "YES", json.dumps([URL_P]), value=PB)
        self.assertIn("unknown", res)
        self.assertEqual(st.claimable(ALICE), 2 * PB)


class Views(Base):
    def test_list_and_get(self):
        st = self.st
        a = st.create(text="First?")
        b = st.create(text="Second?")
        lst = json.loads(st.rv("list_questions"))
        self.assertEqual([x["id"] for x in lst], [b, a])
        self.assertEqual(lst[0]["status"], "OPEN")
        q = st.question(a)
        self.assertEqual(q["answer_type"], "BINARY")
        self.assertEqual(q["allowed_evidence_domains"], DOMAINS)
        self.assertEqual(len(q["spec_hash"]), 64)
        self.assertEqual(st.resolution("q404")["status"], "NOT_FOUND")
        with self.assertRaises(Rollback):
            st.rv("get_question", "q404")
        consts = json.loads(st.rv("get_constants"))
        self.assertEqual(consts["proposer_bond"], PB)

    def test_spec_hash_is_deterministic_and_unique(self):
        st = self.st
        a = st.create(text="Same?")
        b = st.create(text="Same?")
        c = st.create(text="Other?")
        self.assertNotEqual(st.question(c)["spec_hash"], st.question(a)["spec_hash"])
        self.assertEqual(len(st.question(b)["spec_hash"]), 64)

    def test_spec_is_frozen_no_edit_methods(self):
        public = [n for n in dir(REG.QuestionRegistry) if getattr(getattr(REG.QuestionRegistry, n), "_gl_kind", None) == "write"
                  or getattr(getattr(REG.QuestionRegistry, n), "_gl_kind", None) == "payable"]
        for name in public:
            self.assertFalse(name.startswith("edit") or name.startswith("update") or name.startswith("set_question"), name)


if __name__ == "__main__":
    unittest.main()
