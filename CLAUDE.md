# CLAUDE.md

## Project Overview

VPP-RTS is a Virtual Power Plant real-time scheduling system for an NCKU homework. The pipeline runs in phases orchestrated by `src/main.py`:

1. **Phase 1 — Task Generation** (`src/generator/`): randomly generates 6–10 periodic tasks satisfying frame-size, density, and job-count constraints. Output: `output/task_set.json`.
2. **Phase 2 — Day-Ahead Scheduling** (`src/rt_scheduler/`): PuLP MILP solver that expands periodic tasks into concrete job instances over a 72-hour horizon, optimizes generator cost and market revenue subject to 23 constraints (energy demand, ramp rates, SOC, power balance, etc.). Output: `output/schedule_result.json`.
3. **Phase 3 — Acceptance Test** (`src/main.py::run_acceptance_test`): **not yet implemented.** Should process sporadic (hard-deadline) and aperiodic (soft-deadline) jobs at runtime, decide accept/reject, insert accepted jobs into the existing schedule without violating any constraint, and write `rejected_sporadic` / `missed_aperiodic` back into `schedule_result.json`. **Must be implemented before Phase 4 produces meaningful sporadic/aperiodic metrics.**
4. **Phase 4 — Evaluation** (`src/evaluator/`): already implemented. Reads the schedule (including `rejected_sporadic` / `missed_aperiodic` populated by Phase 3) and computes all performance metrics. Output: `output/evaluation_results.json`.

## Architecture

```
src/
├── main.py                  # Pipeline entry: generate_task_set() -> run_scheduler() -> run_evaluator()
├── config.py                # VppConfig: centralised paths & magic numbers
├── generator/               # Phase 1: task set generation
│   ├── task_set_generator.py
│   ├── frame_size_calculator.py
│   └── task_set_validator.py
├── rt_scheduler/            # Phase 2: MILP day-ahead scheduler
│   ├── rt_scheduler.py      # RTScheduler orchestrator
│   ├── expander.py          # JobExpander: periodic tasks → ExpandedJob instances
│   ├── formulator.py        # VppMilpFormulator: builds PuLP problem with 23 constraints
│   └── extractor.py         # SchedulerResultExtractor: parses solved variables → JSON
├── evaluator/               # Phase 4: post-schedule metrics computation (implemented)
│   └── evaluator.py         # Evaluator: reads schedule + inputs → evaluation_results.json
│                            # Phase 3 (sporadic acceptance test) → implement in rt_scheduler/
│                            # then wire into main.py::run_acceptance_test() before run_evaluator()
├── model/                   # Pydantic data models (loaded via AppBaseModel.load_from_json)
│   ├── base/base_model.py   # Base class with _parse template method
│   ├── asset/               # ProcessorSettingsSystem, Generator, Storage, Renewable, ChargingJob
│   ├── task/                # TaskSystem, PeriodicTask, SporadicTask, AperiodicTask, ExpandedJob
│   └── market/              # PriceSystem, PriceRecord
└── utils/file_io.py         # JsonIO: load() and save() static utilities
```

## Key Domain Concepts

- **Devices** (`I`): generators (`Ig`), renewables (`Ir`), storages (`Ib`) — all defined in `input/processor_settings.json`
- **Jobs**: periodic tasks expand into concrete jobs with `release = r + k*p`, `deadline = release + d - 1`; only jobs with `deadline <= 72` are included
- **Charging jobs**: special jobs that route energy from generators/renewables into storage SOC; they cannot be supplied by storage discharge
- **Decision variables**: `P[i][t]` (device output), `k[j][i][t]` (energy routing), `u/start/stop` (generator on/off), `charge_b/discharge_b/SOC` (storage state), `Sell[t]`, `x[j][t]` (job active binary)
- **Objective**: minimize `α·f1 + f2 + f3` where `f1` = aperiodic miss count (α=10000), `f2` = generator cost, `f3` = −market revenue (no aperiodic penalty in Phase 2 since sporadic/aperiodic not yet implemented)
- **Evaluator inputs**: `schedule_result.json`, `task_set.json`, `processor_settings.json`, `price_72hr.json`
- **Evaluator outputs**: `evaluation_results.json` with fields: `hard_deadline_miss_rate`, `soft_deadline_miss_rate`, `average/max_tardiness`, `average/max_response_time`, `completion_time_jitter`, `acceptance_test.sporadic_value_rate`, `generator_cost`, `market_revenue`, `objective_value`

## Code Style

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
Full type hints on all methods. Google-style docstrings (Args / Returns).

## OOP Principles

**Single responsibility** — one class, one job. When a class grows, split it by concern rather than adding more methods. Example: `TaskSetGenerator` delegates calculation to `FrameSizeCalculator` and validation to `TaskSetValidator`.

**Stateless utilities** — group pure, stateless operations into a static-method class (e.g. `JsonIO`) instead of free functions or a mixed bag.

**Template method via `_parse`** — `AppBaseModel.load_from_json` defines the loading skeleton; subclasses override `_parse` for custom construction logic.

**Dependency injection** — accept collaborators as constructor parameters with sensible defaults. Keeps classes testable without monkey-patching.

**Private by default** — prefix internal methods and attributes with `_`. Only expose what callers actually need.

## Git Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>
```

**Types:** `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`

**Scope** is optional — use the module or layer name (e.g. `model`, `generator`).

**Subject** is lowercase, imperative mood, no period at the end.

Examples from this repo:
```
feat(model): implement real-time task data structures
chore: update .gitignore to ignore output folder
```
