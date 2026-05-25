# VPP-RTS — Level 2 Report (Relaxed Assumptions + Advanced Dynamic Scheduling)

This document covers the Level 2 grading items: **3 — relaxed-assumption modelling**
and **8 — advanced dynamic scheduling method** (8-1 design, 8-2 correctness, 8-3
static-vs-dynamic comparison). It builds directly on the Level 1 MILP model; all
notation, decision variables and the 23 base constraints are unchanged.

> **Key rule honoured:** the relaxations *may modify notation and add parameters but
> may not add new decision variables*. Every relaxed constraint below is expressed
> with the Level 1 decision variables `P`, `k`, `SOC`, `Sell`, `x`.

All Level 2 parameters live in `runtime_config.json`; the relaxed constraints are
implemented in `src/rt_scheduler/formulator.py` (`RelaxationConfig` /
`VppMilpFormulator`) and the dynamic method in `src/advanced_scheduler.py`.

---

## 3. Relaxed assumptions (rubric item 3 — 1 point per constraint, cap 10)

We relax three Level 1 assumptions: **11 (renewable forecast certainty)**,
**12 (idealised storage)** and **5 (no job precedence)**. The added parameters:

| Parameter | Symbol | Value | Assumption | Meaning |
|---|---|---|---|---|
| `charge_efficiency` | η_c | 0.95 | 12 | fraction of charge energy stored |
| `discharge_efficiency` | η_d | 0.95 | 12 | fraction of cell energy delivered |
| `self_discharge_rate` | σ | 0.002 | 12 | per-tick self-discharge fraction |
| `cycle_limit` | N | 3.0 | 12 | max full-equivalent cycles / horizon |
| `soc_power_floor` | φ | 0.5 | 12 | min power fraction at the SOC extreme |
| `aging_cost` | c_age | 2.0 | 12 | $/MWh discharged (aging) |
| `renewable_uncertainty_margin` | β | 0.15 | 11 | forecast derating margin |
| `precedence` | — | auto | 5 | ordered `(a,b)` job pairs |

A `RelaxationConfig` left at defaults (η=1, σ=0, β=0, no cycle/φ/aging/precedence)
reproduces the Level 1 model exactly, so the Level 1 static schedule is unaffected.

### Modelled constraints

**Assumption 11 — renewable uncertainty.** The day-ahead plan must not rely on the
full forecast, because realised PV output deviates from it.

- **R1 / C13′ (derated forecast cap):**
  `P[i][t] ≤ capacity_i · forecast[i][t] · (1 − β) · Δt`, ∀ i∈Ir, t∈T.
  (`_add_renewable_constraints`.) β = 0 recovers C13.

**Assumption 12 — realistic storage.** Idealised SOC balance is replaced by a model
with round-trip efficiency, self-discharge, throughput and SOC-dependent power.

- **R2 (charge efficiency, in C16′):** only η_c of the charge energy is stored.
- **R3 (discharge efficiency, in C16′):** delivering `P[i][t]` draws `P[i][t]/η_d`
  from the cell.
- **R4 (self-discharge, in C16′):** the cell loses fraction σ each tick.
  Combined SOC balance:
  `SOC[i][t] = (1−σ)·SOC[i][t−1] + η_c·charge_in[i][t] − (1/η_d)·P[i][t]`,
  ∀ i∈Ib, t∈T. (`_add_storage_constraints`.) η=1, σ=0 recovers C16.
- **R5 (discharge vs usable SOC, C18′):**
  `(1/η_d)·P[i][t] ≤ (1−σ)·SOC[i][t−1] − soc_min_i`.
- **R6 (cycle / throughput limit):**
  `Σ_t P[i][t] ≤ N · (soc_max_i − soc_min_i)`, ∀ i∈Ib.
- **R7 (SOC-dependent discharge power):**
  `P[i][t] ≤ dis_max_i · Δt · [φ + (1−φ)·(SOC[i][t−1] − soc_min_i)/(soc_max_i − soc_min_i)]`
  — full rated power when nearly full, tapering to φ·rated when nearly empty.
