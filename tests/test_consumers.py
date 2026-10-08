import json
import random
import unittest

from helpers import Stack, W, Rollback, REG, BANK, GEN, eoa
from helpers import OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE, ALL_EOAS
from helpers import URL_P, URL_D, URL_T, QUOTE_YES, QUOTE_NO, DOMAINS, llm_const
from _bootstrap import load

POOL = load("prediction_pool")
ESC = load("conditional_escrow")

MIN_BOUNTY = REG.MIN_BOUNTY
PB = REG.PROPOSER_BOND
DB = REG.DISPUTE_BOND


class CStack(Stack):
    def __init__(self):
        super().__init__()
        self.pool = W.deploy(POOL.PredictionPool, OWNER, self.reg._addr)
        self.esc = W.deploy(ESC.ConditionalEscrow, OWNER, self.reg._addr)
        self.supply = W.total_supply()

    def pv(self, method, *a):
        return W.view(self.pool, method, *a)

    def ev(self, method, *a):
        return W.view(self.esc, method, *a)

    def market(self, mid):
        return json.loads(self.pv("get_market", mid))

    def escrow(self, eid):
        return json.loads(self.ev("get_escrow", eid))

    def pool_acct(self):
        return json.loads(self.pv("get_accounting"))

    def esc_acct(self):
        return json.loads(self.ev("get_accounting"))

    # ---- pool helpers
    def make_market(self, creator=CREATOR, atype="BINARY", options=None, fee_bps=100, close_in=3600,
                    resolve_in=7200, response_window=7200, cw=3600, bounty=None, domains=None, title="Will it pass?"):
        now = W.time
        bounty = bounty if bounty is not None else 2 * MIN_BOUNTY
        res = W.tx(creator, self.pool, "create_market", title, atype, json.dumps(options or []),
                   json.dumps(domains or DOMAINS), now + close_in, now + resolve_in,
                   now + resolve_in + response_window, cw, fee_bps, value=bounty)
        if not res.startswith("m"):
            raise AssertionError(res)
        return res

    def qid_of(self, ref, who=None):
        return self.rv("get_question_id_by_ref", who or self.pool._addr, ref)

    def stake(self, mid, who, outcome, amount):
        return W.tx(who, self.pool, "stake", mid, outcome, value=amount)

    def close_market(self, mid):
        W.time = self.market(mid)["close_time"] + 1

    def resolve_optimistic(self, qid, answer="YES"):
        self.propose(qid, answer=answer)
        self.wait_challenge(qid)
        self.finalize(qid)

    # ---- escrow helpers
    def make_escrow(self, payer=ALICE, payee=BOB, atype="BINARY", parties=None, amount=10 * GEN // 10,
                    bounty=None, resolve_in=600, response_window=7200, cw=3600, title="Release on delivery?"):
        now = W.time
        bounty = bounty if bounty is not None else 2 * MIN_BOUNTY
        res = W.tx(payer, self.esc, "create_escrow", title, atype, json.dumps(parties or []), payee,
                   json.dumps(DOMAINS), now + resolve_in, now + resolve_in + response_window, cw, amount,
                   value=amount + bounty)
        if not res.startswith("e"):
            raise AssertionError(res)
        return res

    def check_invariants(self, tc):
        super().check_invariants(tc)
        pa = self.pool_acct()
        tc.assertTrue(pa["ok"], pa)
        tc.assertEqual(pa["balance"], W.balance_of(self.pool._addr))
        ea = self.esc_acct()
        tc.assertTrue(ea["ok"], ea)
        tc.assertEqual(ea["balance"], W.balance_of(self.esc._addr))


class registry_check_bypassed:
    """Simulates state drift between the consumer's synchronous pre-check and the
    asynchronous create_question message, so the registry-side rejection and the
    recovery paths stay covered."""

    def __enter__(self):
        self.orig = REG.QuestionRegistry.check_question

        def check_question(self_, *a):
            return ""
        check_question._gl_kind = "view"
        REG.QuestionRegistry.check_question = check_question
        return self

    def __exit__(self, *exc):
        REG.QuestionRegistry.check_question = self.orig
        return False


class CBase(unittest.TestCase):
    def setUp(self):
        self.st = CStack()

    def tearDown(self):
        self.st.check_invariants(self)


