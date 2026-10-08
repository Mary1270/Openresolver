# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit
PROPOSER_BOND = 100000000000000000
DISPUTE_BOND = 200000000000000000
APPEAL_BOND = 400000000000000000
MIN_BOUNTY = 100000000000000000
MAX_BOUNTY = 10500000000000000000
WINNER_SHARE_PCT = 80
MIN_CHALLENGE_WINDOW = 300
MAX_CHALLENGE_WINDOW = 1209600
MIN_RESPONSE_WINDOW = 300
MAX_RESPONSE_WINDOW = 7776000
MAX_RESOLVE_DELAY = 31536000
STALL_TIMEOUT = 7200
TIMEOUT_TRIGGER_WINDOW = 86400
MAX_PAGE = 50
MAX_QUESTION_TEXT = 600
MAX_EVIDENCE_URLS = 3
MAX_URL_LEN = 300
MAX_DOMAINS = 5
ANSWER_TYPES = ('BINARY', 'CATEGORICAL', 'BUCKETED_RANGE', 'SPLIT')
OPTION_RE = re.compile('[a-z0-9_-]{1,32}')
DOMAIN_RE = re.compile('[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+')
ADDR_RE = re.compile('0x[0-9a-f]{40}')
DIGITS_RE = re.compile('[0-9]+')
REF_RE = re.compile('[A-Za-z0-9_.:-]{1,64}')
URL_FORBIDDEN = '\\<>"\'`{}|^'

def _hex(a):
    return a.as_hex.lower()

def _now():
    return int(datetime.datetime.now().timestamp())

def _clean_addr(s):
    if not isinstance(s, str):
        raise Exception('address must be a string')
    s = s.strip().lower()
    if ADDR_RE.fullmatch(s) is None:
        raise Exception('invalid address')
    return s

def _parse_list(text):
    try:
        v = json.loads(text)
    except Exception:
        return None
    if not isinstance(v, list):
        return None
    return v

def _canon_digits(a):
    if DIGITS_RE.fullmatch(a) is None:
        return None
    if len(a) > 1 and a[0] == '0':
        return None
    return a

def _normalize_answer(answer_type, options, answer):
    if not isinstance(answer, str):
        return None
    a = answer.strip()
    if a == 'INVALID':
        return 'INVALID'
    if answer_type == 'BINARY':
        u = a.upper()
        if u in ('YES', 'NO'):
            return u
        return None
    if answer_type == 'CATEGORICAL':
        if a in options:
            return a
        return None
    if answer_type == 'BUCKETED_RANGE':
        c = _canon_digits(a)
        if c is not None and int(c) <= len(options):
            return c
        return None
    if answer_type == 'SPLIT':
        parts = a.split(',')
        if len(parts) != len(options):
            return None
        total = 0
        for p in parts:
            c = _canon_digits(p)
            if c is None:
                return None
            n = int(c)
            if n % 1000 != 0:
                return None
            total += n
        if total != 10000:
            return None
        return ','.join(parts)
    return None

def _validate_options(answer_type, options):
    if answer_type not in ANSWER_TYPES:
        return 'unknown answer type'
    if answer_type == 'BINARY':
        if len(options) != 0:
            return 'BINARY takes no options'
        return ''
    if answer_type == 'CATEGORICAL':
        if not 2 <= len(options) <= 8:
            return 'CATEGORICAL needs 2-8 options'
        for o in options:
            if not isinstance(o, str) or OPTION_RE.fullmatch(o) is None:
                return 'bad option id'
        if len(set(options)) != len(options):
            return 'duplicate option id'
        return ''
    if answer_type == 'BUCKETED_RANGE':
        if not 1 <= len(options) <= 9:
            return 'BUCKETED_RANGE needs 1-9 edges'
        prev = None
        for e in options:
            if isinstance(e, bool) or not isinstance(e, int):
                return 'edges must be integers'
            if abs(e) > 10 ** 24:
                return 'edge out of range'
            if prev is not None and e <= prev:
                return 'edges must be strictly increasing'
            prev = e
        return ''
    if answer_type == 'SPLIT':
        if not 2 <= len(options) <= 8:
            return 'SPLIT needs 2-8 parties'
        for p in options:
            if not isinstance(p, str) or ADDR_RE.fullmatch(p) is None:
                return 'party must be a lowercase 0x address'
        if len(set(options)) != len(options):
            return 'duplicate party'
        return ''
    return 'unknown answer type'

