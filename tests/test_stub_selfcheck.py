"""The stub must be strict, otherwise passing tests prove nothing."""
import unittest

from helpers import W, Rollback, OWNER, ALICE, GEN
from _bootstrap import G

gl = G.gl
TreeMap = G.TreeMap
DynArray = G.DynArray
u256 = G.u256
Address = G.Address


class Probe(gl.Contract):
    m: TreeMap[str, u256]
    arr: DynArray[str]
    n: u256
    s: str
    flag: bool

    def __init__(self):
        self.s = "init"

    @gl.public.write
    def bad_dict(self):
        self.m = {}

    @gl.public.write
    def bad_int(self):
        self.n = 5

    @gl.public.write
    def good_int(self):
        self.n = u256(int(self.n) + 1)

    @gl.public.write
    def bad_map_value(self):
        self.m["a"] = 5

    @gl.public.write
    def use_get(self):
        return self.m.get("a", 0)

    @gl.public.write
    def undeclared(self):
        self.mystery = 1

    @gl.public.write
    def write_then_fail(self):
        self.n = u256(9)
        raise Exception("boom")

    @gl.public.write.payable
    def take(self):
        return "ok"

    @gl.public.write
    def notpayable(self):
        return "ok"

    @gl.public.view
    def read(self):
        return int(self.n)

    @gl.public.write
    def web_outside(self):
        return gl.nondet.web.render("https://example.com/x")

    @gl.public.write
    def kwargs_nondet(self):
        return gl.vm.run_nondet_unsafe(leader_fn=lambda: 1, validator_fn=lambda r: True)

    @gl.public.write
    def capture_self(self):
        return gl.vm.run_nondet_unsafe(lambda: self.s, lambda r: True)

    @gl.public.write
    def cross_inside_nondet(self):
        def leader():
            gl.get_contract_at(Address("0x" + "1" * 40)).emit(on="accepted")
            return 1
        return gl.vm.run_nondet_unsafe(leader, lambda r: True)

    @gl.public.write
    def emit_to(self, to: str, amount: int):
        gl.get_contract_at(Address(to)).emit_transfer(value=u256(amount))

    @gl.public.write
    def emit_bad_trigger(self, to: str):
        gl.get_contract_at(Address(to)).emit_transfer(value=u256(1), on="accepted")

    @gl.public.write
    def emit_plain_int(self, to: str):
        gl.get_contract_at(Address(to)).emit_transfer(value=1)

    @gl.public.view
    def sender_hex(self):
        return gl.message.sender_address.as_hex


class StubStrictness(unittest.TestCase):
    def setUp(self):
        W.reset()
        W.fund(OWNER, 10 * GEN)
        W.fund(ALICE, 10 * GEN)
        self.p = W.deploy(Probe, OWNER)

    def test_zero_init_and_storage_types(self):
        self.assertEqual(self.p.s, "init")
        self.assertEqual(int(self.p.n), 0)
        for m in ("bad_dict", "bad_int", "bad_map_value", "undeclared"):
            with self.assertRaises(Rollback, msg=m):
                W.tx(OWNER, self.p, m)
        W.tx(OWNER, self.p, "good_int")
        self.assertEqual(W.view(self.p, "read"), 1)

    def test_treemap_get_unavailable(self):
        with self.assertRaises(Rollback):
            W.tx(OWNER, self.p, "use_get")

    def test_failed_tx_rolls_back_state_and_value(self):
        before = W.balance_of(ALICE)
        with self.assertRaises(Rollback):
            W.tx(ALICE, self.p, "write_then_fail", value=0)
        self.assertEqual(W.view(self.p, "read"), 0)
        with self.assertRaises(Rollback):
            W.tx(ALICE, self.p, "notpayable", value=5)
        self.assertEqual(W.balance_of(ALICE), before)
        self.assertEqual(W.balance_of(self.p._addr), 0)
        W.tx(ALICE, self.p, "take", value=7)
        self.assertEqual(W.balance_of(self.p._addr), 7)

    def test_nondet_rules(self):
        for m in ("web_outside", "kwargs_nondet", "capture_self", "cross_inside_nondet"):
            with self.assertRaises(Rollback, msg=m):
                W.tx(OWNER, self.p, m)

    def test_emit_rules_and_async_delivery(self):
        W.tx(ALICE, self.p, "take", value=100)
        W.tx(OWNER, self.p, "emit_to", ALICE, 40)
        self.assertEqual(W.balance_of(self.p._addr), 60)
        with self.assertRaises(Rollback):
            W.tx(OWNER, self.p, "emit_bad_trigger", ALICE)
        with self.assertRaises(Rollback):
            W.tx(OWNER, self.p, "emit_plain_int", ALICE)
        W.tx(OWNER, self.p, "emit_to", ALICE, 10 ** 6)
        self.assertEqual(len(W.failed_messages), 1)
        W.failed_messages[:] = []

    def test_address_hex_is_mixed_case_so_missing_lower_is_caught(self):
        h = W.tx(ALICE, self.p, "sender_hex")
        self.assertNotEqual(h, h.lower())
        self.assertEqual(Address(h), Address(h.lower()))

    def test_u256_bounds(self):
        with self.assertRaises(OverflowError):
            u256(-1)
        with self.assertRaises(OverflowError):
            u256(2 ** 256)
        with self.assertRaises(TypeError):
            u256(True)

    def test_clock_follows_stub_time(self):
        import datetime
        W.time = 1900000000
        self.assertEqual(int(datetime.datetime.now().timestamp()), 1900000000)


if __name__ == "__main__":
    unittest.main()
