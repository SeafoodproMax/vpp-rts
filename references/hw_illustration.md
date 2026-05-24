# VPP Real-Time System Homework Illustration

---

## Level 1 (Score: 0~80)

### 1.1 Objectives

Operate a Virtual Power Plant (VPP) real-time system composed of conventional generators, renewables, and storage devices. In addition to scheduling generation supply, the system must schedule time-constrained energy demands (jobs).

Conventional generators, renewables, and storage devices act as **processors** responsible for supplying the energy required by jobs. Real-time energy demands are classified as **periodic tasks**, **sporadic tasks**, and **aperiodic tasks**. Each task is released as a job at some time point and waits for the system to allocate energy.

To produce a feasible schedule, the following must be implemented:

1. **Design a periodic task set generation algorithm** (see Appendix F). Each periodic task must include: release time, period, execution time, relative deadline, energy demand per time unit (MWh), and preemptive/non-preemptive flag.

2. **Build a 72-hour day-ahead fixed schedule**: Design a valid frame size and build a 72-hour day-ahead fixed schedule (see Appendix G) so that all periodic jobs complete before their absolute deadlines and all mathematical constraints are satisfied.

3. **Design a reserve and Acceptance Test strategy**: While completing periodic jobs, the fixed schedule must reserve idle slots or resource surplus. When a sporadic job is released (see Appendix J), the system performs an acceptance test to determine whether the job can be accepted and completed before its deadline without breaking existing periodic jobs, already-accepted hard-deadline jobs, or system constraints. After acceptance, update the schedule state, slack, reserve, and other internal records. Aperiodic jobs are soft-deadline jobs — no hard-deadline acceptance test is performed; they enter a waiting queue and are scheduled when idle slots or resource surplus are available.

4. **Pursue system stability and efficiency maximization**: Develop algorithms to minimize the number of aperiodic deadline misses, minimize conventional generator cost, and maximize market sell revenue.

---

### 1.2 Terminology

| Term | Definition |
|------|-----------|
| 發電設備 (Generation Device) | Devices that can supply electrical energy, including conventional generators, renewables, and storage devices. |
| 傳統機組 (Conventional Generator) | Controllable output generators, e.g., natural gas and coal units, with on/off, ramp-up/ramp-down constraints. |
| 再生能源 (Renewable Energy) | Renewables with forecastable output, e.g., solar PV, wind power. |
| 儲能裝置 (Storage Device) | Devices that store and release energy; cannot charge and discharge simultaneously. |
| 用電需求 (Energy Demand / Job) | Consumes fixed energy, runs for a fixed duration, has a time deadline. |
| Periodic task | Recurring energy demand released at fixed intervals. Examples: industrial park batch process equipment (dryer runs 3 hours at 8 AM and 2 PM daily), semiconductor factory air conditioning cycle, wastewater treatment pumps/aerators, fixed-schedule EV fleet charging. |
| Sporadic task | Non-periodic demand with a **hard deadline** — urgent, immediate-response demands. Examples: emergency cooling on process temperature overshoot, emergency gas discharge, flood-control pumps in heavy rain. |
| Aperiodic task | Non-periodic demand with a **soft deadline** — late completion is allowed but counted as a miss. Examples: non-scheduled EV charging, non-urgent drainage or water circulation. |

---

### 1.3 Mathematical Model

#### Assumptions

1. Scheduling horizon is **72 time units**; each unit Δt = 1 hour; uses a clock-driven static schedule.
2. Periodic task release time, period, execution time, relative deadline, and energy demand are all fixed and known.
3. Each task generates a corresponding job at its release time; all accepted jobs must complete before their own deadline and must not exceed the scheduling horizon t=72.
4. A single job's energy can be supplied by multiple generation devices; at time t a job executes, the full energy demand must be supplied at that time point.
5. No precedence ordering among jobs.
6. Sporadic jobs may only be inserted into the current schedule without replacing, moving, or re-optimizing existing periodic jobs. The acceptance test checks whether the sporadic job can complete before its hard deadline without violating existing periodic jobs, already-accepted hard-deadline jobs, or any system constraint. Aperiodic jobs are soft-deadline — no hard-deadline acceptance test; scheduled when idle slots or surplus exist; if not completed before soft deadline, record soft deadline miss and tardiness.
7. A single processor can supply different amounts of energy to multiple jobs simultaneously.
8. Scheduling computation time, demand switching time, communication delay, and control delay are ignored.
9. Only MWh energy balance per time unit is checked; intra-slot instantaneous power variation, frequency limits, and voltage limits are ignored.
10. VPP internal line capacity, power flow limits, and transmission losses are ignored.
11. Renewable output is based on day-ahead forecast values; forecast uncertainty, shading, device failure, and efficiency degradation are ignored.
12. Storage rated capacity is fixed and known; available capacity range is 0%–100%; aging, self-discharge, temperature limits, and charge cycle limits are ignored.
13. Conventional generator parameters are fixed and known; device failure, fuel limits, startup delay, and  emission limits are ignored.
14. Market prices are known; transaction costs, volume caps, sell commitments, and sell-commitment breach penalties are ignored.

