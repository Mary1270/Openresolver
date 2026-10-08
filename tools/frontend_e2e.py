"""End-to-end check of index.html against the offline contract stack.

Optional (needs Python Playwright + Chromium). genlayer-js is replaced by a
small mock whose readContract / writeContract calls are executed by the same
SDK stub the unit tests use, so every page, form and button runs against the
real contract code. Run:  python3 tools/frontend_e2e.py
"""
import http.server
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from helpers import W, Rollback, GEN, URL_P, URL_D, QUOTE_NO, llm_const  # noqa: E402
from test_consumers import CStack  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

KEYS = {
    "alice": "0x" + "a1" * 32,
    "bob": "0x" + "b2" * 32,
    "carol": "0x" + "c3" * 32,
}
ADDRS = {k: "0x" + v[-40:] for k, v in KEYS.items()}

MOCK = r"""
const enc = (v) => typeof v === "bigint" ? { $big: v.toString() } : Array.isArray(v) ? v.map(enc) : v;
const dec = (v) => v && typeof v === "object" && "$big" in v ? BigInt(v.$big) : v;
async function rpc(body) {
  const r = await fetch("/rpc", { method: "POST", body: JSON.stringify(body) });
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}
export function generatePrivateKey() { return "0x" + Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, "0")).join(""); }
export function createAccount(pk) { return { address: "0x" + pk.slice(-40) }; }
export function createClient({ chain, account }) {
  return {
    async readContract({ address, functionName, args }) {
      return dec((await rpc({ op: "read", address, fn: functionName, args: enc(args || []) })).result);
    },
    async writeContract({ address, functionName, args, value }) {
      return (await rpc({ op: "write", from: account.address, address, fn: functionName, args: enc(args || []), value: String(value || 0n) })).hash;
    },
    async waitForTransactionReceipt({ hash }) {
      return (await rpc({ op: "receipt", hash })).receipt;
    },
  };
}
export const studionet = { id: 0 };
"""

RECEIPTS = {}
LOCK = threading.Lock()


def undecode(v):
    if isinstance(v, dict) and "$big" in v:
        return int(v["$big"])
    if isinstance(v, list):
        return [undecode(x) for x in v]
    return v


def encode(v):
    if isinstance(v, bool) or not isinstance(v, int):
        return v
    return {"$big": str(v)}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html")
        else:
            self._send(404, "nope", "text/plain")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with LOCK:
            try:
                out = handle(body)
            except Exception as e:
                out = {"error": str(e)}
        self._send(200, json.dumps(out), "application/json")


def handle(b):
    if b["op"] == "read":
        try:
            return {"result": encode(W.view(b["address"], b["fn"], *undecode(b["args"])))}
        except Rollback as e:
            return {"error": "view reverted: " + str(e)}
    if b["op"] == "write":
        h = "0x%064x" % (len(RECEIPTS) + 1)
        try:
            res = W.tx(b["from"], b["address"], b["fn"], *undecode(b["args"]), value=int(b["value"]))
            rc = {"consensus_data": {"leader_receipt": [{"execution_result": "SUCCESS", "result": res}]}}
        except Rollback as e:
            rc = {"consensus_data": {"leader_receipt": [{"execution_result": "ERROR", "result": str(e)}]}}
        RECEIPTS[h] = rc
        return {"hash": h}
    if b["op"] == "receipt":
        return {"receipt": RECEIPTS[b["hash"]]}
    raise Exception("bad op")


