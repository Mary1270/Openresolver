# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass

import datetime
import json
import re

# ---------------------------------------------------------------------------
# ConditionalEscrow - consumer 2 of OpenResolver.
#
# A payer locks GEN for a payee. The funds are released by the FINAL answer of
# an OpenResolver question that this contract itself creates (payer is the
# bounty beneficiary). BINARY: YES -> payee, NO -> payer. SPLIT: every listed
# party gets its share. INVALID -> payer, after a grace period. It has no
# owner and no privileged role anywhere.
#
# Accounting invariant (get_accounting): balance == claimable_total + open_total
# ---------------------------------------------------------------------------

MAX_TITLE = 300
MIN_AMOUNT = 1000
MIN_RESOLVE_LEAD = 300
RECOVER_AFTER = 3600
INVALID_GRACE = 86400
TIMEOUT_GRACE = 86400

ADDR_RE = re.compile(r"0x[0-9a-f]{40}")
DIGITS_RE = re.compile(r"[0-9]+")


def _hex(a):
    return a.as_hex.lower()


def _now():
    return int(datetime.datetime.now().timestamp())


def _parse_shares(answer, n):
    parts = answer.split(",")
    if len(parts) != n:
        return None
    out = []
    for p in parts:
        if DIGITS_RE.fullmatch(p) is None or (len(p) > 1 and p[0] == "0"):
            return None
        v = int(p)
        if v % 1000 != 0:
            return None
        out.append(v)
    if sum(out) != 10000:
        return None
    return out


@allow_storage
@dataclass
class Escrow:
    eid: str
    payer: str
    payee: str
    title: str
    answer_type: str
    parties_json: str
    amount: u256
    bounty: u256
    created_at: u256
    resolve_after: u256
    response_deadline: u256
    state: str
    outcome: str
    invalid_seen_at: u256


