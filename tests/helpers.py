import json
import unittest

from _bootstrap import W, Rollback, ConsensusFailure, load  # noqa: F401

BANK = load("resolver_bank")
REG = load("question_registry")
ENG = load("resolver_engine")

GEN = 10 ** 18


def eoa(n):
    return "0x%040x" % (0xE0A0000 + n)


OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE = [eoa(i) for i in range(1, 8)]
ALL_EOAS = [OWNER, CREATOR, ALICE, BOB, CAROL, DAVE, EVE]

DOMAINS = ["example.com", "data.example.org"]
URL_P = "https://example.com/proposer-report"
URL_D = "https://example.com/disputer-report"
URL_T = "https://data.example.org/timeout-report"

TEXT_YES = ("Official statement: the council confirmed the proposal passed on March 3 with a clear "
            "majority. Further details follow in the annex.")
QUOTE_YES = "the council confirmed the proposal passed on March 3"
TEXT_NO = ("Independent audit: the audit found the proposal was rejected on March 3 by the full "
           "council after a recount. Annex attached.")
QUOTE_NO = "the audit found the proposal was rejected on March 3"

STATUS_FINAL = ("FINAL_OPTIMISTIC", "FINAL_CONSENSUS")


def set_web():
    W.web[URL_P] = TEXT_YES
    W.web[URL_D] = TEXT_NO
    W.web[URL_T] = TEXT_YES


def llm_const(answer, quote):
    def h(prompt):
        return {"answer": answer, "quoted_evidence": quote, "reasoning": "stub"}
    return h


class Stack:
    def __init__(self):
        W.reset()
        for a in ALL_EOAS:
            W.fund(a, 100000 * GEN)
        set_web()
        self.bank = W.deploy(BANK.ResolverBank, OWNER)
        self.reg = W.deploy(REG.QuestionRegistry, OWNER)
        self.eng = W.deploy(ENG.ResolverEngine, OWNER)
        W.tx(OWNER, self.bank, "set_registry", self.reg._addr)
        W.tx(OWNER, self.reg, "set_bank", self.bank._addr)
        W.tx(OWNER, self.reg, "set_engine", self.eng._addr)
        W.tx(OWNER, self.eng, "set_registry", self.reg._addr)
        self.supply = W.total_supply()

    # ------------------------------------------------------------ reads
    def rv(self, method, *a):
        return W.view(self.reg, method, *a)

    def bv(self, method, *a):
        return W.view(self.bank, method, *a)

    def question(self, qid):
        return json.loads(self.rv("get_question", qid))

    def resolution(self, qid):
        parts = self.rv("get_resolution", qid).split("|")
        return {"status": parts[0], "final": parts[1] == "true", "answer": parts[2],
                "path": parts[3], "snapshot_hash": parts[4]}

    def status(self, qid):
        return self.resolution(qid)["status"]

    def claimable(self, addr):
        return self.bv("get_claimable", addr)

    def accounting(self):
        return json.loads(self.bv("get_accounting"))

    def rep(self, addr):
        return self.bv("get_reputation", addr)

    # ------------------------------------------------------------ actions
    def create(self, creator=CREATOR, atype="BINARY", options=None, domains=None, resolve_in=100,
               response_window=7200, cw=None, bounty=None, text="Did the council pass the proposal?"):
        now = W.time
        cw = cw if cw is not None else max(3600, REG.MIN_CHALLENGE_WINDOW)
        bounty = bounty if bounty is not None else 2 * REG.MIN_BOUNTY
        res = W.tx(creator, self.reg, "create_question", text, atype, json.dumps(options or []),
                   json.dumps(domains or DOMAINS), now + resolve_in, now + resolve_in + response_window,
                   cw, "", "", value=bounty)
        if not res.startswith("q"):
            raise AssertionError("create rejected: " + res)
        return res

    def open_window(self, qid):
        q = self.question(qid)
        if W.time < q["resolve_after"]:
            W.time = q["resolve_after"] + 1

    def required_proposer_bond(self, who):
        return REG.PROPOSER_BOND * self.bv("get_bond_multiplier_pct", who) // 100

    def propose(self, qid, who=ALICE, answer="YES", urls=None, value=None):
        self.open_window(qid)
        if value is None:
            value = self.required_proposer_bond(who)
        return W.tx(who, self.reg, "propose", qid, answer, json.dumps(urls or [URL_P]), value=value)

    def dispute(self, qid, who=BOB, answer="NO", urls=None, value=None):
        if value is None:
            q = self.question(qid)
            mult = self.bv("get_bond_multiplier_pct", who)
            value = max(REG.DISPUTE_BOND * mult // 100, q["proposer_bond"])
        return W.tx(who, self.reg, "dispute", qid, answer, json.dumps(urls or [URL_D]), value=value)

    def trigger_timeout(self, qid, who=CAROL, urls_json=None, value=None):
        if value is None:
            value = self.required_proposer_bond(who)
        if urls_json is None:
            urls_json = json.dumps([URL_T])
        return W.tx(who, self.reg, "trigger_timeout", qid, urls_json, value=value)

    def appeal(self, qid, who=BOB, value=None):
        return W.tx(who, self.reg, "appeal", qid, value=value if value is not None else REG.APPEAL_BOND)

    def finalize(self, qid, who=CAROL):
        return W.tx(who, self.reg, "finalize", qid)

    def wait_challenge(self, qid):
        q = self.question(qid)
        W.advance(q["challenge_window"] + 1)

    def run_evidence(self, qid, who=CAROL):
        W.tx(who, self.eng, "open_case", qid)
        W.tx(who, self.eng, "freeze_evidence", qid)
        W.tx(who, self.eng, "publish_evidence", qid)

    def judge_publish(self, qid, round_no=1, who=CAROL):
        res = W.tx(who, self.eng, "judge", qid)
        W.tx(who, self.eng, "publish_verdict", qid, round_no)
        return res

    def engine_first_round(self, qid, answer, quote, who=CAROL):
        W.llm = llm_const(answer, quote)
        self.run_evidence(qid, who)
        return self.judge_publish(qid, 1, who)

    def engine_appeal_round(self, qid, answer, quote, who=CAROL):
        W.llm = llm_const(answer, quote)
        W.tx(who, self.eng, "open_appeal", qid)
        return self.judge_publish(qid, 2, who)

    def withdraw(self, who):
        return W.tx(who, self.bank, "withdraw")

    # ---------------------------------------------------------- invariants
    def check_invariants(self, tc):
        acct = self.accounting()
        tc.assertTrue(acct["ok"], acct)
        tc.assertEqual(W.failed_messages, [])
        tc.assertEqual(W.total_supply(), self.supply, "GEN must be conserved")
        tc.assertEqual(W.balance_of(self.reg._addr), 0, "registry must hold no GEN")
        tc.assertEqual(W.balance_of(self.eng._addr), 0, "engine must hold no GEN")
        tc.assertEqual(acct["balance"], W.balance_of(self.bank._addr))


class Base(unittest.TestCase):
    def setUp(self):
        self.st = Stack()

    def tearDown(self):
        self.st.check_invariants(self)