class PoolFullCycle(CBase):
    def test_binary_yes_wins_with_fee_pro_rata_and_dust_sweep(self):
        st = self.st
        mid = st.make_market(fee_bps=100)
        qid = st.qid_of(mid)
        self.assertEqual(qid, "q0")
        self.assertEqual(st.question(qid)["creator"], st.pool._addr)
        self.assertEqual(st.question(qid)["beneficiary"], CREATOR)
        a, c, b = 3 * 10 ** 17 + 7, 10 ** 17 + 3, 8 * 10 ** 17 + 11
        self.assertEqual(st.stake(mid, ALICE, "YES", a), "STAKED")
        self.assertEqual(st.stake(mid, CAROL, "YES", c), "STAKED")
        self.assertEqual(st.stake(mid, BOB, "NO", b), "STAKED")
        total = a + b + c
        self.assertEqual(st.pool_acct()["open_total"], total)
        with self.assertRaises(Rollback):
            W.tx(CAROL, st.pool, "settle", mid)
        st.close_market(mid)
        with self.assertRaises(Rollback):
            W.tx(CAROL, st.pool, "settle", mid)
        st.resolve_optimistic(qid, "YES")
        self.assertEqual(W.tx(DAVE, st.pool, "settle", mid), "SETTLED:YES")
        fee = total * 100 // 10000
        dist = total - fee
        self.assertEqual(st.claimable_pool(CREATOR) if hasattr(st, "claimable_pool") else st.pv("get_claimable", CREATOR), fee)
        pa = (a * dist) // (a + c)
        pc = dist - pa
        self.assertEqual(int(W.tx(ALICE, st.pool, "claim", mid)), pa)
        self.assertEqual(int(W.tx(CAROL, st.pool, "claim", mid)), pc)
        with self.assertRaises(Rollback):
            W.tx(BOB, st.pool, "claim", mid)
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "claim", mid)
        for who in (ALICE, CAROL, CREATOR):
            W.tx(who, st.pool, "withdraw")
        self.assertEqual(W.balance_of(st.pool._addr), 0)
        self.assertEqual(st.pool_acct()["open_total"], 0)
        self.assertEqual(st.market(mid)["unclaimed"], 0)

    def test_losing_side_gets_nothing_and_cannot_claim(self):
        st = self.st
        mid = st.make_market(fee_bps=0)
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.stake(mid, BOB, "NO", 10 ** 17)
        st.close_market(mid)
        st.resolve_optimistic(qid, "NO")
        W.tx(DAVE, st.pool, "settle", mid)
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "claim", mid)
        W.tx(BOB, st.pool, "claim", mid)
        self.assertEqual(st.pv("get_claimable", BOB), 2 * 10 ** 17)

    def test_categorical_and_bucketed_markets(self):
        for atype, options, outcomes, win in (("CATEGORICAL", ["red", "green", "blue"], ["red", "green"], "green"),
                                              ("BUCKETED_RANGE", [10, 20], ["0", "2"], "2")):
            st = CStack()
            mid = st.make_market(atype=atype, options=options)
            self.assertEqual(st.market(mid)["answer_type"], atype)
            qid = st.qid_of(mid)
            st.stake(mid, ALICE, outcomes[0], 10 ** 17)
            st.stake(mid, BOB, outcomes[1], 10 ** 17)
            st.close_market(mid)
            st.resolve_optimistic(qid, win)
            W.tx(CAROL, st.pool, "settle", mid)
            W.tx(BOB, st.pool, "claim", mid)
            W.tx(BOB, st.pool, "withdraw")
            st.check_invariants(self)

    def test_disputed_consensus_answer_settles_the_pool(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.stake(mid, BOB, "NO", 10 ** 17)
        st.close_market(mid)
        st.propose(qid, who=CAROL, answer="YES")
        st.dispute(qid, who=DAVE, answer="NO")
        st.engine_first_round(qid, "NO", QUOTE_NO)
        with self.assertRaises(Rollback):
            W.tx(EVE, st.pool, "settle", mid)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(W.tx(EVE, st.pool, "settle", mid), "SETTLED:NO")

    def test_invalid_refunds_every_staker_in_full(self):
        st = self.st
        mid = st.make_market(fee_bps=500)
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 3 * 10 ** 17)
        st.stake(mid, ALICE, "NO", 10 ** 17)
        st.stake(mid, BOB, "NO", 2 * 10 ** 17)
        st.close_market(mid)
        st.propose(qid, who=CAROL, answer="YES")
        st.dispute(qid, who=DAVE, answer="NO")
        st.engine_first_round(qid, "INVALID", "")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(W.tx(EVE, st.pool, "settle", mid), "REFUNDED:INVALID")
        self.assertEqual(int(W.tx(ALICE, st.pool, "claim", mid)), 4 * 10 ** 17)
        self.assertEqual(int(W.tx(BOB, st.pool, "claim", mid)), 2 * 10 ** 17)
        self.assertEqual(st.pv("get_claimable", CREATOR), 0)
        self.assertEqual(st.pool_acct()["open_total"], 0)
        for who in (ALICE, BOB):
            W.tx(who, st.pool, "withdraw")
        self.assertEqual(W.balance_of(st.pool._addr), 0)
        self.assertEqual(st.bv("get_claimable", CREATOR), 2 * MIN_BOUNTY)

    def test_no_stake_on_winning_outcome_refunds(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.close_market(mid)
        st.resolve_optimistic(qid, "NO")
        self.assertEqual(W.tx(DAVE, st.pool, "settle", mid), "REFUNDED:NO_WINNING_STAKE")
        self.assertEqual(int(W.tx(ALICE, st.pool, "claim", mid)), 10 ** 17)

    def test_unresolved_timeout_refunds_only_after_grace(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.close_market(mid)
        deadline = st.market(mid)["response_deadline"]
        W.time = deadline + 1
        self.assertEqual(st.status(qid), "UNRESOLVED_TIMEOUT")
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)
        W.time = deadline + POOL.TIMEOUT_GRACE + 1
        self.assertEqual(W.tx(DAVE, st.pool, "settle", mid), "REFUNDED:UNRESOLVED_TIMEOUT")
        self.assertEqual(int(W.tx(ALICE, st.pool, "claim", mid)), 10 ** 17)
        self.assertEqual(st.status(qid), "CANCELLED")
        self.assertEqual(st.bv("get_claimable", CREATOR), 2 * MIN_BOUNTY)
        self.assertTrue(st.trigger_timeout(qid, CAROL).startswith("REJECTED"))
        st.withdraw(CAROL)

    def test_timeout_resolution_in_progress_blocks_the_refund(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.close_market(mid)
        W.time = st.market(mid)["response_deadline"] + 100
        st.trigger_timeout(qid, CAROL, json.dumps([URL_T]))
        W.time = st.market(mid)["response_deadline"] + POOL.TIMEOUT_GRACE + 5
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)
        W.llm = llm_const("YES", QUOTE_YES)
        st.run_evidence(qid)
        st.judge_publish(qid)
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(W.tx(DAVE, st.pool, "settle", mid), "SETTLED:YES")

    def test_state_guards(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        for method in ("claim",):
            with self.assertRaises(Rollback):
                W.tx(ALICE, st.pool, method, mid)
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "settle", "m404")
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "claim", "m404")
        st.close_market(mid)
        st.resolve_optimistic(qid, "YES")
        W.tx(DAVE, st.pool, "settle", mid)
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "withdraw")

    def test_non_final_answer_never_settles(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.close_market(mid)
        st.propose(qid, answer="YES")
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)
        st.dispute(qid, who=BOB, answer="NO")
        st.engine_first_round(qid, "YES", QUOTE_YES)
        self.assertEqual(st.status(qid), "JUDGED")
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)
        st.appeal(qid, who=BOB)
        with self.assertRaises(Rollback):
            W.tx(DAVE, st.pool, "settle", mid)

    def test_user_positions_view(self):
        st = self.st
        mid = st.make_market()
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.stake(mid, ALICE, "YES", 2 * 10 ** 17)
        pos = json.loads(st.pv("get_positions", ALICE))
        self.assertEqual(pos, [{"market": mid, "outcome": "YES", "amount": 3 * 10 ** 17, "claimed": False}])
        lst = json.loads(st.pv("list_markets"))
        self.assertEqual(lst[0]["pools"], {"YES": 3 * 10 ** 17, "NO": 0})


