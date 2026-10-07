"""OFR TFF fetcher tests: slug, derived nets, doc payload, per-series isolation."""
import json
from datetime import date

import pytest

from collector.fetchers import ofr_tff
from collector.fetchers.ofr_tff import fetch_ofr_tff, slug
from collector.store import Store


def test_slug():
    assert slug("TFF-LF_TREAS_NET_POSITION") == "lf_treas_net_position"
    assert slug("TFF-AI_TREAS_LONG_DV01") == "ai_treas_long_dv01"


MNEMONICS = [
    {"mnemonic": "TFF-LF_TREAS_NET_POSITION", "series_name": "LF net treas"},
    {"mnemonic": "TFF-AI_TREAS_LONG_POSITION", "series_name": "AI long treas"},
    {"mnemonic": "TFF-AI_TREAS_SHORT_POSITION", "series_name": "AI short treas"},
    {"mnemonic": "TFF-LF_VIX_NET_POSITION", "series_name": "LF net vix"},
]

TS = {
    "TFF-LF_TREAS_NET_POSITION": [["2026-09-08", -790000000000.0], ["2026-09-15", -798806700000.0]],
    "TFF-AI_TREAS_LONG_POSITION": [["2026-09-08", 500.0], ["2026-09-15", 600.0]],
    "TFF-AI_TREAS_SHORT_POSITION": [["2026-09-08", 200.0], ["2026-09-15", 250.0]],
    "TFF-LF_VIX_NET_POSITION": [["2026-09-15", 12345.0]],
}


def _get_text_factory(fail=()):
    async def get_text(url, params=None):
        if "metadata/mnemonics" in url:
            return json.dumps(MNEMONICS)
        m = (params or {}).get("mnemonic")
        if m in fail:
            raise RuntimeError("boom")
        return json.dumps(TS[m])
    return get_text


@pytest.mark.asyncio
async def test_fetch_ofr_tff(tmp_path):
    store = Store(tmp_path / "t.db")
    assert await fetch_ofr_tff(store, _get_text_factory()) == "ofr_tff"
    # raw series stored
    pts = store.points("cycle:tff-lf_treas_net_position")
    assert len(pts) == 2
    assert pts[date(2026, 9, 15)] == -798806700000.0
    # derived AI net (long - short)
    ai = store.points("cycle:tff-ai_treas_net_position")
    assert len(ai) == 2
    assert ai[date(2026, 9, 15)] == 350.0
    # doc written with curated groups
    doc = store.doc("tff")
    assert doc is not None
    p = doc.payload
    assert p["asof"] == "2026-09-15"
    assert p["mnemonic_count"] == 4
    lf_row = p["groups"][0]["rows"][0]
    assert lf_row["mnemonic"] == "TFF-LF_TREAS_NET_POSITION"
    assert len(lf_row["hist"]) == 2
    assert lf_row["hist"][-1][1] == -798806700000.0


@pytest.mark.asyncio
async def test_per_series_isolation(tmp_path):
    # one bad mnemonic must not starve the others
    store = Store(tmp_path / "t.db")
    await fetch_ofr_tff(store, _get_text_factory(fail={"TFF-LF_VIX_NET_POSITION"}))
    assert len(store.points("cycle:tff-lf_treas_net_position")) == 2
    assert store.doc("tff") is not None
