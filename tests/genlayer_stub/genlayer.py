"""Self-written offline stand-in for the GenLayer Python SDK (no pip needed).

It models only what OpenResolver relies on, and it is deliberately STRICTER
than a naive mock so that GenVM-only mistakes fail offline:

* storage fields are zero-initialised from class annotations and may not be
  assigned a plain dict/list (TreeMap/DynArray), a plain int (u256) or a
  wrong type; undeclared public attributes cannot be assigned
* TreeMap has no .get() (use `in`), values are type-checked
* only @gl.public.* methods are callable from outside; non-payable methods
  reject value; views cannot be used as writes
* a failed transaction rolls back ALL contracts and balances
* emitted messages run AFTER the transaction, asynchronously, each in its own
  sub-transaction (a failing message does not undo its parent)
* gl.nondet.* is only usable inside gl.vm.run_nondet_unsafe, which takes two
  POSITIONAL-ONLY functions, refuses closures that capture a contract, runs
  the leader, then N validators (each with its own view of the web / LLM), and
  needs a strict majority to agree
* Address.as_hex is checksum-like (mixed case) so a missing .lower() is caught
* datetime.datetime.now() follows the stub clock
"""
import copy
import datetime as _dt
import os
import re
import time as _time
import types

__all__ = ["gl", "Address", "u256", "TreeMap", "DynArray", "allow_storage"]

os.environ["TZ"] = "UTC"
try:
    _time.tzset()
except AttributeError:
    pass


class Rollback(Exception):
    def __init__(self, reason, original=None):
        super().__init__(reason)
        self.reason = reason
        self.original = original


class ConsensusFailure(Exception):
    pass


# ------------------------------------------------------------------ types

_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")


class Address:
    __slots__ = ("_h",)

    def __init__(self, v):
        if isinstance(v, Address):
            h = v._h
        elif isinstance(v, str) and _ADDR_RE.fullmatch(v):
            h = v.lower()
        else:
            raise ValueError("invalid address: %r" % (v,))
        object.__setattr__(self, "_h", h)

    @property
    def as_hex(self):
        out = []
        upper = True
        for c in self._h[2:]:
            if c.isalpha():
                out.append(c.upper() if upper else c)
                upper = not upper
            else:
                out.append(c)
        return "0x" + "".join(out)

    def __eq__(self, other):
        return isinstance(other, Address) and other._h == self._h

    def __hash__(self):
        return hash(self._h)

    def __repr__(self):
        return "Address(%s)" % self._h


class u256(int):
    def __new__(cls, v=0):
        if isinstance(v, bool):
            raise TypeError("bool is not a u256")
        v = int(v)
        if v < 0 or v >= 2 ** 256:
            raise OverflowError("u256 out of range: %d" % v)
        return super().__new__(cls, v)


class _Alias:
    def __init__(self, origin, args):
        self.origin = origin
        self.args = args if isinstance(args, tuple) else (args,)


class TreeMap:
    def __class_getitem__(cls, item):
        return _Alias(cls, item)


class DynArray:
    def __class_getitem__(cls, item):
        return _Alias(cls, item)


def _check(t, v, where):
    if isinstance(t, _Alias):
        want = _TreeMapImpl if t.origin is TreeMap else _DynArrayImpl
        if not isinstance(v, want):
            raise AssertionError("Is right the same storage type? %s <- %s (%s)" %
                                 (t.origin.__name__, type(v).__name__, where))
        return
    if t is u256:
        if not isinstance(v, u256):
            raise TypeError("%s expects u256, got %s" % (where, type(v).__name__))
        return
    if t is bool:
        if type(v) is not bool:
            raise TypeError("%s expects bool, got %s" % (where, type(v).__name__))
        return
    if t is str:
        if type(v) is not str:
            raise TypeError("%s expects str, got %s" % (where, type(v).__name__))
        return
    if isinstance(t, type):
        if not isinstance(v, t):
            raise TypeError("%s expects %s, got %s" % (where, t.__name__, type(v).__name__))


class _TreeMapImpl(dict):
    def __init__(self, kt, vt):
        super().__init__()
        self._kt = kt
        self._vt = vt

    def __setitem__(self, k, v):
        _check(self._kt, k, "TreeMap key")
        _check(self._vt, v, "TreeMap value")
        super().__setitem__(k, v)

    def get(self, *a, **k):
        raise AttributeError("TreeMap.get is not available on GenVM: use `in` then []")

    def __deepcopy__(self, memo):
        c = _TreeMapImpl(self._kt, self._vt)
        for k, v in self.items():
            dict.__setitem__(c, copy.deepcopy(k, memo), copy.deepcopy(v, memo))
        return c


