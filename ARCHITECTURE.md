# OpenResolver - Architecture

OpenResolver is a shared, reusable resolution layer for GenLayer. Any person or contract
can ask a question with a bounty. A bonded resolver proposes an answer; if nobody disputes
it within the challenge window it is final (optimistic path). If it is disputed, validators
freeze web evidence from both sides and each derives the answer from that frozen evidence
only, agreeing exactly (consensus path).

## Contracts

| Contract | Holds GEN | Role |
|---|---|---|
| ResolverBank | yes, all resolution GEN | bounties, bonds, claimable balances (pull payments), reputation; only the registry may move funds |
| QuestionRegistry | no | lifecycle and public read interface; forwards every payable value to the bank |
| ResolverEngine | no | evidence freeze and consensus verdicts; writes results to the registry only |
| PredictionPool | its own stakes | consumer 1: pooled N-outcome market |
| ConditionalEscrow | its own escrows | consumer 2: payment released by a resolved question |

Consumers have no owner and no privileged role; their only constructor argument is the registry
address. They create questions as ordinary callers and read answers through `get_resolution`.

## Wiring and access control

Each link is set once by the owner and then frozen: `bank.set_registry`, `registry.set_bank`,
`registry.set_engine`, `engine.set_registry`. The bank trusts only the registry (least
privilege: the engine cannot touch funds). The registry accepts `record_evidence` and
`record_verdict` only from the engine. Payable methods never revert after taking value: a
rejected call credits the value to the caller's (or beneficiary's) claimable balance and
returns `REJECTED:<reason>`.

## Question lifecycle

```
OPEN --propose--> PROPOSED --(challenge window)--> FINAL_OPTIMISTIC
                     \--dispute--> DISPUTED -> EVIDENCE_FROZEN -> JUDGED --(appeal window)--> FINAL_CONSENSUS
                                                                    \--appeal--> APPEALED --> FINAL_CONSENSUS
OPEN past the response deadline = UNRESOLVED_TIMEOUT (virtual)
   --trigger_timeout (within TIMEOUT_TRIGGER_WINDOW)--> TIMEOUT_PENDING -> EVIDENCE_FROZEN -> JUDGED -> (appeal) -> FINAL_CONSENSUS
OPEN / UNRESOLVED_TIMEOUT --creator cancels--> CANCELLED
DISPUTED, EVIDENCE_FROZEN, TIMEOUT_PENDING stuck > STALL_TIMEOUT --abort_stalled--> FINAL_CONSENSUS (INVALID, ABORTED)
APPEALED stuck > STALL_TIMEOUT --abort_stalled--> FINAL_CONSENSUS (first verdict stands, appeal bond returned)
```

`get_resolution(qid)` returns `status|final|answer|path|snapshot_hash` (paths OPTIMISTIC,
CONSENSUS, TIMEOUT, ABORTED, NONE). Consumers act only on `final=true`.

## Answer types

BINARY (YES/NO), CATEGORICAL (2-8 option ids), BUCKETED_RANGE (bucket index over 1-9 strictly
increasing integer edges), SPLIT (shares of 10000 in multiples of 1000, one per party). INVALID
is always a legal consensus outcome but can never be proposed or counter-proposed. Every answer
is categorical (numbers are bucketed first), so validators compare exactly, with no tolerance.

## Evidence freeze

1. `open_case` copies the case from the registry; every evidence URL starts as PENDING.
2. The leader fetches each URL twice and normalises it (whitespace collapsed; `<` and `>`
   replaced so evidence can never forge prompt delimiters). A page whose two fetches agree on
   the first 4000 characters is frozen as OK with that excerpt; otherwise UNAVAILABLE.
3. Each validator re-fetches. An OK excerpt must equal exactly the first 4000 characters of the
   validator's own text (nothing inserted, deleted, reordered or truncated) and its
   `content_hash` must be `sha256(excerpt)`. An UNAVAILABLE claim is accepted only if the
   validator's own two fetches also fail or disagree, so a reachable stable page cannot be hidden.
4. `freeze_evidence` captures all pending documents; `freeze_url` captures one, so one bad page
   never blocks the others.
5. A page that renders differently on different nodes can never be captured. After
   `FREEZE_GRACE` from the case opening, `release_unfrozen` marks remaining documents
   UNAVAILABLE and the case continues. A dead or unstable URL can only weaken the side that
   submitted it; it can never block or annul a case.
6. `snapshot_hash = sha256([side, url, status, sha256(excerpt)] ...)`.

## Judging

The prompt contains the question, the allowed answers, both claims (labelled as possibly
wrong) and the frozen excerpts inside delimiters, marked as untrusted data; UNAVAILABLE
documents appear as "supports no claim". The leader's answer needs a quote that is an exact
substring of an OK excerpt. A quote copied loosely (case, punctuation, spacing) is mapped back to
the exact passage; a quote that cannot be found makes the answer INVALID.

