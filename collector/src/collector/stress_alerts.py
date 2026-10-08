"""Stress Monitor v2 alert engine (Phase 2, WS2). Spec section 9 (ALERTS).

Evaluates every section-9 rule against the cached ``stress_matrix`` and
``stress_velocity`` docs (read via ``store.doc``). Scores are never recomputed
inline here: this module only reads the cached docs produced by
``collector.stress_score.refresh_stress``.

State tracking: transitions such as "first time in 4 weeks" and "crosses" are
detected across runs via the persisted ``stress_alerts_state`` doc
(fired rule keys + dates). An ongoing condition fires once, on transition;
it does not re-fire daily. Rule (1) re-arms 4 weeks after its last firing.

Public API:
    evaluate_alerts(store) -> dict   # evaluate rules, persist state, return fired alerts
    alerts_payload(store)  -> dict   # {as_of, alerts, counts} for GET /api/stress/alerts

Stdlib only. No collector imports (cached docs only).

NOTE for WS1 / coordinator: chain ``evaluate_alerts(store)`` immediately
after the matrix refresh in the stress scheduler job. scheduler.py is owned
by another worker; this module only exposes the function.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

STATE_DOC = "stress_alerts_state"
SOURCE_LABEL = "stress-v2-alerts"

# Rule thresholds (spec section 9).
RED_SCORE = 90.0          # cell "turns red"
REARM_DAYS = 28           # rule (1): re-arm after 4 weeks
COMP_HIGH = 70.0          # rule (2): category composite crosses
BREADTH_PCT = 0.30        # rule (3): breadth (share above 75) crosses
SIGMA_D1 = 3.0            # rule (4): 1d sigma in stress direction
SIGMA_D5 = 2.5            # rule (4): 5d sigma in stress direction
CLUSTER_N = 5             # rule (5): indicators per category
CLUSTER_PCT = 90.0        # rule (5): 5d velocity percentile
ACCEL_DAYS = 3            # rule (6): consecutive days
EMERGING_PCT = 95.0       # rule (7): velocity percentile
SPEED_TRIGGER = {"Rapid", "Violent"}  # rule (8)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _doc_as_of(matrix: dict) -> date:
    """Trading date the matrix describes (overall as_of, else updated_at)."""
    overall = matrix.get("overall") or {}
    d = _to_date(overall.get("as_of")) or _to_date(matrix.get("updated_at"))
    return d or date.today()


def _fresh_state() -> dict:
    return {
        "as_of": None,
        "updated_at": None,
        "red_last_fired": {},      # rule 1: iid -> date iso
        "comp_last": {},           # rule 2: category -> last composite score
        "breadth_last": None,      # rule 3: last share_above_75
        "fast_active": {},         # rule 4: iid -> fired date iso (ongoing)
        "cluster_active": {},      # rule 5: category -> fired date iso
        "accel_days": {},          # rule 6: category -> [date iso, ...] trailing
        "accel_warned": {},        # rule 6: category -> bool (fired this streak)
        "emerging_active": {},     # rule 7: iid -> fired date iso
        "speed_last": None,        # rule 8: last overall speed label
        # Fast-move alert log for the section-10 false-alarm-rate check.
        "fast_move_alerts": [],    # [{indicator, category, fired_at, composite_at_fire}]
    }


def _load_state(store: Any) -> dict:
    state = _fresh_state()
    try:
        sdoc = store.doc(STATE_DOC)
    except Exception:  # noqa: BLE001 - a broken state store must not kill alerts
        sdoc = None
    if sdoc is not None and isinstance(sdoc.payload, dict):
        for key in state:
            if key in sdoc.payload:
                state[key] = sdoc.payload[key]
    return state


def _matrix_ok(ind: dict) -> bool:
    """Eligible for level rules: matrix status exactly 'ok'.

    Never fire on stale or n/a series (WS5 marks stale rows status='stale';
    building/no_data/no_velocity/error are likewise excluded).
    """
    return isinstance(ind, dict) and ind.get("status") == "ok"


def _vel_ok(vel_indicators: dict, iid: str) -> bool:
    v = vel_indicators.get(iid)
    return isinstance(v, dict) and v.get("status") == "ok"


def _mk_alert(rule: str, severity: str, message: str, as_of: date,
              indicator: str | None = None, category: str | None = None,
              detail: str | None = None) -> dict:
    return {
        "rule": rule,
        "severity": severity,
        "indicator": indicator,
        "category": category,
        "message": message,
        "detail": detail,
        "fired_at": as_of.isoformat(),
        "evaluated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# rule evaluation
# ---------------------------------------------------------------------------

def _rule_new_stress(matrix: dict, state: dict, as_of: date) -> list[dict]:
    """(1) Cell turns red (score >= 90) for the first time in 4 weeks."""
    alerts = []
    red_last = state["red_last_fired"]
    for iid, ind in (matrix.get("indicators") or {}).items():
        if not _matrix_ok(ind):
            continue
        score = ind.get("score")
        if score is None or score < RED_SCORE:
            continue
        last = _to_date(red_last.get(iid))
        if last is not None and (as_of - last).days <= REARM_DAYS:
            continue  # fired within the last 4 weeks; re-arm not yet due
        alerts.append(_mk_alert(
            "new_stress", "critical",
            f"New stress in {ind.get('name') or iid}",
            as_of, indicator=iid, category=ind.get("category"),
            detail=f"score {score:.1f} >= {RED_SCORE:.0f}"))
        red_last[iid] = as_of.isoformat()
    return alerts


def _rule_category_high(matrix: dict, state: dict, as_of: date) -> list[dict]:
    """(2) Category composite crosses 70. Requires composite status 'ok'
    (a low-coverage composite is not a trustworthy crossing)."""
    alerts = []
    comp_last = state["comp_last"]
    for cat, comp in (matrix.get("composites") or {}).items():
        if not isinstance(comp, dict) or comp.get("status") != "ok":
            continue
        score = comp.get("score")
        prev = comp_last.get(cat)
        if score is not None:
            # First sighting at/above 70 counts as a crossing (operator
            # should know the current state); afterwards only transitions.
            if score >= COMP_HIGH and (prev is None or prev < COMP_HIGH):
                alerts.append(_mk_alert(
                    "category_high", "high", f"{cat} High", as_of,
                    category=cat,
                    detail=f"composite {score:.1f} crossed {COMP_HIGH:.0f}"))
            comp_last[cat] = score
    return alerts


def _rule_breadth(velocity: dict, state: dict, as_of: date) -> list[dict]:
    """(3) Breadth (share of scores above 75) crosses 30%."""
    alerts = []
    breadth = velocity.get("breadth") or {}
    share = breadth.get("share_above_75")
    prev = state.get("breadth_last")
    if share is not None:
        if share >= BREADTH_PCT and (prev is None or prev < BREADTH_PCT):
            alerts.append(_mk_alert(
                "breadth", "high", "Stress broadening", as_of,
                detail=(f"{share * 100:.1f}% of indicators score above 75 "
                        f"(n={(breadth.get('n'))})")))
        state["breadth_last"] = share
    return alerts


def _rule_fast_move(matrix: dict, velocity: dict, state: dict,
                    as_of: date) -> list[dict]:
    """(4) Fast move: 1d sigma > 3 or 5d sigma > 2.5 in the stress direction.

    The cached sigma values are direction-adjusted by the engine (positive =
    stress direction), so the threshold test itself is the direction test.
    Fires once per ongoing move; re-arms when the condition clears.
    """
    alerts = []
    vel_indicators = velocity.get("indicators") or {}
    active = state["fast_active"]
    log = state["fast_move_alerts"]
    for iid, ind in (matrix.get("indicators") or {}).items():
        if not _matrix_ok(ind) or not _vel_ok(vel_indicators, iid):
            active.pop(iid, None)
            continue
        sigma = (vel_indicators[iid].get("sigma") or {})
        d1 = sigma.get("d1")
        d5 = sigma.get("d5")
        legs = []
        if d1 is not None and d1 > SIGMA_D1:
            legs.append(f"1d {d1:+.1f}σ")
        if d5 is not None and d5 > SIGMA_D5:
            legs.append(f"5d {d5:+.1f}σ")
        if legs:
            if iid not in active:
                comp = (matrix.get("composites") or {}).get(
                    ind.get("category") or "", {})
                alerts.append(_mk_alert(
                    "fast_move", "high",
                    f"Fast move in {ind.get('name') or iid}",
                    as_of, indicator=iid, category=ind.get("category"),
                    detail=", ".join(legs)))
                active[iid] = as_of.isoformat()
                log.append({
                    "indicator": iid,
                    "category": ind.get("category"),
                    "fired_at": as_of.isoformat(),
                    "composite_at_fire": comp.get("score"),
                })
                # Bound the log; the false-alarm check only needs the last 1Y.
                del log[:-2000]
        else:
            active.pop(iid, None)  # condition cleared -> re-arm
    return alerts


def _rule_velocity_cluster(matrix: dict, velocity: dict, state: dict,
                           as_of: date) -> list[dict]:
    """(5) >= 5 indicators in one category with 5d velocity percentile > 90
    on the same day. Fires on transition; re-arms when the cluster clears.

    Note: the cached pctile is magnitude-based (|change| rank), so this rule
    follows the spec literally without a stress-direction filter.
    """
    alerts = []
    vel_indicators = velocity.get("indicators") or {}
    counts: dict[str, int] = {}
    for iid, ind in (matrix.get("indicators") or {}).items():
        if not _matrix_ok(ind) or not _vel_ok(vel_indicators, iid):
            continue
        pct = (vel_indicators[iid].get("pctile") or {}).get("d5")
        if pct is not None and pct > CLUSTER_PCT:
            cat = ind.get("category") or "?"
            counts[cat] = counts.get(cat, 0) + 1
    active = state["cluster_active"]
    for cat, n in counts.items():
        if n >= CLUSTER_N and cat not in active:
            alerts.append(_mk_alert(
                "velocity_cluster", "high",
                f"Velocity cluster in {cat}", as_of, category=cat,
                detail=f"{n} indicators with 5d velocity percentile > 90"))
            active[cat] = as_of.isoformat()
    for cat in list(active):
        if counts.get(cat, 0) < CLUSTER_N:
            del active[cat]  # cluster cleared -> re-arm
    return alerts


def _rule_acceleration(matrix: dict, velocity: dict, state: dict,
                       as_of: date) -> list[dict]:
    """(6) Acceleration warning: composite above 50 with positive acceleration
    for 3 consecutive days. Consecutive *dates* are tracked in state so hourly
    re-runs within one day do not inflate the streak."""
    alerts = []
    vel_comps = velocity.get("composites") or {}
    matrix_comps = matrix.get("composites") or {}
    as_of_iso = as_of.isoformat()
    for cat, vc in vel_comps.items():
        if not isinstance(vc, dict):
            continue
        comp = matrix_comps.get(cat) or {}
        score = comp.get("score")
        accel = vc.get("accel")
        cond = (score is not None and score > 50
                and accel is not None and accel > 0)
        days = state["accel_days"].get(cat) or []
        if cond:
            if not days or days[-1] != as_of_iso:
                days.append(as_of_iso)
            days = days[-ACCEL_DAYS:]
            state["accel_days"][cat] = days
            if len(days) >= ACCEL_DAYS and not state["accel_warned"].get(cat):
                alerts.append(_mk_alert(
                    "acceleration", "medium",
                    f"Acceleration warning in {cat}", as_of, category=cat,
                    detail=(f"composite {score:.1f} > 50 with positive "
                            f"acceleration {ACCEL_DAYS} consecutive days")))
                state["accel_warned"][cat] = True
        else:
            state["accel_days"][cat] = []
            state["accel_warned"][cat] = False
    return alerts


def _rule_emerging_stress(matrix: dict, velocity: dict, state: dict,
                          as_of: date) -> list[dict]:
    """(7) Emerging stress: score below the 50th percentile but 5d velocity
    percentile above 95. The cached pctile is magnitude-based, so a
    stress-direction guard (5d sigma > 0) is applied and documented here."""
    alerts = []
    vel_indicators = velocity.get("indicators") or {}
    active = state["emerging_active"]
    for iid, ind in (matrix.get("indicators") or {}).items():
        if not _matrix_ok(ind) or not _vel_ok(vel_indicators, iid):
            active.pop(iid, None)
            continue
        score = ind.get("score")
        v = vel_indicators[iid]
        pct5 = (v.get("pctile") or {}).get("d5")
        sig5 = (v.get("sigma") or {}).get("d5")
        cond = (score is not None and score < 50
                and pct5 is not None and pct5 > EMERGING_PCT
                and sig5 is not None and sig5 > 0)
        if cond:
            if iid not in active:
                alerts.append(_mk_alert(
                    "emerging_stress", "medium",
                    f"Emerging stress in {ind.get('name') or iid}",
                    as_of, indicator=iid, category=ind.get("category"),
                    detail=(f"score {score:.1f} (< 50) with 5d velocity "
                            f"percentile {pct5:.0f} (> 95)")))
                active[iid] = as_of.isoformat()
        else:
            active.pop(iid, None)
    return alerts


def _rule_speed(velocity: dict, state: dict, as_of: date) -> list[dict]:
    """(8) Overall speed label moves to Rapid or Violent. Fires on transition
    into {Rapid, Violent}; re-arms when the label leaves that set."""
    alerts = []
    label = (velocity.get("overall") or {}).get("speed_label")
    prev = state.get("speed_last")
    if label in SPEED_TRIGGER and prev not in SPEED_TRIGGER:
        alerts.append(_mk_alert(
            "speed", "critical" if label == "Violent" else "high",
            f"Speed: {label}", as_of,
            detail="overall market speed moved to " + str(label)))
    state["speed_last"] = label
    return alerts


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def evaluate_alerts(store: Any) -> dict:
    """Evaluate all section-9 rules against the cached docs and persist state.

    Returns {"as_of", "status", "alerts", "counts", "active"} where "alerts"
    are the alerts fired by *this* evaluation (transitions only).
    """
    mdoc = store.doc("stress_matrix")
    vdoc = store.doc("stress_velocity")
    if mdoc is None or vdoc is None or not isinstance(mdoc.payload, dict) \
            or not isinstance(vdoc.payload, dict):
        missing = [k for k, d in (("stress_matrix", mdoc),
                                  ("stress_velocity", vdoc)) if d is None]
        return {"as_of": None, "status": "no_data",
                "reason": f"missing cached docs: {', '.join(missing)}",
                "alerts": [], "counts": {"total": 0}, "active": {}}

    matrix, velocity = mdoc.payload, vdoc.payload
    as_of = _doc_as_of(matrix)
    state = _load_state(store)

    alerts: list[dict] = []
    alerts += _rule_new_stress(matrix, state, as_of)
    alerts += _rule_category_high(matrix, state, as_of)
    alerts += _rule_breadth(velocity, state, as_of)
    alerts += _rule_fast_move(matrix, velocity, state, as_of)
    alerts += _rule_velocity_cluster(matrix, velocity, state, as_of)
    alerts += _rule_acceleration(matrix, velocity, state, as_of)
    alerts += _rule_emerging_stress(matrix, velocity, state, as_of)
    alerts += _rule_speed(velocity, state, as_of)

    state["as_of"] = as_of.isoformat()
    state["updated_at"] = _now_iso()
    store.put_doc(STATE_DOC, state, source=SOURCE_LABEL)

    by_rule: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for a in alerts:
        by_rule[a["rule"]] = by_rule.get(a["rule"], 0) + 1
        by_sev[a["severity"]] = by_sev.get(a["severity"], 0) + 1

    return {
        "as_of": as_of.isoformat(),
        "status": "ok",
        "alerts": alerts,
        "counts": {"total": len(alerts), "by_rule": by_rule,
                   "by_severity": by_sev},
        "active": {
            "fast_move": sorted(state["fast_active"]),
            "velocity_cluster": sorted(state["cluster_active"]),
            "emerging_stress": sorted(state["emerging_active"]),
            "acceleration_streaks": {c: len(d)
                                     for c, d in state["accel_days"].items()
                                     if d},
            "speed_label": state["speed_last"],
        },
    }


def alerts_payload(store: Any) -> dict:
    """Payload for GET /api/stress/alerts (wired by WS1)."""
    result = evaluate_alerts(store)
    result["note"] = (
        "Evaluated from the cached stress_matrix / stress_velocity docs; "
        "WS1/coordinator must chain evaluate_alerts() after each matrix "
        "refresh in the stress scheduler job.")
    return result
