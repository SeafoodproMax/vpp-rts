# vpp-rts

NCKU Real-Time Systems homework — a Virtual Power Plant (VPP) real-time scheduling system that generates a periodic task set, solves a day-ahead MILP schedule, runs an acceptance test for sporadic/aperiodic jobs, and evaluates the result.

## Quick start

**With Poetry (recommended):**
```bash
poetry install
poetry run main
```

**Without Poetry:**
```bash
pip install pulp pydantic
python -m src.main
```

## Self-check (Level 1)

Validate the generated artifacts against the Level 1 grading rubric (items 1–3). Standard-library only — no Poetry environment required:

```bash
python3 -m src.validator
```

Reads `output/task_set.json`, `output/schedule_result.json` and `input/processor_settings.json`, prints a per-item pass/fail report with a self-grade, and exits non-zero on any covered violation. Constraint C4 (aperiodic) is reported as `SKIP` until aperiodic handling lands.

## Pipeline

The full pipeline runs four phases in sequence:

```
Phase 1 — Task Generation
  → output/task_set.json   (periodic tasks; add sporadic/aperiodic entries manually)

Phase 2 — Day-Ahead Scheduling (MILP)
  ← input/processor_settings.json
  ← input/price_72hr.json
  ← output/task_set.json
  → output/schedule_result.json

Phase 3 — Acceptance Test  (integrated into RTScheduler.run())
  ← reserve computed by Phase 2 solver
  ← output/task_set.json   (sporadic / aperiodic tasks)
  → output/schedule_result.json  (annotated with rejected_sporadic / missed_aperiodic)
  → output/acceptance_test_log.json  (per-job accept/reject decisions + rationale)

Phase 4 — Evaluation
  ← input/processor_settings.json
  ← input/price_72hr.json
  ← output/task_set.json
  ← output/schedule_result.json
  → output/evaluation_results.json
```

### Phase 1 — Task Generation

Randomly generates 6–10 periodic tasks satisfying all assignment constraints for a 72-unit hyperperiod:

| Constraint | Rule |
|---|---|
| Frame size `f` | `f ≥ max(e)`, `72 % f == 0`, `2f − gcd(f, p) ≤ d` for every task |
| Utilisation | `Σ(e/p) ≥ 0.7` |
| Job count | Jobs fitting in 72 units `> 30` |
| Energy demand | At least 2 tasks with `w ≥ 14` |

Each task has the following fields:

| Field | Meaning |
|---|---|
| `r` | Release time |
| `p` | Period |
| `e` | Execution time (WCET) |
| `d` | Relative deadline |
| `w` | Energy demand (MWh/h) |
| `preempt` | `1` = preemptible, `0` = non-preemptive |

The output `task_set.json` also includes empty `sporadic` and `aperiodic` sections. Add entries there before running Phase 2 to exercise the acceptance test.

### Phase 2 — Day-Ahead Scheduling

Expands periodic tasks into concrete jobs over the 72-hour horizon and solves a PuLP MILP problem with 23 constraints covering:

- Job execution completeness and ordering
- Generator ramp-up/down, min up/down time, output limits
- Renewable forecast limits
- Storage SOC, charge/discharge limits, no simultaneous charge+discharge
- Energy balance per time step
- Market sell quantity

**Objective:** minimise `α·f1 + f2 + f3`
- `f1` = aperiodic deadline miss count (α = 10,000 $/miss)
- `f2` = Σ (cost\_fixed · min(1, P) + cost\_variable · P) for generators
- `f3` = −Σ (λ_t · Sell_t) (market revenue, negated for minimisation)

### Phase 3 — Acceptance Test

Processes sporadic (hard-deadline) and aperiodic (soft-deadline) jobs using the reserve left by the MILP solver. **Implemented in `AcceptanceTester` and automatically called inside `RTScheduler.run()`.**

| Task type | Rule |
|---|---|
| Sporadic | Hard deadline. Accepted only if it can finish between `r` and `r + d - 1`; otherwise recorded in `rejected_sporadic`. |
| Aperiodic | Soft deadline. Scheduled when enough reserve exists; otherwise recorded in `missed_aperiodic`. |
| `preempt = 1` | May use non-contiguous slots. |
| `preempt = 0` | Must use one contiguous execution window. |

Accepted/scheduled jobs consume reserve by `w` for each of their `e` execution ticks. To exercise this phase, add sporadic/aperiodic entries to `output/task_set.json` before running Phase 2.

Every decision is written to `output/acceptance_test_log.json`: a `summary` block (accept/reject and scheduled/missed counts, sporadic value rate, leftover reserve per tick) plus per-job records carrying the chosen slots, completion tick, and a human-readable accept/reject rationale — directly supporting rubric items 4-1 (method) and 4-2 (decision rationality).

### Phase 4 — Evaluation

Reads the solved schedule and computes all required performance metrics:

| Metric | Formula |
|---|---|
| `hard_deadline_miss_rate` | missed periodic + sporadic jobs / total |
| `soft_deadline_miss_rate` | missed aperiodic jobs / total |
| `average_tardiness` | avg max(0, C_j − d_j) |
| `max_tardiness` | max max(0, C_j − d_j) |
| `average_response_time` | avg (C_j − r_j) |
| `max_response_time` | max (C_j − r_j) |
| `completion_time_jitter` | avg peak-to-peak of C across instances of same task |
| `sporadic_value_rate` | exec time completed before deadline / total exec time |
| `generator_cost` | f2 from objective |
| `market_revenue` | Σ (λ_t · Sell_t) |
| `objective_value` | α·f1 + f2 − market\_revenue |

## Project structure

```
src/
├── main.py                      # Pipeline entry point
├── config.py                    # Centralised paths and constants
├── validator.py                 # Level 1 self-check (stdlib only)
├── generator/                   # Phase 1: task set generation
│   ├── task_set_generator.py
│   ├── frame_size_calculator.py
│   └── task_set_validator.py
├── rt_scheduler/                # Phase 2 + 3: MILP scheduler + acceptance test
│   ├── rt_scheduler.py          # Orchestrator (calls AcceptanceTester at end)
│   ├── acceptance_tester.py     # Sporadic/aperiodic scheduling on reserve
│   ├── expander.py              # Expands tasks → concrete jobs
│   ├── formulator.py            # Builds PuLP problem (23 constraints)
│   └── extractor.py             # Parses solved variables → JSON + reserve
├── evaluator/                   # Phase 4: performance metrics
│   └── evaluator.py
├── model/                       # Pydantic data models
│   ├── base/base_model.py
│   ├── asset/                   # Generator, Storage, Renewable, ChargingJob
│   ├── task/                    # PeriodicTask, SporadicTask, AperiodicTask, ExpandedJob
│   └── market/                  # PriceSystem, PriceRecord
└── utils/
    └── file_io.py               # JsonIO.load() / JsonIO.save()

input/
├── processor_settings.json      # VPP asset configuration
└── price_72hr.json              # 72-hour market price forecast

output/                          # git-ignored, generated at runtime
├── task_set.json
├── schedule_result.json
├── acceptance_test_log.json
└── evaluation_results.json
```

## Running tests

```bash
# With Poetry
poetry run pytest

# Without Poetry
python -m pytest
```