class _DynArrayImpl(list):
    def __init__(self, vt):
        super().__init__()
        self._vt = vt

    def append(self, v):
        _check(self._vt, v, "DynArray item")
        super().append(v)

    def __deepcopy__(self, memo):
        c = _DynArrayImpl(self._vt)
        for v in self:
            list.append(c, copy.deepcopy(v, memo))
        return c


def _zero(t):
    if isinstance(t, _Alias):
        if t.origin is TreeMap:
            return _TreeMapImpl(t.args[0], t.args[1])
        return _DynArrayImpl(t.args[0])
    if t is u256:
        return u256(0)
    if t is str:
        return ""
    if t is bool:
        return False
    return None


def _hints(cls):
    out = {}
    for klass in reversed(cls.__mro__):
        out.update(getattr(klass, "__annotations__", {}))
    return out


def allow_storage(cls):
    hints = _hints(cls)
    orig = cls.__setattr__

    def __setattr__(self, name, value):
        if name in hints:
            _check(hints[name], value, "%s.%s" % (cls.__name__, name))
        orig(self, name, value)

    cls.__setattr__ = __setattr__
    return cls


# --------------------------------------------------------------- contract


class Contract:
    def __new__(cls, *a, **k):
        inst = object.__new__(cls)
        for name, t in _hints(cls).items():
            inst.__dict__[name] = _zero(t)
        return inst

    def __setattr__(self, name, value):
        hints = _hints(type(self))
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if name not in hints:
            raise AttributeError("undeclared storage field: " + name)
        _check(hints[name], value, "%s.%s" % (type(self).__name__, name))
        object.__setattr__(self, name, value)

    @property
    def balance(self):
        return u256(W.balances.get(self._addr, 0))


class _Write:
    def __call__(self, fn):
        fn._gl_kind = "write"
        return fn

    def payable(self, fn):
        fn._gl_kind = "payable"
        return fn


class _Public:
    write = _Write()

    @staticmethod
    def view(fn):
        fn._gl_kind = "view"
        return fn


# ------------------------------------------------------------------ world


class _Frame:
    def __init__(self, addr, sender, value, mode):
        self.addr = addr
        self.sender = sender
        self.value = value
        self.mode = mode


class _Msg:
    def __init__(self, sender_address, value, contract_address=None):
        self.sender_address = sender_address
        self.value = u256(value)
        self.contract_address = Address(contract_address) if contract_address else None


