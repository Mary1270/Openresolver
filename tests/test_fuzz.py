"""Deterministic random walk over the whole protocol.

After EVERY transaction: bank accounting is exact, GEN is conserved, registry and
engine hold nothing, no emitted message failed unexpectedly, and every finished
question has an empty ledger.
"""
import json
import random
import unittest

from helpers import Stack, W, Rollback, ConsensusFailure, REG, ENG, BANK, eoa
from helpers import OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE, ALL_EOAS
from helpers import URL_P, URL_D, URL_T, QUOTE_YES, QUOTE_NO, TEXT_YES, TEXT_NO, DOMAINS, llm_const

TYPES = [
    ("BINARY", [], ["YES", "NO"]),
    ("CATEGORICAL", ["red", "green", "blue"], ["red", "green", "blue"]),
    ("BUCKETED_RANGE", [10, 20, 30], ["0", "1", "2", "3"]),
    ("SPLIT", [eoa(41), eoa(42), eoa(43)], ["5000,3000,2000", "4000,3000,3000", "10000,0,0"]),
]
FINAL = ("FINAL_OPTIMISTIC", "FINAL_CONSENSUS", "CANCELLED")
USERS = [CREATOR, ALICE, BOB, CAROL, DAVE, EVE]


class Fuzz(unittest.TestCase):
    def run_seed(self, seed, steps, shuffle=False):
        rnd = random.Random(seed)
        st = Stack()
        W.rng = random.Random(seed * 7 + 1) if shuffle else None
        qids = []
        tried = {"ok": 0, "rollback": 0}

        def attempt(fn):
            try:
                fn()
                tried["ok"] += 1
            except Rollback:
                tried["rollback"] += 1

        def pick():
            return rnd.choice(qids) if qids else None

        def set_llm(q):
            atype, options, answers = TYPES[int(q["id"][1:]) % len(TYPES)]
            kind = rnd.random()
            if kind < 0.15:
                W.llm = llm_const("INVALID", "")
            elif kind < 0.25:
                W.llm = llm_const(rnd.choice(answers), "an invented passage that is not in the evidence")
            else:
                W.llm = llm_const(rnd.choice(answers), rnd.choice([QUOTE_YES, QUOTE_NO]))
            W.validator_llm = {}
            if rnd.random() < 0.1:
                W.validator_llm = {0: llm_const("INVALID", ""), 1: llm_const("INVALID", "")}

        for step in range(steps):
            action = rnd.choice(["create", "create", "propose", "propose", "dispute", "appeal", "finalize",
                                 "open_case", "freeze", "pub_ev", "judge", "pub_v", "open_appeal", "timeout",
                                 "cancel", "withdraw", "advance", "advance", "fund_noise",
                                 "drive", "drive", "drive", "drive", "drive"])
            who = rnd.choice(USERS)
            qid = pick()
            if action == "create" and len(qids) < 8:
                atype, options, answers = TYPES[len(qids) % len(TYPES)]
                bounty = rnd.choice([REG.MIN_BOUNTY, 2 * REG.MIN_BOUNTY, REG.MIN_BOUNTY - 1])
                now = W.time

                def f():
                    r = W.tx(who, st.reg, "create_question", "Q%d?" % len(qids), atype, json.dumps(options),
                             json.dumps(DOMAINS), now + rnd.choice([10, 100, 1000]), now + 10000, 3600, "", "",
                             value=bounty)
                    if r.startswith("q"):
                        qids.append(r)
                attempt(f)
            elif qid is None:
                continue
            else:
                q = st.question(qid)
                atype, options, answers = TYPES[int(qid[1:]) % len(TYPES)]
                bond_choice = rnd.choice([1.0, 1.0, 1.0, 0.5, 2.0])
                if action == "drive":
                    status = q["status"]
                    if status == "OPEN":
                        attempt(lambda: W.tx(ALICE, st.reg, "propose", qid, rnd.choice(answers),
                                             json.dumps([URL_P]), value=REG.PROPOSER_BOND))
                    elif status == "PROPOSED":
                        attempt(lambda: W.tx(BOB, st.reg, "dispute", qid, rnd.choice(answers),
                                             json.dumps([URL_D]), value=REG.DISPUTE_BOND))
                    elif status in ("DISPUTED", "TIMEOUT_PENDING"):
                        for m in ("open_case", "freeze_evidence", "publish_evidence"):
                            attempt(lambda: W.tx(CAROL, st.eng, m, qid))
                    elif status == "EVIDENCE_FROZEN":
                        set_llm(q)
                        attempt(lambda: W.tx(CAROL, st.eng, "judge", qid))
                        attempt(lambda: W.tx(CAROL, st.eng, "publish_verdict", qid, 1))
                    elif status == "JUDGED":
                        if rnd.random() < 0.5:
                            attempt(lambda: W.tx(BOB, st.reg, "appeal", qid, value=REG.APPEAL_BOND))
                            attempt(lambda: W.tx(ALICE, st.reg, "appeal", qid, value=REG.APPEAL_BOND))
                        else:
                            W.advance(q["challenge_window"] + 1)
                            attempt(lambda: W.tx(CAROL, st.reg, "finalize", qid))
                    elif status == "APPEALED":
                        set_llm(q)
                        attempt(lambda: W.tx(CAROL, st.eng, "open_appeal", qid))
                        attempt(lambda: W.tx(CAROL, st.eng, "judge", qid))
                        attempt(lambda: W.tx(CAROL, st.eng, "publish_verdict", qid, 2))
                elif action == "propose":
                    value = int(REG.PROPOSER_BOND * rnd.choice([1, 1, 2]) * bond_choice)
                    urls = rnd.choice([[URL_P], [URL_P], ["https://evil.org/x"], [URL_P, URL_T]])
                    attempt(lambda: W.tx(who, st.reg, "propose", qid, rnd.choice(answers + ["bogus"]),
                                         json.dumps(urls), value=value))
                elif action == "dispute":
                    value = int(REG.DISPUTE_BOND * 2 * bond_choice)
                    attempt(lambda: W.tx(who, st.reg, "dispute", qid, rnd.choice(answers + ["INVALID"]),
                                         json.dumps([URL_D]), value=value))
                elif action == "appeal":
                    value = int(REG.APPEAL_BOND * bond_choice)
                    attempt(lambda: W.tx(who, st.reg, "appeal", qid, value=value))
                elif action == "finalize":
                    attempt(lambda: W.tx(who, st.reg, "finalize", qid))
                elif action == "open_case":
                    attempt(lambda: W.tx(who, st.eng, "open_case", qid))
                elif action == "freeze":
                    attempt(lambda: W.tx(who, st.eng, "freeze_evidence", qid))
                elif action == "pub_ev":
                    attempt(lambda: W.tx(who, st.eng, "publish_evidence", qid))
                elif action == "judge":
                    set_llm(q)
                    attempt(lambda: W.tx(who, st.eng, "judge", qid))
                elif action == "pub_v":
                    attempt(lambda: W.tx(who, st.eng, "publish_verdict", qid, rnd.choice([1, 1, 2])))
                elif action == "open_appeal":
                    attempt(lambda: W.tx(who, st.eng, "open_appeal", qid))
                elif action == "timeout":
                    attempt(lambda: st.trigger_timeout(qid, who, json.dumps([URL_T])))
                elif action == "cancel":
                    attempt(lambda: W.tx(who, st.reg, "cancel_question", qid))
                elif action == "withdraw":
                    attempt(lambda: W.tx(who, st.bank, "withdraw"))
                elif action == "advance":
                    W.advance(rnd.choice([1, 60, 3601, 3601, 20000]))
                elif action == "fund_noise":
                    attempt(lambda: W.tx(who, st.bank, "lock_bounty", qid, who, value=rnd.choice([0, 5])))
            W.failed_messages[:] = [m for m in W.failed_messages if m[1] not in (
                "record_evidence", "record_verdict")]
            self.assertEqual(W.failed_messages, [], (seed, step, action))
            st.check_invariants(self)
            for qid_ in qids:
                status = st.status(qid_)
                if status in FINAL:
                    self.assertEqual(st.bv("get_bounty", qid_), 0, (seed, step, qid_, status))
                    for kind in "PDAT":
                        self.assertEqual(st.bv("get_bond", qid_, kind), 0, (seed, step, qid_, status, kind))
        return tried, st, qids

    def test_random_walks_hold_every_invariant(self):
        for seed in range(12):
            tried, st, qids = self.run_seed(seed, 350)
            self.assertGreater(tried["ok"], 50, seed)

    def test_random_walks_with_out_of_order_message_delivery(self):
        try:
            for seed in range(12):
                tried, st, qids = self.run_seed(500 + seed, 300, shuffle=True)
                self.assertGreater(tried["ok"], 50, seed)
        finally:
            W.rng = None

    def test_walk_reaches_finals_of_every_kind(self):
        seen = set()
        for seed in range(40):
            tried, st, qids = self.run_seed(1000 + seed, 250)
            for q in qids:
                r = st.resolution(q)
                if r["final"]:
                    seen.add(r["path"])
        self.assertIn("OPTIMISTIC", seen)
        self.assertIn("CONSENSUS", seen)

    def test_everything_withdrawn_leaves_empty_ledger(self):
        tried, st, qids = self.run_seed(77, 400)
        for _ in range(3):
            for q in qids:
                W.advance(40000)
                for fn in (lambda: W.tx(CAROL, st.reg, "finalize", q),):
                    try:
                        fn()
                    except Rollback:
                        pass
        for a in ALL_EOAS + [st.reg._addr]:
            try:
                W.tx(a, st.bank, "withdraw")
            except Rollback:
                pass
        acct = st.accounting()
        self.assertTrue(acct["ok"])
        self.assertEqual(acct["claimable_total"], 0)


if __name__ == "__main__":
    unittest.main()