- **R8 (SOC-dependent charge power):**
  `charge_in[i][t] ≤ chg_max_i · Δt · [φ + (1−φ)·(soc_max_i − SOC[i][t−1])/(soc_max_i − soc_min_i)]`.
- **R9 (aging cost, objective):** add `Σ_i∈Ib Σ_t c_age · P[i][t]` to the objective so
  the optimizer trades battery cycling against generator fuel. (`_add_objective`.)

**Assumption 5 — job precedence.**

- **R10 (precedence):** for an ordered pair `(a, b)`, job b may not be active until a
  has finished: `e_a · x[b][t] ≤ Σ_{s<t} x[a][s]`, ∀ t in b's window.
  (`_add_precedence_constraints`, reusing `x`.) A feasible non-trivial pair is
  auto-selected from the periodic jobs when none is configured.

**Reservation strategy (supports items 6/7 and the dynamic method).**

- **R-reserve (reservation floor):** `Sell[t] ≥ R_t`. The redirectable surplus held
  for sporadic/aperiodic acceptance is reserved by a per-tick floor on `Sell`
  (`_add_reserve_floor_constraints`), reusing `Sell`. In Level 2 the floor `R_t` is
  built up *online* from the windows of revealed-but-unfinished jobs.

That is **10 relaxed constraints (R1–R10)** plus the reservation floor — at or above
the item-3 cap of 10, and none introduces a new decision variable.

---

## 8. Advanced dynamic scheduling method

### 8-1 Method design and objective trade-offs

**Method: receding-horizon (rolling) re-optimization.** Level 1 solves one static
MILP over `[1, 72]` with all information known at t=0. The Level 2 method
(`src/advanced_scheduler.py`) walks the horizon forward and re-optimizes the
*remaining* horizon at trigger points, adapting to information the static plan cannot
see.

**State carried forward (receding horizon).** At each trigger the already-executed
ticks `[1, t0)` are pinned to their committed values via
`VppMilpFormulator.pin_prefix`, and only `[t0, 72]` is re-optimized. Pinning the
continuous state (`P`, `k`, `SOC`, `Sell`) plus the derived prefix binaries
(`u`, `charge_b`, `discharge_b`, `x`) carries generator on/off duration, ramp position
and SOC across the boundary and lets the solver presolve away the frozen prefix
(keeping each re-solve cheap). No new decision variable is introduced.

**Update timing (triggers).** Re-optimization fires on (a) a periodic cadence
(`reopt_interval = 24` ticks), (b) every sporadic-job release (hard jobs need prompt
admission), and (c) a capped number of ticks where realised PV deviates most from the
forecast. Aperiodic (soft) jobs are revealed at the next cadence boundary.

**Renewable realisation.** A seeded stochastic model realises the actual PV
availability around the forecast (`renewable_noise_std = 0.2`, `seed = 42`). The
committed block uses the *realised* availability; the still-unseen tail uses the
*derated* forecast (R1). Thus the plan is conservative about the future yet exact
about the block it commits.

**Online sporadic/aperiodic admission = reservation feasibility.** When jobs are
revealed at their release, admission is decided by whether the re-optimization can
*hold enough redirectable surplus* (`Sell`) across the job's execution window (the
reservation floor R-reserve). A sporadic (hard) job is accepted only if a
reservation-feasible solve exists, otherwise rejected; aperiodic (soft) jobs are
reserved best-effort. Held reserve is carried forward and the job is routed into
committed blocks as they freeze, so an admitted hard job always completes before its
deadline. This is the acceptance test realised as MILP feasibility.

**Objective trade-offs.** The three objectives conflict:

- Raising **sporadic/aperiodic acceptance** (lower miss) forces the plan to *hold
  reserve* → more generator commitment → higher **generator cost f2** and/or lower
  **market revenue −f3** (surplus held instead of sold at peak price).
- **Renewable uncertainty** (R1 derating + realised shortfalls) shifts energy from PV
  to thermal → higher f2.