class _World:
    def __init__(self):
        self.reset()

    def reset(self):
        self.contracts = {}
        self.balances = {}
        self.time = 1800000000
        self.queue = []
        self.stack = []
        self.web = {}
        self.llm = None
        self.validators = 3
        self.validator_web = {}
        self.validator_llm = {}
        self.failed_messages = []
        self.nondet = None
        self.hold = False
        self.rng = None
        self.tamper = None
        self._n = 0
        self.log = []

    # ---- helpers
    def advance(self, seconds):
        self.time += int(seconds)

    def fund(self, addr, amount):
        a = Address(addr)._h
        self.balances[a] = self.balances.get(a, 0) + int(amount)

    def balance_of(self, addr):
        return self.balances.get(Address(addr)._h, 0)

    def total_supply(self):
        return sum(self.balances.values())

    def deploy(self, cls, deployer, *args):
        self._n += 1
        addr = "0x%040x" % (0xC0DE0000 + self._n)
        inst = cls.__new__(cls)
        object.__setattr__(inst, "_addr", addr)
        self.contracts[addr] = inst
        snap = self._snapshot()
        try:
            self.stack.append(_Frame(addr, Address(deployer), 0, "write"))
            try:
                cls.__init__(inst, *args)
            finally:
                self.stack.pop()
        except Exception as e:
            self._restore(snap)
            del self.contracts[addr]
            raise Rollback("deploy failed: %s" % e, e)
        return inst

    def _snapshot(self):
        return ({a: copy.deepcopy(c.__dict__) for a, c in self.contracts.items()},
                dict(self.balances), len(self.queue))

    def _restore(self, snap):
        states, bal, qlen = snap
        for a, st in states.items():
            self.contracts[a].__dict__.clear()
            self.contracts[a].__dict__.update(st)
        self.balances = dict(bal)
        del self.queue[qlen:]

    def _move(self, frm, to, value):
        if value < 0:
            raise Exception("negative value")
        if value == 0:
            return
        if self.balances.get(frm, 0) < value:
            raise Exception("insufficient balance for transfer")
        self.balances[frm] -= value
        self.balances[to] = self.balances.get(to, 0) + value

    def _invoke(self, addr, method, args, kwargs, sender, value, mode):
        if addr not in self.contracts:
            raise Exception("no contract at " + addr)
        inst = self.contracts[addr]
        fn = getattr(type(inst), method, None)
        kind = getattr(fn, "_gl_kind", None)
        if kind is None:
            raise Exception("method %s is not public" % method)
        if mode == "view" and kind != "view":
            raise Exception("write method %s called as view" % method)
        if value and kind != "payable":
            raise Exception("method %s is not payable" % method)
        self.stack.append(_Frame(addr, sender, value, mode))
        try:
            return fn(inst, *args, **kwargs)
        finally:
            self.stack.pop()

    # ---- public test API
    def tx(self, sender, contract, method, *args, value=0, **kwargs):
        addr = Address(getattr(contract, "_addr", contract))._h
        s = Address(sender)
        snap = self._snapshot()
        try:
            self._move(s._h, addr, int(value))
            res = self._invoke(addr, method, args, kwargs, s, int(value), "write")
        except Exception as e:
            self._restore(snap)
            raise Rollback(str(e), e)
        self._flush()
        return res

    def view(self, contract, method, *args, **kwargs):
        addr = Address(getattr(contract, "_addr", contract))._h
        try:
            return self._invoke(addr, method, args, kwargs, Address("0x" + "0" * 40), 0, "view")
        except Exception as e:
            raise Rollback(str(e), e)

    def deliver(self, reverse=False):
        """Deliver held messages; reverse=True models out-of-order delivery."""
        self.hold = False
        if reverse:
            self.queue.reverse()
        self._flush()

    def deliver_one(self):
        """Deliver exactly the oldest queued message; its children stay queued while held."""
        held = self.hold
        self.hold = True
        try:
            self._run_message(self.queue.pop(0))
        finally:
            self.hold = held

    def _flush(self):
        if self.hold:
            return
        while self.queue:
            self._run_message(self.queue.pop(self.rng.randrange(len(self.queue)) if self.rng else 0))

    def _run_message(self, m):
        snap = self._snapshot()
        held = list(self.queue)
        try:
            self._move(m["from"], m["to"], m["value"])
            if m["kind"] == "call":
                self._invoke(m["to"], m["method"], m["args"], m["kwargs"],
                             Address(m["from"]), m["value"], "write")
        except Exception as e:
            self._restore(snap)
            self.queue[:] = held
            self.failed_messages.append((m["to"], m.get("method"), str(e)))

    # ---- inside contract execution
    def top(self):
        if not self.stack:
            raise Exception("no active message")
        return self.stack[-1]

    def enqueue(self, msg):
        self.queue.append(msg)
        return msg


W = _World()


def _datetime_now(cls_unused=None):
    return _dt.datetime.fromtimestamp(W.time, tz=_dt.timezone.utc).replace(tzinfo=None)


class _FakeDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        base = _dt.datetime.fromtimestamp(W.time, tz=_dt.timezone.utc)
        if tz is None:
            base = base.replace(tzinfo=None)
        else:
            base = base.astimezone(tz)
        return cls(base.year, base.month, base.day, base.hour, base.minute, base.second,
                   base.microsecond, base.tzinfo)


_dt.datetime = _FakeDT


# ---------------------------------------------------------- cross-contract


class _Emitter:
    def __init__(self, msg):
        self._msg = msg

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self._msg["kind"] = "call"
            self._msg["method"] = name
            self._msg["args"] = args
            self._msg["kwargs"] = kwargs
        return call


class _ViewProxy:
    def __init__(self, addr):
        self._addr = addr

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            if W.nondet is not None:
                raise Exception("cross-contract call inside a non-deterministic block")
            caller = W.top().addr
            return W._invoke(self._addr, name, args, kwargs, Address(caller), 0, "view")
        return call


