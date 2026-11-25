import numpy as np
import pandas as pd
import pytest

from app import commands as cmd
from app.analytics import sentiment as s
from app.reports.digest import format_mood


def series(values, start="2025-01-01"):
    return pd.Series(np.asarray(values, dtype=float), index=pd.bdate_range(start, periods=len(values)))


def us_inputs(trend: float, n=400):
    """Synthetic US market. trend > 0: steady rally into the last day; < 0: sell-off."""
    base = np.linspace(100, 100, n)
    tail = np.linspace(0, trend, 40)
    gspc = base.copy(); gspc[-40:] += tail
    vix = np.full(n, 20.0); vix[-40:] -= tail / 5        # rally -> VIX falls
    spy = gspc.copy()
    ief = np.full(n, 100.0)
    hyg = np.full(n, 100.0); hyg[-40:] += tail / 3
    noise = np.sin(np.arange(n)) * 0.01                    # avoid flat ties
    return {k: series(v + noise) for k, v in
            {"^GSPC": gspc, "^VIX": vix, "SPY": spy, "IEF": ief, "HYG": hyg}.items()}


def test_pct_rank_excludes_itself_and_needs_history():
    r = s.pct_rank(series(range(100)), window=50)
    assert r.iloc[-1] == 100          # highest of its window
    assert r.iloc[:49].isna().all() and not pd.isna(r.iloc[49])
    assert s.pct_rank(series(range(100, 0, -1)), window=50).iloc[-1] == 0


def test_rally_reads_greed_and_selloff_reads_fear():
    up = s.compute("US", us_inputs(+20))
    down = s.compute("US", us_inputs(-20))
    assert up.score > 75 and up.label == "Extreme greed"
    assert down.score < 25 and down.label == "Extreme fear"
    assert {c.key for c in up.components} == {"momentum", "volatility", "safe_haven", "junk_bonds"}
    assert "VIX elevated" in [c.reading for c in down.components]
    assert down.week_ago is not None


def test_idx_uses_rupiah_and_eido():
    n = 400
    jkse = series(7000 + np.sin(np.arange(n)) * 5)
    eido = series(np.r_[np.full(n - 30, 20.0), np.linspace(20, 26, 30)])  # Indonesia ETF rallies
    spy = series(np.full(n, 500.0) + np.sin(np.arange(n)) * 0.01)
    usdidr = series(np.r_[np.full(n - 30, 16000.0), np.linspace(16000, 18000, 30)])  # rupiah slides
    mood = s.compute("IDX", {"^JKSE": jkse, "EIDO": eido, "SPY": spy}, usdidr)
    by_key = {c.key: c for c in mood.components}
    assert by_key["foreign"].value > 90 and "strong" in by_key["foreign"].reading
    assert by_key["rupiah"].value < 10 and by_key["rupiah"].reading == "rupiah weakening"


def test_needs_three_inputs():
    only_index = {"^GSPC": us_inputs(5)["^GSPC"]}
    assert s.compute("US", only_index) is None
    assert s.compute("IDX", {}) is None


@pytest.mark.parametrize("score,label", [(0, "Extreme fear"), (30, "Fear"), (50, "Neutral"),
                                         (60, "Greed"), (90, "Extreme greed")])
def test_labels(score, label):
    assert s.label_for(score) == label


def test_neutral_inputs_are_not_called_fear_or_greed():
    c = s.Component("volatility", "x", 47, "IHSG swinging hard", "IHSG calm")
    assert c.reading == "neutral"


def test_format_mood():
    mood = s.compute("US", us_inputs(-20))
    line = format_mood(mood)
    assert line.startswith("😱 Mood <b>") and "Extreme fear" in line and "1w ago" in line


class StoredOnly:
    """Provider that returns nothing, so commands use what's in the DB."""
    def get_history(self, instruments, period):
        return {}


def test_refresh_and_sentiment_command(db):
    class Fake:
        def __init__(self):
            self.periods = []

        def get_history(self, instruments, period):
            self.periods.append(period)
            data = us_inputs(-20)
            frames = {}
            for i in instruments:
                c = data.get(i.symbol)
                if c is not None:
                    frames[i.symbol] = pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0.0})
            return frames

    fake = Fake()
    s.refresh(db, fake, "US")
    assert fake.periods == ["2y"]
    s.refresh(db, fake, "US")
    assert fake.periods == ["2y", "1mo"]   # history is there now
    out = cmd.cmd_sentiment(db, ["us"], provider=StoredOnly())
    assert "🇺🇸 US" in out and "Extreme fear" in out and "VIX vs 50-day avg" in out
    assert "Not enough data yet" in cmd.cmd_sentiment(db, ["idx"], provider=StoredOnly())
    assert "Usage" in cmd.cmd_sentiment(db, ["eu"])
