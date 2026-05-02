import json, re, time, logging, asyncio
from app.config import config
from app.models import Signal, Candle
from app.indicators import candles_to_df, calc_all
from app.exchange import exchange
from app.database import save_signal, load_strategy_params
import pandas as pd, ta

log = logging.getLogger("strategy")

SYS = """You are a crypto quantitative trading strategist. Output JSON:
{"direction":"buy/sell/neutral","confidence":0-100,"entry_price":price,"stop_loss":sl,"take_profit":tp,"reasoning":"analysis","risk_reward_ratio":rr}
Rules: confidence>60 for direction, rr>=1.5. Respond in Chinese."""


def robust_json_parse(text: str) -> dict | None:
    """Enhanced JSON parsing that handles markdown blocks, nested JSON, partial JSON, etc."""
    if not text:
        return None
    text = text.strip()
    # Remove markdown code fences
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        else:
            text = text[3:]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # FIX: Use brace counting to find complete JSON object (handles nested {})
    start = text.find('{')
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    # Try fixing common issues
    fixed = text.replace("'", '"').replace("None", "null").replace("True", "true").replace("False", "false")
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # Try extracting key-value pairs manually
    result = {}
    for key in ["direction", "confidence", "entry_price", "stop_loss", "take_profit", "reasoning",
                 "risk_reward_ratio"]:
        # Handle both string and numeric values, including nested strings
        pattern = rf'"{key}"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|([^,}}\s]+))'
        m = re.search(pattern, text)
        if m:
            val = m.group(1) if m.group(1) is not None else m.group(2)
            if key in ("confidence", "entry_price", "stop_loss", "take_profit", "risk_reward_ratio"):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
            result[key] = val
    return result if result else None


class LLM:
    @staticmethod
    async def call(mk, sys_msg, usr_msg):
        ai = config.ai
        if mk == "openrouter":
            if not ai.openrouter_key:
                raise ValueError("OPENROUTER_API_KEY missing")
            from openai import AsyncOpenAI
            c = AsyncOpenAI(api_key=ai.openrouter_key, base_url=ai.openrouter_base)
            r = await c.chat.completions.create(
                model=ai.openrouter_model, temperature=0.3, max_tokens=2000,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": sys_msg},
                          {"role": "user", "content": usr_msg}]
            )
            return r.choices[0].message.content
        elif mk == "gpt-4o":
            if not ai.openai_key:
                raise ValueError("OpenAI API key missing")
            from openai import AsyncOpenAI
            c = AsyncOpenAI(api_key=ai.openai_key)
            r = await c.chat.completions.create(
                model=ai.openai_model, temperature=0.3, max_tokens=2000,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": sys_msg},
                          {"role": "user", "content": usr_msg}]
            )
            return r.choices[0].message.content
        elif mk == "claude-3.5":
            if not ai.anthropic_key:
                raise ValueError("Anthropic API key missing")
            import anthropic
            c = anthropic.AsyncAnthropic(api_key=ai.anthropic_key)
            r = await c.messages.create(
                model=ai.anthropic_model, max_tokens=2000,
                system=sys_msg + " Respond in JSON format.",
                messages=[{"role": "user", "content": usr_msg}]
            )
            return r.content[0].text
        elif mk == "gemini-pro":
            if not ai.google_key:
                raise ValueError("Google API key missing")
            import google.generativeai as genai
            genai.configure(api_key=ai.google_key)
            m = genai.GenerativeModel(ai.google_model, system_instruction=sys_msg)
            r = await m.generate_content_async(usr_msg)
            return r.text
        return await LLM.call("openrouter", sys_msg, usr_msg)