class ConditionalEscrow(gl.Contract):
    registry: str
    next_id: u256
    claimable_total: u256
    open_total: u256
    escrows: TreeMap[str, Escrow]
    escrow_ids: DynArray[str]
    claimable: TreeMap[str, u256]

    def __init__(self, registry: str):
        r = registry.strip().lower()
        if ADDR_RE.fullmatch(r) is None:
            raise Exception("invalid registry address")
        self.registry = r

    # ----------------------------------------------------------- internals

    def _sender(self) -> str:
        return _hex(gl.message.sender_address)

    def _self(self) -> str:
        return _hex(gl.message.contract_address)

    def _reg(self):
        return gl.get_contract_at(Address(self.registry))

    def _credit(self, who: str, amt: int):
        if amt <= 0:
            return
        cur = int(self.claimable[who]) if who in self.claimable else 0
        self.claimable[who] = u256(cur + amt)
        self.claimable_total = u256(int(self.claimable_total) + amt)

    def _reject(self, who: str, value: int, reason: str) -> str:
        self._credit(who, value)
        return "REJECTED:" + reason

    def _qid(self, eid: str) -> str:
        return self._reg().view().get_question_id_by_ref(self._self(), eid)

    def _escrow(self, eid: str) -> Escrow:
        if eid not in self.escrows:
            raise Exception("unknown escrow")
        return self.escrows[eid]

    def _pay_out(self, e: Escrow, payouts: list):
        total = 0
        for who, amt in payouts:
            total += amt
            self._credit(who, amt)
        if total != int(e.amount):
            raise Exception("payout does not add up")
        self.open_total = u256(int(self.open_total) - int(e.amount))

    # ------------------------------------------------------------- create

    @gl.public.write.payable
    def create_escrow(self, title: str, answer_type: str, parties_json: str, payee: str,
                      domains_json: str, resolve_after: int, response_deadline: int,
                      challenge_window: int, amount: int) -> str:
        sender = self._sender()
        value = int(gl.message.value)
        now = _now()
        if not isinstance(title, str) or not (1 <= len(title.strip()) <= MAX_TITLE):
            return self._reject(sender, value, "bad title")
        if answer_type != "BINARY" and answer_type != "SPLIT":
            return self._reject(sender, value, "answer type must be BINARY or SPLIT")
        try:
            parties = json.loads(parties_json)
        except Exception:
            return self._reject(sender, value, "parties must be a JSON list")
        if not isinstance(parties, list):
            return self._reject(sender, value, "parties must be a JSON list")
        if not isinstance(payee, str):
            return self._reject(sender, value, "bad payee")
        payee = payee.strip().lower()
        if ADDR_RE.fullmatch(payee) is None:
            return self._reject(sender, value, "bad payee")
        if payee == sender:
            return self._reject(sender, value, "payer and payee must differ")
        if answer_type == "BINARY":
            if len(parties) != 0:
                return self._reject(sender, value, "BINARY takes no parties")
        else:
            if not (2 <= len(parties) <= 8):
                return self._reject(sender, value, "SPLIT needs 2-8 parties")
            for p in parties:
                if not isinstance(p, str) or ADDR_RE.fullmatch(p) is None:
                    return self._reject(sender, value, "parties must be lowercase addresses")
            if len(set(parties)) != len(parties):
                return self._reject(sender, value, "duplicate party")
            if payee not in parties:
                return self._reject(sender, value, "payee must be one of the parties")
        for n in (resolve_after, response_deadline, challenge_window, amount):
            if isinstance(n, bool) or not isinstance(n, int):
                return self._reject(sender, value, "numbers must be integers")
        if amount < MIN_AMOUNT:
            return self._reject(sender, value, "amount too small")
        if value <= amount:
            return self._reject(sender, value, "value must cover the amount plus a bounty")
        if resolve_after < now + MIN_RESOLVE_LEAD:
            return self._reject(sender, value, "resolve_after too soon")
        bounty = value - amount
        eid = "e" + str(int(self.next_id))
        err = self._reg().view().check_question(
            self._self(), title.strip(), answer_type, json.dumps(parties), domains_json, resolve_after,
            response_deadline, challenge_window, sender, eid, bounty)
        if err != "":
            return self._reject(sender, value, "registry would reject the question: " + err)
        self.next_id = u256(int(self.next_id) + 1)
        self.escrows[eid] = Escrow(
            eid=eid, payer=sender, payee=payee, title=title.strip(), answer_type=answer_type,
            parties_json=json.dumps(parties), amount=u256(amount), bounty=u256(bounty),
            created_at=u256(now), resolve_after=u256(resolve_after),
            response_deadline=u256(response_deadline), state="OPEN",
            outcome="", invalid_seen_at=u256(0))
        self.escrow_ids.append(eid)
        self.open_total = u256(int(self.open_total) + amount)
        self._reg().emit(value=u256(bounty), on="accepted").create_question(
            title.strip(), answer_type, json.dumps(parties), domains_json, resolve_after,
            response_deadline, challenge_window, sender, eid)
        return eid

    # ------------------------------------------------------------- cancel

    @gl.public.write
    def cancel_escrow(self, eid: str) -> str:
        e = self._escrow(eid)
        if self._sender() != e.payer:
            raise Exception("payer only")
        if e.state != "OPEN":
            raise Exception("escrow not open")
        if _now() >= int(e.resolve_after):
            raise Exception("resolution has started: only the resolved answer can release the funds")
        qid = self._qid(eid)
        if qid == "":
            raise Exception("question not created yet")
        status = self._reg().view().get_resolution(qid).split("|")[0]
        if status != "OPEN" and status != "UNRESOLVED_TIMEOUT":
            raise Exception("question already proposed: cannot cancel")
        if not self._reg().view().can_cancel(qid):
            raise Exception("question bounty not settled in the bank yet: retry shortly")
        e.state = "CANCELLED"
        e.outcome = "CANCELLED"
        self.open_total = u256(int(self.open_total) - int(e.amount))
        self._credit(e.payer, int(e.amount))
        self._reg().emit(on="accepted").cancel_question(qid)
        return "CANCELLED"

    # ------------------------------------------------------------- settle

    @gl.public.write
    def settle(self, eid: str) -> str:
        e = self._escrow(eid)
        if e.state != "OPEN":
            raise Exception("escrow already settled")
        qid = self._qid(eid)
        if qid == "":
            raise Exception("question not created")
        parts = self._reg().view().get_resolution(qid).split("|")
        status = parts[0]
        final = parts[1] == "true"
        answer = parts[2]
        now = _now()
        amount = int(e.amount)
        if final:
            if e.answer_type == "BINARY" and answer == "YES":
                self._pay_out(e, [(e.payee, amount)])
                e.state = "RELEASED"
                e.outcome = "YES"
                return "RELEASED"
            if e.answer_type == "BINARY" and answer == "NO":
                self._pay_out(e, [(e.payer, amount)])
                e.state = "REFUNDED"
                e.outcome = "NO"
                return "REFUNDED"
            if e.answer_type == "SPLIT" and answer != "INVALID":
                parties = json.loads(e.parties_json)
                shares = _parse_shares(answer, len(parties))
                if shares is not None:
                    payouts = []
                    paid = 0
                    for i in range(len(parties)):
                        part = amount * shares[i] // 10000
                        payouts.append([parties[i], part])
                        paid += part
                    dust = amount - paid
                    if dust > 0:
                        for p in payouts:
                            if p[1] > 0:
                                p[1] += dust
                                break
                    self._pay_out(e, [(p[0], p[1]) for p in payouts])
                    e.state = "SPLIT_PAID"
                    e.outcome = answer
                    return "SPLIT_PAID"
            if int(e.invalid_seen_at) == 0:
                e.invalid_seen_at = u256(now)
                return "INVALID_ARMED"
            if now < int(e.invalid_seen_at) + INVALID_GRACE:
                raise Exception("grace period running")
            self._pay_out(e, [(e.payer, amount)])
            e.state = "REFUNDED"
            e.outcome = "INVALID"
            return "REFUNDED"
        if status == "UNRESOLVED_TIMEOUT" and now > int(e.response_deadline) + TIMEOUT_GRACE:
            self._pay_out(e, [(e.payer, amount)])
            e.state = "REFUNDED"
            e.outcome = "UNRESOLVED_TIMEOUT"
            self._reg().emit(on="accepted").cancel_question(qid)
            return "REFUNDED"
        raise Exception("question not final: " + status)

    @gl.public.write
    def recover_failed_escrow(self, eid: str) -> str:
        e = self._escrow(eid)
        if e.state != "OPEN":
            raise Exception("escrow not open")
        if _now() < int(e.created_at) + RECOVER_AFTER:
            raise Exception("too early")
        if self._qid(eid) != "":
            raise Exception("question exists")
        self._pay_out(e, [(e.payer, int(e.amount))])
        surplus = int(self.balance) - int(self.claimable_total) - int(self.open_total)
        self._credit(e.payer, min(int(e.bounty), max(0, surplus)))
        e.state = "REFUNDED"
        e.outcome = "QUESTION_NEVER_CREATED"
        return "REFUNDED:QUESTION_NEVER_CREATED"

    # ----------------------------------------------------------- withdraw

    @gl.public.write
    def withdraw(self) -> str:
        s = self._sender()
        amt = int(self.claimable[s]) if s in self.claimable else 0
        if amt == 0:
            raise Exception("nothing to withdraw")
        if int(self.balance) < amt:
            raise Exception("insufficient contract balance")
        self.claimable[s] = u256(0)
        self.claimable_total = u256(int(self.claimable_total) - amt)
        gl.get_contract_at(Address(s)).emit_transfer(value=u256(amt))
        return str(amt)

    @gl.public.write
    def withdraw_to_contract(self) -> str:
        s = self._sender()
        amt = int(self.claimable[s]) if s in self.claimable else 0
        if amt == 0:
            raise Exception("nothing to withdraw")
        if int(self.balance) < amt:
            raise Exception("insufficient contract balance")
        self.claimable[s] = u256(0)
        self.claimable_total = u256(int(self.claimable_total) - amt)
        gl.get_contract_at(Address(s)).emit_transfer(value=u256(amt), on="accepted")
        return str(amt)

    # ---------------------------------------------------------------- views

    def _escrow_dict(self, e: Escrow) -> dict:
        return {"id": e.eid, "payer": e.payer, "payee": e.payee, "title": e.title,
                "answer_type": e.answer_type, "parties": json.loads(e.parties_json),
                "amount": int(e.amount), "bounty": int(e.bounty), "created_at": int(e.created_at),
                "resolve_after": int(e.resolve_after),
                "response_deadline": int(e.response_deadline), "state": e.state, "outcome": e.outcome,
                "invalid_seen_at": int(e.invalid_seen_at), "question_id": self._qid(e.eid)}

    @gl.public.view
    def get_escrow(self, eid: str) -> str:
        return json.dumps(self._escrow_dict(self._escrow(eid)))

    @gl.public.view
    def list_escrows(self) -> str:
        out = []
        i = len(self.escrow_ids) - 1
        while i >= 0 and len(out) < 50:
            out.append(self._escrow_dict(self.escrows[self.escrow_ids[i]]))
            i -= 1
        return json.dumps(out)

    @gl.public.view
    def get_claimable(self, addr: str) -> int:
        a = addr.strip().lower()
        return int(self.claimable[a]) if a in self.claimable else 0

    @gl.public.view
    def get_accounting(self) -> str:
        bal = int(self.balance)
        c = int(self.claimable_total)
        o = int(self.open_total)
        return json.dumps({"balance": bal, "claimable_total": c, "open_total": o, "ok": bal == c + o})

    @gl.public.view
    def get_config(self) -> str:
        return json.dumps({"registry": self.registry, "min_amount": MIN_AMOUNT,
                           "invalid_grace": INVALID_GRACE, "timeout_grace": TIMEOUT_GRACE})
