# Nutrition Fueling Skill — Spec

Post-activity nutritionist. Pulls a completed Garmin activity, estimates how much of the kcal burn came from carbs vs fat, and uses that to plan the recovery meal and the next 24 hours of macros. The MCP owns Garmin data access and the HR-zone fuel-mix math; this skill owns the athlete profile, the RER table, interval-workout fuel math, and the recovery/macro recommendation logic.

## Two call paths

The MCP tool `estimate_activity_macros_burned` supports two paths, selected by the skill per call:

- **Standard path (steady-state runs/rides):** pass `zone_carb_fractions`. The MCP weights the RER table by Garmin's time-in-HR-zone and returns the full carb/fat split.
- **Interval path:** omit `zone_carb_fractions`. The MCP returns only `activeCalories` and `durationMin`. The skill computes the split using the user's stated interval structure, since HR lag biases time-in-zone for short reps.

See `interval-macro-estimation-agent-spec.md` for the full interval-path spec, math, and worked example.

## Athlete profile (skill-side config)

Lives next to the skill, e.g. `profile.json`:

```json
{
  "bodyWeightKg": 78,
  "maxHr": 192,
  "thresholdHr": 168,
  "zoneCarbFractions": [0.05, 0.15, 0.35, 0.60, 0.85, 0.95],
  "fuelAdaptation": "average"
}
```

`zoneCarbFractions` is the canonical RER table for this athlete, length 6, ordered `[below-Z1, Z1, Z2, Z3, Z4, Z5]`. Each entry is the fraction of energy in that zone coming from carbohydrate (0..1). Fat fraction is `1 - carb`. The leading `below-Z1` entry covers warmup/recovery time below the Z1 lower boundary (e.g. resting walks), which is heavily fat-dominant. Reasonable starting points:

| Athlete profile          | <Z1  | Z1   | Z2   | Z3   | Z4   | Z5   |
|--------------------------|------|------|------|------|------|------|
| Average / mixed-trained  | 0.05 | 0.15 | 0.35 | 0.60 | 0.85 | 0.95 |
| Highly fat-adapted       | 0.05 | 0.10 | 0.20 | 0.45 | 0.75 | 0.90 |
| Carb-fueled / glycolytic | 0.10 | 0.25 | 0.45 | 0.70 | 0.90 | 0.97 |

Tune over time as real-world data (gut tolerance, post-ride RPE, glucose monitor) accumulates.

`bodyWeightKg`, `maxHr`, `thresholdHr` aren't passed to the MCP — they're for the skill to provide context-aware advice (e.g., g/kg/hr targets, zone sanity checks).

## Activity selection

The skill is invoked post-workout, so the activity already exists in Garmin. Common entry points:

- "Plan my recovery from this morning's run" → most recent activity today
- "How should I eat tomorrow after today's long ride?" → most recent endurance-type activity today
- "Recovery for activity 12345678" → explicit ID

The skill resolves an `activity_id` via `list_activities`:

```python
acts = mcp.list_activities(start_date=today, end_date=today)["activities"]
# disambiguate by activityType, startTimeLocal, duration if multiple
target = pick_target(acts, user_intent)
activity_id = target["activityId"]
```

If no activity is found for today, fall back to yesterday before asking the user — same-day recovery questions sometimes come in late evening for a morning session.

## Interval detection

Activate the interval path when the user:

1. Describes the run as **intervals** (or threshold reps, sprints, fartlek, "5×400s", etc.), and
2. Provides (or can answer when asked) **interval zone** and **prescribed interval minutes**.

If either signal is missing, ask before proceeding. Do not infer.

## Tool calls

### Standard path

```python
result = mcp.estimate_activity_macros_burned(
    activity_id=activity_id,
    zone_carb_fractions=profile["zoneCarbFractions"],
)
```

Returns:

```json
{
  "durationMin": 90.0,
  "activeCalories": 1240,
  "carbKcal": 768.8,
  "carbG": 192.2,
  "fatKcal": 471.2,
  "fatG": 52.4
}
```

### Interval path

```python
result = mcp.estimate_activity_macros_burned(activity_id=activity_id)
# returns: {"durationMin": 49.0, "activeCalories": 700}
```

Then the skill computes the split locally — see `interval-macro-estimation-agent-spec.md` for the formula. Output to the user includes audit fields:

```json
{
  "durationMin": 49.0,
  "activeCalories": 700,
  "carbG": 75.6,
  "fatG": 44.2,
  "weightingSource": "manual_interval",
  "intervalZone": "Z4",
  "intervalMinutes": 25,
  "remainderMinutes": 24
}
```

## Skill responsibilities

1. **Resolve activity** via `list_activities` (see above).
2. **Load athlete profile** from `profile.json`.
3. **Detect interval workout** from user input. If interval path applies, gather `intervalZone` and `intervalMinutes` (ask the user if not volunteered).
4. **Call `estimate_activity_macros_burned`** with or without `zone_carb_fractions` based on path.
5. **Compute interval-path split locally** when the standard path was skipped.
6. **Build the recovery plan** using `carbG`, `fatG`, `activeCalories`, `durationMin`:
   - **Immediate recovery (0–2 hr post)**: 1.0–1.2 g/kg carb + 0.3–0.4 g/kg protein. Higher end of carb range if `carbG` burned was high or another session is within 24 hr.
   - **Same-day total**: replace ≥80% of `carbG` burned across remaining meals; protein ~1.6–2.2 g/kg/day total.
   - **Next 24 hr macro mix**: bias carb intake toward what was burned; tomorrow's carb target ≈ baseline + (burned `carbG` − replaced today). Fat stays near baseline; protein floor regardless of session.
7. **Return a structured recommendation** with: immediate recovery meal targets (g carb, g protein, fluid mL), remainder-of-day carb gap, next-24hr macro split, and the audit fields if interval-path was used.

## Caveats to surface in the user-facing answer

- The RER table is a population approximation; tune over time.
- Fed vs fasted shifts substrate use; estimates assume a typical fed pre-workout state.
- Power-based zones (cycling) would be more accurate than HR for fuel mix. If `estimate_activity_macros_burned` later supports power zones, prefer them.
- Garmin's auto-zones drift with fitness — re-verify `thresholdHr` and Garmin zone settings quarterly.
- Interval-path math assumes non-interval time fell at Z1 effort. For long warmups/cooldowns at Z2, the split slightly underestimates carbs.

## Skill interface

```python
def recovery_plan(user_request: str) -> RecoveryPlan:
    profile = load_profile()
    activity_id = resolve_activity(user_request)  # list_activities → pick target

    if is_interval_workout(user_request):
        interval_zone, interval_min = extract_interval_params(user_request)
        raw = mcp.estimate_activity_macros_burned(activity_id=activity_id)
        estimate = compute_interval_split(raw, interval_zone, interval_min, profile["zoneCarbFractions"])
    else:
        estimate = mcp.estimate_activity_macros_burned(
            activity_id=activity_id,
            zone_carb_fractions=profile["zoneCarbFractions"],
        )

    return build_recovery_plan(estimate, profile)
```

The plan covers: immediate post-workout meal, remainder of today, and the next 24 hours of macro targets. Bodyweight scaling happens skill-side; kcal and (standard-path) grams come from the MCP.
