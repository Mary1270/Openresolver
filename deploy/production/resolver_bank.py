# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json
import re
REP_START = 100
REP_MAX = 1000
REP_WIN_STEP = 10
REP_SLASH_STEP = 20
MAX_BOUNTY_BASE = 500000000000000000
MAX_BOUNTY_REP_UNIT = 50
ADDR_RE = re.compile('0x[0-9a-f]{40}')

def _hex(a):
    return a.as_hex.lower()

def _clean_addr(s):
    if not isinstance(s, str):
        raise Exception('address must be a string')
    s = s.strip().lower()
    if ADDR_RE.fullmatch(s) is None:
        raise Exception('invalid address')
    return s

def _bond_multiplier_pct(rep):
    if rep < 60:
        return 200
    if rep < 100:
        return 150
    return 100

def _max_bounty(rep):
    return MAX_BOUNTY_BASE * (rep // MAX_BOUNTY_REP_UNIT + 1)

class ResolverBank(gl.Contract):
    owner: str
    registry: str
    registry_set: bool
    claimable_total: u256
    locked_total: u256
    claimable: TreeMap[str, u256]
    bounty_amt: TreeMap[str, u256]
    bounty_beneficiary: TreeMap[str, str]
    bond_amt: TreeMap[str, u256]
    bond_owner: TreeMap[str, str]
    rep: TreeMap[str, u256]
    wins: TreeMap[str, u256]
    slashes: TreeMap[str, u256]
    resolvers: DynArray[str]

    def __init__(self):
        self.owner = _hex(gl.message.sender_address)
        self.registry = ''
        self.registry_set = False

    def _sender(self) -> str:
        return _hex(gl.message.sender_address)

    def _is_core(self, s: str) -> bool:
        return self.registry_set and s == self.registry

    def _require_core(self):
        if not self._is_core(self._sender()):
            raise Exception('unauthorized: registry only')

    def _payable_guard(self) -> bool:
        s = self._sender()
        v = int(gl.message.value)
        if self._is_core(s):
            return True
        if v == 0:
            raise Exception('unauthorized: registry only')
        self._credit(s, v)
        return False

    def _credit(self, who: str, amt: int):
        if amt <= 0:
            return
        cur = int(self.claimable[who]) if who in self.claimable else 0
        self.claimable[who] = u256(cur + amt)
        self.claimable_total = u256(int(self.claimable_total) + amt)

    def _lock(self, amt: int):
        self.locked_total = u256(int(self.locked_total) + amt)

    def _unlock(self, amt: int):
        self.locked_total = u256(int(self.locked_total) - amt)

    def _touch(self, who: str):
        if who not in self.rep:
            self.rep[who] = u256(REP_START)
            self.wins[who] = u256(0)
            self.slashes[who] = u256(0)
            self.resolvers.append(who)

    def _rep_of(self, who: str) -> int:
        if who in self.rep:
            return int(self.rep[who])
        return REP_START

    @gl.public.write
    def set_registry(self, addr: str) -> str:
        if self._sender() != self.owner:
            raise Exception('owner only')
        if self.registry_set:
            raise Exception('registry already wired')
        self.registry = _clean_addr(addr)
        self.registry_set = True
        return self.registry

    @gl.public.write.payable
    def lock_bounty(self, qid: str, beneficiary: str) -> str:
        if not self._payable_guard():
            return 'REJECTED:unauthorized'
        v = int(gl.message.value)
        beneficiary = _clean_addr(beneficiary)
        if qid in self.bounty_beneficiary or v == 0:
            self._credit(beneficiary, v)
            return 'REJECTED:bounty'
        self.bounty_amt[qid] = u256(v)
        self.bounty_beneficiary[qid] = beneficiary
        self._lock(v)
        return 'OK'

    @gl.public.write.payable
    def lock_bond(self, qid: str, kind: str, who: str) -> str:
        if not self._payable_guard():
            return 'REJECTED:unauthorized'
        v = int(gl.message.value)
        who = _clean_addr(who)
        key = qid + '|' + kind
        if kind not in ('P', 'D', 'A', 'T') or key in self.bond_owner or v == 0:
            self._credit(who, v)
            return 'REJECTED:bond'
        self.bond_amt[key] = u256(v)
        self.bond_owner[key] = who
        self._lock(v)
        return 'OK'

    @gl.public.write.payable
    def credit_refund(self, who: str) -> str:
        if not self._payable_guard():
            return 'REJECTED:unauthorized'
        v = int(gl.message.value)
        self._credit(_clean_addr(who), v)
        return 'OK'

    @gl.public.write
    def pay_bounty(self, qid: str, to: str) -> str:
        self._require_core()
        amt = int(self.bounty_amt[qid]) if qid in self.bounty_amt else 0
        if amt == 0:
            return '0'
        self.bounty_amt[qid] = u256(0)
        self._unlock(amt)
        self._credit(_clean_addr(to), amt)
        return str(amt)

    @gl.public.write
    def refund_bounty(self, qid: str) -> str:
        self._require_core()
        amt = int(self.bounty_amt[qid]) if qid in self.bounty_amt else 0
        if amt == 0:
            return '0'
        self.bounty_amt[qid] = u256(0)
        self._unlock(amt)
        self._credit(self.bounty_beneficiary[qid], amt)
        return str(amt)

    @gl.public.write
    def return_bond(self, qid: str, kind: str) -> str:
        self._require_core()
        key = qid + '|' + kind
        amt = int(self.bond_amt[key]) if key in self.bond_amt else 0
        if amt == 0:
            return '0'
        self.bond_amt[key] = u256(0)
        self._unlock(amt)
        self._credit(self.bond_owner[key], amt)
        return str(amt)

    @gl.public.write
    def award_bond(self, qid: str, kind: str, winner: str, share_pct: int, remainder_to: str) -> str:
        self._require_core()
        if share_pct < 0 or share_pct > 100:
            raise Exception('bad share')
        key = qid + '|' + kind
        amt = int(self.bond_amt[key]) if key in self.bond_amt else 0
        if amt == 0:
            return '0'
        self.bond_amt[key] = u256(0)
        self._unlock(amt)
        w = amt * share_pct // 100
        remainder_to = _clean_addr(remainder_to)
        self._credit(_clean_addr(winner), w)
        self._credit(remainder_to, amt - w)
        return str(amt)

    @gl.public.write
    def rep_win(self, who: str) -> str:
        self._require_core()
        who = _clean_addr(who)
        self._touch(who)
        cur = int(self.rep[who])
        self.rep[who] = u256(min(REP_MAX, cur + REP_WIN_STEP))
        self.wins[who] = u256(int(self.wins[who]) + 1)
        return str(int(self.rep[who]))

    @gl.public.write
    def rep_slash(self, who: str) -> str:
        self._require_core()
        who = _clean_addr(who)
        self._touch(who)
        cur = int(self.rep[who])
        self.rep[who] = u256(max(0, cur - REP_SLASH_STEP))
        self.slashes[who] = u256(int(self.slashes[who]) + 1)
        return str(int(self.rep[who]))

    @gl.public.write
    def withdraw(self) -> str:
        s = self._sender()
        amt = int(self.claimable[s]) if s in self.claimable else 0
        if amt == 0:
            raise Exception('nothing to withdraw')
        self.claimable[s] = u256(0)
        self.claimable_total = u256(int(self.claimable_total) - amt)
        gl.get_contract_at(Address(s)).emit_transfer(value=u256(amt))
        return str(amt)

    @gl.public.write
    def withdraw_to_contract(self) -> str:
        s = self._sender()
        amt = int(self.claimable[s]) if s in self.claimable else 0
        if amt == 0:
            raise Exception('nothing to withdraw')
        self.claimable[s] = u256(0)
        self.claimable_total = u256(int(self.claimable_total) - amt)
        gl.get_contract_at(Address(s)).emit_transfer(value=u256(amt), on='accepted')
        return str(amt)

    @gl.public.view
    def get_claimable(self, addr: str) -> int:
        a = addr.strip().lower()
        return int(self.claimable[a]) if a in self.claimable else 0

    @gl.public.view
    def get_bounty(self, qid: str) -> int:
        return int(self.bounty_amt[qid]) if qid in self.bounty_amt else 0

    @gl.public.view
    def get_bond(self, qid: str, kind: str) -> int:
        key = qid + '|' + kind
        return int(self.bond_amt[key]) if key in self.bond_amt else 0

    @gl.public.view
    def get_reputation(self, addr: str) -> int:
        return self._rep_of(addr.strip().lower())

    @gl.public.view
    def get_bond_multiplier_pct(self, addr: str) -> int:
        return _bond_multiplier_pct(self._rep_of(addr.strip().lower()))

    @gl.public.view
    def get_max_bounty(self, addr: str) -> int:
        return _max_bounty(self._rep_of(addr.strip().lower()))

    @gl.public.view
    def get_resolver(self, addr: str) -> str:
        a = addr.strip().lower()
        w = int(self.wins[a]) if a in self.wins else 0
        s = int(self.slashes[a]) if a in self.slashes else 0
        return json.dumps({'address': a, 'reputation': self._rep_of(a), 'wins': w, 'slashes': s, 'bond_multiplier_pct': _bond_multiplier_pct(self._rep_of(a)), 'max_bounty': _max_bounty(self._rep_of(a))})

    @gl.public.view
    def list_resolvers(self) -> str:
        out = []
        for a in self.resolvers:
            out.append({'address': a, 'reputation': int(self.rep[a]), 'wins': int(self.wins[a]), 'slashes': int(self.slashes[a])})
        out.sort(key=lambda r: (-r['reputation'], r['address']))
        return json.dumps(out[:50])

    @gl.public.view
    def get_accounting(self) -> str:
        bal = int(self.balance)
        c = int(self.claimable_total)
        l = int(self.locked_total)
        return json.dumps({'balance': bal, 'claimable_total': c, 'locked_total': l, 'ok': bal == c + l})

    @gl.public.view
    def get_wiring(self) -> str:
        return json.dumps({'owner': self.owner, 'registry': self.registry, 'registry_set': self.registry_set})
