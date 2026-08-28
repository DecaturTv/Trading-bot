"""Promote a tournament winner into the live configuration.

Writes two places, mirroring how the live loops read their settings:
  * decision_engine/scoring.py — DEFAULT_WEIGHTS (equities) or FOREX_WEIGHTS
    (forex), the dicts dashboard/context.py hands to WeightedFactorModel.
  * .env — the confidence-threshold / exit-rule / sizing knobs, which
    config.settings.Settings reads at startup.

Always prints a diff first. Nothing is written without --yes.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import date
from pathlib import Path

from .strategies import Strategy, by_name

REPO_ROOT = Path(__file__).resolve().parent.parent
SCORING_PATH = REPO_ROOT / "decision_engine" / "scoring.py"
ENV_PATH = REPO_ROOT / ".env"
LATEST_RESULT = Path(__file__).parent / "results" / "latest.json"

_FACTOR_ORDER = ("momentum", "trend", "macd", "unusual_volume", "gap", "candlestick", "congress")

# .env keys each market's knobs map to. target_delta/dte are options-chain
# selection knobs on the live entry loop; the rest mirror Settings fields.
_EQUITIES_ENV = {
    "CONFIDENCE_THRESHOLD": lambda s: s.equities.confidence_threshold,
    "OPTION_TARGET_DELTA": lambda s: s.equities.target_delta,
    "OPTION_TARGET_DTE": lambda s: s.equities.target_dte,
    "STOP_LOSS_PCT": lambda s: s.equities.stop_loss_pct,
    "PROFIT_TARGET_DOLLARS": lambda s: s.equities.profit_target_dollars,
    "TRAILING_STOP_PCT": lambda s: s.equities.trailing_stop_pct,
    "KELLY_FRACTION": lambda s: s.equities.kelly_fraction,
}
_FOREX_ENV = {
    "FOREX_CONFIDENCE_THRESHOLD": lambda s: s.forex.confidence_threshold,
    "FOREX_RISK_PCT_PER_TRADE": lambda s: s.forex.risk_pct_per_trade,
    "FOREX_STOP_ATR_MULTIPLIER": lambda s: s.forex.stop_atr_multiplier,
    "FOREX_TAKE_PROFIT_R_MULTIPLE": lambda s: s.forex.take_profit_r_multiple,
}


def winner_from_latest(market: str, path: Path = LATEST_RESULT) -> str:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run `python -m tournament run` first")
    payload = json.loads(path.read_text())
    board = payload.get("boards", {}).get(market)
    if not board:
        raise KeyError(f"no {market!r} board in {path}; run the tournament for that market first")
    return board["winner"]


def _full_weights(strategy: Strategy) -> dict[str, float]:
    return {name: float(strategy.weights.get(name, 0.0)) for name in _FACTOR_ORDER}


def _render_weight_block(var_name: str, weights: dict[str, float], strategy_name: str) -> str:
    lines = [f"{var_name} = {{"]
    lines += [f'    "{name}": {weights[name]},' for name in _FACTOR_ORDER]
    lines.append("}")
    stamp = date.today().isoformat()
    lines.append(f"# ^ promoted from tournament winner {strategy_name!r} on {stamp}")
    return "\n".join(lines)


def _replace_weight_block(source: str, var_name: str, new_block: str) -> str:
    # Match `VAR = {` ... up to the closing `}` at column 0, plus any single
    # trailing "# ^ promoted ..." provenance line from a prior promote. A
    # negated char class (not `.` + DOTALL) spans newlines within the dict
    # body without letting the optional provenance tail run to EOF.
    pattern = re.compile(
        rf"^{re.escape(var_name)} = \{{[^}}]*?^\}}"
        rf"(?:\n# \^ promoted from tournament winner [^\n]*)?",
        re.MULTILINE,
    )
    if not pattern.search(source):
        raise ValueError(f"could not locate `{var_name} = {{...}}` block in {SCORING_PATH}")
    return pattern.sub(lambda _: new_block, source, count=1)


def _apply_env(source: str, updates: dict[str, float]) -> str:
    lines = source.splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            lines[i] = f"{key}={_env_value(updates[key])}"
            seen.add(key)
    trailing = [f"{k}={_env_value(v)}" for k, v in updates.items() if k not in seen]
    if trailing:
        lines.append("")
        lines.append("# promoted from tournament winner")
        lines.extend(trailing)
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _env_value(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _diff(path: Path, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=str(path), tofile=f"{path} (promoted)",
        )
    )


def promote(market: str, strategy_name: str, *, apply: bool) -> str:
    if market not in ("equities", "forex"):
        raise ValueError("market must be 'equities' or 'forex'")
    strategy = by_name(strategy_name)
    var_name = "DEFAULT_WEIGHTS" if market == "equities" else "FOREX_WEIGHTS"
    env_map = _EQUITIES_ENV if market == "equities" else _FOREX_ENV

    scoring_old = SCORING_PATH.read_text()
    scoring_new = _replace_weight_block(
        scoring_old, var_name, _render_weight_block(var_name, _full_weights(strategy), strategy.name)
    )
    env_old = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    env_updates = {k: fn(strategy) for k, fn in env_map.items()}
    env_new = _apply_env(env_old, env_updates)

    report = [
        f"Promoting {strategy.name!r} -> live {market} config",
        "",
        _diff(SCORING_PATH, scoring_old, scoring_new) or "(scoring.py unchanged)",
        _diff(ENV_PATH, env_old, env_new) or "(.env unchanged)",
    ]
    if strategy.min_coverage != 0.5:
        report.append(
            f"NOTE: {strategy.name} uses min_available_weight_fraction={strategy.min_coverage}. "
            f"dashboard/context.py builds WeightedFactorModel() with the 0.5 default — "
            f"change that call to WeightedFactorModel(min_available_weight_fraction={strategy.min_coverage}) "
            f"by hand to match tournament behavior."
        )

    if apply:
        SCORING_PATH.write_text(scoring_new)
        ENV_PATH.write_text(env_new)
        report.append("\nWRITTEN. Restart the trading-dashboard service for it to take effect.")
    else:
        report.append("\nDry run — pass --yes to write these changes.")
    return "\n".join(report)
