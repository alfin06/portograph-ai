"""Screens: rules such as  "roe > 15 and pb < 2 and rsi < 40"  over price + fundamental metrics.

Rules are parsed with Python's ast module but only a small allow-list of node types is
accepted (comparisons, and/or, numbers, known field names), so a rule can never run code.
A comparison involving a missing value (e.g. a bank has no debt-to-equity) is False."""
import ast
import operator
from dataclasses import dataclass, field

from app.analytics.snapshot import Snapshot

# Every field a rule can use, with a one-line description for /screens help
FIELDS = {
    # price / technicals
    "close": "last close (trading currency)",
    "chg_1d": "1-day change %",
    "chg_5d": "5-day change %",
    "chg_1m": "1-month change %",
    "rsi": "RSI(14)",
    "vol_surge": "volume vs previous 5-day avg (x)",
    "from_52w_high": "% below 52-week high (0 = at the high, -20 = 20% below)",
    "holding": "1 if you own it, else 0",
    # fundamentals (percent fields in percent: 15 = 15%)
    "pe": "trailing P/E",
    "forward_pe": "forward P/E",
    "pb": "price / book",
    "roe": "return on equity %",
    "roa": "return on assets %",
    "der": "debt / equity (x)",
    "div_yield": "dividend yield %",
    "net_margin": "net profit margin %",
    "rev_growth": "revenue growth % (yoy, latest quarter)",
    "eps_growth": "earnings growth % (yoy, latest quarter)",
    "market_cap_usd_bn": "market cap, USD billions",
}

_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}


MAX_RULE_LEN = 300


class RuleError(ValueError):
    pass


@dataclass
class Rule:
    text: str
    tree: ast.Expression

    def matches(self, metrics: dict) -> bool:
        return bool(_eval(self.tree.body, metrics))


def parse_rule(text: str) -> Rule:
    src = " ".join(text.strip().lower().split())
    if not src:
        raise RuleError("Rule is empty.")
    if len(src) > MAX_RULE_LEN:   # also keeps ast.parse away from deeply nested input
        raise RuleError(f"Rule is too long (max {MAX_RULE_LEN} characters).")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise RuleError(f"Can't read rule: {text!r}. Example: roe > 15 and pb < 2") from None
    _validate(tree.body)
    return Rule(src, tree)


def _validate(node):
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _validate(v)
    elif isinstance(node, ast.Compare):
        if not all(type(op) in _CMP for op in node.ops):
            raise RuleError("Use only <, <=, >, >=, ==, != in comparisons.")
        for v in (node.left, *node.comparators):
            _validate_operand(v)
    else:
        raise RuleError("A rule is comparisons joined by 'and' / 'or', e.g. roe > 15 and pb < 2")


def _validate_operand(node):
    if isinstance(node, ast.Name):
        if node.id not in FIELDS:
            raise RuleError(f"Unknown field '{node.id}'. Fields: {', '.join(FIELDS)}")
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        pass
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        _validate_operand(node.operand)
    else:
        raise RuleError("Compare fields with numbers, e.g. pe < 15 or chg_1m > -10")


def _value(node, m):
    if isinstance(node, ast.Name):
        return m.get(node.id)
    if isinstance(node, ast.Constant):
        return node.value
    v = _value(node.operand, m)  # USub
    return None if v is None else -v


def _eval(node, m) -> bool:
    if isinstance(node, ast.BoolOp):
        results = (_eval(v, m) for v in node.values)
        return all(results) if isinstance(node.op, ast.And) else any(results)
    left = _value(node.left, m)
    for op, comp in zip(node.ops, node.comparators):
        right = _value(comp, m)
        if left is None or right is None or not _CMP[type(op)](left, right):
            return False
        left = right
    return True


# ---------------------------------------------------------------------------
def metrics_for(snap: Snapshot, fundamentals: dict | None) -> dict:
    m = {
        "close": snap.close, "chg_1d": snap.chg_1d, "chg_5d": snap.chg_5d, "chg_1m": snap.chg_1m,
        "rsi": snap.rsi, "vol_surge": snap.vol_surge,
        "from_52w_high": (snap.close / snap.high_52w - 1) * 100 if snap.high_52w else None,
        "holding": 1 if snap.is_holding else 0,
    }
    f = fundamentals or {}
    for k in ("pe", "forward_pe", "pb", "roe", "roa", "der", "div_yield",
              "net_margin", "rev_growth", "eps_growth"):
        m[k] = f.get(k)
    cap = f.get("market_cap_usd")
    m["market_cap_usd_bn"] = None if cap is None else cap / 1e9
    return m


def collect_metrics(db, snaps: list[Snapshot]) -> dict[str, dict]:
    return {s.symbol: metrics_for(s, db.get_latest_fundamentals(s.symbol)) for s in snaps}


@dataclass
class ScreenResult:
    name: str
    rule: str
    hits: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    error: str | None = None


def run_screens(db, market: str, snaps: list[Snapshot], today: str,
                record: bool = True) -> list[ScreenResult]:
    """Evaluate every active screen for this market. With record=True, hits are stored
    and `new` lists symbols that were not matching on the previous run."""
    metrics = collect_metrics(db, snaps)
    results = []
    for sc in db.get_screens(market=market):
        res = ScreenResult(sc["name"], sc["rule"])
        try:
            rule = parse_rule(sc["rule"])
        except RuleError as e:
            res.error = str(e)
            results.append(res)
            continue
        res.hits = sorted(sym for sym, m in metrics.items() if rule.matches(m))
        if record:
            res.new = db.record_screen_hits(sc["name"], list(metrics), res.hits, today)
        results.append(res)
    return results