def main():
    st = CStack()
    W.time = int(time.time())
    for a in ADDRS.values():
        W.fund(a, 1000 * GEN)
    st.supply = W.total_supply()
    W.web[URL_P] = "Official statement: the council confirmed the proposal passed on March 3 with a clear majority."
    W.web[URL_D] = "Independent audit: the audit found the proposal was rejected on March 3 by the full council after a recount."
    W.llm = llm_const("NO", QUOTE_NO)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/" % port
    q = "?registry=%s&bank=%s&engine=%s&pool=%s&escrow=%s" % (st.reg._addr, st.bank._addr, st.eng._addr,
                                                              st.pool._addr, st.esc._addr)
    errors = []
    checks = []

    def check(cond, label):
        checks.append((bool(cond), label))
        print(("PASS " if cond else "FAIL ") + label)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        cfg = json.dumps({"registry": st.reg._addr, "bank": st.bank._addr, "engine": st.eng._addr,
                          "pool": st.pool._addr, "escrow": st.esc._addr})

        def open_as(who, preset=True):
            ctx = browser.new_context(viewport={"width": 375, "height": 800})
            ctx.add_init_script("localStorage.setItem('or_pk', %s)" % json.dumps(KEYS[who]))
            if preset:
                ctx.add_init_script("if (!localStorage.getItem('or_cfg')) localStorage.setItem('or_cfg', %s)" % json.dumps(cfg))
            ctx.route("https://esm.sh/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=MOCK))
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append("%s: %s" % (who, e)))
            pg.on("dialog", lambda d: d.accept())
            pg.clock.install(time=W.time)
            pg.goto(base + q)
            pg.wait_for_selector("h1")
            return pg

        def visit(pg, path):
            pg.goto(base + path)
            pg.reload()
            pg.wait_for_selector("h1")

        def advance(pages, seconds):
            W.advance(seconds)
            for pg in pages:
                pg.clock.set_system_time(W.time)

        def wait_toast(pg, text):
            pg.wait_for_function("t => document.querySelector('#toast').innerText.includes(t)", arg=text, timeout=15000)

        def no_hscroll(pg, label):
            check(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no horizontal scroll: " + label)

        alice = open_as("alice", preset=False)
        check("for this visit only" in alice.inner_text("main"), "link addresses shown as a warning banner")
        alice.goto(base + q + "#/settings")
        alice.click("#sSave")
        wait_toast(alice, "Saved")

        # create a question through the form
        alice.goto(base + "#/create")
        alice.wait_for_selector("#qtext")
        alice.fill("#qtext", "Did the council pass the proposal on March 3?")
        alice.fill("#qdom", "example.com")
        alice.fill("#qres", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 120)))
        alice.fill("#qdl", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 120 + 7200)))
        alice.fill("#qcw", "60")
        alice.fill("#qbounty", "0.5")
        alice.click("#cQ")
        alice.wait_for_function("location.hash.startsWith('#/question/')", timeout=15000)
        qid = alice.evaluate("decodeURIComponent(location.hash.split('/')[2])")
        check(qid == "q0", "question created from the form and opened (%s)" % qid)
        no_hscroll(alice, "question page")

        # bad form input is refused before sending
        alice.goto(base + "#/create")
        alice.wait_for_selector("#qtext")
        alice.fill("#qtext", "Bad domain question")
        alice.fill("#qdom", "not a domain")
        alice.click("#cQ")
        wait_toast(alice, "registry would reject")
        check(len(json.loads(st.rv("list_questions"))) == 1, "invalid question never sent")

        bob = open_as("bob")
        carol = open_as("carol")
        advance([alice, bob, carol], 200)

        visit(bob, "#/question/q0")
        bob.wait_for_selector("#bPropose")
        bob.select_option("#pa", "YES")
        bob.fill("#pu", URL_P)
        bob.click("#bPropose")
        bob.wait_for_selector("text=Challenge window ends", timeout=15000)
        check(st.status("q0") == "PROPOSED", "proposal through the UI")

        visit(alice, "#/question/q0")
        check(alice.query_selector("#bDispute") is None, "creator is not offered a dispute")

        visit(carol, "#/question/q0")
        carol.wait_for_selector("#bDispute")
        carol.select_option("#da", "NO")
        carol.fill("#du", URL_D)
        carol.click("#bDispute")
        carol.wait_for_selector("text=Resolution pipeline", timeout=15000)
        check(st.status("q0") == "DISPUTED", "dispute through the UI")

        for step in ("Open case", "Freeze evidence", "Publish evidence", "Judge (round 1)", "Publish verdict"):
            carol.wait_for_selector("button.eng", timeout=15000)
            label = carol.inner_text("button.eng")
            check(label == step, "pipeline offers '%s' (got '%s')" % (step, label))
            carol.click("button.eng")
            wait_toast(carol, "applied")
            carol.wait_for_function("l => { const b = document.querySelector('button.eng'); return !b || (b.innerText !== l && b.innerText !== 'Working…'); }", arg=label, timeout=15000)
        check(st.status("q0") == "JUDGED", "first verdict recorded")

        visit(bob, "#/question/q0")
        check(bob.query_selector("#bAppeal") is not None, "losing proposer is offered an appeal")
        visit(carol, "#/question/q0")
        check(carol.query_selector("#bAppeal") is None, "winning disputer is not offered an appeal")

        advance([alice, bob, carol], 3700)
        visit(carol, "#/question/q0")
        carol.wait_for_selector("#bFinalize")
        carol.click("#bFinalize")
        carol.wait_for_selector("text=Final answer: NO", timeout=15000)
        check(st.resolution("q0")["answer"] == "NO", "final answer NO")

        carol.goto(base + "#/bank")
        carol.wait_for_selector("#bW")
        carol.click("#bW")
        wait_toast(carol, "withdraw applied")
        check(st.claimable(ADDRS["carol"]) == 0, "withdraw through the UI")

        # market flow
        alice.goto(base + "#/create")
        alice.wait_for_selector("#ckind")
        alice.select_option("#ckind", "m")
        alice.fill("#mtitle", "Will it rain?")
        alice.fill("#mdom", "example.com")
        alice.fill("#mclose", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 600)))
        alice.fill("#mres", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 1200)))
        alice.fill("#mdl", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 1200 + 7200)))
        alice.fill("#mcw", "60")
        alice.click("#cM")
        alice.wait_for_function("location.hash.startsWith('#/market/')", timeout=15000)
        check(alice.evaluate("location.hash") == "#/market/m0", "market created and opened")
        bob.goto(base + "#/market/m0")
        bob.wait_for_selector("#bStake")
        bob.fill("#sa", "0.3")
        bob.click("#bStake")
        bob.wait_for_selector("text=YES: 0.3 GEN", timeout=15000)
        check(json.loads(st.pv("get_market", "m0"))["total"] == 3 * 10 ** 17, "stake through the UI")
        bob.fill("#sa", "0.0000001")
        bob.click("#bStake")
        wait_toast(bob, "was not applied")
        check(st.pv("get_claimable", ADDRS["bob"]) == 10 ** 11, "rejected stake reported as an error and refunded")
        no_hscroll(bob, "market page")

        # escrow flow
        alice.goto(base + "#/create")
        alice.wait_for_selector("#ckind")
        alice.select_option("#ckind", "e")
        alice.fill("#etitle", "Was the package delivered?")
        alice.fill("#epayee", ADDRS["bob"])
        alice.fill("#edom", "example.com")
        alice.fill("#eres", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 600)))
        alice.fill("#edl", time.strftime("%Y-%m-%dT%H:%M", time.localtime(W.time + 600 + 7200)))
        alice.fill("#ecw", "60")
        alice.click("#cE")
        alice.wait_for_function("location.hash.startsWith('#/escrow/')", timeout=15000)
        alice.wait_for_selector("h1:has-text('Was the package delivered?')")
        check(alice.query_selector("#bCancel") is not None, "payer can cancel before resolve_after")
        no_hscroll(alice, "escrow page")

        for path in ("#/", "#/markets", "#/escrows", "#/resolvers", "#/bank", "#/settings"):
            alice.goto(base + path)
            alice.wait_for_selector("h1")
            check("Error" not in alice.inner_text("main")[:200], "page renders: " + path)
            no_hscroll(alice, path)

        # malformed hash does not hang the router
        alice.goto(base + "#/question/%E0%A4%A")
        alice.wait_for_selector("h2")
        check("Error" in alice.inner_text("main"), "malformed link shows an error card")
        browser.close()

    check(not errors, "no uncaught page errors %s" % errors)
    st.check_invariants(type("T", (), {"assertTrue": lambda s, c, m=None: check(c, "invariant %s" % m),
                                       "assertEqual": lambda s, a, b, m=None: check(a == b, "invariant %s" % m)})())
    failed = [label for ok, label in checks if not ok]
    print("checks: %d, failed: %d" % (len(checks), len(failed)))
    srv.shutdown()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