class _ContractAt:
    def __init__(self, addr):
        if not isinstance(addr, Address):
            raise TypeError("get_contract_at expects an Address")
        self._addr = addr._h

    def view(self):
        return _ViewProxy(self._addr)

    def emit(self, *, value=u256(0), on="finalized"):
        if W.nondet is not None:
            raise Exception("cross-contract call inside a non-deterministic block")
        if on not in ("accepted", "finalized"):
            raise ValueError("bad trigger name")
        if not isinstance(value, u256):
            raise TypeError("emit value must be u256")
        msg = {"kind": "plain", "from": W.top().addr, "to": self._addr, "value": int(value),
               "method": None, "args": (), "kwargs": {}}
        W.enqueue(msg)
        return _Emitter(msg)

    def emit_transfer(self, *, value=u256(0), on=None):
        if W.nondet is not None:
            raise Exception("transfer inside a non-deterministic block")
        if self._addr in W.contracts:
            if on not in ("accepted", "finalized"):
                raise ValueError("emit_transfer to a contract needs on='accepted' or 'finalized'")
        elif on is not None:
            raise TypeError("emit_transfer to an EOA takes only value=")
        if not isinstance(value, u256):
            raise TypeError("emit_transfer value must be u256")
        W.enqueue({"kind": "plain", "from": W.top().addr, "to": self._addr, "value": int(value),
                   "method": None, "args": (), "kwargs": {}})


# ------------------------------------------------------------------ nondet


class _Return:
    def __init__(self, calldata):
        self.calldata = calldata


class _UserError(Exception):
    pass


def _captures_contract(fn):
    for cell in (getattr(fn, "__closure__", None) or ()):
        try:
            if isinstance(cell.cell_contents, Contract):
                return True
        except ValueError:
            pass
    return False


def _check_calldata(v, depth=0):
    if depth > 16:
        raise TypeError("leader result nests too deeply for calldata")
    if v is None or isinstance(v, (bool, int, str, bytes)):
        return
    if isinstance(v, (list, tuple)):
        for x in v:
            _check_calldata(x, depth + 1)
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise TypeError("calldata dict keys must be strings")
            _check_calldata(x, depth + 1)
        return
    raise TypeError("leader result is not calldata-encodable: %s" % type(v).__name__)


def _run_nondet_unsafe(leader_fn, validator_fn, /):
    if W.nondet is not None:
        raise Exception("nested non-deterministic block")
    if not callable(leader_fn) or not callable(validator_fn):
        raise TypeError("run_nondet_unsafe needs two callables")
    if _captures_contract(leader_fn) or _captures_contract(validator_fn):
        raise TypeError("closure captures the contract (self): cannot be pickled by GenVM")
    W.nondet = {"web": W.web, "llm": W.llm}
    try:
        leader_result = leader_fn()
    finally:
        W.nondet = None
    if W.tamper is not None:
        leader_result = W.tamper(copy.deepcopy(leader_result))
    _check_calldata(leader_result)
    wrapped = _Return(copy.deepcopy(leader_result))
    agree = 1
    total = 1 + int(W.validators)
    for i in range(int(W.validators)):
        W.nondet = {"web": W.validator_web.get(i, W.web), "llm": W.validator_llm.get(i, W.llm)}
        try:
            try:
                ok = validator_fn(wrapped)
            except Exception:
                ok = False
        finally:
            W.nondet = None
        if ok is True:
            agree += 1
    if agree * 2 <= total:
        raise ConsensusFailure("validators did not agree (%d/%d)" % (agree, total))
    return leader_result


def _render(url, *, mode="text"):
    if W.nondet is None:
        raise Exception("web access outside a non-deterministic block")
    W.log.append(("render", url))
    src = W.nondet["web"].get(url)
    if src is None:
        raise Exception("WEBPAGE_LOAD_FAILED 404 " + url)
    if isinstance(src, Exception):
        raise src
    if callable(src):
        return src()
    return src


def _exec_prompt(prompt, *, response_format="text"):
    if W.nondet is None:
        raise Exception("LLM access outside a non-deterministic block")
    W.log.append(("llm", prompt))
    h = W.nondet["llm"]
    if h is None:
        raise Exception("no LLM configured in stub")
    out = h(prompt)
    if isinstance(out, Exception):
        raise out
    if response_format == "json":
        return copy.deepcopy(out)
    return out


class _GL:
    Contract = Contract
    public = _Public()
    nondet = types.SimpleNamespace(web=types.SimpleNamespace(render=staticmethod(_render)),
                                   exec_prompt=staticmethod(_exec_prompt))
    vm = types.SimpleNamespace(run_nondet_unsafe=staticmethod(_run_nondet_unsafe),
                               Return=_Return, UserError=_UserError)

    @property
    def message(self):
        f = W.top()
        return _Msg(f.sender, f.value, f.addr)

    @staticmethod
    def get_contract_at(addr):
        return _ContractAt(addr)


gl = _GL()
