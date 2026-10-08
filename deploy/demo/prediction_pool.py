# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import datetime
import json
import re
MAX_TITLE = 300
MAX_FEE_BPS = 1000
MIN_STAKE = 1000000000000000
MIN_CLOSE_LEAD = 60
MIN_RESOLVE_LEAD = 300
TIMEOUT_GRACE = 86400
RECOVER_AFTER = 3600
ADDR_RE = re.compile('0x[0-9a-f]{40}')
OPTION_RE = re.compile('[a-z0-9_-]{1,32}')

def _hex(a):
    return a.as_hex.lower()

def _now():
    return int(datetime.datetime.now().timestamp())

def _outcomes(answer_type, options):
    if answer_type == 'BINARY':
        if len(options) != 0:
            return None
        return ['YES', 'NO']
    if answer_type == 'CATEGORICAL':
        if not 2 <= len(options) <= 8:
            return None
        for o in options:
            if not isinstance(o, str) or OPTION_RE.fullmatch(o) is None:
                return None
        if len(set(options)) != len(options):
            return None
        return list(options)
    if answer_type == 'BUCKETED_RANGE':
        if not 1 <= len(options) <= 9:
            return None
        for e in options:
            if isinstance(e, bool) or not isinstance(e, int):
                return None
        return [str(i) for i in range(len(options) + 1)]
    return None

@allow_storage
@dataclass
class Market:
    mid: str
    creator: str
    title: str
    answer_type: str
    options_json: str
    outcomes_json: str
    created_at: u256
    close_time: u256
    response_deadline: u256
    fee_bps: u256
    bounty: u256
    state: str
    reason: str
    winning: str
    total: u256
    fee: u256
    distributable: u256
    unclaimed: u256
    winner_stake_unclaimed: u256