def _validate_domains(domains):
    if not 1 <= len(domains) <= MAX_DOMAINS:
        return 'need 1-%d allowed domains' % MAX_DOMAINS
    for d in domains:
        if not isinstance(d, str) or len(d) > 100 or DOMAIN_RE.fullmatch(d) is None:
            return 'bad domain'
    if len(set(domains)) != len(domains):
        return 'duplicate domain'
    return ''

def _match_domain(host, domains):
    best = None
    for d in domains:
        if host == d or host.endswith('.' + d):
            if best is None or len(d) > len(best):
                best = d
    return best

def _check_urls(urls, domains):
    if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_EVIDENCE_URLS:
        return 'need 1-%d evidence urls' % MAX_EVIDENCE_URLS
    seen = set()
    for u in urls:
        if not isinstance(u, str) or len(u) > MAX_URL_LEN:
            return 'bad url'
        for c in u:
            if ord(c) < 33 or ord(c) > 126 or c in URL_FORBIDDEN:
                return 'bad url characters'
        try:
            p = urlsplit(u)
            port = p.port
        except Exception:
            return 'bad url'
        if p.scheme != 'https':
            return 'https required'
        if '@' in p.netloc or p.username is not None or p.password is not None:
            return 'userinfo not allowed'
        if port is not None:
            return 'port not allowed'
        host = p.hostname
        if not host:
            return 'missing host'
        key = _match_domain(host, domains)
        if key is None:
            return 'domain not allowed'
        if key in seen:
            return 'two urls from the same domain'
        seen.add(key)
    return ''

def _validate_create(question_text, answer_type, options_json, domains_json, resolve_after, response_deadline, challenge_window, bounty, now):
    if not isinstance(question_text, str) or not 1 <= len(question_text.strip()) <= MAX_QUESTION_TEXT:
        return 'bad question text'
    if not isinstance(answer_type, str):
        return 'unknown answer type'
    options = _parse_list(options_json) if isinstance(options_json, str) else None
    domains = _parse_list(domains_json) if isinstance(domains_json, str) else None
    if options is None or domains is None:
        return 'options and domains must be JSON lists'
    err = _validate_options(answer_type, options)
    if err:
        return err
    err = _validate_domains(domains)
    if err:
        return err
    for n in (resolve_after, response_deadline, challenge_window, bounty):
        if isinstance(n, bool) or not isinstance(n, int):
            return 'numbers must be integers'
    if resolve_after < now or resolve_after > now + MAX_RESOLVE_DELAY:
        return 'resolve_after out of range'
    rw = response_deadline - resolve_after
    if rw < MIN_RESPONSE_WINDOW or rw > MAX_RESPONSE_WINDOW:
        return 'response window out of range'
    if challenge_window < MIN_CHALLENGE_WINDOW or challenge_window > MAX_CHALLENGE_WINDOW:
        return 'challenge window out of range'
    if bounty < MIN_BOUNTY:
        return 'bounty below minimum'
    if bounty > MAX_BOUNTY:
        return 'bounty above maximum'
    return ''

def _spec_hash(question_text, answer_type, options, domains, resolve_after, response_deadline, challenge_window):
    spec = {'q': question_text, 't': answer_type, 'o': options, 'd': domains, 'ra': resolve_after, 'rd': response_deadline, 'cw': challenge_window}
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

@allow_storage
@dataclass
class Question:
    qid: str
    creator: str
    beneficiary: str
    question_text: str
    answer_type: str
    options_json: str
    domains_json: str
    spec_hash: str
    resolve_after: u256
    response_deadline: u256
    challenge_window: u256
    bounty: u256
    created_at: u256
    status: str
    path: str
    answer: str
    snapshot_hash: str
    proposer: str
    proposer_answer: str
    proposer_urls_json: str
    proposed_at: u256
    proposer_bond: u256
    disputer: str
    dispute_answer: str
    dispute_urls_json: str
    disputed_at: u256
    dispute_bond: u256
    first_answer: str
    judged_at: u256
    appellant: str
    appeal_bond: u256
    timeout_caller: str
    timeout_urls_json: str
    timeout_bond: u256
    stage_at: u256
    settled: bool