class PoolRejections(CBase):
    def test_stake_rejections_credit_the_sender_instead_of_trapping_value(self):
        st = self.st
        mid = st.make_market()
        cases = [
            ("unknown market", ("m404", "YES"), 10 ** 17),
            ("unknown outcome", (mid, "MAYBE"), 10 ** 17),
            ("below minimum", (mid, "YES"), POOL.MIN_STAKE - 1),
            ("zero value", (mid, "YES"), 0),
        ]
        expect = 0
        for name, args, value in cases:
            res = W.tx(ALICE, st.pool, "stake", *args, value=value)
            self.assertTrue(res.startswith("REJECTED:"), name)
            expect += value
            self.assertEqual(st.pv("get_claimable", ALICE), expect, name)
        st.close_market(mid)
        res = W.tx(ALICE, st.pool, "stake", mid, "YES", value=10 ** 17)
        self.assertIn("closed", res)
        expect += 10 ** 17
        self.assertEqual(st.pv("get_claimable", ALICE), expect)
        W.tx(ALICE, st.pool, "withdraw")
        self.assertEqual(W.balance_of(st.pool._addr), 0)

    def test_stake_after_settlement_rejected(self):
        st = self.st
        mid = st.make_market()
        qid = st.qid_of(mid)
        st.stake(mid, ALICE, "YES", 10 ** 17)
        st.close_market(mid)
        st.resolve_optimistic(qid, "YES")
        W.tx(DAVE, st.pool, "settle", mid)
        self.assertIn("not open", W.tx(BOB, st.pool, "stake", mid, "YES", value=10 ** 17))

    def test_create_market_rejections_credit_the_creator(self):
        st = self.st
        now = W.time
        ok = dict(title="T?", atype="BINARY", options=[], domains=DOMAINS, close=now + 3600,
                  resolve=now + 7200, deadline=now + 14400, cw=3600, fee=100)
        bad = {
            "split not supported": dict(ok, atype="SPLIT", options=[eoa(51), eoa(52)]),
            "fee too high": dict(ok, fee=POOL.MAX_FEE_BPS + 1),
            "negative fee": dict(ok, fee=-1),
            "close too soon": dict(ok, close=now + 5),
            "close after resolve": dict(ok, close=now + 8000),
            "resolve too soon": dict(ok, resolve=now + 10, close=now + 5),
            "empty title": dict(ok, title="  "),
            "binary with options": dict(ok, options=["a"]),
            "categorical one option": dict(ok, atype="CATEGORICAL", options=["a"]),
        }
        n = 0
        for name, c in bad.items():
            res = W.tx(DAVE, st.pool, "create_market", c["title"], c["atype"], json.dumps(c["options"]),
                       json.dumps(c["domains"]), c["close"], c["resolve"], c["deadline"], c["cw"], c["fee"],
                       value=2 * MIN_BOUNTY)
            self.assertTrue(res.startswith("REJECTED:"), (name, res))
            n += 1
        res = W.tx(DAVE, st.pool, "create_market", "T?", "BINARY", "[]", json.dumps(DOMAINS), now + 3600,
                   now + 7200, now + 14400, 3600, 100, value=MIN_BOUNTY - 1)
        self.assertIn("minimum", res)
        self.assertEqual(st.pv("get_claimable", DAVE), n * 2 * MIN_BOUNTY + MIN_BOUNTY - 1)
        self.assertEqual(json.loads(st.pv("list_markets")), [])

    def test_question_the_registry_would_reject_is_refused_up_front(self):
        st = self.st
        now = W.time
        for domains, cw, bounty, why in ((["not a domain"], 3600, 2 * MIN_BOUNTY, "bad domain"),
                                         (DOMAINS, 60, 2 * MIN_BOUNTY, "challenge window"),
                                         (DOMAINS, 3600, REG.MAX_BOUNTY + 1, "bounty above maximum")):
            res = W.tx(CREATOR, st.pool, "create_market", "T", "BINARY", "[]", json.dumps(domains),
                       now + 3600, now + 7200, now + 14400, cw, 0, value=bounty)
            self.assertIn(why, res)
        self.assertEqual(json.loads(st.pv("list_markets")), [])
        self.assertEqual(st.pv("get_claimable", CREATOR), 4 * MIN_BOUNTY + REG.MAX_BOUNTY + 1)
        W.tx(CREATOR, st.pool, "withdraw")

    def test_async_registry_rejection_refunds_the_creator_in_the_bank_and_market_recovers(self):
        st = self.st
        with registry_check_bypassed():
            mid = st.make_market(domains=["not a domain"])
        self.assertEqual(st.qid_of(mid), "")
        self.assertEqual(st.bv("get_claimable", CREATOR), 2 * MIN_BOUNTY)
        self.assertIn("question not created", W.tx(ALICE, st.pool, "stake", mid, "YES", value=10 ** 17))
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "recover_failed_market", mid)
        W.advance(POOL.RECOVER_AFTER + 1)
        self.assertEqual(W.tx(ALICE, st.pool, "recover_failed_market", mid), "REFUNDED:QUESTION_NEVER_CREATED")
        self.assertEqual(st.pv("get_claimable", CREATOR), 0)
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "recover_failed_market", mid)
        st.withdraw(CREATOR)
        W.tx(ALICE, st.pool, "withdraw")

    def test_bounty_bounced_by_a_failed_message_is_recovered_not_stranded(self):
        W.reset()
        for a in (OWNER, CREATOR):
            W.fund(a, 100 * GEN)
        reg = W.deploy(REG.QuestionRegistry, OWNER)
        pool = W.deploy(POOL.PredictionPool, OWNER, reg._addr)
        now = W.time
        args = ("T", "BINARY", "[]", json.dumps(DOMAINS), now + 3600, now + 7200, now + 14400, 3600, 0)
        self.assertIn("not wired", W.tx(CREATOR, pool, "create_market", *args, value=2 * MIN_BOUNTY))
        with registry_check_bypassed():
            mid = W.tx(CREATOR, pool, "create_market", *args, value=2 * MIN_BOUNTY)
        self.assertEqual(len(W.failed_messages), 1)
        self.assertFalse(json.loads(W.view(pool, "get_accounting"))["ok"])
        W.advance(POOL.RECOVER_AFTER + 1)
        W.tx(CREATOR, pool, "recover_failed_market", mid)
        acct = json.loads(W.view(pool, "get_accounting"))
        self.assertTrue(acct["ok"], acct)
        self.assertEqual(W.view(pool, "get_claimable", CREATOR), 4 * MIN_BOUNTY)
        self.st = CStack()

    def test_recover_not_possible_when_question_exists(self):
        st = self.st
        mid = st.make_market()
        W.advance(POOL.RECOVER_AFTER + 1)
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.pool, "recover_failed_market", mid)

    def test_pool_has_no_owner_or_admin_surface(self):
        for mod, cls in ((POOL, POOL.PredictionPool), (ESC, ESC.ConditionalEscrow)):
            names = [n for n in dir(cls) if getattr(getattr(cls, n), "_gl_kind", None) in ("write", "payable")]
            for n in names:
                self.assertFalse(any(w in n for w in ("owner", "admin", "set_", "sweep", "rescue", "pause")), n)

    def test_registry_address_must_be_valid(self):
        with self.assertRaises(Rollback):
            W.deploy(POOL.PredictionPool, OWNER, "0x12")
        with self.assertRaises(Rollback):
            W.deploy(ESC.ConditionalEscrow, OWNER, "nope")


