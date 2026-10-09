# Portal submission

**Title**
OpenResolver: a shared, dispute-proof resolution layer for GenLayer

**Description**
OpenResolver lets any person or contract ask a question with a GEN bounty and get a final,
machine-readable answer. A bonded resolver proposes; if nobody disputes within the challenge
window the answer is final. On a dispute, GenLayer validators freeze the web evidence of both
sides and each derives the answer independently from that frozen evidence, agreeing exactly,
with an exact-quote requirement and one appeal round.

Engineering highlights:
- Evidence integrity: excerpts must match every validator's own fetch exactly, so a dishonest
  leader cannot insert, delete, truncate or hide evidence; delimiter injection is neutralised.
- Liveness: dead or node-dependent URLs are frozen as UNAVAILABLE after a grace period instead
  of blocking a case; every stuck stage has an on-chain exit.
- Funds: a single bank with pull payments, exact bonds and a checked accounting invariant,
  safe under out-of-order message delivery.
- Two real consumers: PredictionPool (pooled N-outcome markets) and ConditionalEscrow
  (payments released by a resolved question), both with pre-validation and recovery paths.
- Quality: 202 offline tests with a strict GenVM stub (dishonest leaders, divergent pages,
  random message order), 51/51 mutants killed, three independent audit rounds, browser end-to-end
  test, CI, and a single-file frontend.

**Live test on GenLayer Studio (real network, five contracts deployed from deploy/demo/)**
- Optimistic path: question created, answer proposed with a bond, challenge window passed, finalized, bounty and bond withdrawn; the bank invariant held after every step.
- Dispute path: a second account disputed the proposed answer (NO) with its own bond; the engine opened the case, froze both evidence documents (identical text agreed by all validators), published the evidence to the registry, and the LLM judge answered YES quoting the frozen evidence ("# OpenResolver"). After the appeal window the case finalized as CONSENSUS; the disputer received its bond, the bounty and 80% of the proposer's bond.
- PredictionPool: market created (its resolution question was created automatically through the registry), two accounts staked on opposite outcomes, the question finalized, the market settled with the right winner, the winner claimed the pool and the creator fee, and the pool balance returned to zero.
- ConditionalEscrow: escrow created with a resolution question, the answer YES released the escrowed amount to the payee, who withdrew it. A create call made less than five minutes before "resolve after" was rejected by the contract and the GEN was credited back, as designed.
- Accounting stayed exact on every contract; the withdraw transfer is asynchronous, so the bank invariant can show a short settling period after a withdraw.

**Tags**
oracle, dispute-resolution, prediction-market, escrow, optimistic, consensus, evidence, infrastructure

**Evidence links**
- Repository: https://github.com/Mary1270/Openresolver
- Architecture and threat model: https://github.com/Mary1270/Openresolver/blob/main/ARCHITECTURE.md
- CI runs: https://github.com/Mary1270/Openresolver/actions
- Frontend: https://mary1270.github.io/Openresolver/ (after enabling GitHub Pages)
- Contract addresses (Studio): bank 0x7Ae76fE417Ab74a6A6Bd16DBE6Bd6Ac583454166, registry 0x1C7CBc478C9E032F7886921019177d0D4eA9474E, engine 0x0DE3F4505CAcd0aFAec3F6D876aC2E6777Cb582A, pool 0x69cAf490e82AfaA552275Cd4231684Ccd0926360, escrow 0x8643515860d2417c60AB9d8B69d436B6a343D9A4
