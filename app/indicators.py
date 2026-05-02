import pandas as pd, numpy as np, ta
from app.models import Candle


def candles_to_df(candles):
    df = pd.DataFrame([c.to_dict() for c in candles])
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("time")


def calc_support_resistance(df, lookback=20):
    """Calculate support and resistance levels using pivot points."""
    highs = df["high"].rolling(lookback, center=True).max()
    lows = df["low"].rolling(lookback, center=True).min()
    levels = []
    current_price = df["close"].iloc[-1]
    # Get recent pivots
    for i in range(lookback, len(df) - lookback):
        if df["high"].iloc[i] == highs.iloc[i]:
            levels.append({"type": "resistance", "price": round(df["high"].iloc[i], 2)})
        if df["low"].iloc[i] == lows.iloc[i]:
            levels.append({"type": "support", "price": round(df["low"].iloc[i], 2)})
    # Filter to nearby levels
    nearby = [l for l in levels if abs(l["price"] - current_price) / current_price < 0.05]
    # Deduplicate
    seen = set()
    unique = []
    for l in nearby:
        key = round(l["price"], 0)
        if key not in seen:
            seen.add(key)
            unique.append(l)
    supports = sorted([l for l in unique if l["type"] == "support"],
                       key=lambda x: x["price"], reverse=True)[:3]
    resistances = sorted([l for l in unique if l["type"] == "resistance"],
                          key=lambda x: x["price"])[:3]
    return {"supports": supports, "resistances": resistances}


def calc_all(df):
    r = {}
    # RSI
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], 14).rsi()
    r["rsi"] = {"value": round(df["rsi"].iloc[-1], 2)}

    # MACD
    m = ta.trend.MACD(df["close"])
    df["macd"] = m.macd()
    df["macd_sig"] = m.macd_signal()
    df["macd_hist"] = m.macd_diff()

    def xover(a, b):
        if len(a) < 2:
            return "none"
        pd2 = a.iloc[-2] - b.iloc[-2]
        cd = a.iloc[-1] - b.iloc[-1]
        if pd2 <= 0 and cd > 0:
            return "golden_cross"
        if pd2 >= 0 and cd < 0:
            return "death_cross"
        return "none"

    r["macd"] = {
        "macd": round(df["macd"].iloc[-1], 4),
        "signal": round(df["macd_sig"].iloc[-1], 4),
        "histogram": round(df["macd_hist"].iloc[-1], 4),
        "crossover": xover(df["macd"], df["macd_sig"])
    }

    # EMA
    for p in [9, 21, 50, 200]:
        df[f"ema{p}"] = ta.trend.EMAIndicator(df["close"], p).ema_indicator()
    r["ema"] = {
        "ema_9": round(df["ema9"].iloc[-1], 2),
        "ema_21": round(df["ema21"].iloc[-1], 2),
        "ema_50": round(df["ema50"].iloc[-1], 2),
        "ema_200": round(df["ema200"].iloc[-1], 2),
        "trend": ("bullish" if df["ema9"].iloc[-1] > df["ema21"].iloc[-1] > df["ema50"].iloc[-1]
                  else "bearish")
    }

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(df["close"], 20, 2)
    df["bbu"] = bb.bollinger_hband()
    df["bbm"] = bb.bollinger_mavg()
    df["bbl"] = bb.bollinger_lband()
    pp = ((df["close"].iloc[-1] - df["bbl"].iloc[-1]) /
          (df["bbu"].iloc[-1] - df["bbl"].iloc[-1])
          if df["bbu"].iloc[-1] != df["bbl"].iloc[-1] else 0.5)
    r["bollinger"] = {
        "upper": round(df["bbu"].iloc[-1], 2),
        "middle": round(df["bbm"].iloc[-1], 2),
        "lower": round(df["bbl"].iloc[-1], 2),
        "price_position": round(pp, 2)
    }

    # ATR
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"], 14
    ).average_true_range()
    r["atr"] = {"value": round(df["atr"].iloc[-1], 2)}

    # Stochastic
    s = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"])
    df["sk"] = s.stoch()
    df["sd"] = s.stoch_signal()
    r["stochastic"] = {"k": round(df["sk"].iloc[-1], 2), "d": round(df["sd"].iloc[-1], 2)}

    # Volume analysis
    df["vsma"] = df["volume"].rolling(20).mean()
    vr = (df["volume"].iloc[-1] / df["vsma"].iloc[-1]
          if df["vsma"].iloc[-1] > 0 else 1)
    volume_spike = vr >= 2.0
    r["volume"] = {
        "ratio": round(vr, 2),
        "spike": volume_spike,
        "current": round(df["volume"].iloc[-1], 2),
        "average": round(df["vsma"].iloc[-1], 2)
    }

    # Support/Resistance
    try:
        sr = calc_support_resistance(df)
        r["support_resistance"] = sr
    except Exception:
        r["support_resistance"] = {"supports": [], "resistances": []}

    # Scoring
    score = 0
    reasons = []
    ri = r["rsi"]["value"]
    if ri < 30:
        score += 30
        reasons.append(f"RSI oversold ({ri})")
    elif ri > 70:
        score -= 30
        reasons.append(f"RSI overbought ({ri})")

    mc = r["macd"]
    if mc["crossover"] == "golden_cross":
        score += 25
        reasons.append("MACD golden cross")
    elif mc["crossover"] == "death_cross":
        score -= 25
        reasons.append("MACD death cross")
    score += 10 if mc["histogram"] > 0 else -10

    if r["ema"]["trend"] == "bullish":
        score += 20
        reasons.append("EMA bullish alignment")
    else:
        score -= 20
        reasons.append("EMA bearish alignment")

    if pp < 0.1:
        score += 15
        reasons.append("Near Bollinger lower band")
    elif pp > 0.9:
        score -= 15
        reasons.append("Near Bollinger upper band")

    stoch = r["stochastic"]
    if stoch["k"] < 20 and stoch["d"] < 20:
        score += 15
        reasons.append("Stochastic oversold")
    elif stoch["k"] > 80 and stoch["d"] > 80:
        score -= 15
        reasons.append("Stochastic overbought")

    if volume_spike and score > 0:
        score += 15
        reasons.append(f"Volume spike ({vr:.1f}x)")
    elif volume_spike and score < 0:
        score -= 15
        reasons.append(f"Volume spike downside ({vr:.1f}x)")

    score = max(-100, min(100, score))
    direction = "buy" if score > 15 else "sell" if score < -15 else "neutral"
    r["summary"] = {
        "score": score,
        "direction": direction,
        "label": "Long" if direction == "buy" else "Short" if direction == "sell" else "Hold",
        "confidence": min(100, abs(score)),
        "reasons": reasons
    }

    # EMA series for chart overlay
    r["ema_series"] = {
        "ema9": df["ema9"].dropna().round(2).tolist()[-100:],
        "ema21": df["ema21"].dropna().round(2).tolist()[-100:],
        "bb_upper": df["bbu"].dropna().round(2).tolist()[-100:],
        "bb_lower": df["bbl"].dropna().round(2).tolist()[-100:],
        "bb_middle": df["bbm"].dropna().round(2).tolist()[-100:],
    }

    return r
