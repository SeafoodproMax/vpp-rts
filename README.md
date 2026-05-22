# vpp-rts

NCKU Real-Time Systems homework — generates a valid periodic task set for a Virtual Power Plant (VPP) cyclic executive scheduler.

## Quick start

> Requires [Python 3.12+](https://www.python.org/downloads/release/python-3120/) and [Poetry](https://python-poetry.org/).

```bash
poetry install
poetry run main
```

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

## Project structure

```
src/
├── main.py                  # Entry point
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