class EscrowCycle(CBase):
    AMOUNT = 5 * 10 ** 17

    def _final(self, qid, answer):
        self.st.propose(qid, answer=answer)
        self.st.wait_challenge(qid)
        self.st.finalize(qid)

    def test_yes_releases_to_payee(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        self.assertEqual(st.question(qid)["beneficiary"], ALICE)
        self.assertEqual(st.escrow(eid)["state"], "OPEN")
        with self.assertRaises(Rollback):
            W.tx(CAROL, st.esc, "settle", eid)
        self._final(qid, "YES")
        self.assertEqual(W.tx(CAROL, st.esc, "settle", eid), "RELEASED")
        self.assertEqual(st.ev("get_claimable", BOB), self.AMOUNT)
        before = W.balance_of(BOB)
        W.tx(BOB, st.esc, "withdraw")
        self.assertEqual(W.balance_of(BOB), before + self.AMOUNT)
        with self.assertRaises(Rollback):
            W.tx(CAROL, st.esc, "settle", eid)
        self.assertEqual(W.balance_of(st.esc._addr), 0)

    def test_no_refunds_payer(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        self._final(qid, "NO")
        self.assertEqual(W.tx(CAROL, st.esc, "settle", eid), "REFUNDED")
        self.assertEqual(st.ev("get_claimable", ALICE), self.AMOUNT)
        self.assertEqual(st.ev("get_claimable", BOB), 0)

    def test_invalid_refunds_payer_only_after_grace(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        st.propose(qid, who=CAROL, answer="YES")
        st.dispute(qid, who=DAVE, answer="NO")
        st.engine_first_round(qid, "INVALID", "")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(W.tx(EVE, st.esc, "settle", eid), "INVALID_ARMED")
        self.assertEqual(st.escrow(eid)["state"], "OPEN")
        with self.assertRaises(Rollback):
            W.tx(EVE, st.esc, "settle", eid)
        W.advance(ESC.INVALID_GRACE + 1)
        self.assertEqual(W.tx(EVE, st.esc, "settle", eid), "REFUNDED")
        self.assertEqual(st.ev("get_claimable", ALICE), self.AMOUNT)
        self.assertEqual(st.bv("get_claimable", ALICE), 2 * MIN_BOUNTY)

    def test_unresolved_timeout_refunds_payer_after_grace(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        W.time = st.escrow(eid)["response_deadline"] + 1
        with self.assertRaises(Rollback):
            W.tx(EVE, st.esc, "settle", eid)
        W.advance(ESC.TIMEOUT_GRACE + 1)
        self.assertEqual(W.tx(EVE, st.esc, "settle", eid), "REFUNDED")
        self.assertEqual(st.ev("get_claimable", ALICE), self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        self.assertEqual(st.status(qid), "CANCELLED")
        self.assertEqual(st.bv("get_claimable", ALICE), 2 * MIN_BOUNTY)

    def test_payer_can_cancel_before_proposal_and_bounty_returns(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        for who in (BOB, CAROL, CREATOR):
            with self.assertRaises(Rollback):
                W.tx(who, st.esc, "cancel_escrow", eid)
        self.assertEqual(W.tx(ALICE, st.esc, "cancel_escrow", eid), "CANCELLED")
        self.assertEqual(st.status(qid), "CANCELLED")
        self.assertEqual(st.ev("get_claimable", ALICE), self.AMOUNT)
        self.assertEqual(st.bv("get_claimable", ALICE), 2 * MIN_BOUNTY)
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.esc, "cancel_escrow", eid)
        with self.assertRaises(Rollback):
            W.tx(EVE, st.esc, "settle", eid)

    def test_payer_cannot_cancel_once_resolution_has_started(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        W.time = st.escrow(eid)["resolve_after"]
        self.assertEqual(st.status(qid), "OPEN")
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.esc, "cancel_escrow", eid)
        st.propose(qid, who=BOB, answer="YES")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(W.tx(EVE, st.esc, "settle", eid), "RELEASED")
        self.assertEqual(st.ev("get_claimable", BOB), self.AMOUNT)

    def test_escrow_refused_up_front_when_registry_would_reject(self):
        st = self.st
        now = W.time
        res = W.tx(ALICE, st.esc, "create_escrow", "T", "BINARY", "[]", BOB, json.dumps(["bad domain"]),
                   now + 600, now + 7800, 3600, self.AMOUNT, value=self.AMOUNT + 2 * MIN_BOUNTY)
        self.assertIn("bad domain", res)
        self.assertEqual(st.esc_acct()["open_total"], 0)
        W.tx(ALICE, st.esc, "withdraw")

    def test_escrow_whose_question_was_never_created_can_be_recovered(self):
        st = self.st
        with registry_check_bypassed():
            now = W.time
            eid = W.tx(ALICE, st.esc, "create_escrow", "T", "BINARY", "[]", BOB, json.dumps(["bad domain"]),
                       now + 600, now + 7800, 3600, self.AMOUNT, value=self.AMOUNT + 2 * MIN_BOUNTY)
        self.assertEqual(st.qid_of(eid, st.esc._addr), "")
        self.assertEqual(st.bv("get_claimable", ALICE), 2 * MIN_BOUNTY)
        for method in ("cancel_escrow", "settle"):
            with self.assertRaises(Rollback):
                W.tx(ALICE, st.esc, method, eid)
        with self.assertRaises(Rollback):
            W.tx(EVE, st.esc, "recover_failed_escrow", eid)
        W.advance(ESC.RECOVER_AFTER + 1)
        self.assertEqual(W.tx(EVE, st.esc, "recover_failed_escrow", eid), "REFUNDED:QUESTION_NEVER_CREATED")
        self.assertEqual(st.ev("get_claimable", ALICE), self.AMOUNT)
        self.assertEqual(st.esc_acct()["open_total"], 0)
        with self.assertRaises(Rollback):
            W.tx(EVE, st.esc, "recover_failed_escrow", eid)
        W.tx(ALICE, st.esc, "withdraw")
        st.withdraw(ALICE)

    def test_recover_escrow_not_possible_when_question_exists(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        W.advance(ESC.RECOVER_AFTER + 1)
        with self.assertRaises(Rollback):
            W.tx(EVE, st.esc, "recover_failed_escrow", eid)

    def test_cancel_after_proposal_rejected(self):
        st = self.st
        eid = st.make_escrow(amount=self.AMOUNT)
        qid = st.qid_of(eid, st.esc._addr)
        st.propose(qid, answer="YES")
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.esc, "cancel_escrow", eid)

    def test_split_pays_every_party_and_sweeps_dust(self):
        st = self.st
        parties = [BOB, CAROL, ALICE]
        amount = 10 ** 17 + 7
        eid = st.make_escrow(atype="SPLIT", parties=parties, payee=BOB, amount=amount)
        qid = st.qid_of(eid, st.esc._addr)
        self._final(qid, "5000,3000,2000")
        self.assertEqual(W.tx(DAVE, st.esc, "settle", eid), "SPLIT_PAID")
        got = [st.ev("get_claimable", p) for p in parties]
        self.assertEqual(sum(got), amount)
        self.assertEqual(got[1], amount * 3000 // 10000)
        self.assertEqual(got[2], amount * 2000 // 10000)
        self.assertEqual(got[0], amount - got[1] - got[2])
        for p in parties:
            W.tx(p, st.esc, "withdraw")
        self.assertEqual(W.balance_of(st.esc._addr), 0)

    def test_split_zero_share_party_and_full_share(self):
        st = self.st
        eid = st.make_escrow(atype="SPLIT", parties=[BOB, CAROL], payee=BOB, amount=10 ** 17)
        qid = st.qid_of(eid, st.esc._addr)
        self._final(qid, "0,10000")
        W.tx(DAVE, st.esc, "settle", eid)
        self.assertEqual(st.ev("get_claimable", BOB), 0)
        self.assertEqual(st.ev("get_claimable", CAROL), 10 ** 17)

    def test_split_invalid_refunds_payer_after_grace(self):
        st = self.st
        eid = st.make_escrow(atype="SPLIT", parties=[BOB, CAROL], payee=BOB, amount=10 ** 17)
        qid = st.qid_of(eid, st.esc._addr)
        st.propose(qid, who=DAVE, answer="5000,5000")
        st.dispute(qid, who=EVE, answer="10000,0")
        st.engine_first_round(qid, "INVALID", "")
        st.wait_challenge(qid)
        st.finalize(qid)
        self.assertEqual(W.tx(DAVE, st.esc, "settle", eid), "INVALID_ARMED")
        W.advance(ESC.INVALID_GRACE + 1)
        self.assertEqual(W.tx(DAVE, st.esc, "settle", eid), "REFUNDED")
        self.assertEqual(st.ev("get_claimable", ALICE), 10 ** 17)

    def test_create_rejections_credit_the_sender(self):
        st = self.st
        now = W.time
        ok = dict(title="T?", atype="BINARY", parties=[], payee=BOB, amount=10 ** 17, value=10 ** 17 + 2 * MIN_BOUNTY,
                  resolve=now + 600, deadline=now + 8000)
        bad = {
            "payer is payee": dict(ok, payee=ALICE),
            "bad payee": dict(ok, payee="0x12"),
            "binary with parties": dict(ok, parties=[BOB, CAROL]),
            "categorical unsupported": dict(ok, atype="CATEGORICAL"),
            "split one party": dict(ok, atype="SPLIT", parties=[BOB]),
            "split payee missing": dict(ok, atype="SPLIT", parties=[CAROL, DAVE]),
            "split duplicate": dict(ok, atype="SPLIT", parties=[BOB, BOB]),
            "amount tiny": dict(ok, amount=10),
            "no bounty": dict(ok, value=10 ** 17),
            "bounty too small": dict(ok, value=10 ** 17 + MIN_BOUNTY - 1),
            "resolve too soon": dict(ok, resolve=now + 5),
            "empty title": dict(ok, title=""),
        }
        for name, c in bad.items():
            res = W.tx(ALICE, st.esc, "create_escrow", c["title"], c["atype"], json.dumps(c["parties"]), c["payee"],
                       json.dumps(DOMAINS), c["resolve"], c["deadline"], 3600, c["amount"], value=c["value"])
            self.assertTrue(res.startswith("REJECTED:"), (name, res))
            claim = st.ev("get_claimable", ALICE)
            W.tx(ALICE, st.esc, "withdraw")
            self.assertEqual(claim, c["value"], name)
        self.assertEqual(json.loads(st.ev("list_escrows")), [])

    def test_escrow_lists(self):
        st = self.st
        a = st.make_escrow()
        b = st.make_escrow(payer=CAROL, payee=DAVE)
        lst = json.loads(st.ev("list_escrows"))
        self.assertEqual([x["id"] for x in lst], [b, a])
        self.assertEqual(lst[0]["question_id"], st.qid_of(b, st.esc._addr))


class ConsumerFuzz(unittest.TestCase):
    def test_random_walk_keeps_every_ledger_exact(self):
        for seed in range(8):
            rnd = random.Random(seed)
            st = CStack()
            mids, eids = [], []
            for step in range(260):
                act = rnd.choice(["mkt", "stake", "stake", "stake", "esc", "settle", "settle", "claim", "claim",
                                  "wd", "wd", "adv", "adv", "resolve", "resolve", "resolve", "cancel", "timeout"])
                who = rnd.choice([ALICE, BOB, CAROL, DAVE, EVE, CREATOR])
                try:
                    if act == "mkt" and len(mids) < 4:
                        mids.append(st.make_market(creator=who, fee_bps=rnd.choice([0, 100, 333])))
                    elif act == "stake" and mids:
                        mid = rnd.choice(mids)
                        W.tx(who, st.pool, "stake", mid, rnd.choice(["YES", "NO", "X"]),
                             value=rnd.choice([0, POOL.MIN_STAKE, 10 ** 17 + rnd.randint(0, 999), 3 * 10 ** 17]))
                    elif act == "esc" and len(eids) < 4:
                        payee = rnd.choice([p for p in (ALICE, BOB, CAROL, DAVE) if p != who])
                        if rnd.random() < 0.5:
                            eids.append(st.make_escrow(payer=who, payee=payee, amount=rnd.randint(1000, 10 ** 17)))
                        else:
                            eids.append(st.make_escrow(payer=who, payee=payee, atype="SPLIT",
                                                       parties=[payee, EVE if who != EVE else DAVE],
                                                       amount=rnd.randint(1000, 10 ** 17)))
                    elif act == "settle":
                        if mids and rnd.random() < 0.5:
                            W.tx(who, st.pool, "settle", rnd.choice(mids))
                        elif eids:
                            W.tx(who, st.esc, "settle", rnd.choice(eids))
                    elif act == "claim" and mids:
                        W.tx(who, st.pool, "claim", rnd.choice(mids))
                    elif act == "wd":
                        W.tx(who, rnd.choice([st.pool, st.esc, st.bank]), "withdraw")
                    elif act == "adv":
                        W.advance(rnd.choice([30, 3601, 7300, 90000]))
                    elif act == "resolve":
                        refs = [(m, st.pool._addr) for m in mids] + [(e, st.esc._addr) for e in eids]
                        if refs:
                            ref, owner = rnd.choice(refs)
                            qid = st.qid_of(ref, owner)
                            if qid:
                                q = st.question(qid)
                                if q["status"] == "OPEN" and W.time >= q["resolve_after"]:
                                    ans = rnd.choice(["YES", "NO"]) if q["answer_type"] == "BINARY" else "5000,5000"
                                    W.tx(CAROL, st.reg, "propose", qid, ans, json.dumps([URL_P]),
                                         value=REG.PROPOSER_BOND)
                                elif q["status"] == "PROPOSED":
                                    if rnd.random() < 0.3:
                                        W.tx(DAVE, st.reg, "dispute", qid, "NO" if q["answer_type"] == "BINARY" else "10000,0",
                                             json.dumps([URL_D]), value=REG.DISPUTE_BOND)
                                    else:
                                        W.advance(q["challenge_window"] + 1)
                                        W.tx(EVE, st.reg, "finalize", qid)
                                elif q["status"] == "DISPUTED":
                                    W.llm = llm_const("INVALID", "")
                                    st.run_evidence(qid)
                                    st.judge_publish(qid)
                                elif q["status"] == "JUDGED":
                                    W.advance(q["challenge_window"] + 1)
                                    W.tx(EVE, st.reg, "finalize", qid)
                    elif act == "cancel" and eids:
                        W.tx(who, st.esc, "cancel_escrow", rnd.choice(eids))
                    elif act == "timeout" and (mids or eids):
                        refs = [(m, st.pool._addr) for m in mids] + [(e, st.esc._addr) for e in eids]
                        ref, owner = rnd.choice(refs)
                        qid = st.qid_of(ref, owner)
                        if qid:
                            st.trigger_timeout(qid, who, json.dumps([URL_T]))
                except Rollback:
                    pass
                W.failed_messages[:] = [m for m in W.failed_messages
                                        if m[1] not in ("record_evidence", "record_verdict", "cancel_question")]
                self.assertEqual(W.failed_messages, [], (seed, step, act))
                st.check_invariants(self)


if __name__ == "__main__":
    unittest.main()


class EscrowCancelOrdering(CBase):
    def test_cancel_before_the_bounty_is_locked_is_refused_and_retry_works(self):
        st = self.st
        W.hold = True
        eid = st.make_escrow(amount=10 ** 18)
        W.deliver_one()
        qid = st.qid_of(eid, st.esc._addr)
        self.assertTrue(qid)
        self.assertEqual(st.bv("get_bounty", qid), 0)
        self.assertFalse(st.rv("can_cancel", qid))
        with self.assertRaises(Rollback):
            W.tx(ALICE, st.esc, "cancel_escrow", eid)
        W.deliver()
        self.assertTrue(st.rv("can_cancel", qid))
        self.assertEqual(W.tx(ALICE, st.esc, "cancel_escrow", eid), "CANCELLED")
        self.assertEqual(st.status(qid), "CANCELLED")
        self.assertEqual(st.bv("get_claimable", ALICE), 2 * MIN_BOUNTY)


class TimeoutWindowsAreDisjoint(CBase):
    def test_consumer_grace_matches_the_registry_trigger_window(self):
        self.assertEqual(POOL.TIMEOUT_GRACE, REG.TIMEOUT_TRIGGER_WINDOW)
        self.assertEqual(ESC.TIMEOUT_GRACE, REG.TIMEOUT_TRIGGER_WINDOW)

    def test_no_timeout_resolution_can_start_after_a_consumer_refunded(self):
        st = self.st
        eid = st.make_escrow(amount=10 ** 18)
        qid = st.qid_of(eid, st.esc._addr)
        W.time = st.escrow(eid)["response_deadline"] + ESC.TIMEOUT_GRACE + 1
        self.assertIn("window closed", st.trigger_timeout(qid, CAROL))
        self.assertEqual(W.tx(EVE, st.esc, "settle", eid), "REFUNDED")
        self.assertEqual(st.status(qid), "CANCELLED")
        st.withdraw(CAROL)
