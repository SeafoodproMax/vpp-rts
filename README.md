# vpp-rts

NCKU Real-Time Systems homework — generates a valid periodic task set for a Virtual Power Plant (VPP) cyclic executive scheduler.

## Quick start

> Requires [Python 3.12+](https://www.python.org/downloads/release/python-3120/) and [Poetry](https://python-poetry.org/).

```bash
poetry install
poetry run main
```

## Self-check (Level 1)

Validate the generated artifacts against the Level 1 grading rubric (items 1–3). Standard-library only — no Poetry environment required:

```bash
python3 -m src.validator
```

Reads `output/task_set.json`, `output/schedule_result.json` and `input/processor_settings.json`, prints a per-item pass/fail report with a self-grade, and exits non-zero on any covered violation. Constraint C4 (aperiodic) is reported as `SKIP` until aperiodic handling lands.

## What it does

Randomly generates 6–10 periodic tasks that satisfy the following constraints for a 72-unit hyperperiod:

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
| `d` | Deadline |
| `w` | Energy demand |
| `preempt` | `1` = preemptible, `0` = non-preemptive |

Output is written to `output/task_set.json`.

## Scheduling and acceptance tests

After task generation, `RTScheduler` runs a PuLP-based day-ahead MILP scheduler for
the 72-hour horizon. It expands periodic tasks into concrete jobs, schedules VPP
generation/storage/renewable output, and computes the remaining reserve per tick.

Phase 3 then runs `AcceptanceTester` on that reserve:

| Task type | Rule |
|---|---|
| Sporadic | Hard deadline. Accepted only if it can finish between `r` and `r + d - 1`; otherwise recorded in `rejected_sporadic`. |
| Aperiodic | Soft deadline. Scheduled when enough reserve exists; otherwise recorded in `missed_aperiodic`. |
| `preempt = 1` | May use non-contiguous slots. |
| `preempt = 0` | Must use one contiguous execution window. |

Accepted/scheduled jobs consume reserve by `w` for each of their `e` execution
ticks. Schedule records may include `accepted_sporadic` and
`scheduled_aperiodic` annotations in addition to the original
`rejected_sporadic` and `missed_aperiodic` fields.

## Project structure

```
src/
├── main.py                  # Entry point
├── validator.py             # Level 1 self-check (stdlib only)
├── generator/
│   ├── task_set_generator.py    # Random task generation
│   ├── frame_size_calculator.py # Frame size search
│   └── task_set_validator.py    # Constraint validation
├── model/                   # Pydantic data models
│   ├── task/                # RT task types
│   ├── asset/               # VPP physical assets
│   └── market/              # Electricity price data
└── utils/
    └── file_io.py           # JSON read/write helpers

input/
├── processor_settings.json  # VPP asset configuration
└── price_72hr.json          # 72-hour market price data

output/
└── task_set.json            # Generated task set (git-ignored)
```