---

#### Notation

**Sets**

| Symbol | Description |
|--------|-------------|
| T = 1,2,...,H | Discrete time index set; H is the total number of time slots in the scheduling horizon. |
| Δt | Length of each discrete time slot; this model sets Δt = 1 hour. |
| I = {i₁, i₂, ..., iₐ} | Set of all generation devices. |
| Ig ⊆ I | Set of conventional generators (e.g., natural gas, coal). |
| Ir ⊆ I | Set of renewable generation devices. |
| Ib ⊆ I | Set of storage devices. |
| b(j) ⊆ Ib | Storage device corresponding to charging demand j ∈ Jchg. Example: if j = battery_1_chg, then b(j) = battery_1. |
| J = {j₁, j₂, ..., jβ} | Set of all time-constrained energy demands. |
| Jp ⊆ J | Periodic task set — periodically executed energy demands with hard deadlines. |
| Js ⊆ J | Sporadic task set — temporary energy demands with hard deadlines. |
| Ja ⊆ J | Aperiodic task set — temporary energy demands with soft deadlines. |
| Jnp ⊆ Jp∪Js∪Ja | Non-preemptive task set — generated jobs cannot be interrupted during execution. |
| Jchg ⊆ J | Storage charging demand set; Jchg ∩ (Jp∪Js∪Ja∪Jnp) = ∅. Used to represent storage charging behavior modeled in the system; not an external energy demand; excluded from deadline, response time, tardiness, and jitter evaluation. |

**Energy Demand Parameters**

| Symbol | Description |
|--------|-------------|
| rⱼ ∈ T | Earliest start time of demand j (release time). |
| eⱼ ∈ Z⁺ | Total execution time slots required by demand j (execution time). |
| wⱼ ∈ Z⁺ | Energy demand of j — fixed energy quantity (MWh) required at each execution time slot. |
| deadlineⱼ ∈ Z⁺ | Relative deadline of demand j. |
| periodⱼ ∈ Z⁺ | Execution period of periodic demand j ∈ Jp — time interval to the next release of the same demand. |
| preemptableⱼ ∈ {0,1} | Whether demand j can be interrupted; non-preemptive = 0, preemptive = 1. **Non-preemptive task**: once started, must run continuously for eⱼ time slots. **Preemptive task**: can be interrupted; only needs to accumulate eⱼ execution time slots within the acceptable window. |

**Market Parameters**

| Symbol | Description |
|--------|-------------|
| λₜ ∈ Z⁺∪0 | Market sell price at time t ($/MWh). |

**Conventional Generator Parameters**

| Symbol | Description |
|--------|-------------|
| outputminᵢ ∈ Z⁺ | Minimum output of generator i ∈ Ig (MW). |
| outputmaxᵢ ∈ Z⁺ | Maximum output of generator i ∈ Ig (MW). |
| RUᵢ ∈ Z⁺ | Hourly ramp-up rate of generator i ∈ Ig (MW/h). |
| RDᵢ ∈ Z⁺ | Hourly ramp-down rate of generator i ∈ Ig (MW/h). |
| UTᵢ ∈ Z⁺ | Minimum continuous on-time slots of generator i ∈ Ig. |
| DTᵢ ∈ Z⁺ | Minimum continuous off-time slots of generator i ∈ Ig. |
| costonᵢ ∈ Z⁺ | Fixed cost paid per hour when generator i ∈ Ig is on ($/h). |
| costupᵢ ∈ Z⁺ | Cost per 1 MWh generated by generator i ∈ Ig ($/MWh). |
| TNᵢ ∈ Z⁺∪0 | How long generator i has been continuously on before scheduling starts. |
| TFᵢ ∈ Z⁺∪0 | How long generator i has been continuously off before scheduling starts. |
| Pᵢ,₀ ∈ Z⁺ | Output of generator i ∈ Ig before scheduling starts (MWh). |

**Renewable Parameters**

| Symbol | Description |
|--------|-------------|
| renewmaxᵢ ∈ Z⁺ | Rated maximum output of renewable i ∈ Ir (MW). |
| renewforecastᵢ,ₜ ∈ [0,1] | Average predicted output percentage of renewable i ∈ Ir at time t. |

**Storage Device Parameters**

