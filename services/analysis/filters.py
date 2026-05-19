from typing import Tuple

_RSI_MIN = 30.0
_RSI_MAX = 70.0
_SELL_RSI_MIN = 60.0  # RSI must be elevated to consider selling


def passes_sell_filter(snapshot: dict) -> Tuple[bool, str]:
    """
    Stage 1 filter for held positions — identifies sell setups.

    A sell is worth examining when the position shows weakness:
    - RSI >= 60 (overbought territory), OR
    - Price has dropped below SMA20 (trend breakdown), OR
    - SMA20 is falling (momentum reversal)

    Any one of these conditions passes — we send it to Claude for
    the final call rather than trying to encode all sell logic here.

    Returns (True, "") if the snapshot shows at least one sell signal.
    Returns (False, reason) if the position looks healthy (no sell pressure).
    """
    try:
        rsi = float(snapshot["rsi"])
        price = float(snapshot["price"])
        sma20 = float(snapshot["sma20"])
        sma20_prev = float(snapshot["sma20_prev"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"Missing or invalid indicator field: {exc}"

    if rsi >= _SELL_RSI_MIN:
        return True, ""
    if price < sma20:
        return True, ""
    if sma20 <= sma20_prev:
        return True, ""

    return False, (
        f"No sell signal: RSI={rsi:.1f} < {_SELL_RSI_MIN}, "
        f"price={price:.2f} >= SMA20={sma20:.2f}, SMA20 rising"
    )


def passes_technical_filter(snapshot: dict) -> Tuple[bool, str]:
    """
    Apply the Stage 1 technical filter to a market snapshot.

    Rules:
    1. RSI strictly between 30 and 70 (avoids overbought/oversold extremes)
    2. Price strictly above SMA50 (uptrend confirmation)
    3. Price above SMA20 AND SMA20 > SMA20_prev (momentum: approximates
       "price crossed SMA20 in the last 3 bars" using only snapshot data)

    Returns (True, "") if all rules pass.
    Returns (False, reason) describing the first failing rule.
    """
    try:
        rsi = float(snapshot["rsi"])
        price = float(snapshot["price"])
        sma20 = float(snapshot["sma20"])
        sma50 = float(snapshot["sma50"])
        sma20_prev = float(snapshot["sma20_prev"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"Missing or invalid indicator field: {exc}"

    if not (_RSI_MIN < rsi < _RSI_MAX):
        return False, f"RSI {rsi:.1f} outside exclusive range ({_RSI_MIN}, {_RSI_MAX})"

    if price <= sma50:
        return False, f"Price {price:.2f} not above SMA50 {sma50:.2f}"

    if not (price > sma20 and sma20 > sma20_prev):
        return False, (
            f"SMA20 crossover not confirmed: "
            f"price={price:.2f} sma20={sma20:.2f} sma20_prev={sma20_prev:.2f}"
        )

    return True, ""