class PredictionPool(gl.Contract):
    registry: str
    next_id: u256
    claimable_total: u256
    open_total: u256
    markets: TreeMap[str, Market]
    market_ids: DynArray[str]
    claimable: TreeMap[str, u256]
    outcome_pool: TreeMap[str, u256]
    stake_amt: TreeMap[str, u256]
    claimed: TreeMap[str, u256]

    def __init__(self, registry: str):
        r = registry.strip().lower()
        if ADDR_RE.fullmatch(r) is None:
            raise Exception('invalid registry address')
        self.registry = r

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
        return 'REJECTED:' + reason

    def _qid(self, mid: str) -> str:
        return self._reg().view().get_question_id_by_ref(self._self(), mid)

    def _market(self, mid: str) -> Market:
        if mid not in self.markets:
            raise Exception('unknown market')
        return self.markets[mid]

    def _pool(self, mid: str, outcome: str) -> int:
        k = mid + '|' + outcome
        return int(self.outcome_pool[k]) if k in self.outcome_pool else 0

    def _stake(self, mid: str, outcome: str, who: str) -> int:
        k = mid + '|' + outcome + '|' + who
        return int(self.stake_amt[k]) if k in self.stake_amt else 0

    @gl.public.write.payable
    def create_market(self, title: str, answer_type: str, options_json: str, domains_json: str, close_time: int, resolve_after: int, response_deadline: int, challenge_window: int, fee_bps: int) -> str:
        sender = self._sender()
        value = int(gl.message.value)
        now = _now()
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= MAX_TITLE:
            return self._reject(sender, value, 'bad title')
        try:
            options = json.loads(options_json)
        except Exception:
            return self._reject(sender, value, 'options must be a JSON list')
        if not isinstance(options, list):
            return self._reject(sender, value, 'options must be a JSON list')
        outcomes = _outcomes(answer_type, options)
        if outcomes is None:
            return self._reject(sender, value, 'answer type must be BINARY, CATEGORICAL or BUCKETED_RANGE with valid options')
        for n in (close_time, resolve_after, response_deadline, challenge_window, fee_bps):
            if isinstance(n, bool) or not isinstance(n, int):
                return self._reject(sender, value, 'numbers must be integers')
        if fee_bps < 0 or fee_bps > MAX_FEE_BPS:
            return self._reject(sender, value, 'fee too high')
        if close_time < now + MIN_CLOSE_LEAD:
            return self._reject(sender, value, 'close time too soon')
        if resolve_after < now + MIN_RESOLVE_LEAD or close_time > resolve_after:
            return self._reject(sender, value, 'market must close before it can be resolved')
        mid = 'm' + str(int(self.next_id))
        err = self._reg().view().check_question(self._self(), title.strip(), answer_type, json.dumps(options), domains_json, resolve_after, response_deadline, challenge_window, sender, mid, value)
        if err != '':
            return self._reject(sender, value, 'registry would reject the question: ' + err)
        self.next_id = u256(int(self.next_id) + 1)
        self.markets[mid] = Market(mid=mid, creator=sender, title=title.strip(), answer_type=answer_type, options_json=json.dumps(options), outcomes_json=json.dumps(outcomes), created_at=u256(now), close_time=u256(close_time), response_deadline=u256(response_deadline), fee_bps=u256(fee_bps), bounty=u256(value), state='OPEN', reason='', winning='', total=u256(0), fee=u256(0), distributable=u256(0), unclaimed=u256(0), winner_stake_unclaimed=u256(0))
        self.market_ids.append(mid)
        self._reg().emit(value=u256(value), on='accepted').create_question(title.strip(), answer_type, json.dumps(options), domains_json, resolve_after, response_deadline, challenge_window, sender, mid)
        return mid

    @gl.public.write.payable
    def stake(self, mid: str, outcome: str) -> str:
        sender = self._sender()
        value = int(gl.message.value)
        if mid not in self.markets:
            return self._reject(sender, value, 'unknown market')
        m = self.markets[mid]
        if m.state != 'OPEN':
            return self._reject(sender, value, 'market not open')
        if _now() >= int(m.close_time):
            return self._reject(sender, value, 'staking closed')
        if outcome not in json.loads(m.outcomes_json):
            return self._reject(sender, value, 'unknown outcome')
        if value < MIN_STAKE:
            return self._reject(sender, value, 'stake below minimum')
        if self._qid(mid) == '':
            return self._reject(sender, value, 'question not created yet')
        pk = mid + '|' + outcome
        sk = pk + '|' + sender
        self.outcome_pool[pk] = u256(self._pool(mid, outcome) + value)
        self.stake_amt[sk] = u256(self._stake(mid, outcome, sender) + value)
        m.total = u256(int(m.total) + value)
        self.open_total = u256(int(self.open_total) + value)
        return 'STAKED'

    @gl.public.write
    def settle(self, mid: str) -> str:
        m = self._market(mid)
        if m.state != 'OPEN':
            raise Exception('market already settled')
        now = _now()
        if now < int(m.close_time):
            raise Exception('market still open for staking')
        qid = self._qid(mid)
        if qid == '':
            raise Exception('question not created')
        parts = self._reg().view().get_resolution(qid).split('|')
        status = parts[0]
        final = parts[1] == 'true'
        answer = parts[2]
        outcomes = json.loads(m.outcomes_json)
        if final:
            if answer != 'INVALID' and answer in outcomes and (self._pool(mid, answer) > 0):
                total = int(m.total)
                fee = total * int(m.fee_bps) // 10000
                m.state = 'SETTLED'
                m.winning = answer
                m.fee = u256(fee)
                m.distributable = u256(total - fee)
                m.unclaimed = u256(total - fee)
                m.winner_stake_unclaimed = u256(self._pool(mid, answer))
                if fee > 0:
                    self.open_total = u256(int(self.open_total) - fee)
                    self._credit(m.creator, fee)
                return 'SETTLED:' + answer
            m.state = 'REFUNDED'
            if answer == 'INVALID':
                m.reason = 'INVALID'
            elif answer not in outcomes:
                m.reason = 'UNUSABLE_ANSWER'
            else:
                m.reason = 'NO_WINNING_STAKE'
            return 'REFUNDED:' + m.reason
        if status == 'UNRESOLVED_TIMEOUT' and now > int(m.response_deadline) + TIMEOUT_GRACE:
            m.state = 'REFUNDED'
            m.reason = 'UNRESOLVED_TIMEOUT'
            self._reg().emit(on='accepted').cancel_question(qid)
            return 'REFUNDED:UNRESOLVED_TIMEOUT'
        raise Exception('question not final: ' + status)

    @gl.public.write
    def recover_failed_market(self, mid: str) -> str:
        m = self._market(mid)
        if m.state != 'OPEN':
            raise Exception('market not open')
        if _now() < int(m.created_at) + RECOVER_AFTER:
            raise Exception('too early')
        if self._qid(mid) != '':
            raise Exception('question exists')
        m.state = 'REFUNDED'
        m.reason = 'QUESTION_NEVER_CREATED'
        surplus = int(self.balance) - int(self.claimable_total) - int(self.open_total)
        back = min(int(m.bounty), max(0, surplus))
        self._credit(m.creator, back)
        return 'REFUNDED:QUESTION_NEVER_CREATED'

    @gl.public.write
    def claim(self, mid: str) -> str:
        m = self._market(mid)
        who = self._sender()
        if m.state == 'OPEN':
            raise Exception('market not settled')
        ck = mid + '|' + who
        if ck in self.claimed:
            raise Exception('already claimed')
        if m.state == 'SETTLED':
            s = self._stake(mid, m.winning, who)
            if s == 0:
                raise Exception('nothing to claim')
            pool_win = self._pool(mid, m.winning)
            if s == int(m.winner_stake_unclaimed):
                payout = int(m.unclaimed)
            else:
                payout = s * int(m.distributable) // pool_win
                payout = min(payout, int(m.unclaimed))
            m.unclaimed = u256(int(m.unclaimed) - payout)
            m.winner_stake_unclaimed = u256(int(m.winner_stake_unclaimed) - s)
        else:
            payout = 0
            for o in json.loads(m.outcomes_json):
                payout += self._stake(mid, o, who)
            if payout == 0:
                raise Exception('nothing to claim')
        self.claimed[ck] = u256(1)
        self.open_total = u256(int(self.open_total) - payout)
        self._credit(who, payout)
        return str(payout)

    @gl.public.write
    def withdraw(self) -> str:
        s = self._sender()
        amt = int(self.claimable[s]) if s in self.claimable else 0
        if amt == 0:
            raise Exception('nothing to withdraw')
        if int(self.balance) < amt:
            raise Exception('insufficient contract balance')
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
        if int(self.balance) < amt:
            raise Exception('insufficient contract balance')
        self.claimable[s] = u256(0)
        self.claimable_total = u256(int(self.claimable_total) - amt)
        gl.get_contract_at(Address(s)).emit_transfer(value=u256(amt), on='accepted')
        return str(amt)

    def _market_dict(self, m: Market) -> dict:
        outcomes = json.loads(m.outcomes_json)
        qid = self._qid(m.mid)
        return {'id': m.mid, 'creator': m.creator, 'title': m.title, 'answer_type': m.answer_type, 'options': json.loads(m.options_json), 'outcomes': outcomes, 'pools': {o: self._pool(m.mid, o) for o in outcomes}, 'created_at': int(m.created_at), 'close_time': int(m.close_time), 'response_deadline': int(m.response_deadline), 'fee_bps': int(m.fee_bps), 'bounty': int(m.bounty), 'state': m.state, 'reason': m.reason, 'winning': m.winning, 'total': int(m.total), 'fee': int(m.fee), 'distributable': int(m.distributable), 'unclaimed': int(m.unclaimed), 'question_id': qid}

    @gl.public.view
    def get_market(self, mid: str) -> str:
        return json.dumps(self._market_dict(self._market(mid)))

    @gl.public.view
    def list_markets(self) -> str:
        out = []
        i = len(self.market_ids) - 1
        while i >= 0 and len(out) < 50:
            out.append(self._market_dict(self.markets[self.market_ids[i]]))
            i -= 1
        return json.dumps(out)

    @gl.public.view
    def get_positions(self, addr: str) -> str:
        a = addr.strip().lower()
        out = []
        for mid in self.market_ids:
            m = self.markets[mid]
            for o in json.loads(m.outcomes_json):
                s = self._stake(mid, o, a)
                if s > 0:
                    out.append({'market': mid, 'outcome': o, 'amount': s, 'claimed': mid + '|' + a in self.claimed})
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
        return json.dumps({'balance': bal, 'claimable_total': c, 'open_total': o, 'ok': bal == c + o})

    @gl.public.view
    def get_config(self) -> str:
        return json.dumps({'registry': self.registry, 'min_stake': MIN_STAKE, 'max_fee_bps': MAX_FEE_BPS, 'timeout_grace': TIMEOUT_GRACE})