class StrategyEngine:
    def __init__(self):
        self.active_model = config.ai.active_model
        self.params_map = {}
        self.last_signal = {}
        self.history = []

    async def get_params(self, symbol):
        if symbol in self.params_map:
            return self.params_map[symbol]
        saved = await load_strategy_params(symbol, config.exchange.default_timeframe)
        if saved and saved["params"]:
            self.params_map[symbol] = saved["params"]
            log.info("Loaded strategy params %s: %s", symbol, saved["params"])
        else:
            self.params_map[symbol] = {
                "rsi_oversold": 30, "rsi_overbought": 70,
                "atr_sl": 1.5, "atr_tp": 3.0
            }
        return self.params_map[symbol]

    def set_params(self, symbol, params):
        self.params_map[symbol] = params

    async def get_high_tf_trend(self, symbol):
        try:
            tf = config.exchange.higher_tf
            ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=100)
            if len(ohlcv) < 20:
                return "neutral"
            df = candles_to_df([
                Candle(int(c[0] // 1000), c[1], c[2], c[3], c[4], c[5])
                for c in ohlcv
            ])
            for p in [9, 21, 50]:
                df[f"ema{p}"] = ta.trend.EMAIndicator(df["close"], p).ema_indicator()
            ema9 = df["ema9"].iloc[-1]
            ema21 = df["ema21"].iloc[-1]
            ema50 = df["ema50"].iloc[-1]
            if ema9 > ema21 > ema50:
                return "bullish"
            if ema9 < ema21 < ema50:
                return "bearish"
            return "neutral"
        except Exception as e:
            log.error("Higher TF trend failed %s: %s", symbol, e)
            return "neutral"

    async def check_funding_rate(self, symbol):
        """Check if funding rate is too high to open positions."""
        try:
            rate = await exchange.fetch_funding_rate(symbol)
            if abs(rate) > config.risk.max_funding_rate:
                log.warning("High funding rate %s: %.4f%%", symbol, rate * 100)
                return {"ok": False, "rate": rate, "reason": f"Funding rate too high: {rate*100:.4f}%"}
            return {"ok": True, "rate": rate}
        except Exception:
            return {"ok": True, "rate": 0}

    async def generate_signal(self, symbol, tf, candles):
        if len(candles) < 50:
            return Signal("neutral", symbol, tf, 0, 0, 0, 0, "Insufficient data")

        last = self.last_signal.get(symbol, 0)
        if time.time() - last < config.strategy.signal_cooldown:
            return Signal("neutral", symbol, tf, 0, 0, 0, 0, "Cooldown")

        params = await self.get_params(symbol)
        df = await asyncio.to_thread(candles_to_df, candles)
        ind = await asyncio.to_thread(calc_all, df)
        price = df["close"].iloc[-1]
        atr = ind["atr"]["value"]
        tech_dir = ind["summary"]["direction"]

        # Multi-timeframe confirmation
        if config.exchange.use_mtf and config.strategy.higher_tf_required:
            ht_trend = await self.get_high_tf_trend(symbol)
            if tech_dir == "buy" and ht_trend == "bearish":
                return Signal("neutral", symbol, tf, 0, price, 0, 0,
                              "Higher TF bearish - rejected long")
            if tech_dir == "sell" and ht_trend == "bullish":
                return Signal("neutral", symbol, tf, 0, price, 0, 0,
                              "Higher TF bullish - rejected short")

        # Funding rate check
        fr_check = await self.check_funding_rate(symbol)
        if not fr_check["ok"]:
            return Signal("neutral", symbol, tf, 0, price, 0, 0, fr_check["reason"])

        # AI analysis
        usr = (f"Symbol:{symbol} TF:{tf} Price:{price}\n\n"
               f"Indicators:\n{json.dumps(ind, indent=2, ensure_ascii=False)}\n\n"
               f"Params:\n{json.dumps(params)}")

        ai_result = None
        try:
            raw = await asyncio.wait_for(
                LLM.call(self.active_model, SYS, usr), timeout=30)
            ai_result = robust_json_parse(raw)
        except Exception as ex:
            log.error("AI failed %s: %s", symbol, ex)

        tech_conf = abs(ind["summary"]["score"])
        final_dir = "neutral"
        confidence = 0.0
        reasoning = ""

        if ai_result:
            ai_dir = ai_result.get("direction", "neutral")
            ai_conf = float(ai_result.get("confidence", 0))
            if tech_dir != "neutral":
                if ai_dir == tech_dir:
                    confidence = (config.strategy.ai_weight * ai_conf +
                                  config.strategy.technical_weight * tech_conf)
                    final_dir = tech_dir
                    reasoning = (f"[AI+Tech] {ai_result.get('reasoning', '')} | "
                                 f"Tech: {', '.join(ind['summary']['reasons'])}")
                else:
                    if ai_conf > tech_conf and ai_conf >= config.strategy.min_confidence:
                        final_dir = ai_dir
                        confidence = ai_conf
                        reasoning = f"[AI-led] {ai_result.get('reasoning', '')}"
                    elif tech_conf >= config.strategy.min_confidence:
                        final_dir = tech_dir
                        confidence = tech_conf
                        reasoning = (f"[Tech-led] "
                                     f"{', '.join(ind['summary']['reasons'])}")
            else:
                if ai_conf >= config.strategy.min_confidence:
                    final_dir = ai_dir
                    confidence = ai_conf
                    reasoning = f"[AI-only] {ai_result.get('reasoning', '')}"
        elif tech_dir != "neutral" and tech_conf >= config.strategy.min_confidence:
            final_dir = tech_dir
            confidence = tech_conf
            reasoning = f"[Tech-only] {', '.join(ind['summary']['reasons'])}"

        if confidence < config.strategy.min_confidence:
            final_dir = "neutral"

        entry = price
        sl = 0
        tp = 0
        if final_dir != "neutral":
            sl = (entry - atr * params["atr_sl"] if final_dir == "buy"
                  else entry + atr * params["atr_sl"])
            tp = (entry + atr * params["atr_tp"] if final_dir == "buy"
                  else entry - atr * params["atr_tp"])

        signal = Signal(
            final_dir, symbol, tf, confidence,
            round(entry, 2), round(sl, 2), round(tp, 2), reasoning,
            {
                "rsi": ind["rsi"]["value"],
                "score": ind["summary"]["score"],
                "macd_hist": ind["macd"]["histogram"],
                "bb_pos": ind["bollinger"]["price_position"],
                "volume_ratio": ind["volume"]["ratio"],
                "volume_spike": ind["volume"]["spike"],
                "funding_rate": fr_check.get("rate", 0),
                "sr_levels": ind.get("support_resistance", {}),
                "risk_reward": round(abs(tp - entry) / max(abs(entry - sl), 0.01), 2) if sl and tp else 0,
                "volatility_regime": "high" if atr / price > 0.03 else "normal",
            }
        )
        self.last_signal[symbol] = time.time()
        self.history.append(signal)
        if len(self.history) > 100:
            self.history = self.history[-50:]
        asyncio.create_task(save_signal(signal))
        log.info("Signal %s %s %s conf=%.1f%% rr=%.2f",
                 symbol, signal.direction, tf, signal.confidence,
                 signal.indicators.get("risk_reward", 0))
        return signal

    async def chat(self, msg, model=None):
        try:
            return await asyncio.wait_for(
                LLM.call(model or self.active_model,
                         "You are AI Trader quantitative assistant. Be concise and professional. Reply in Chinese.",
                         msg),
                timeout=30
            )
        except Exception as e:
            return f"AI call failed: {e}"


strategy_engine = StrategyEngine()
