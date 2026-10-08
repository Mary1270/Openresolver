import json

from helpers import Base, Stack, W, Rollback, REG, eoa
from helpers import OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE, DOMAINS
from helpers import URL_P, URL_D, QUOTE_YES, QUOTE_NO

PB = REG.PROPOSER_BOND
DB = REG.DISPUTE_BOND
BOUNTY = 2 * REG.MIN_BOUNTY


def make(st, creator=CREATOR, beneficiary="", ref="", value=BOUNTY):
    now = W.time
    return W.tx(creator, st.reg, "create_question", "Who won?", "BINARY", "[]", json.dumps(DOMAINS),
                now + 100, now + 8000, 3600, beneficiary, ref, value=value)


class Beneficiary(Base):
    def test_default_beneficiary_is_the_creator(self):
        st = self.st
        qid = make(st)
        self.assertEqual(st.question(qid)["beneficiary"], CREATOR)
        self.assertEqual(st.question(qid)["creator"], CREATOR)

    def test_bounty_refund_and_loser_bond_remainder_go_to_beneficiary(self):
        st = self.st
        qid = make(st, beneficiary=DAVE)
        st.propose(qid)
        st.dispute(qid)
        st.engine_first_round(qid, "YES", QUOTE_YES)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.claimable(DAVE), DB - DB * REG.WINNER_SHARE_PCT // 100)
        self.assertEqual(st.claimable(CREATOR), 0)

    def test_invalid_refunds_the_bounty_to_the_beneficiary(self):
        st = self.st
        qid = make(st, beneficiary=DAVE)
        st.propose(qid)
        st.dispute(qid)
        st.engine_first_round(qid, "INVALID", "")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(st.claimable(DAVE), BOUNTY)

    def test_cancel_refunds_the_beneficiary_but_only_the_creator_can_cancel(self):
        st = self.st
        qid = make(st, beneficiary=DAVE)
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.reg, "cancel_question", qid)
        W.tx(CREATOR, st.reg, "cancel_question", qid)
        self.assertEqual(st.claimable(DAVE), BOUNTY)

    def test_invalid_beneficiary_is_rejected_with_refund(self):
        st = self.st
        for bad in ("0x12", "not-an-address", "0x" + "g" * 40):
            res = make(st, creator=DAVE, beneficiary=bad)
            self.assertIn("beneficiary", res)
        self.assertEqual(st.claimable(DAVE), 3 * BOUNTY)
        st.withdraw(DAVE)

    def test_beneficiary_is_case_normalised(self):
        st = self.st
        qid = make(st, beneficiary=DAVE.upper().replace("0X", "0x"))
        self.assertEqual(st.question(qid)["beneficiary"], DAVE)


class RefIndex(Base):
    def test_ref_lookup_and_uniqueness_per_creator(self):
        st = self.st
        a = make(st, ref="market-1")
        self.assertEqual(st.rv("get_question_id_by_ref", CREATOR, "market-1"), a)
        self.assertEqual(st.rv("get_question_id_by_ref", CREATOR.upper().replace("0X", "0x"), "market-1"), a)
        self.assertEqual(st.rv("get_question_id_by_ref", ALICE, "market-1"), "")
        self.assertEqual(st.rv("get_question_id_by_ref", CREATOR, "nope"), "")
        res = make(st, ref="market-1")
        self.assertIn("ref already used", res)
        b = make(st, creator=ALICE, ref="market-1")
        self.assertEqual(st.rv("get_question_id_by_ref", ALICE, "market-1"), b)
        self.assertEqual(st.claimable(CREATOR), BOUNTY)

    def test_bad_refs_rejected(self):
        st = self.st
        for ref in ("a b", "x" * 65, "ü", "a|b", "a/b"):
            res = make(st, creator=EVE, ref=ref)
            self.assertIn("invalid ref", res, ref)
        self.assertEqual(st.claimable(EVE), 5 * BOUNTY)
        st.withdraw(EVE)

    def test_empty_ref_creates_no_index_entry(self):
        st = self.st
        make(st)
        self.assertEqual(st.rv("get_question_id_by_ref", CREATOR, ""), "")

    def test_a_squatter_cannot_take_another_creators_ref(self):
        st = self.st
        make(st, creator=EVE, ref="m0")
        self.assertEqual(st.rv("get_question_id_by_ref", CREATOR, "m0"), "")
        a = make(st, creator=CREATOR, ref="m0")
        self.assertEqual(st.rv("get_question_id_by_ref", CREATOR, "m0"), a)
