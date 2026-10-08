"""Plant deliberate bugs in a scratch copy of the contracts; the suite must catch every one."""
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTANTS = [
    # ResolverBank
    ("resolver_bank.py", "        self.bond_amt[key] = u256(0)\n        self._unlock(amt)\n        self._credit(self.bond_owner[key], amt)",
     "        self.bond_amt[key] = u256(0)\n        self._credit(self.bond_owner[key], amt)", "return_bond forgets to unlock"),
    ("resolver_bank.py", "if not self._is_core(self._sender()):\n            raise Exception(\"unauthorized: registry only\")",
     "if False:\n            raise Exception(\"unauthorized\")", "ledger methods open to everyone"),
    ("resolver_bank.py", "        self.claimable_total = u256(int(self.claimable_total) - amt)\n        gl.get_contract_at(Address(s)).emit_transfer",
     "        gl.get_contract_at(Address(s)).emit_transfer", "withdraw forgets claimable_total"),
    # QuestionRegistry
    ("question_registry.py", "if not self.engine_set or self._sender() != self.engine:", "if False:", "engine-only check removed"),
    ("question_registry.py", "        if sender == q.proposer or sender == q.creator:\n            return self._reject(sender, value, \"proposer and creator may not dispute\")",
     "        pass", "self-dispute allowed"),
    ("question_registry.py", "        if q.status != \"EVIDENCE_FROZEN\":\n                raise Exception(\"question not awaiting a first verdict\")",
     "        if False:\n                raise Exception(\"x\")", "verdict without evidence"),
    ("question_registry.py", "if sender == q.proposer and q.proposer_answer != q.first_answer:", "if sender == q.proposer:", "winner may appeal"),
    ("question_registry.py", "if now >= int(q.proposed_at) + int(q.challenge_window):\n            return self._reject(sender, value, \"challenge window closed\")",
     "if False:\n            return self._reject(sender, value, \"closed\")", "dispute after window"),
    ("question_registry.py", "if key in seen:\n            return \"two urls from the same domain\"", "if False:\n            return \"dup\"", "same-domain duplicates allowed"),
    ("question_registry.py", "if \"@\" in p.netloc or p.username is not None or p.password is not None:\n            return \"userinfo not allowed\"",
     "if False:\n            return \"x\"", "userinfo allowed"),
    ("question_registry.py", "if host == d or host.endswith(\".\" + d):", "if d in host:", "substring domain match"),
    ("question_registry.py", "if ord(c) < 33 or ord(c) > 126 or c in URL_FORBIDDEN:", "if ord(c) < 33 or ord(c) > 126:", "delimiter characters allowed in urls"),
    ("question_registry.py", "            b.emit(on=\"accepted\").award_bond(qid, \"D\", q.proposer, WINNER_SHARE_PCT,\n                                             self._remainder_to(q, q.disputer, q.proposer))",
     "            b.emit(on=\"accepted\").return_bond(qid, \"D\")", "loser bond never slashed"),
    ("question_registry.py", "if _now() < int(q.stage_at) + STALL_TIMEOUT:\n            raise Exception(\"not stalled long enough\")",
     "if False:\n            raise Exception(\"x\")", "abort_stalled works immediately"),
    ("question_registry.py", "refund_to = cand", "refund_to = sender", "rejected consumer questions refund the contract, not the beneficiary"),
    ("question_registry.py", "        b.emit(value=u256(required), on=\"accepted\").lock_bond(qid, kind, who)\n        if value > required:",
     "        b.emit(value=u256(value), on=\"accepted\").lock_bond(qid, kind, who)\n        if False:", "whole bond value locked (whale bonds price out disputers)"),
    ("question_registry.py", "        if int(self._bank().view().get_bounty(qid)) == 0:\n            raise Exception(\"bounty not locked in the bank yet: retry shortly\")",
     "        pass", "cancel before the bounty is locked"),
    ("question_registry.py", "        if int(bank.get_bounty(qid)) != int(q.bounty):\n            return self._reject(sender, value, \"bounty not locked in the bank yet: retry shortly\")",
     "        pass", "proposal before the bounty is locked"),
    ("question_registry.py", "    if bounty > MAX_BOUNTY:\n        return \"bounty above maximum\"", "    pass", "unresolvable bounty accepted"),
    ("question_registry.py", "opponent = q.disputer if q.dispute_answer == ans else q.beneficiary", "opponent = q.disputer", "appeal bond paid to a side that also lost"),
    ("question_registry.py", "        b.emit(on=\"accepted\").return_bond(q.qid, \"T\")\n", "", "timeout bond never returned"),
    ("question_registry.py", "            self._require_bonds(qid, self._case_bonds(q) + [\"A\"])\n", "", "appeal settled before its bond is locked"),
    ("question_registry.py", "            self._require_bonds(qid, [\"P\"])\n", "", "optimistic finalize before the bond is locked"),
    ("question_registry.py", "        if q.beneficiary != loser:\n            return q.beneficiary\n        return winner", "        return q.beneficiary", "loser recovers part of its own slashed bond"),
    ("question_registry.py", "        if value < required:\n            return self._reject(sender, value, \"bond below required %d\" % required)\n        q.timeout_caller = sender",
     "        q.timeout_caller = sender", "timeout trigger without a bond"),
    # ResolverEngine
    ("resolver_engine.py", "    for ex in excerpts:\n        if quote in ex:\n            return True\n    return False", "    return True", "quote backstop removed"),
    ("resolver_engine.py", "        return mine == la", "        return True", "validators rubber-stamp"),
    ("resolver_engine.py", "            if mine is None or not _excerpt_matches(ex, mine):\n                return False", "            pass", "excerpt verification removed"),
    ("resolver_engine.py", "    return excerpt == page[:EXCERPT_MAX]", "    return excerpt in page", "truncated or shifted excerpt accepted"),
    ("resolver_engine.py", "        if _now() < int(c.opened_at) + FREEZE_GRACE:\n            raise Exception(\"freeze grace period still running\")",
     "        pass", "evidence released as unavailable before the grace period"),
    ("resolver_engine.py", "            if mine is None or mine == \"INVALID\":\n                return True\n            return not _quote_loose_ok(quote, excerpts)",
     "            return True", "leader INVALID accepted without validator check"),
    ("resolver_engine.py", "        if docs[index][\"status\"] != \"PENDING\":\n            raise Exception(\"document already captured\")",
     "        pass", "captured evidence can be re-captured"),
    ("resolver_engine.py", "                if _fetch_stable(item[1]) is not None:\n                    return False", "                pass", "leader may hide reachable evidence"),
    ("resolver_engine.py", "        if not _quote_ok(lq, excerpts):\n            return False\n        mine, _unused", "        mine, _unused", "leader quote not verified by validators"),
    ("resolver_engine.py", "    return t.replace(\"<\", \"\\u2039\").replace(\">\", \"\\u203a\")", "    return t", "evidence can forge prompt delimiters"),
    ("resolver_engine.py", "    if b is None or a[:EXCERPT_MAX] != b[:EXCERPT_MAX]:\n        return None", "    if b is None:\n        return None", "unstable pages frozen as evidence"),
    ("resolver_engine.py", "            quote = _recover_quote(quote, excerpts)\n            if quote is None:", "            quote = None\n            if quote is None:", "loosely quoted honest verdicts stall"),
    ("resolver_engine.py", "            if not isinstance(a, dict) or set(a.keys()) != DOC_KEYS:", "            if not isinstance(a, dict):", "leader may smuggle fields into the snapshot"),
    ("question_registry.py", "        if now > int(q.response_deadline) + TIMEOUT_TRIGGER_WINDOW:\n            return self._reject(sender, value, \"timeout resolution window closed: the creator may cancel\")",
     "        pass", "timeout resolution may start after consumers refunded"),
    ("question_registry.py", "                    else:\n                        opponent = q.beneficiary\n                elif q.appellant == q.proposer:",
     "                    else:\n                        opponent = q.proposer\n                elif q.appellant == q.proposer:", "lost timeout appeal bond sent to a missing proposer"),
    # PredictionPool
    ("prediction_pool.py", "            if s == int(m.winner_stake_unclaimed):\n                payout = int(m.unclaimed)\n            else:",
     "            if False:\n                payout = int(m.unclaimed)\n            else:", "pool dust never swept"),
    ("prediction_pool.py", "        if final:\n            if answer != \"INVALID\"", "        if True:\n            if answer != \"INVALID\"", "pool settles on non-final answers"),
    ("prediction_pool.py", "        if _now() >= int(m.close_time):\n            return self._reject(sender, value, \"staking closed\")",
     "        if False:\n            return self._reject(sender, value, \"staking closed\")", "staking after close"),
    ("prediction_pool.py", "        if err != \"\":\n            return self._reject(sender, value, \"registry would reject the question: \" + err)",
     "        pass", "pool skips the registry pre-check"),
    ("prediction_pool.py", "            m.reason = \"UNRESOLVED_TIMEOUT\"\n            self._reg().emit(on=\"accepted\").cancel_question(qid)",
     "            m.reason = \"UNRESOLVED_TIMEOUT\"", "pool timeout refund leaves the question live"),
    ("prediction_pool.py", "        back = min(int(m.bounty), max(0, surplus))\n        self._credit(m.creator, back)", "        pass", "bounced bounty stranded in the pool"),
    # ConditionalEscrow
    ("conditional_escrow.py", "        if not self._reg().view().can_cancel(qid):\n            raise Exception(\"question bounty not settled in the bank yet: retry shortly\")",
     "        pass", "escrow cancel before the bounty is locked"),
    ("conditional_escrow.py", "        if _now() >= int(e.resolve_after):\n            raise Exception(\"resolution has started: only the resolved answer can release the funds\")",
     "        pass", "payer cancels after the condition is met"),
    ("conditional_escrow.py", "            if now < int(e.invalid_seen_at) + INVALID_GRACE:\n                raise Exception(\"grace period running\")",
     "            if False:\n                raise Exception(\"x\")", "escrow INVALID refund without grace"),
    ("conditional_escrow.py", "        if _now() < int(e.created_at) + RECOVER_AFTER:\n            raise Exception(\"too early\")\n        if self._qid(eid) != \"\":\n            raise Exception(\"question exists\")",
     "        pass", "escrow recovery while the question is live"),
    ("conditional_escrow.py", "            e.outcome = \"UNRESOLVED_TIMEOUT\"\n            self._reg().emit(on=\"accepted\").cancel_question(qid)",
     "            e.outcome = \"UNRESOLVED_TIMEOUT\"", "escrow timeout refund leaves the question live"),
]



def run(mutant):
    fname, old, new, label = mutant
    d = tempfile.mkdtemp()
    try:
        for f in os.listdir(os.path.join(ROOT, "contracts")):
            if f.endswith(".py"):
                shutil.copy(os.path.join(ROOT, "contracts", f), d)
        p = os.path.join(d, fname)
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        if old not in src:
            return label, "PATTERN NOT FOUND"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new, 1))
        env = dict(os.environ, OR_CONTRACT_DIR=d)
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(ROOT, "tests")],
                           env=env, capture_output=True, text=True)
        return label, "KILLED" if r.returncode != 0 else "SURVIVED"
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    workers = max(1, min(8, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(run, MUTANTS))
    survivors = []
    for label, verdict in results:
        print("%-18s %s" % (verdict, label))
        if verdict != "KILLED":
            survivors.append(label)
    print("mutants: %d, killed: %d" % (len(results), len(results) - len(survivors)))
    print("survivors:", survivors)
    sys.exit(1 if survivors else 0)