class QuestionRegistry(gl.Contract):
    owner: str
    bank: str
    engine: str
    bank_set: bool
    engine_set: bool
    next_id: u256
    questions: TreeMap[str, Question]
    question_ids: DynArray[str]
    ref_index: TreeMap[str, str]

    def __init__(self):
        self.owner = _hex(gl.message.sender_address)
        self.bank = ''
        self.engine = ''
        self.bank_set = False
        self.engine_set = False

    def _sender(self) -> str:
        return _hex(gl.message.sender_address)

    def _bank(self):
        return gl.get_contract_at(Address(self.bank))

    def _require_wired(self):
        if not (self.bank_set and self.engine_set):
            raise Exception('registry not wired')

    def _only_engine(self):
        if not self.engine_set or self._sender() != self.engine:
            raise Exception('unauthorized: engine only')

    def _reject(self, who: str, value: int, reason: str) -> str:
        if value > 0:
            self._bank().emit(value=u256(value), on='accepted').credit_refund(who)
        return 'REJECTED:' + reason

    def _lock_exact(self, qid: str, kind: str, who: str, value: int, required: int):
        b = self._bank()
        b.emit(value=u256(required), on='accepted').lock_bond(qid, kind, who)
        if value > required:
            b.emit(value=u256(value - required), on='accepted').credit_refund(who)

    def _require_bonds(self, qid: str, kinds):
        bank = self._bank().view()
        for k in kinds:
            if int(bank.get_bond(qid, k)) == 0:
                raise Exception('bond ' + k + ' not locked in the bank yet: retry shortly')

    def _case_bonds(self, q: Question):
        return ['T'] if q.path == 'TIMEOUT' else ['P', 'D']

    def _remainder_to(self, q: Question, loser: str, winner: str) -> str:
        if q.beneficiary != loser:
            return q.beneficiary
        return winner

    def _get(self, qid: str) -> Question:
        if qid not in self.questions:
            raise Exception('unknown question')
        return self.questions[qid]

    def _status(self, q: Question) -> str:
        if q.status == 'OPEN' and _now() > int(q.response_deadline):
            return 'UNRESOLVED_TIMEOUT'
        return q.status

    def _options(self, q: Question):
        return json.loads(q.options_json)

    def _domains(self, q: Question):
        return json.loads(q.domains_json)

    @gl.public.write
    def set_bank(self, addr: str) -> str:
        if self._sender() != self.owner:
            raise Exception('owner only')
        if self.bank_set:
            raise Exception('bank already wired')
        self.bank = _clean_addr(addr)
        self.bank_set = True
        return self.bank

    @gl.public.write
    def set_engine(self, addr: str) -> str:
        if self._sender() != self.owner:
            raise Exception('owner only')
        if self.engine_set:
            raise Exception('engine already wired')
        self.engine = _clean_addr(addr)
        self.engine_set = True
        return self.engine

    @gl.public.write.payable
    def create_question(self, question_text: str, answer_type: str, options_json: str, domains_json: str, resolve_after: int, response_deadline: int, challenge_window: int, beneficiary: str, ref: str) -> str:
        self._require_wired()
        sender = self._sender()
        value = int(gl.message.value)
        now = _now()
        refund_to = sender
        benef = sender
        benef_error = ''
        if not isinstance(beneficiary, str) or not isinstance(ref, str):
            benef_error = 'beneficiary and ref must be strings'
        elif beneficiary.strip() != '':
            cand = beneficiary.strip().lower()
            if ADDR_RE.fullmatch(cand) is None:
                benef_error = 'invalid beneficiary'
            else:
                benef = cand
                refund_to = cand
        if benef_error:
            return self._reject(sender, value, benef_error)
        err = _validate_create(question_text, answer_type, options_json, domains_json, resolve_after, response_deadline, challenge_window, value, now)
        if err:
            return self._reject(refund_to, value, err)
        question_text = question_text.strip()
        options = _parse_list(options_json)
        domains = _parse_list(domains_json)
        if ref != '':
            if REF_RE.fullmatch(ref) is None:
                return self._reject(refund_to, value, 'invalid ref')
            if sender + '|' + ref in self.ref_index:
                return self._reject(refund_to, value, 'ref already used')
        qid = 'q' + str(int(self.next_id))
        self.next_id = u256(int(self.next_id) + 1)
        zero = ''
        self.questions[qid] = Question(qid=qid, creator=sender, beneficiary=benef, question_text=question_text, answer_type=answer_type, options_json=json.dumps(options), domains_json=json.dumps(domains), spec_hash=_spec_hash(question_text, answer_type, options, domains, resolve_after, response_deadline, challenge_window), resolve_after=u256(resolve_after), response_deadline=u256(response_deadline), challenge_window=u256(challenge_window), bounty=u256(value), created_at=u256(now), status='OPEN', path='', answer='', snapshot_hash='', proposer=zero, proposer_answer='', proposer_urls_json='[]', proposed_at=u256(0), proposer_bond=u256(0), disputer=zero, dispute_answer='', dispute_urls_json='[]', disputed_at=u256(0), dispute_bond=u256(0), first_answer='', judged_at=u256(0), appellant=zero, appeal_bond=u256(0), timeout_caller=zero, timeout_urls_json='[]', timeout_bond=u256(0), stage_at=u256(now), settled=False)
        self.question_ids.append(qid)
        if ref != '':
            self.ref_index[sender + '|' + ref] = qid
        self._bank().emit(value=u256(value), on='accepted').lock_bounty(qid, benef)
        return qid

    @gl.public.write
    def cancel_question(self, qid: str) -> str:
        self._require_wired()
        q = self._get(qid)
        if self._sender() != q.creator:
            raise Exception('creator only')
        if q.status != 'OPEN':
            raise Exception('question can no longer be cancelled')
        if int(self._bank().view().get_bounty(qid)) == 0:
            raise Exception('bounty not locked in the bank yet: retry shortly')
        q.status = 'CANCELLED'
        q.settled = True
        self._bank().emit(on='accepted').refund_bounty(qid)
        return 'CANCELLED'

    @gl.public.write.payable
    def propose(self, qid: str, answer: str, evidence_urls_json: str) -> str:
        self._require_wired()
        sender = self._sender()
        value = int(gl.message.value)
        if qid not in self.questions:
            return self._reject(sender, value, 'unknown question')
        q = self.questions[qid]
        now = _now()
        if q.status != 'OPEN':
            return self._reject(sender, value, 'question not open')
        if now < int(q.resolve_after):
            return self._reject(sender, value, 'too early: resolve_after not reached')
        if now > int(q.response_deadline):
            return self._reject(sender, value, 'response deadline passed')
        if sender == q.creator:
            return self._reject(sender, value, 'creator may not propose')
        norm = _normalize_answer(q.answer_type, self._options(q), answer)
        if norm is None or norm == 'INVALID':
            return self._reject(sender, value, 'answer not valid for this question')
        urls = _parse_list(evidence_urls_json)
        if urls is None:
            return self._reject(sender, value, 'evidence must be a JSON list')
        err = _check_urls(urls, self._domains(q))
        if err:
            return self._reject(sender, value, err)
        bank = self._bank().view()
        if int(bank.get_bounty(qid)) != int(q.bounty):
            return self._reject(sender, value, 'bounty not locked in the bank yet: retry shortly')
        mult = int(bank.get_bond_multiplier_pct(sender))
        if int(q.bounty) > int(bank.get_max_bounty(sender)):
            return self._reject(sender, value, 'bounty exceeds your reputation limit')
        required = PROPOSER_BOND * mult // 100
        if value < required:
            return self._reject(sender, value, 'bond below required %d' % required)
        q.proposer = sender
        q.proposer_answer = norm
        q.proposer_urls_json = json.dumps(urls)
        q.proposed_at = u256(now)
        q.proposer_bond = u256(required)
        q.status = 'PROPOSED'
        self._lock_exact(qid, 'P', sender, value, required)
        return 'PROPOSED'

    @gl.public.write.payable
    def dispute(self, qid: str, counter_answer: str, evidence_urls_json: str) -> str:
        self._require_wired()
        sender = self._sender()
        value = int(gl.message.value)
        if qid not in self.questions:
            return self._reject(sender, value, 'unknown question')
        q = self.questions[qid]
        now = _now()
        if q.status != 'PROPOSED':
            return self._reject(sender, value, 'question not in PROPOSED state')
        if now >= int(q.proposed_at) + int(q.challenge_window):
            return self._reject(sender, value, 'challenge window closed')
        if sender == q.proposer or sender == q.creator:
            return self._reject(sender, value, 'proposer and creator may not dispute')
        norm = _normalize_answer(q.answer_type, self._options(q), counter_answer)
        if norm is None or norm == 'INVALID' or norm == q.proposer_answer:
            return self._reject(sender, value, 'counter answer must be valid and different')
        urls = _parse_list(evidence_urls_json)
        if urls is None:
            return self._reject(sender, value, 'evidence must be a JSON list')
        err = _check_urls(urls, self._domains(q))
        if err:
            return self._reject(sender, value, err)
        mult = int(self._bank().view().get_bond_multiplier_pct(sender))
        required = max(DISPUTE_BOND * mult // 100, int(q.proposer_bond))
        if value < required:
            return self._reject(sender, value, 'bond below required %d' % required)
        q.disputer = sender
        q.dispute_answer = norm
        q.dispute_urls_json = json.dumps(urls)
        q.disputed_at = u256(now)
        q.dispute_bond = u256(required)
        q.stage_at = u256(now)
        q.status = 'DISPUTED'
        self._lock_exact(qid, 'D', sender, value, required)
        return 'DISPUTED'

    @gl.public.write.payable
    def trigger_timeout(self, qid: str, evidence_urls_json: str) -> str:
        self._require_wired()
        sender = self._sender()
        value = int(gl.message.value)
        if qid not in self.questions:
            return self._reject(sender, value, 'unknown question')
        q = self.questions[qid]
        now = _now()
        if q.status != 'OPEN':
            return self._reject(sender, value, 'question not open')
        if now <= int(q.response_deadline):
            return self._reject(sender, value, 'response deadline not reached')
        if now > int(q.response_deadline) + TIMEOUT_TRIGGER_WINDOW:
            return self._reject(sender, value, 'timeout resolution window closed: the creator may cancel')
        if sender == q.creator:
            return self._reject(sender, value, 'creator may not trigger timeout resolution')
        urls = _parse_list(evidence_urls_json)
        if urls is None:
            return self._reject(sender, value, 'evidence must be a JSON list')
        err = _check_urls(urls, self._domains(q))
        if err:
            return self._reject(sender, value, err)
        bank = self._bank().view()
        if int(bank.get_bounty(qid)) != int(q.bounty):
            return self._reject(sender, value, 'bounty not locked in the bank yet: retry shortly')
        mult = int(bank.get_bond_multiplier_pct(sender))
        if int(q.bounty) > int(bank.get_max_bounty(sender)):
            return self._reject(sender, value, 'bounty exceeds your reputation limit')
        required = PROPOSER_BOND * mult // 100
        if value < required:
            return self._reject(sender, value, 'bond below required %d' % required)
        q.timeout_caller = sender
        q.timeout_urls_json = json.dumps(urls)
        q.timeout_bond = u256(required)
        q.path = 'TIMEOUT'
        q.stage_at = u256(now)
        q.status = 'TIMEOUT_PENDING'
        self._lock_exact(qid, 'T', sender, value, required)
        return 'TIMEOUT_PENDING'

    @gl.public.write.payable
    def appeal(self, qid: str) -> str:
        self._require_wired()
        sender = self._sender()
        value = int(gl.message.value)
        if qid not in self.questions:
            return self._reject(sender, value, 'unknown question')
        q = self.questions[qid]
        now = _now()
        if q.status != 'JUDGED':
            return self._reject(sender, value, 'question not in JUDGED state')
        if now >= int(q.judged_at) + int(q.challenge_window):
            return self._reject(sender, value, 'appeal window closed')
        allowed = False
        if q.path == 'TIMEOUT':
            allowed = sender != q.creator
        else:
            if sender == q.proposer and q.proposer_answer != q.first_answer:
                allowed = True
            if sender == q.disputer and q.dispute_answer != q.first_answer:
                allowed = True
        if not allowed:
            return self._reject(sender, value, 'only a side that lost the first verdict may appeal')
        if value < APPEAL_BOND:
            return self._reject(sender, value, 'bond below required %d' % APPEAL_BOND)
        q.appellant = sender
        q.appeal_bond = u256(APPEAL_BOND)
        q.stage_at = u256(now)
        q.status = 'APPEALED'
        self._lock_exact(qid, 'A', sender, value, APPEAL_BOND)
        return 'APPEALED'

    @gl.public.write
    def finalize(self, qid: str) -> str:
        self._require_wired()
        q = self._get(qid)
        now = _now()
        if q.status == 'PROPOSED':
            if now < int(q.proposed_at) + int(q.challenge_window):
                raise Exception('challenge window still open')
            self._require_bonds(qid, ['P'])
            q.status = 'FINAL_OPTIMISTIC'
            q.path = 'OPTIMISTIC'
            q.answer = q.proposer_answer
            q.settled = True
            b = self._bank()
            b.emit(on='accepted').return_bond(qid, 'P')
            b.emit(on='accepted').pay_bounty(qid, q.proposer)
            b.emit(on='accepted').rep_win(q.proposer)
            return 'FINAL_OPTIMISTIC'
        if q.status == 'JUDGED':
            if now < int(q.judged_at) + int(q.challenge_window):
                raise Exception('appeal window still open')
            self._require_bonds(qid, self._case_bonds(q))
            q.status = 'FINAL_CONSENSUS'
            q.answer = q.first_answer
            q.settled = True
            if q.path == 'TIMEOUT':
                self._settle_timeout(q, q.first_answer)
            else:
                self._settle_disputed(q, q.first_answer)
            return 'FINAL_CONSENSUS'
        raise Exception('nothing to finalize in state ' + q.status)

    @gl.public.write
    def record_evidence(self, qid: str, snapshot_hash: str) -> str:
        self._only_engine()
        q = self._get(qid)
        if q.status != 'DISPUTED' and q.status != 'TIMEOUT_PENDING':
            raise Exception('question not awaiting evidence')
        if not isinstance(snapshot_hash, str) or not 8 <= len(snapshot_hash) <= 128:
            raise Exception('bad snapshot hash')
        q.snapshot_hash = snapshot_hash
        q.stage_at = u256(_now())
        q.status = 'EVIDENCE_FROZEN'
        return 'EVIDENCE_FROZEN'

    @gl.public.write
    def record_verdict(self, qid: str, answer: str, round_no: int) -> str:
        self._only_engine()
        q = self._get(qid)
        ans = _normalize_answer(q.answer_type, self._options(q), answer)
        if ans is None:
            raise Exception('verdict is not a legal answer')
        if round_no == 1:
            if q.status != 'EVIDENCE_FROZEN':
                raise Exception('question not awaiting a first verdict')
            if q.path != 'TIMEOUT':
                q.path = 'CONSENSUS'
            q.first_answer = ans
            q.answer = ans
            q.judged_at = u256(_now())
            q.status = 'JUDGED'
            return 'JUDGED'
        if round_no == 2:
            if q.status != 'APPEALED':
                raise Exception('question not awaiting an appeal verdict')
            self._require_bonds(qid, self._case_bonds(q) + ['A'])
            q.status = 'FINAL_CONSENSUS'
            q.answer = ans
            q.settled = True
            b = self._bank()
            if ans == q.first_answer:
                if q.path == 'TIMEOUT':
                    if ans != 'INVALID' and q.timeout_caller != q.appellant:
                        opponent = q.timeout_caller
                    else:
                        opponent = q.beneficiary
                elif q.appellant == q.proposer:
                    opponent = q.disputer if q.dispute_answer == ans else q.beneficiary
                else:
                    opponent = q.proposer if q.proposer_answer == ans else q.beneficiary
                if opponent == q.appellant:
                    b.emit(on='accepted').return_bond(qid, 'A')
                else:
                    b.emit(on='accepted').award_bond(qid, 'A', opponent, WINNER_SHARE_PCT, self._remainder_to(q, q.appellant, opponent))
                b.emit(on='accepted').rep_slash(q.appellant)
            else:
                b.emit(on='accepted').return_bond(qid, 'A')
            if q.path == 'TIMEOUT':
                self._settle_timeout(q, ans)
            else:
                self._settle_disputed(q, ans)
            return 'FINAL_CONSENSUS'
        raise Exception('round must be 1 or 2')

    def _settle_timeout(self, q: Question, final: str):
        b = self._bank()
        b.emit(on='accepted').return_bond(q.qid, 'T')
        if final == 'INVALID':
            b.emit(on='accepted').refund_bounty(q.qid)
        else:
            b.emit(on='accepted').pay_bounty(q.qid, q.timeout_caller)
            b.emit(on='accepted').rep_win(q.timeout_caller)

    @gl.public.write
    def abort_stalled(self, qid: str) -> str:
        self._require_wired()
        q = self._get(qid)
        st = q.status
        if st != 'DISPUTED' and st != 'EVIDENCE_FROZEN' and (st != 'APPEALED') and (st != 'TIMEOUT_PENDING'):
            raise Exception('nothing to abort in state ' + st)
        if _now() < int(q.stage_at) + STALL_TIMEOUT:
            raise Exception('not stalled long enough')
        self._require_bonds(qid, self._case_bonds(q) + (['A'] if st == 'APPEALED' else []))
        b = self._bank()
        was_timeout = q.path == 'TIMEOUT'
        q.status = 'FINAL_CONSENSUS'
        q.settled = True
        if st == 'APPEALED':
            q.answer = q.first_answer
            b.emit(on='accepted').return_bond(qid, 'A')
            if was_timeout:
                self._settle_timeout(q, q.first_answer)
            else:
                self._settle_disputed(q, q.first_answer)
            return 'FINAL_CONSENSUS'
        q.answer = 'INVALID'
        q.path = 'ABORTED'
        if was_timeout:
            b.emit(on='accepted').return_bond(qid, 'T')
        else:
            b.emit(on='accepted').return_bond(qid, 'P')
            b.emit(on='accepted').return_bond(qid, 'D')
        b.emit(on='accepted').refund_bounty(qid)
        return 'FINAL_CONSENSUS'

    def _settle_disputed(self, q: Question, final: str):
        b = self._bank()
        qid = q.qid
        if final == q.proposer_answer:
            b.emit(on='accepted').return_bond(qid, 'P')
            b.emit(on='accepted').award_bond(qid, 'D', q.proposer, WINNER_SHARE_PCT, self._remainder_to(q, q.disputer, q.proposer))
            b.emit(on='accepted').pay_bounty(qid, q.proposer)
            b.emit(on='accepted').rep_win(q.proposer)
            b.emit(on='accepted').rep_slash(q.disputer)
        elif final == q.dispute_answer:
            b.emit(on='accepted').return_bond(qid, 'D')
            b.emit(on='accepted').award_bond(qid, 'P', q.disputer, WINNER_SHARE_PCT, self._remainder_to(q, q.proposer, q.disputer))
            b.emit(on='accepted').pay_bounty(qid, q.disputer)
            b.emit(on='accepted').rep_win(q.disputer)
            b.emit(on='accepted').rep_slash(q.proposer)
        else:
            b.emit(on='accepted').return_bond(qid, 'P')
            b.emit(on='accepted').return_bond(qid, 'D')
            b.emit(on='accepted').refund_bounty(qid)

    @gl.public.view
    def get_resolution(self, qid: str) -> str:
        if qid not in self.questions:
            return 'NOT_FOUND|false||NONE|'
        q = self.questions[qid]
        st = self._status(q)
        final = st == 'FINAL_OPTIMISTIC' or st == 'FINAL_CONSENSUS'
        path = q.path if q.path else 'NONE'
        ans = q.answer if final else ''
        return '|'.join([st, 'true' if final else 'false', ans, path, q.snapshot_hash])

    def _summary(self, q: Question) -> dict:
        st = self._status(q)
        return {'id': q.qid, 'question_text': q.question_text, 'answer_type': q.answer_type, 'status': st, 'final': st in ('FINAL_OPTIMISTIC', 'FINAL_CONSENSUS'), 'answer': q.answer, 'bounty': int(q.bounty), 'creator': q.creator, 'beneficiary': q.beneficiary, 'created_at': int(q.created_at), 'resolve_after': int(q.resolve_after), 'response_deadline': int(q.response_deadline)}

    @gl.public.view
    def list_questions(self) -> str:
        out = []
        i = len(self.question_ids) - 1
        while i >= 0 and len(out) < MAX_PAGE:
            out.append(self._summary(self.questions[self.question_ids[i]]))
            i -= 1
        return json.dumps(out)

    @gl.public.view
    def list_questions_page(self, offset: int, limit: int) -> str:
        n = len(self.question_ids)
        lim = max(1, min(int(limit), MAX_PAGE))
        i = n - 1 - max(0, int(offset))
        out = []
        while i >= 0 and len(out) < lim:
            out.append(self._summary(self.questions[self.question_ids[i]]))
            i -= 1
        return json.dumps({'total': n, 'items': out})

    @gl.public.view
    def get_question(self, qid: str) -> str:
        q = self._get(qid)
        d = self._summary(q)
        d.update({'options': json.loads(q.options_json), 'allowed_evidence_domains': json.loads(q.domains_json), 'spec_hash': q.spec_hash, 'challenge_window': int(q.challenge_window), 'path': q.path, 'snapshot_hash': q.snapshot_hash, 'proposer': q.proposer, 'proposer_answer': q.proposer_answer, 'proposer_urls': json.loads(q.proposer_urls_json), 'proposed_at': int(q.proposed_at), 'proposer_bond': int(q.proposer_bond), 'disputer': q.disputer, 'dispute_answer': q.dispute_answer, 'dispute_urls': json.loads(q.dispute_urls_json), 'disputed_at': int(q.disputed_at), 'dispute_bond': int(q.dispute_bond), 'first_answer': q.first_answer, 'judged_at': int(q.judged_at), 'appellant': q.appellant, 'appeal_bond': int(q.appeal_bond), 'timeout_caller': q.timeout_caller, 'timeout_urls': json.loads(q.timeout_urls_json), 'timeout_bond': int(q.timeout_bond), 'stage_at': int(q.stage_at), 'settled': q.settled})
        return json.dumps(d)

    @gl.public.view
    def get_question_id_by_ref(self, creator: str, ref: str) -> str:
        key = creator.strip().lower() + '|' + ref
        if key in self.ref_index:
            return self.ref_index[key]
        return ''

    @gl.public.view
    def get_case(self, qid: str) -> str:
        q = self._get(qid)
        return json.dumps({'qid': q.qid, 'status': q.status, 'path': q.path, 'question_text': q.question_text, 'answer_type': q.answer_type, 'options_json': q.options_json, 'domains_json': q.domains_json, 'spec_hash': q.spec_hash, 'proposer_answer': q.proposer_answer, 'dispute_answer': q.dispute_answer, 'proposer_urls_json': q.proposer_urls_json, 'dispute_urls_json': q.dispute_urls_json, 'timeout_urls_json': q.timeout_urls_json})

    @gl.public.view
    def get_constants(self) -> str:
        return json.dumps({'proposer_bond': PROPOSER_BOND, 'dispute_bond': DISPUTE_BOND, 'appeal_bond': APPEAL_BOND, 'min_bounty': MIN_BOUNTY, 'max_bounty': MAX_BOUNTY, 'winner_share_pct': WINNER_SHARE_PCT, 'max_resolve_delay': MAX_RESOLVE_DELAY, 'timeout_trigger_window': TIMEOUT_TRIGGER_WINDOW, 'min_challenge_window': MIN_CHALLENGE_WINDOW, 'max_challenge_window': MAX_CHALLENGE_WINDOW, 'min_response_window': MIN_RESPONSE_WINDOW, 'max_response_window': MAX_RESPONSE_WINDOW, 'max_evidence_urls': MAX_EVIDENCE_URLS, 'stall_timeout': STALL_TIMEOUT})

    @gl.public.view
    def can_cancel(self, qid: str) -> bool:
        if qid not in self.questions:
            return False
        q = self.questions[qid]
        if q.status != 'OPEN' or not self.bank_set:
            return False
        return int(self._bank().view().get_bounty(qid)) > 0

    @gl.public.view
    def check_question(self, creator: str, question_text: str, answer_type: str, options_json: str, domains_json: str, resolve_after: int, response_deadline: int, challenge_window: int, beneficiary: str, ref: str, bounty: int) -> str:
        if not (self.bank_set and self.engine_set):
            return 'registry not wired'
        if not isinstance(beneficiary, str) or not isinstance(ref, str) or (not isinstance(creator, str)):
            return 'creator, beneficiary and ref must be strings'
        if beneficiary.strip() != '' and ADDR_RE.fullmatch(beneficiary.strip().lower()) is None:
            return 'invalid beneficiary'
        err = _validate_create(question_text, answer_type, options_json, domains_json, resolve_after, response_deadline, challenge_window, bounty, _now())
        if err:
            return err
        if ref != '':
            if REF_RE.fullmatch(ref) is None:
                return 'invalid ref'
            if creator.strip().lower() + '|' + ref in self.ref_index:
                return 'ref already used'
        return ''

    @gl.public.view
    def get_wiring(self) -> str:
        return json.dumps({'owner': self.owner, 'bank': self.bank, 'engine': self.engine, 'bank_set': self.bank_set, 'engine_set': self.engine_set})
