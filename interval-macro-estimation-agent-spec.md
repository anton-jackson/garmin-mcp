# Interval Run Macro Estimation — Spec

Status: **draft**

---

## Problem

`estimate_activity_macros_burned` weights the carb/fat split by time-in-HR-zone. For
interval runs, HR lag understates time at high intensity — a threshold-pace rep may peak
HR in Z3, never reaching Z4 — pulling the carb fraction below reality.

Total active kcal is accurate. Garmin's FirstBeat fuses HR, GPS, and accelerometer, so
`activeCalories` is reliable. The bias is in the **macro split only**.

---

## Solution

Add a manual interval path in the `activity-macros-burned` skill. The agent computes the
carb/fat split itself using operator-provided session structure. The MCP is called only
for `activeCalories`.

---

## Two Call Paths

### Standard path (steady-state runs)
`zone_carb_fractions` provided → MCP computes full split via HR-zone weighting.
Unchanged behavior.

```
estimate_activity_macros_burned(activity_id, zone_carb_fractions)
→ { activeCalories, durationMin, carbG, fatG, carbKcal, fatKcal }
```

### Interval path
`zone_carb_fractions` omitted → MCP returns kcal and duration only. Agent computes split.

```
estimate_activity_macros_burned(activity_id)
→ { activeCalories, durationMin }
```

---

## MCP Change

**Make `zone_carb_fractions` optional.**

Server-side: guard the split calculation behind `if zone_carb_fractions provided`. When
omitted, return only `activeCalories` and `durationMin`. No new tool, no behavioral
change for existing callers.

---

## Skill Trigger

Activate the interval path when the user:
1. Describes the run as **"intervals"**, and
2. Provides **interval zone** and **prescribed interval minutes**

If either is missing, ask before proceeding. Do not infer.

---

## Operator-Provided Inputs

| Field | Example | Notes |
|---|---|---|
| `interval_zone` | `Z4` | Zone where reps were performed |
| `interval_minutes` | `25` | Total prescribed time at that zone, not including rest |
| `total_duration_min` | `49` | Total session duration — from user or Garmin |

`activeCalories` comes from the MCP call above.

---

## Agent Calculation

```
remainder_min = total_duration_min - interval_minutes   # all non-interval time → Z1

w_rest     = remainder_min / total_duration_min
w_interval = interval_minutes / total_duration_min

zone_index = { Z1:1, Z2:2, Z3:3, Z4:4, Z5:5 }

carb_fraction = (w_rest × zcf[1]) + (w_interval × zcf[interval_zone_index])

carb_g = (activeCalories × carb_fraction) / 4
fat_g  = (activeCalories × (1 − carb_fraction)) / 9
```

`zcf` = `zone_carb_fractions` from athlete profile.

**Multiple interval zones** (e.g. Z4 + Z5): sum a term per zone, remainder still → Z1.

---

## Output

Same fields as standard path. Add audit fields:

| Field | Value |
|---|---|
| `weightingSource` | `"manual_interval"` |
| `intervalZone` | from user |
| `intervalMinutes` | from user |
| `remainderMinutes` | computed |

---

## Worked Example

49-min run, 700 kcal active, 25 min at Z4.
Profile `zone_carb_fractions`: `[0.05, 0.10, 0.20, 0.45, 0.75, 0.90]`

```
remainder = 49 - 25 = 24 min
w_rest     = 24/49 = 0.490
w_interval = 25/49 = 0.510

carb_fraction = (0.490 × 0.10) + (0.510 × 0.75)
              = 0.049 + 0.383 = 0.432

carb_g = (700 × 0.432) / 4 = 75.6g
fat_g  = (700 × 0.568) / 9 = 44.2g
```

HR path comparison: lag pulls most of those 25 min into Z3 → fraction ≈ 0.35 → ~61g
carbs. Interval path recovers ~14g.

---

## Error Handling

| Condition | Action |
|---|---|
| `interval_minutes > total_duration_min` | Error — ask user to verify inputs |
| `remainder_min < 5` | Warn — very little non-interval time, confirm before proceeding |
| User provides sets not minutes (`6×5 min`) | Convert to minutes, confirm before computing |
| `activeCalories` not returned by MCP | Cannot proceed — surface error to user |
