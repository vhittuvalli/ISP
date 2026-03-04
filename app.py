from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Tuple

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_DEMO_KEY = "CG-TmL5g16xtNeeAgXt7Gqhn94G"

CACHE_TTL_HISTORY = 1800
CACHE_TTL_MARKETS = 300
MIN_SECONDS_BETWEEN_CG_CALLS = 2.0

_cache: Dict[str, Tuple[float, Any]] = {}
_last_cg_call_at = 0.0

app = Flask(__name__)
CORS(app)


def cache_get(key: str):
    hit = _cache.get(key)
    if not hit:
        return None
    exp, val = hit
    if time.time() > exp:
        _cache.pop(key, None)
        return None
    return val


def cache_set(key: str, val: Any, ttl: int):
    _cache[key] = (time.time() + ttl, val)


def _sleep_if_needed():
    global _last_cg_call_at
    now = time.time()
    wait = (_last_cg_call_at + MIN_SECONDS_BETWEEN_CG_CALLS) - now
    if wait > 0:
        time.sleep(wait)
    _last_cg_call_at = time.time()


def cg_get(path: str, params: Dict[str, Any] | None = None, ttl: int = 180):
    params = params or {}
    cache_key = f"{path}:{tuple(sorted(params.items()))}"

    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    _sleep_if_needed()

    url = f"{COINGECKO_BASE}{path}"
    headers = {
        "User-Agent": "CryptoSlippageAnalyzer/1.0",
        "Accept": "application/json",
        "x-cg-demo-api-key": COINGECKO_DEMO_KEY,
    }

    r = requests.get(url, params=params, headers=headers, timeout=20)

    if r.status_code == 429:
        raise RuntimeError("RATE_LIMIT_429")

    if r.status_code != 200:
        raise RuntimeError(f"CoinGecko error {r.status_code}: {r.text[:250]}")

    data = r.json()
    cache_set(cache_key, data, ttl)
    return data


def downsample_prices(prices: List[Tuple[int, float]], max_points: int):
    n = len(prices)
    if n <= max_points or max_points < 3:
        return prices
    step = (n - 1) / (max_points - 1)
    out = []
    for i in range(max_points):
        idx = round(i * step)
        out.append(prices[idx])
    out[0] = prices[0]
    out[-1] = prices[-1]
    return out


def compute_volatility_pct(prices: List[Tuple[int, float]]):
    if len(prices) < 3:
        return 0.0
    rets = []
    prev = prices[0][1]
    for _, p in prices[1:]:
        if prev > 0 and p > 0:
            rets.append(math.log(p / prev))
        prev = p
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return float(math.sqrt(var) * 100.0)


def liquidity_proxy_usd(market: Dict[str, Any]):
    vol = float(market.get("total_volume") or 0.0)
    cap = float(market.get("market_cap") or 0.0)
    liq = 0.85 * vol + 0.15 * (cap * 0.01)
    return max(liq, 1.0)


def market_impact_slippage_pct(order_usd: float, liquidity_usd: float):
    ratio = max(order_usd / max(liquidity_usd, 1.0), 0.0)
    impact = (ratio ** 0.6) * 5.0
    return float(min(impact, 25.0))


def risk_score(order_usd: float, liquidity_usd: float, vol_pct: float):
    score = 0.0
    score += min(order_usd / max(liquidity_usd, 1.0), 1.0) * 60.0
    score += min(vol_pct / 4.0, 1.0) * 40.0

    if score >= 70:
        level, color = "High", "#ff3b30"
    elif score >= 40:
        level, color = "Moderate", "#ff9500"
    else:
        level, color = "Low", "#34c759"

    return round(score, 2), level, color


@app.post("/api/analyze")
def analyze():
    body = request.get_json(force=True) or {}
    coin_id = (body.get("coin_id") or "").strip()
    vs = (body.get("vs") or "usd").strip()
    days = int(body.get("history_days") or 7)
    order_usd = float(body.get("order_usd") or 0.0)
    expected_price = float(body.get("expected_price") or 0.0)
    slip_tol = float(body.get("slippage_tolerance_pct") or 0.5)
    max_points = int(body.get("max_points") or 140)
    max_points = max(40, min(max_points, 300))

    if not coin_id:
        return jsonify({"error": "coin_id required"}), 400
    if order_usd <= 0:
        return jsonify({"error": "order_usd must be > 0"}), 400
    if days < 1 or days > 365:
        return jsonify({"error": "history_days must be 1..365"}), 400

    try:
        markets = cg_get(
            "/coins/markets",
            params={"vs_currency": vs, "ids": coin_id, "per_page": 1, "page": 1, "sparkline": "false"},
            ttl=CACHE_TTL_MARKETS
        )

        if not markets:
            return jsonify({"error": "Coin not found"}), 400

        market = markets[0]
        live_price = float(market.get("current_price") or 0.0)
        liq = liquidity_proxy_usd(market)

        hist = cg_get(
            f"/coins/{coin_id}/market_chart",
            params={"vs_currency": vs, "days": days},
            ttl=CACHE_TTL_HISTORY
        )

        raw_prices = [(int(ts), float(p)) for ts, p in hist.get("prices", [])]

        # compute volatility from full dataset, not downsampling for this
        vol = compute_volatility_pct(raw_prices)

        # only downsample for chart display
        ds_prices = downsample_prices(raw_prices, max_points)

        if expected_price <= 0:
            expected_price = live_price

        impact_pct = market_impact_slippage_pct(order_usd, liq)
        simulated_actual = live_price * (1.0 + impact_pct / 100.0)
        slippage_pct = abs((simulated_actual - expected_price) / expected_price) * 100.0
        exceeds_tolerance = slippage_pct > slip_tol

        score, level, color = risk_score(order_usd, liq, vol)

        bars = []
        for m in [0.25, 0.5, 1.0, 2.0, 4.0]:
            s = round(order_usd * m, 2)
            bars.append({"order_usd": s, "impact_slippage_pct": round(market_impact_slippage_pct(s, liq), 4)})

        return jsonify({
            "live_price": round(live_price, 8),
            "simulated_actual_price": round(simulated_actual, 8),
            "slippage_pct": round(slippage_pct, 4),
            "volatility_pct": round(vol, 4),
            "liquidity_proxy_usd": round(liq, 2),
            "risk": {
                "score": score,
                "level": level,
                "color": color,
                "exceeds_tolerance": exceeds_tolerance
            },
            "price_history": [{"ts": ts, "price": p} for ts, p in ds_prices],
            "slippage_by_order_size": bars
        })

    except RuntimeError as e:
        if str(e) == "RATE_LIMIT_429":
            return jsonify({"error": "Rate limited"}), 429
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
