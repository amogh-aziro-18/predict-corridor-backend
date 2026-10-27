# Phase 17 — Communications Domain Contract (FROZEN)

**Status:** Phase 17.0 architecture contract  
**Scope:** POC only — not a full telecom OSS  
**Phase 16.0–16.6:** CLOSED (do not reopen)

This document freezes the Communications domain for implementation.
Water, Power, and Gas behavior must remain unchanged.

---

## 1. Domain scope

### In scope (initial POC)

| Asset type key | Role | GIS |
|---|---|---|
| `edge_pop` | Corridor edge point-of-presence / aggregation hut | Point (lat/lon) |
| `iot_gateway` | Field IoT / SCADA edge gateway | Point (lat/lon) |

**Topology (seed intent):** 1 `edge_pop` + 1 `iot_gateway` per zone A–D → **8 assets**.

Follow Water/Power/Gas seed catalog conventions for deterministic IDs when seeding (Phase 17.2). Do not invent IDs in this contract phase.

### Explicitly out of scope

- Fibre plant / fibre ring geometry (no LineString)
- Cellular RAN, MPLS core, SD-WAN fleets, customer CPE
- Radio base stations, optical BER / optical dBm
- Full telecom OSS / NMS replacement
- XGBoost, Prophet, anomaly detection, outage ML, forecast APIs
- Communications dependency edges in early Phase 17 (see §7)
- Digital Twin parameter wiring (frozen targets only — implement later)
- Frontend `/communications` command center (implement later; no ML/forecast panels)

---

## 2. Metric contract (ONLY these five)

| Key | Unit | Nature | Encoding |
|---|---|---|---|
| `link_rtt_ms` | ms | Continuous | float |
| `packet_loss_pct` | % | Continuous | float |
| `link_up` | 0–1 | Discrete state | float (`1.0` up, `0.0` down) — required because `Telemetry.value` is float |
| `cabinet_temp_c` | °C | Continuous | float |
| `power_draw_w` | W | Continuous | float |

### Deferred / not suitable (do not add)

`signal_strength_dbm`, `bandwidth_utilization_pct`, `cpu_pct`, `memory_pct`, optical BER/dBm, string `link_status`.

---

## 3. Health model

Reuse Power/Gas condition vocabulary:

`healthy` | `warning` | `high_risk` | `critical` | `unavailable`

| Rule (conceptual) | Condition |
|---|---|
| `link_up == 0` | `unavailable` |
| Sustained severe latency and/or loss | `critical` |
| Elevated latency and/or loss | `high_risk` |
| High cabinet temperature | `high_risk` or `critical` (POC bands) |
| Mild sustained degradation | `warning` |
| Link up + nominal telemetry | `healthy` |

**Thresholds:** When implemented in `health.py`, label every numeric band:

> **POC ASSUMPTION — NOT ENGINEERING STANDARD**

Calibrate bands to the Communications simulator baselines/clamps so all states are demonstrable (same provenance pattern as Gas `health.py`).

---

## 4. Operational pipeline (initial)

```
Telemetry → Rule-based Health → Alerts → OI → Recommendations
```

- No automatic Work Orders.
- Existing WO schema is reusable (`utility` string).
- Technicians may later use `utility_kinds` including `communications` (no names in 17.0).

---

## 5. Simulator (intent for Phase 17.3)

Reuse Water/Power/Gas simulator architecture:

- Tick cadence ~5 s
- Modes: `normal` / `degrading` / `mixed`
- Sticky outage FSM for `link_up` (not a per-tick random boolean)
- Gradual drift for continuous metrics under degradation intensity

---

## 6. Digital Twin (frozen targets — not implemented in 17.0)

Future parameter-first DT parameters (max 5):

1. `link_rtt_ms`
2. `packet_loss_pct`
3. `cabinet_temp_c`
4. `link_up`
5. optional `power_draw_w`

No random sliders. Deterministic projection only when wired.

---

## 7. Dependencies (not in 17.0 / 17.1)

- Current `DependencyType` supports **`supplies` only**.
- Do **not** misuse `supplies` to mean Communications “supports” Water/Power/Gas.
- Power → Communications as electrical `supplies` may be added later if explicitly scoped.
- Communications → other utilities needs a new semantic (`supports` / `enables`) — later architectural change.
- **No Communications dependency edges in Phase 17.0 / 17.1.**

---

## 8. Frontend (not in 17.0 / 17.1)

Future route: `/communications`

Expected sections later: live status, zones, telemetry, health, alerts, recommendations, GIS, Digital Twin, Operations.

**No ML panel. No forecast panel.**

---

## 9. Implementation principles

1. Reuse generic Asset / Sensor / Telemetry / Alert / WO / GIS point infrastructure.
2. Follow Water/Power/Gas plugin package layout under `app/utilities/communications/`.
3. Communications-specific behavior stays in this domain package.
4. Do not change Water/Power/Gas behavior.
5. Do not invent metrics outside §2.
6. Do not change dependency semantics until a dedicated phase.
7. Preserve existing API and migration conventions.
8. Register plugin only when Phase 17.1 implements it (`bootstrap_plugins` + `ENABLED_UTILITIES`).

---

## 10. Suggested later phase sequence

| Phase | Content |
|---|---|
| 17.0 | This contract (done) |
| 17.1 | Plugin + metrics |
| 17.2 | Asset + sensor seed |
| 17.3 | Simulator |
| 17.4 | Health |
| 17.5 | Alerts + OI |
| 17.6–17.7 | **Skip** ML / forecast initially |
| 17.8 | Dashboard API + command center FE |
| 17.9 | Technicians + WO path check |
| 17.10 | GIS points |
| 17.11 | Digital Twin parameters |
| 17.12 | Dependencies (careful semantics) |
| 17.13 | Corridor integration |
| 17.14 | E2E validation |

---

## 11. Repository anchors (verified at contract time)

| Item | Location / fact |
|---|---|
| `UtilityKind.COMMUNICATIONS` | `app/domain/enums.py` |
| Plugin registry (W/P/G/Comm) | `app/utilities/registry.py` |
| Telemetry value type | `float` (`app/domain/models/telemetry.py`) |
| Dependency type | `supplies` only (`DependencyType`) |
| Corridor dashboard | Water + Power + Gas + Communications composition |

---

*Phase 17.0 — architecture contract only. No plugin registration, simulator, seed, health, FE, ML, or dependency edges.*
