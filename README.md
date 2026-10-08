# OpenResolver

A shared resolution layer for GenLayer Intelligent Contracts. Ask a question with a bounty and
get an answer through bonded optimistic proposals. When someone disputes, validators freeze
the web evidence of both sides and independently derive the answer from it, agreeing exactly.

Five contracts: **ResolverBank**, **QuestionRegistry**, **ResolverEngine**, and two consumers
that prove the interface, **PredictionPool** and **ConditionalEscrow**.
Design and threat model: [ARCHITECTURE.md](ARCHITECTURE.md).

## Highlights

- Evidence integrity: an evidence excerpt is accepted only if every validator sees exactly the
  same text; a dishonest leader cannot insert, delete, truncate or hide evidence.
- Liveness: a dead or node-dependent URL is frozen as UNAVAILABLE instead of blocking a case;
  every stuck stage has an on-chain exit.
- Verdicts are grounded: the answer needs an exact quote from the frozen evidence, and a
  dishonest leader cannot force INVALID over well-supported evidence.
- Funds: one bank, pull payments, exact bonds, checked invariant
  `balance == claimable_total + locked_total`, safe under out-of-order message delivery.

## Repository layout

| Path | Content |
|---|---|
| contracts/ | annotated source contracts |
| deploy/production/ | Studio-ready files (comments stripped by AST) |
| deploy/demo/ | same code with short windows for live demos |
| tests/ | offline suite with a strict SDK stub (202 tests) |
| tools/build_deploy.py | AST-based deploy file builder |
| tools/mutation_check.py | plants 51 bugs; the suite must catch every one |
| tools/frontend_e2e.py | optional browser test of index.html against the contracts |
| index.html | single-file frontend |

## Checks (Python 3 only, no dependencies)

    python3 -m unittest discover -s tests
    python3 tools/build_deploy.py
    OR_CONTRACT_DIR=deploy/production python3 -m unittest discover -s tests
    python3 tools/mutation_check.py
    python3 tools/frontend_e2e.py   # optional, needs Playwright + Chromium

## Deploy order (GenLayer Studio)

| Step | Contract / method | Argument |
|---|---|---|
| 1 | deploy resolver_bank.py | - |
| 2 | deploy question_registry.py | - |
| 3 | deploy resolver_engine.py | - |
| 4 | bank.set_registry | registry address |
| 5 | registry.set_bank | bank address |
| 6 | registry.set_engine | engine address |
| 7 | engine.set_registry | registry address |
| 8 | deploy prediction_pool.py | registry address |
| 9 | deploy conditional_escrow.py | registry address |
| 10 | open index.html, Settings, paste the five addresses | - |

Use `deploy/demo/` for a live walk-through (5 minute windows, 10 minute freeze grace).

## Frontend

Host `index.html` anywhere static (for example GitHub Pages). Addresses can be put in the
`DEFAULTS` constant, saved in Settings, or passed once in the link:
`?registry=0x..&bank=0x..&engine=0x..&pool=0x..&escrow=0x..`.

## License

MIT