| Symbol | Description |
|--------|-------------|
| SOCᵢ,₀ ∈ Z⁺ | Initial stored energy of storage device i ∈ Ib (MWh). |
| stominᵢ ∈ Z⁺ | Minimum stored energy of storage device i ∈ Ib (MWh). |
| stomaxᵢ ∈ Z⁺ | Maximum stored energy of storage device i ∈ Ib (MWh). |
| disᵢ ∈ Z⁺ | Maximum hourly discharge power of storage device i ∈ Ib (MW). |
| chgᵢ ∈ Z⁺ | Maximum hourly charge power of storage device i ∈ Ib (MW). |

**State Variables** (variables that change during scheduling due to decision variable assignments)

| Symbol | Description |
|--------|-------------|
| Missⱼ ∈ {0,1} | For aperiodic task j ∈ Ja — whether deadline is missed; 0 = not missed, 1 = missed. |
| Sellₜ ∈ Z⁺∪0 | Energy sold to market at time t (MWh). |
| SOCᵢ,ₜ ∈ Z⁺ | Stored energy of storage device i ∈ Ib at time t (MWh). |

**Decision Variables**

| Symbol | Description |
|--------|-------------|
| Pᵢ,ₜ ∈ Z⁺∪0 | Total output of generation device i at time t (MWh). |
| kⱼ,ᵢ,ₜ ∈ Z⁺∪0 | Energy (MWh) supplied by device i to demand j ∈ Jp∪Js∪Ja at time t; or energy (MWh) supplied by device i to storage b(j) for charging demand j ∈ Jchg at time t. |

---

#### Objective Function

$$\min F = \alpha f_1 + f_2 + f_3$$

**Objective 1: Minimize aperiodic deadline miss count**

$$f_1 = \sum_{j \in Ja} Miss_j$$

Since f₁'s unit is job count and cannot be directly added with cost and revenue, a penalty coefficient α converts it to monetary units.
- α = 10000, unit: $/miss
- Each missed aperiodic job incurs a $10,000 penalty cost.

**Objective 2: Minimize conventional generator cost**

$$f_2 = \sum_{i \in Ig} \sum_{t \in T} \left( coston_i \cdot \min(1, P_{i,t}) + costup_i \cdot P_{i,t} \right)$$

**Objective 3: Maximize market sell revenue**

$$f_3 = -\sum_{t \in T} (\lambda_t \cdot Sell_t)$$

The sell revenue is negated because the overall objective uses minimization form. When sell revenue increases, f₃ decreases, lowering the overall objective F.

---

#### Constraints

