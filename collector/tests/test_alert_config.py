"""Per-alert-type tuning: defaults, validation, threshold scaling, muting."""
import pytest

from collector.alert_config import (
    get_config, is_muted, scaled_thresholds, set_config,
)
from collector.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "t.db")


def test_defaults_seeded(store):
    cfg = get_config(store)
    assert set(cfg) == {"anomaly", "trend"}
    assert cfg["anomaly"]["threshold_mult"] == 1.0
    assert cfg["anomaly"]["muted"] is False
    assert "label" in cfg["trend"]


def test_set_and_get_roundtrip(store):
    entry = set_config(store, "anomaly", threshold_mult=2.0, muted=True)
    assert entry["threshold_mult"] == 2.0 and entry["muted"] is True
    assert is_muted(store, "anomaly") is True
    assert is_muted(store, "trend") is False
    # partial update keeps the other field
    entry = set_config(store, "anomaly", muted=False)
    assert entry["threshold_mult"] == 2.0 and entry["muted"] is False


def test_validation(store):
    with pytest.raises(ValueError, match="unknown alert type"):
        set_config(store, "nope", muted=True)
    with pytest.raises(ValueError, match="between"):
        set_config(store, "anomaly", threshold_mult=99)
    with pytest.raises(ValueError, match="between"):
        set_config(store, "anomaly", threshold_mult=0)
    with pytest.raises(ValueError, match="must be true or false"):
        set_config(store, "anomaly", muted="yes")
    # nothing persisted on failed validation
    assert get_config(store)["anomaly"]["threshold_mult"] == 1.0


def test_scaled_thresholds(store):
    z, t = scaled_thresholds(store, 2.2, 1.15)
    assert (z, t) == (2.2, 1.15)
    set_config(store, "anomaly", threshold_mult=2.0)
    z, t = scaled_thresholds(store, 2.2, 1.15)
    assert z == 4.4 and t == 1.15


def test_threshold_mult_reduces_digest_alerts(tmp_path):
    """End-to-end: doubling the anomaly multiplier drops borderline alerts."""
    from dataclasses import replace
    from datetime import date, timedelta
    from pathlib import Path

    from collector.config import load_config
    from collector.insights import build_digest

    cfg0 = load_config(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    store = Store(tmp_path / "t.db")
    base = date(2026, 1, 1)
    pts = [(base + timedelta(days=i), 100.0 + (i % 2)) for i in range(40)]
    pts.append((base + timedelta(days=40), 102.0))  # ~3σ pop: fires at 2.2σ, not at 4.4σ
    store.upsert_points("cycle:t1", pts)

    class S:
        id = "t1"; name = "T1"; unit = "%"; transform = "none"; hidden = False

    cfg = replace(cfg0, series=[], cycle_series=[S()], indexes=[], bonds=[],
                  cb_rates=[], refs=replace(cfg0.refs, aave=[], llama_chart=[],
                                            pendle=[], funding=[]))
    d0 = build_digest(store, cfg)
    assert len(d0["alerts"]) >= 1
    set_config(store, "anomaly", threshold_mult=2.0)  # 2.2σ -> 4.4σ
    d1 = build_digest(store, cfg)
    assert len(d1["alerts"]) == 0


def test_muted_types_never_send(tmp_path, monkeypatch):
    """notify.push_new_alerts skips muted kinds but still advances baseline."""
    import asyncio
    import os

    from collector import notify
    from collector.store import Store

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    store = Store(tmp_path / "t.db")
    store.put_doc("insights", {"alerts": [
        {"id": "anomaly:x", "name": "X", "summary": "s", "as_of": "2026-10-01"}],
        "trends": []}, "t")
    sent_msgs = []

    async def fake_post(url, json=None):
        sent_msgs.append(json["text"])

    async def run():
        # baseline
        assert await notify.push_new_alerts(store, fake_post) == 0
        # new alert arrives while muted
        set_config(store, "anomaly", muted=True)
        store.put_doc("insights", {"alerts": [
            {"id": "anomaly:x", "name": "X", "summary": "s", "as_of": "2026-10-01"},
            {"id": "anomaly:y", "name": "Y", "summary": "s", "as_of": "2026-10-02"}],
            "trends": []}, "t")
        assert await notify.push_new_alerts(store, fake_post) == 0
        assert sent_msgs == []
        # unmuted -> the queued alert sends
        set_config(store, "anomaly", muted=False)
        store.put_doc("insights", {"alerts": [
            {"id": "anomaly:x", "name": "X", "summary": "s", "as_of": "2026-10-01"},
            {"id": "anomaly:y", "name": "Y", "summary": "s", "as_of": "2026-10-02"},
            {"id": "anomaly:z", "name": "Z", "summary": "s", "as_of": "2026-10-03"}],
            "trends": []}, "t")
        assert await notify.push_new_alerts(store, fake_post) == 1
        assert len(sent_msgs) == 1 and "Z" in sent_msgs[0]

    asyncio.run(run())
