import json
import unittest

from helpers import Base, Stack, W, Rollback, REG, BANK, ENG, GEN, eoa
from helpers import OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE
from helpers import URL_P, URL_D, QUOTE_YES, QUOTE_NO, llm_const

PB = REG.PROPOSER_BOND
STRANGERS = [OWNER, CREATOR, ALICE, BOB, CAROL]


class BankAccessControl(Base):
    def _calls(self):
        a = eoa(77)
        return [
            ("pay_bounty", ("q0", a)),
            ("refund_bounty", ("q0",)),
            ("return_bond", ("q0", "P")),
            ("award_bond", ("q0", "P", a, 80, a)),
            ("rep_win", (a,)),
            ("rep_slash", (a,)),
        ]

    def test_ledger_methods_reject_everyone_but_the_registry(self):
        st = self.st
        for sender in STRANGERS + [st.bank._addr, st.eng._addr]:
            for method, args in self._calls():
                with self.assertRaises(Rollback, msg=(sender, method)):
                    W.tx(sender, st.bank, method, *args)

    def test_registry_is_allowed(self):
        st = self.st
        for method, args in self._calls():
            W.tx(st.reg._addr, st.bank, method, *args)

    def test_engine_has_no_authority_over_funds(self):
        st = self.st
        self.assertNotIn("engine", json.loads(st.bv("get_wiring")))
        self.assertFalse(hasattr(BANK.ResolverBank, "set_engine"))

    def test_payable_methods_reject_without_value(self):
        st = self.st
        for method, args in [("lock_bounty", ("q0", ALICE)), ("lock_bond", ("q0", "P", ALICE)),
                             ("credit_refund", (ALICE,))]:
            for sender in STRANGERS:
                with self.assertRaises(Rollback, msg=(sender, method)):
                    W.tx(sender, st.bank, method, *args)

    def test_payable_methods_with_value_credit_the_sender_never_lock_funds(self):
        st = self.st
        for i, (method, args) in enumerate([("lock_bounty", ("q0", ALICE)), ("lock_bond", ("q0", "P", ALICE)),
                                            ("credit_refund", (ALICE,))]):
            res = W.tx(DAVE, st.bank, method, *args, value=1000)
            self.assertEqual(res, "REJECTED:unauthorized")
            self.assertEqual(st.claimable(DAVE), 1000 * (i + 1))
        self.assertEqual(st.bv("get_bounty", "q0"), 0)
        self.assertEqual(st.bv("get_bond", "q0", "P"), 0)
        self.assertEqual(st.accounting()["locked_total"], 0)
        st.withdraw(DAVE)

    def test_owner_has_no_power_over_funds(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        with self.assertRaises(Rollback):
            W.tx(OWNER, st.bank, "withdraw")
        for name in dir(BANK.ResolverBank):
            fn = getattr(BANK.ResolverBank, name)
            self.assertNotIn(name, ("sweep", "rescue", "set_owner", "transfer_ownership", "emergency_withdraw"))

    def test_private_helpers_are_not_callable(self):
        st = self.st
        for name in ("_credit", "_lock", "_unlock", "_touch", "_require_core"):
            with self.assertRaises(Rollback):
                W.tx(st.reg._addr, st.bank, name, ALICE, 1)

    def test_withdraw_only_pays_the_sender_and_only_once(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        st.wait_challenge(qid)
        st.finalize(qid)
        with self.assertRaises(Rollback):
            W.tx(BOB, st.bank, "withdraw")
        st.withdraw(ALICE)
        with self.assertRaises(Rollback):
            st.withdraw(ALICE)


class RegistryAccessControl(Base):
    def _disputed(self):
        st = self.st
        qid = st.create()
        st.propose(qid)
        st.dispute(qid)
        return qid

    def test_engine_results_only_from_engine(self):
        st = self.st
        qid = self._disputed()
        for sender in STRANGERS + [st.bank._addr, st.reg._addr]:
            with self.assertRaises(Rollback, msg=sender):
                W.tx(sender, st.reg, "record_evidence", qid, "a" * 64)
            with self.assertRaises(Rollback, msg=sender):
                W.tx(sender, st.reg, "record_verdict", qid, "NO", 1)
        self.assertEqual(st.status(qid), "DISPUTED")

    def test_verdict_cannot_skip_evidence_or_replay(self):
        st = self.st
        qid = self._disputed()
        eng = st.eng._addr
        with self.assertRaises(Rollback):
            W.tx(eng, st.reg, "record_verdict", qid, "NO", 1)
        W.tx(eng, st.reg, "record_evidence", qid, "b" * 64)
        with self.assertRaises(Rollback):
            W.tx(eng, st.reg, "record_evidence", qid, "b" * 64)
        with self.assertRaises(Rollback):
            W.tx(eng, st.reg, "record_verdict", qid, "NO", 2)
        with self.assertRaises(Rollback):
            W.tx(eng, st.reg, "record_verdict", qid, "maybe", 1)
        W.tx(eng, st.reg, "record_verdict", qid, "NO", 1)
        with self.assertRaises(Rollback):
            W.tx(eng, st.reg, "record_verdict", qid, "YES", 1)
        self.assertEqual(st.status(qid), "JUDGED")

    def test_engine_cannot_record_on_an_open_or_proposed_question(self):
        st = self.st
        qid = st.create()
        with self.assertRaises(Rollback):
            W.tx(st.eng._addr, st.reg, "record_evidence", qid, "b" * 64)
        st.propose(qid)
        with self.assertRaises(Rollback):
            W.tx(st.eng._addr, st.reg, "record_evidence", qid, "b" * 64)
        with self.assertRaises(Rollback):
            W.tx(st.eng._addr, st.reg, "record_verdict", qid, "YES", 1)

    def test_a_fake_engine_cannot_resolve_anything(self):
        st = self.st
        qid = self._disputed()
        fake = W.deploy(ENG.ResolverEngine, EVE)
        W.tx(EVE, fake, "set_registry", st.reg._addr)
        W.llm = llm_const("NO", QUOTE_NO)
        W.tx(EVE, fake, "open_case", qid)
        W.tx(EVE, fake, "freeze_evidence", qid)
        W.tx(EVE, fake, "publish_evidence", qid)
        self.assertEqual(st.status(qid), "DISPUTED")
        self.assertEqual(len(W.failed_messages), 1)
        W.tx(EVE, fake, "judge", qid)
        W.tx(EVE, fake, "publish_verdict", qid, 1)
        self.assertEqual(st.status(qid), "DISPUTED")
        self.assertEqual(len(W.failed_messages), 2)
        W.failed_messages[:] = []

    def test_a_fake_bank_registry_pair_cannot_touch_real_funds(self):
        st = self.st
        qid = self._disputed()
        fake_reg = W.deploy(REG.QuestionRegistry, EVE)
        W.tx(EVE, fake_reg, "set_bank", st.bank._addr)
        W.tx(EVE, fake_reg, "set_engine", EVE)
        with self.assertRaises(Rollback):
            W.tx(fake_reg._addr, st.bank, "return_bond", qid, "P")
        qid2 = W.tx(EVE, fake_reg, "create_question", "Q?", "BINARY", "[]", json.dumps(["example.com"]),
                    W.time + 100, W.time + 8000, 3600, "", "", value=2 * REG.MIN_BOUNTY)
        self.assertEqual(qid2, "q0")
        self.assertEqual(st.bv("get_bounty", "q0"), 2 * PB)
        self.assertEqual(st.bv("get_bounty", qid2), 2 * PB)
        self.assertEqual(st.claimable(fake_reg._addr), 2 * REG.MIN_BOUNTY)
        self.assertEqual(st.accounting()["locked_total"], 2 * REG.MIN_BOUNTY + PB + REG.DISPUTE_BOND)
        self.assertEqual(W.balance_of(fake_reg._addr), 0)

    def test_non_payable_methods_reject_value(self):
        st = self.st
        qid = st.create()
        for method, args in [("finalize", (qid,)), ("cancel_question", (qid,)),
                             ("set_bank", (eoa(5),))]:
            with self.assertRaises(Rollback, msg=method):
                W.tx(CREATOR, st.reg, method, *args, value=1)

    def test_private_helpers_are_not_callable(self):
        st = self.st
        qid = self._disputed()
        for name, args in [("_settle_disputed", (qid, "YES")), ("_reject", (ALICE, 1, "x")), ("_get", (qid,))]:
            with self.assertRaises(Rollback):
                W.tx(ALICE, st.reg, name, *args)


class WiringIsOneTimeAndOwnerOnly(unittest.TestCase):
    def setUp(self):
        W.reset()
        for a in (OWNER, ALICE):
            W.fund(a, 10 * GEN)
        self.bank = W.deploy(BANK.ResolverBank, OWNER)
        self.reg = W.deploy(REG.QuestionRegistry, OWNER)
        self.eng = W.deploy(ENG.ResolverEngine, OWNER)

    def test_each_link_is_owner_only_once_then_frozen(self):
        links = [(self.bank, "set_registry", self.reg),
                 (self.reg, "set_bank", self.bank), (self.reg, "set_engine", self.eng),
                 (self.eng, "set_registry", self.reg)]
        for contract, method, target in links:
            with self.assertRaises(Rollback):
                W.tx(ALICE, contract, method, target._addr)
            with self.assertRaises(Rollback):
                W.tx(OWNER, contract, method, "0x1234")
            with self.assertRaises(Rollback):
                W.tx(OWNER, contract, method, 5)
            W.tx(OWNER, contract, method, target._addr)
            with self.assertRaises(Rollback):
                W.tx(OWNER, contract, method, target._addr)
            with self.assertRaises(Rollback):
                W.tx(OWNER, contract, method, eoa(9))

    def test_registry_refuses_to_work_before_wiring_and_refund_is_not_needed(self):
        now = W.time
        with self.assertRaises(Rollback):
            W.tx(ALICE, self.reg, "create_question", "Q?", "BINARY", "[]", json.dumps(["example.com"]),
                 now + 100, now + 8000, 3600)
        self.assertEqual(W.balance_of(self.reg._addr), 0)

    def test_engine_refuses_before_wiring(self):
        with self.assertRaises(Rollback):
            W.tx(ALICE, self.eng, "open_case", "q0")

    def test_wiring_views(self):
        W.tx(OWNER, self.bank, "set_registry", self.reg._addr)
        w = json.loads(W.view(self.bank, "get_wiring"))
        self.assertTrue(w["registry_set"])
        self.assertEqual(w["registry"], self.reg._addr)
        w = json.loads(W.view(self.reg, "get_wiring"))
        self.assertFalse(w["bank_set"])
        self.assertFalse(w["engine_set"])


if __name__ == "__main__":
    unittest.main()