| No | Description & Formulation |
|----|--------------------------|
| 1 | If demand j is active at time t, the total energy received must equal wⱼ. $$\sum_{i \in I} k_{j,i,t} = w_j \cdot \min\!\left(1, \sum_{i \in I} k_{j,i,t}\right), \quad \forall j \in J \setminus Jchg, \forall t \in T$$ |
| 2 | Demand j cannot execute before its release time. $$k_{j,i,t} = 0, \quad \forall j \in J \setminus Jchg, \forall i \in I, \forall t < r_j$$ |
| 3 | Demand j ∈ Jp∪Js must complete all required time slots eⱼ before its deadline. $$\sum_{t=r_j}^{r_j + deadline_j - 1} \min\!\left(1, \sum_{i \in I} k_{j,i,t}\right) = e_j, \quad \forall j \in Jp \cup Js$$ |
| 4 | **Aperiodic task miss definition**. If Miss_j = 0: aperiodic task j must complete before deadline: $$\sum_{t=r_j}^{r_j+deadline_j-1} \min\!\left(1,\sum_{i \in I}k_{j,i,t}\right) \geq e_j \cdot (1-Miss_j), \quad \forall j \in Ja$$. If Miss_j = 1: task j does not complete before deadline; recorded as deadline miss: $$\sum_{t=r_j}^{r_j+deadline_j-1} \min\!\left(1,\sum_{i \in I}k_{j,i,t}\right) \leq e_j - 1 + e_j \cdot (1-Miss_j), \quad \forall j \in Ja$$. Demand j ∈ Ja must complete all required time slots eⱼ before the last discrete time point H: $$\sum_{t=r_j}^{H} \min\!\left(1,\sum_{i \in I}k_{j,i,t}\right) = e_j, \quad \forall j \in Ja$$ |
| 5 | Non-preemptive tasks must execute continuously. $$\sum_{t=r_j}^{H-1} \left\lvert \min\!\left(1,\sum_{i \in I}k_{j,i,t+1}\right) - \min\!\left(1,\sum_{i \in I}k_{j,i,t}\right) \right\rvert \leq 2, \quad \forall j \in Jnp$$ |
| 6 | Conventional generator output upper and lower bounds. $$outputmin_i \cdot \Delta t \cdot \min(1,P_{i,t}) \leq P_{i,t} \leq outputmax_i \cdot \Delta t \cdot \min(1,P_{i,t}), \quad \forall i \in Ig, \forall t \in T$$ |
| 7 | Conventional generator ramp-up/ramp-down must not exceed limits. $$P_{i,t} - P_{i,t-1} \leq RU_i \cdot \Delta t, \quad \forall i \in Ig, \forall t \in T$$ $$P_{i,t-1} - P_{i,t} \leq RD_i \cdot \Delta t, \quad \forall i \in Ig, \forall t \in T$$ |
| 8 | Generator minimum output must not exceed the ramp-up capability achievable in a single time slot. $$outputmin_i \leq RU_i \cdot \Delta t, \quad \forall i \in Ig$$ |
| 9 | If generator i transitions from off to on at time t, it must stay on for at least UTᵢ slots. $$\sum_{\tau=t}^{t+UT_i-1} \min(1,P_{i,\tau}) \geq UT_i \cdot \max(0, \min(1,P_{i,t}) - \min(1,P_{i,t-1})), \quad \forall i \in Ig$$ |
| 10 | If generator i transitions from on to off at time t, it must stay off for at least DTᵢ slots. $$\sum_{\tau=t}^{t+DT_i-1} \min(1,P_{i,\tau}) \leq DT_i - DT_i \cdot \max(0, \min(1,P_{i,t-1}) - \min(1,P_{i,t})), \quad \forall i \in Ig$$ |
| 11 | If generator i is already on before scheduling starts and its continuous on-time has not yet met UTᵢ, it must fulfill the remaining minimum on-time. $$\sum_{t=1}^{UT_i - TN_i} \min(1,P_{i,t}) = UT_i - TN_i, \quad \forall i \in Ig$$ |
| 12 | If generator i is already off before scheduling starts and its continuous off-time has not yet met DTᵢ, it must fulfill the remaining minimum off-time. $$\sum_{t=1}^{DT_i - TF_i} \min(1,P_{i,t}) = 0, \quad \forall i \in Ig$$ |
| 13 | Renewable output at each time slot must not exceed the forecasted available energy for that slot. $$0 \leq P_{i,t} \leq renewmax_i \cdot \Delta t \cdot renewforecast_{i,t}, \quad \forall i \in Ir, \forall t \in T$$ |
| 14 | Storage device i discharge Pᵢ,ₜ must not exceed its maximum hourly discharge capacity. $$0 \leq P_{i,t} \leq dis_i \cdot \Delta t, \quad \forall i \in Ib, \forall t \in T$$ |
| 15 | Total charge received by storage device i at time t must not exceed its maximum hourly charge capacity. $$0 \leq \sum_{j \in Jchg: b(j)=i} \sum_{i \in Ig \cup Ir} k_{j,i,t} \leq chg_i \cdot \Delta t, \quad \forall i \in Ib, \forall t \in T$$ |
| 16 | SOC of storage device i at time t equals previous time slot SOC plus charge energy minus discharge energy. $$SOC_{i,t} = SOC_{i,t-1} + \sum_{j \in Jchg: b(j)=i} \sum_{i \in Ig \cup Ir} k_{j,i,t} - P_{i,t}, \quad \forall i \in Ib, \forall t \in T$$ |
| 17 | Storage device i SOC upper and lower bounds. $$stomin_i \leq SOC_{i,t} \leq stomax_i, \quad \forall i \in Ib, \forall t \in T$$ |
| 18 | Storage device i cannot discharge more than the energy above its minimum SOC. $$P_{i,t} \leq SOC_{i,t-1} - stomin_i, \quad \forall i \in Ib, \forall t \in T$$ |
| 19 | A single storage device i cannot charge and discharge simultaneously at time t. $$P_{i,t} \cdot \sum_{j \in Jchg: b(j)=i} \sum_{i \in Ig \cup Ir} k_{j,i,t} = 0, \quad \forall i \in Ib, \forall t \in T$$ |
| 20 | Conventional generators and renewables can supply external loads or charge storage. Storage discharge can only supply external loads, not charge other storage devices. $$\sum_{j \in J} k_{j,i,t} \leq P_{i,t}, \quad \forall i \in Ig \cup Ir, \forall t \in T$$ $$\sum_{j \in J \setminus Jchg} k_{j,i,t} \leq P_{i,t}, \quad \forall i \in Ib, \forall t \in T$$ |
| 21 | Charging demand j ∈ Jchg cannot be supplied by non-generation devices. $$k_{j,i,t} = 0, \quad \forall j \in Jchg, \forall i \notin Ig \cup Ir, \forall t \in T$$ |
| 22 | Total sell energy must not be negative. $$Sell_t \geq 0, \quad \forall t \in T$$ |
| 23 | At each time t, total generation must equal the sum of energy demand, storage charging consumption, and sell energy. $$\sum_{i \in I} P_{i,t} = \sum_{j \in J \setminus Jchg} \sum_{i \in I} k_{j,i,t} + \sum_{j \in Jchg} \sum_{i \in Ig \cup Ir} k_{j,i,t} + Sell_t, \quad \forall t \in T$$ |

