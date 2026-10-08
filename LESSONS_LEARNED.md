# Lessons learned

1. Access control from day one: one-time owner wiring, `sender_address` checks on every
   cross-contract write, and least privilege (the engine has no authority over funds).
2. A payable method must never revert after taking value; rejections refund through a
   claimable balance, and the client must read the result back instead of trusting "accepted".
3. Never mix a non-deterministic evaluation and a cross-contract call in one method.
4. Validators derive the categorical answer themselves and compare exactly. Numbers are
   bucketed before consensus; no tolerance.
5. Sampling a leader's evidence is not verification: probe windows let a dishonest leader
   insert text between them. Verify the whole excerpt, and never allow edge slack, because a
   short dropped sentence ("CORRECTION: ...") can flip a verdict.
6. Validators must check the leader's quote, not only its answer; otherwise a leader can turn
   any agreed verdict into INVALID with a garbage quote. Conversely, a validator's own imperfect
   quote must not block an honest leader.
7. Liveness and integrity pull in opposite directions for web evidence. Strict per-document
   checks plus a timed fallback (UNAVAILABLE after a grace period) give both: a bad URL can only
   weaken the side that submitted it.
8. Treat fetched text as hostile: neutralise delimiter characters and reject them in URLs.
9. Asynchronous messages arrive in any order. Check that funds are locked before acting on
   them, and test with randomised delivery.
10. Bonds must be exact: accepting an inflated bond let a whale price out every disputer.
11. A consumer must pre-validate with the same rules as the registry and must have a recovery
    path for a question that never appears.
12. An escrow whose payer can cancel after the condition is met protects nobody.
13. Use server-rendered plain-text evidence (for example GitHub raw files).
14. Independent review plus mutation testing: three independent audit rounds, 51 planted bugs, all caught.
15. Frontend: genlayer-js from esm.sh, `createClient({chain: studionet, account})` only.