- **Storage realism** (efficiency, self-discharge, cycle limit, aging) makes the
  battery a costlier, lossier buffer → less arbitrage revenue.

So the dynamic method does not optimize all objectives simultaneously; it trades extra
generator cost for fewer deadline misses and better responsiveness (quantified in 8-3).

### 8-2 Correctness of the produced schedule

Output: `output/schedule_result_dynamic.json` (Level 1 schema),
`output/acceptance_test_log_dynamic.json`, `output/dynamic_run_log.json`,
`output/evaluation_results_dynamic.json`.

On the reference run (9 rounds / 9 MILP solves, ~15 s):

- **Online arrival handled.** Jobs are revealed and admitted as they arrive over the
  horizon — e.g. `s1,a1,a2` at t0=2, `s2,a3` at t0=14, `s3,a5` at t0=28, … `s6,a10`
  at t0=66 (see `dynamic_run_log.json`).
- **All hard deadlines met.** All 49 periodic jobs and all 6 sporadic jobs complete
  before their absolute deadlines; `hard_deadline_miss_rate = 0.0`.
- **All soft jobs scheduled.** All 10 aperiodic jobs complete by their soft deadline;
  `soft_deadline_miss_rate = 0.0`, `sporadic_value_rate = 1.0`.
- **System feasibility maintained every tick.** Verified directly on the output:
  power balance C23 holds (0 violations), storage SOC stays within `[soc_min,
  soc_max]` (0 violations), and the relaxed storage / generator / renewable
  constraints hold in every re-solve.
- **No rejections / reschedule failures** occurred on this instance; had a sporadic
  job been reservation-infeasible it would have been rejected (logged with reason) and
  the round re-solved without it, never violating an existing commitment.

### 8-3 Static (Level 1) vs dynamic (Level 2) comparison

Both methods run on the *same* `task_set.json` (9 periodic tasks → 49 jobs, 6 sporadic,
10 aperiodic). Reference numbers (`evaluation_results.json` vs
`evaluation_results_dynamic.json`):

| Metric | Static (L1) | Dynamic (L2) | Δ |
|---|---|---|---|
| objective_value | −39 344.8 | **−52 329.1** | −12 984 (better) |
| generator_cost | 292 020 | 303 280 | +11 260 |
| market_revenue | 351 365 | 355 609 | +4 244 |
| hard_deadline_miss_rate | 0.0 | 0.0 | — |
| **soft_deadline_miss_rate** | **0.2** | **0.0** | −0.2 |
| average_response_time | 3.52 | 3.00 | −0.52 |
| sporadic_value_rate | 1.0 | 1.0 | — |

**Interpretation.**

- The static day-ahead plan, knowing all jobs at once but committing to a single
  schedule, **misses 2 of 10 aperiodic jobs** (soft miss rate 0.2). The dynamic method
  re-optimizes as jobs arrive and *reserves* capacity for them, so it **schedules all
  10 on time** (soft miss rate 0.0) and lowers average response time 3.52 → 3.00.
- Eliminating 2 soft misses removes `2 × α = 20 000` of penalty (α = 10 000/miss),
  which dominates the objective: dynamic objective −52 329 vs static −39 345.
- The trade-off is visible: holding reserve and compensating for realised renewable
  shortfalls **raises generator cost (+11 260)**; the larger committed generation also
  raises market revenue slightly (+4 244). Net of the miss penalty, the dynamic method
  is clearly better on the total objective, confirming the 8-1 analysis that improving
  acceptance/miss costs generator fuel.

---

## How to reproduce

```bash
# full Level 2 comparison pipeline (generate → static L1 → dynamic L2 → compare)
python -c "from src.main import run_level2; run_level2()"

# dynamic scheduler only, on the existing task_set.json
python -m src.advanced_scheduler
```

Parameters are in `runtime_config.json`. Outputs land in `output/` with `*_dynamic`
suffixes for the Level 2 artifacts. The renewable realisation is seeded (`seed`), so
runs are reproducible.