Validators accept:
- a non-INVALID leader answer only if it is canonical, the leader's quote is in the frozen
  evidence, and the validator's own model gives the same answer (the validator's own quote
  wording never blocks an honest leader);
- an INVALID leader answer only if the validator's model also says INVALID or cannot ground its
  answer even under a loose match (case, punctuation and spacing ignored), so a dishonest leader
  cannot force INVALID over well-supported evidence.

The quote is checked again outside the non-deterministic block. `publish_evidence` and
`publish_verdict` reach the registry with `on="finalized"`, so the registry never acts on an
engine result that could still be overturned. The side that lost the first verdict may appeal
once (on the TIMEOUT path anyone except the creator); round 2 re-judges the same snapshot.

## Economics

| Item | Rule |
|---|---|
| Proposer bond | 0.1 GEN x reputation multiplier; exactly that is locked, any excess is refunded |
| Dispute bond | max(0.2 GEN x multiplier, proposer bond) |
| Timeout bond | as proposer bond; returned on every verdict; bounty paid unless INVALID |
| Appeal bond | 0.4 GEN, exact; a lost appeal pays the side that was right (80%) and the beneficiary; if nobody else was right it is returned |
| Bounty | 0.1 to 10.5 GEN (10.5 = bounty cap at maximum reputation, so every question is resolvable) |
| Loser's bond | 80% to the winner, 20% to the beneficiary (to the winner if the beneficiary is the loser) |
| Third answer / INVALID | both bonds returned, bounty refunded to the beneficiary |
| Reputation | start 100, max 1000; every win (optimistic, contested or timeout) +10, every loss -20 |
| Bond multiplier | rep < 60: 200%, rep < 100: 150%, else 100% |
| Bounty cap per resolver | 0.5 GEN x (rep // 50 + 1) |

Bank invariant, asserted after every transaction in the tests: `balance == claimable_total + locked_total`.

## Message ordering

Cross-contract messages are asynchronous and may arrive in any order. The registry refuses
to cancel, propose or trigger a timeout until the bounty is locked in the bank, and refuses to
settle until every bond involved is locked (the refused settlement fails only its own message,
and anyone can re-send it). The suite replays random walks with random delivery order.

## Beneficiary, ref and pre-check

`create_question(..., beneficiary, ref)` lets a consumer be the creator while the user is the
beneficiary: refunds and bond remainders go straight to the beneficiary inside the bank, so
consumers never handle resolution funds. `ref` (`[A-Za-z0-9_.:-]{1,64}`) is unique per
creator; `get_question_id_by_ref(consumer, id)` finds a consumer's question. The view
`check_question(...)` runs exactly the validation of `create_question`; consumers call it
before accepting value, and `can_cancel(qid)` before cancelling.

## Consumers

The timeout path can only be started within `TIMEOUT_TRIGGER_WINDOW` (24 h) after the response
deadline. Consumers refund only after the same 24 h, so a consumer refund and a timeout
resolution can never both happen.

**PredictionPool**: stake until close; once the question is final anyone calls `settle`.
Winners share the pool minus the creator fee pro rata; the last claimer sweeps rounding dust.
INVALID, no winning stake, or an unresolved timeout (after a 24 h grace) refunds every stake;
on a timeout refund the pool also cancels its question so the bounty returns to the creator.
`recover_failed_market` refunds a market whose question was never created (after one hour),
including a bounty that bounced back.

**ConditionalEscrow**: the payer locks an amount plus a bounty. BINARY: YES pays the payee,
NO refunds the payer; SPLIT pays every party its share; INVALID refunds the payer after a grace
period. The payer may cancel only before `resolve_after`; after that only the resolved answer
moves the money. `recover_failed_escrow` refunds an escrow whose question was never created.

## Known limits

- The offline stub is not GenVM. It models consensus, dishonest leaders, divergent pages,
  asynchronous out-of-order messages and rollback to test logic, accounting and access control.
- The optimistic path has no LLM check by design; its security is bonds plus an open challenge.
- Allowed domains are chosen by the creator and frozen. A domain hosting user content (including
  its subdomains) can carry prompt-injection text; delimiters and the quote rule reduce but do
  not remove that risk. Plain-text, server-rendered sources are recommended.
- The timeout path uses one-sided evidence; an appeal re-judges the same snapshot.
- Reputation is a soft signal (addresses are cheap). It only gates the bounty size a resolver
  may take; it is not sybil resistance.
- INVALID costs no bond: when evidence is genuinely insufficient both sides are made whole.
