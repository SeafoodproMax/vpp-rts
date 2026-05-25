# CLAUDE.md

## Project Overview

VPP-RTS is a Virtual Power Plant real-time scheduling system for an NCKU homework. The pipeline runs in phases orchestrated by `src/main.py`:

1. **Phase 1 — Task Generation** (`src/generator/`): randomly generates 6–10 periodic tasks satisfying frame-size, density, and job-count constraints. Output: `output/task_set.json`.
2. **Phase 2 — Day-Ahead Scheduling** (`src/rt_scheduler/rt_scheduler.py`): PuLP MILP solver that expands periodic tasks into concrete job instances over a 72-hour horizon, optimizes generator cost and market revenue subject to 23 constraints (energy demand, ramp rates, SOC, power balance, etc.). Output: `output/schedule_result.json`.
3. **Phase 3 — Acceptance Test** (`src/rt_scheduler/acceptance_tester.py`): uses the reserve left by Phase 2 to accept/reject sporadic hard-deadline jobs and schedule/miss aperiodic soft-deadline jobs.

## Architecture

```
src/
├── main.py                  # Pipeline entry: generate_task_set() -> run_scheduler()
├── generator/               # Phase 1: task set generation
│   ├── task_set_generator.py
│   ├── frame_size_calculator.py
│   └── task_set_validator.py
├── rt_scheduler/            # Phase 2/3: MILP scheduler and acceptance testing
├── model/                   # Pydantic data models (loaded via AppBaseModel.load_from_json)
│   ├── base/base_model.py   # Base class with _parse template method
│   ├── asset/               # ProcessorSettingsSystem, Generator, Storage, Renewable, ChargingJob
│   ├── task/                # TaskSystem, PeriodicTask, SporadicTask, AperiodicTask
│   └── market/              # PriceSystem, PriceRecord
└── utils/file_io.py         # JsonIO static utility
```

## Key Domain Concepts

- **Devices** (`I`): generators (`Ig`), renewables (`Ir`), storages (`Ib`) — all defined in `input/processor_settings.json`
- **Jobs**: periodic tasks expand into concrete jobs with `release = r + k*p`, `deadline = release + d - 1`; only jobs with `deadline <= 72` are included
- **Charging jobs**: special jobs that route energy from generators/renewables into storage SOC; they cannot be supplied by storage discharge
- **Sporadic jobs**: hard-deadline jobs accepted only when remaining reserve can satisfy `e` ticks of demand `w` within `[r, r + d - 1]`
- **Aperiodic jobs**: soft-deadline jobs scheduled when reserve is available; otherwise marked as missed
- **Preemption**: `preempt = 1` jobs may use non-contiguous reserve slots; `preempt = 0` jobs require one contiguous execution window
- **Decision variables**: `P[i][t]` (device output), `k[j][i][t]` (energy routing), `u/start/stop` (generator on/off), `charge_b/discharge_b/SOC` (storage state), `Sell[t]`, `x[j][t]` (job active binary)
- **Objective**: minimize generator cost − maximize sell revenue (no aperiodic penalty in Phase 2)
- **Acceptance output fields**: schedule records can include `accepted_sporadic`, `scheduled_aperiodic`, `rejected_sporadic`, and `missed_aperiodic`

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