---

### 1.4 Level 1 Grading Rubric

| No | Description | Score |
|----|-------------|-------|
| **1** | **Periodic Task Set Design (17 pts total)** | - |
| 1-1 | Periodic task set must include: job ID, release time, period, execution time, energy demand, relative deadline, preemptive/non-preemptive; presented in JSON format. | 3 |
| 1-2 | Periodic task count: must satisfy **6 ≤ \|Jp\| ≤ 10**. | 2 |
| 1-3 | Periodic job total count: expanded periodic jobs must be **> 30**. | 2 |
| 1-4 | Parameter range constraints — each task must satisfy: **1 ≤ rⱼ ≤ periodⱼ**; **6 ≤ periodⱼ ≤ 24(h)**, at least 3 distinct period values; **1 ≤ eⱼ ≤ 4(h)**, at least 2 periodic tasks with eⱼ=2, at least 1 with eⱼ≥3; **eⱼ ≤ deadlineⱼ ≤ periodⱼ**; **6 ≤ wⱼ ≤ 18 (MWh/h)**, at least 2 periodic tasks with wⱼ≥14. Any condition violated → 0 pts for this item. | 2 |
| 1-5 | Periodic workload density: must satisfy **0.7 ≤ D_W = Σ(eⱼ/periodⱼ) for j∈Jp**. | 2 |
| 1-6 | Periodic task deadline: at least 20% of periodic tasks must satisfy **deadlineⱼ = eⱼ**. | 2 |
| 1-7 | Non-preemptive count: at least 2 periodic tasks with **eⱼ ≠ 1** must be non-preemptive. | 2 |
| 1-8 | Frame size constraints — must satisfy: **f ≥ max(eⱼ)**; **H mod f = 0**; **2f − gcd(f, periodⱼ) ≤ deadlineⱼ**. Any condition violated → 0 pts. | 2 |
| **2** | **Model Constraints (27 pts total)** | - |
| 2-1 | Basic constraints: must satisfy constraints 1~3, 5, 20. Deduct 1 pt per missing or violated type. | 5 |
| 2-2 | Aperiodic task constraints: must satisfy constraint 4. | 2 |
| 2-3 | Conventional generator constraints: must satisfy constraints 6~12. Deduct 1 pt per missing or violated type. | 7 |
| 2-4 | Renewable constraints: must satisfy constraint 13. | 1 |
| 2-5 | Storage device constraints: must satisfy constraints 14~19, 21. Deduct 1 pt per missing or violated type. | 7 |
| 2-6 | Sell energy constraint: must satisfy constraint 22. | 1 |
| 2-7 | Hourly supply-demand balance constraint: must satisfy constraint 23. Deduct 0.5 pts per violated hour; stops at 0. | 4 |
| **3** | **Schedule Results & Periodic Task Performance (8 pts total)** | - |
| 3-1 | 72-hour day-ahead fixed schedule: output schedule results in JSON format, compatible with task set, processor settings, and evaluation results. | 2 |
| 3-2 | Periodic jobs complete execution: each periodic job's accumulated execution slots must equal its execution time, and each execution slot must fully supply its energy demand. | 2 |
| 3-3 | Periodic jobs deadline & response time: all periodic jobs must complete before their absolute deadline; if any periodic job misses its deadline → 0 pts. If all complete on time, report the average response time of periodic jobs (shorter average response time = better schedule). | 4 |
| **4** | **Acceptance Test (11 pts total)** | - |
| 4-1 | Acceptance test method description: explain how to perform accept/reject decision when a sporadic job arrives, including available time slot or resource surplus check, deadline check, energy demand check, and how to update schedule state after acceptance. | 3 |
| 4-2 | Accept/Reject decision rationality: for each sporadic job, explain the accept or reject reason. If accepted, state the scheduled slots and deadline feasibility; if rejected, state the rational reason (e.g., insufficient idle slots, insufficient power, insufficient SOC, violated generator constraints, or would cause existing job/constraint violation). | 3 |
| 4-3 | Sporadic schedule value: sporadic task performance is evaluated not by acceptance count but by total execution time actually completed before hard deadline as the schedule value. **Sporadic Value Rate** = (sum of sporadic jobs' execution time completed before hard deadline) / (total sporadic jobs execution time provided at Demo). Rate = 0 → 0 pts; 0 < rate < 0.4 → 1 pt; 0.4 ≤ rate < 0.7 → 2 pts; rate ≥ 0.7 → 3 pts. If system rejects all sporadic jobs → not a scheduling violation, but 0 pts for this item. | 5 |
| **5** | **Evaluation Metrics (7 pts total)** | - |
| 5-1 | Hard deadline miss rate: correctly compute hard deadline job miss rate. | 1 |
| 5-2 | Soft deadline miss rate: correctly compute aperiodic job soft deadline miss rate. | 1 |
| 5-3 | Avg/Max Tardiness: correctly compute **Tⱼ = max(0, Cⱼ − dⱼ)** and report average and maximum. Cⱼ = completion time; dⱼ = absolute deadline. | 2 |
| 5-4 | Avg/Max Response Time: correctly compute **Rⱼ = Cⱼ − rⱼ** and report average and maximum. rⱼ = release time; Cⱼ = completion time. | 2 |
| 5-5 | Completion-time Jitter: compute for different job instances of the same periodic task. | 1 |
| **6** | **Day-Ahead Reserve Strategy Performance Analysis (10 pts total)** | - |
| 6-1 | Reserve strategy algorithm description: explain the algorithm logic and how idle slots, energy surplus, or reserve are preserved. Content may include: how much to reserve, in which time slots, how reserved resources are used when sporadic jobs arrive, and demonstrate the reserve strategy's effect with actual data. | 5 |
| 6-2 | Objective function trade-off analysis: use actual scheduling result data to explain the trade-off relationship among the three objectives (minimize aperiodic miss, minimize conventional generator cost, maximize sell revenue), and include sporadic schedule value or sporadic value rate in the discussion. | 5 |

---

## Level 2 (Score: 80~100)

### 2.1 Objectives

Level 2 builds on the scheduling model established in Level 1, extends the relaxed assumptions (including notation, textual constraint descriptions, and mathematical formulations), designs related parameter tables, and implements a set of advanced dynamic scheduling or rescheduling methods.

### 2.2 Relaxed Assumptions

I. **Renewable uncertainty**: consider situations where actual renewable output differs from forecast values.
II. **Real storage operation scenarios**: consider charge/discharge efficiency, aging costs, self-discharge, cycle limits, or SOC-dependent power limits.
III. **Flexible market mechanisms**: consider day-ahead sell commitments, sell cancellation penalties, or real-time market price changes.
IV. **Job precedence ordering**: add precedence constraints among jobs.

### 2.3 Level 2 Grading Rubric

| No | Description | Score |
|----|-------------|-------|
| 1 | Periodic task set design (17 pts) | - |
| 2 | Model constraints (27 pts) | - |
| 3 | **Relaxed assumptions (10 pts total)** | - |
| 3-1 | Specify which assumptions are relaxed, build the model (notation, textual constraints, and mathematical formulations), and implement in the algorithm. 1 pt per constraint, max 10 pts. Note: notation may be modified but no new decision variables may be added. | 10 |
| 4 | Acceptance Test (11 pts) | - |
| 5 | Schedule Results (8 pts) | - |
| 6 | Evaluation Metrics (7 pts) | - |
| 7 | Day-ahead reserve strategy performance analysis (10 pts) | - |
| **8** | **Advanced Dynamic Scheduling Method Design (10 pts total)** | - |
| 8-1 | Advanced scheduling method design: based on Level 2's relaxed assumptions, design a scheduling method that can handle dynamic information, uncertainty, or real-time state changes, with clearly defined update timing, decision rules, or overall process. Explain the rationale for choosing the method, and analyze its expected impact on rescheduling frequency, computation cost, sporadic job acceptance rate, and trade-offs among the three objectives. Also explain potential conflicts among objectives, e.g., increasing sporadic job acceptance rate may increase generation cost or reduce energy trading revenue, so the scheduling method may not simultaneously optimize all objectives. | 2 |
| 8-2 | Schedule result correctness: output the final scheduling results generated by the advanced dynamic scheduling method and verify in the report whether the results satisfy the main constraints. Content should include: job arrangement results after each update or rescheduling, accept/reject situations for sporadic/aperiodic jobs, whether all hard-deadline jobs completed on time, and whether system resource limits, energy balance, and storage state remain feasible. If job rejection, missed deadline, rescheduling failure, or cost increase occurs, explain the cause and impact on final results. | 4 |
| 8-3 | Schedule result comparison: compare the advanced dynamic scheduling method with the Level 1 static schedule on the three objectives and explain the reasons. For example: how dynamic updates affect sell revenue, conventional generator cost, aperiodic miss, sporadic job acceptance rate, or system feasibility. Must be supported with actual data. | 4 |

---

## Submission Requirements

### 3.1 Source Code

- Task Set Generation Program
- Scheduling Algorithm Program
- Evaluation Program
- Advanced Dynamic Scheduling Program [Level 2 only]
- Runtime Script / Configuration File [Level 2 only, if applicable]

### 3.2 Submission Folder Structure

```
Submission_{GroupID}/
├── README.md
├── report.pdf
├── src/
│   ├── task_generator.*
│   ├── scheduler.*
│   ├── evaluator.*
│   └── advanced_scheduler.*   # Level 2 only
├── input/
│   ├── processor_settings.json
│   └── price_72hr.json
├── output/
│   ├── task_set.json
│   ├── schedule_result.json
│   ├── evaluation_results.json
│   └── acceptance_test_log.json
└── runtime_config.* or crontab.txt   # Level 2 only, if applicable
```

### 3.3 README.md

- Programming language, version, and package requirements
- Compilation method or environment setup
- Program execution flow
- Input/output file descriptions for each program
- How to reproduce the submitted output JSON

### 3.4 Input/Output Files

For Level 2 extensions, use the same output format. If additional fields are needed, describe them in the README. All JSON files must be valid JSON — no comments or trailing commas; required fields must not be deleted or arbitrarily renamed. If a JSON file cannot be read by a standard parser, the related grading items receive 0 pts.

- Task Set (filename: `task_set.json`)
- Schedule Result (filename: `schedule_result.json`)
- Evaluation Results (filename: `evaluation_results.json`)

### 3.5 Report Content

- Periodic task set generation method description
- Relaxed assumption constraint modeling [Level 2 only]
- Scheduling algorithm design description
  - Day-ahead scheduling
  - Advanced dynamic scheduling method [Level 2 only]
- Performance analysis
  - Day-ahead scheduling
  - Advanced dynamic scheduling method [Level 2 only]
- Discussion and reflection
  - Used AI assistance: describe which AI tools, how you collaborated with AI, prompt strategies, and insights
  - Did not use AI assistance: describe how the work was done without AI, references and online resources, and insights

---

## Appendix: Data Format and Output Format Descriptions

> This appendix provides parameter tables, input examples, and output examples. Fields may be added as needed, but required fields and naming conventions in the examples must not be deleted or arbitrarily renamed, so they can be verified and graded.

### A. Conventional Generator Parameters (see `processor_settings.json`)

| Field | Type | Symbol | Description |
|-------|------|--------|-------------|
| generator | Array | - | Conventional generator parameter set; each item represents one conventional generator. |
| generator_id | String | i ∈ Ig | Generator ID, e.g., `thermal_power_unit_1`. |
| output_min | Int | outputminᵢ | Minimum output (MW); once on, output cannot go below this. |
| output_max | Int | outputmaxᵢ | Maximum output (MW); output at any time slot cannot exceed this. |
| ramp_up_rate | Int | RUᵢ | Maximum increase in output between adjacent time slots (MW/h). |
| ramp_down_rate | Int | RDᵢ | Maximum decrease in output between adjacent time slots (MW/h). |
| min_up_time | Int | UTᵢ | Minimum continuous on-time slots once started. |
| min_down_time | Int | DTᵢ | Minimum continuous off-time slots once stopped. |
| cost_fixed | Int | costonᵢ | Fixed cost paid per hour when on ($/h). |
| cost_variable | Int | costupᵢ | Cost per 1 MWh generated ($/MWh). |
| initial_on_time | Int | TNᵢ | Number of slots the generator has been continuously on before scheduling starts; 0 if not on. |
| initial_off_time | Int | TFᵢ | Number of slots the generator has been continuously off before scheduling starts; 0 if not off. |
| initial_energy | Int | Pᵢ,₀ | Generator's initial output (MWh) at t=0 before scheduling starts. |

### B. Renewable Generation Parameters (see `processor_settings.json`)

| Field | Type | Symbol | Description |
|-------|------|--------|-------------|
| renewable_capacity | Array | - | Renewable generation parameter set; each item represents one renewable source. |
| renewable_id | String | i ∈ Ir | Renewable ID, e.g., `pv_1`. |
| capacity | Int | renewmaxᵢ | Rated maximum output (MW). |

### C. Renewable Forecast Parameters (see `processor_settings.json`)

| Field | Type | Symbol | Description |
|-------|------|--------|-------------|
| renewable_forecast | Array | - | Renewable forecast parameter set. |
| {renewable_id} | Array | i ∈ Ir | Single renewable ID, e.g., `pv_1`. |
| hour | Int | - | Forecast time slot; 72 total slots. |
| pv_forecast | Float | renewforecastᵢ,ₜ | Hourly solar predicted output percentage. |

### D. Storage Device Parameters (see `processor_settings.json`)

| Field | Type | Symbol | Description |
|-------|------|--------|-------------|
| storage | Array | - | Storage device parameter set. |
| storage_id | String | i ∈ Ib | Storage device ID, e.g., `battery_1`. |
| soc_min | Int | stominᵢ | Minimum energy that must be retained (MWh). |
| soc_max | Int | stomaxᵢ | Maximum storable energy (MWh). |
| discharge_max | Int | disᵢ | Maximum hourly discharge power (MW). |
| charge_max | Int | chgᵢ | Maximum hourly charge power (MW). |
| soc_init | Int | SOCᵢ,₀ | Initial stored energy before scheduling starts (MWh). |

### E. Market Price Parameters (see `price_72hr.json`)

| Field | Type | Symbol | Description |
|-------|------|--------|-------------|
| price | Array | - | Market price parameter set; each item represents the market sell price for one time slot. |
| hour | Int | - | Forecast price time slot; 72 total slots. |
| market_price | Int | λₜ | Market sell price per time slot ($/MWh). |

### F. Periodic Task Set Example

```json
{
    "periodic": {
        "p1": {"r": 1, "p": 12, "e": 3, "d": 10, "w": 15, "preempt": 1},
        "p2": {"r": 3, "p": 20, "e": 4, "d": 15, "w": 10, "preempt": 1},
        "p3": {"r": 5, "p": 24, "e": 4, "d": 18, "w": 12, "preempt": 0}
    }
}
```

| Key | Description |
|-----|-------------|
| r | release time |
| p | period |
| e | execution time |
| d | relative deadline |
| w | energy demand, unit: MWh |
| preempt | 1 = preemptable, 0 = non-preemptable |

### G. Schedule Result Example

Level 1 and Level 2 both use the same output format. Level 2's advanced dynamic scheduling method should explain how to output scheduling results in the report.

```json
{
  "schedule_result": [
    {
      "t": 1,
      "P": {"thermal_1": 25.0, "pv_1": 5.0, "battery_1": 5.0},
      "k": {
        "p1": {"thermal_1": 10.0, "pv_1": 5.0},
        "p3": {"thermal_1": 10.0},
        "battery_1_chg": {"thermal_1": 0.0}
      },
      "sell": 5.0,
      "soc": {"battery_1": 45.0},
      "missed_aperiodic": [],
      "rejected_sporadic": []
    },
    {
      "t": 2,
      "P": {"thermal_1": 20.0, "pv_1": 8.0, "battery_1": 0.0},
      "k": {
        "p1": {"thermal_1": 10.0, "pv_1": 5.0},
        "battery_1_chg": {"thermal_1": 3.0, "pv_1": 0.0}
      },
      "sell": 10.0,
      "soc": {"battery_1": 48.0},
      "missed_aperiodic": [],
      "rejected_sporadic": ["s1"]
    }
  ]
}
```

| Key | Description |
|-----|-------------|
| t | time point |
| P | total output of each generation device at this time point, unit: MWh |
| k | energy allocation received by each demand from each device, unit: MWh |
| sell | sell energy, unit: MWh |
| soc | remaining stored energy of storage device at the end of this time point, unit: MWh |
| missed_aperiodic | overdue aperiodic tasks — represents soft deadline miss |
| rejected_sporadic | rejected sporadic tasks — acceptance test not passed |

### H. Evaluation Results Example

Level 1 and Level 2 both use the same output format. Level 2's advanced dynamic scheduling method should explain how to output evaluation results in the report.

```json
{
  "hard_deadline_miss_rate": 0.1,
  "soft_deadline_miss_rate": 0.3,
  "average_tardiness": 1.2,
  "max_tardiness": 3,
  "average_response_time": 4.5,
  "max_response_time": 5,
  "completion_time_jitter": 0.8,
  "generator_cost": 18500,
  "market_revenue": 3200,
  "objective_value": 25300
}
```

| Key | Description |
|-----|-------------|
| hard_deadline_miss_rate | hard deadline job miss ratio |
| soft_deadline_miss_rate | soft deadline job miss ratio |
| average_tardiness | average tardiness |
| max_tardiness | maximum tardiness |
| average_response_time | average response time |
| max_response_time | maximum response time |
| completion_time_jitter | completion time jitter |
| acceptance_test | acceptance test related metrics |
| sporadic_value_rate | (sum of sporadic jobs' execution time completed before hard deadline) / (total sporadic jobs execution time provided at Demo) |
| post_acceptance_violation_rate | ratio of cases where accepting a new sporadic job leads to violation of existing jobs or system constraints |
| generator_cost | conventional generator generation cost, unit: $ |
| market_revenue / objective_value | market sell revenue and total objective value, unit: $ |

### I. Sporadic / Aperiodic Jobs Hints (Provided at Demo)

| Type | Hints |
|------|-------|
| Sporadic jobs | 4~7 jobs total; each execution time 1~3 hours; each energy demand 5~20 MWh; may appear at any time slot. Sporadic jobs are hard-deadline jobs requiring acceptance test; performance is measured by total execution time completed before deadline, not simply by acceptance count. |
| Aperiodic jobs | 7~13 jobs total; each execution time 1~4 hours; each energy demand 5~15 MWh; may appear at any time slot. Aperiodic jobs are soft-deadline jobs — no hard-deadline acceptance test; may wait for idle slots to be scheduled; if completed late, record soft deadline miss and tardiness. |
