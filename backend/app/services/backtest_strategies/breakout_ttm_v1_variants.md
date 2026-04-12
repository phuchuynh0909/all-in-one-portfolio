# Breakout TTM: V1, V1b, and V1c

All three variants share the **same numeric parameters** (BB, Keltner, Donchian oscillator, ATR exit stack, KAMA flat filter, `low_stop_lookback`). They differ only in **extra entry filters** and **exit rules**.

## Shared core (V1 logic)

- **Squeeze state:** not inside squeeze and not in the classic squeeze “off” band (`no_sqz`).
- **Momentum:** TTM oscillator (`LINEARREG` of the Donchian midpoint histogram) is **positive** (`ttms > 0`).
- **KAMA gate:** KAMA slope is **flat** (small % change over `kama_slope_win` bars).
- **Stops / risk:** initial stop from rolling minimum low (`low_stop_lookback`); **ATR trailing** exit when price crosses below the trail.

Multi-feature **MS regime** (GMM-style) is computed for **charting** on V1, V1b, and V1c; only V1b historically used regime in the entry line—**current code** does **not** require `regime == 1` on V1b (see below).

---

## V1 — `BreakoutTTMV1StrategyBT`

| Aspect | Behavior |
|--------|----------|
| **Entry** | `no_sqz & (ttms > 0) & flat` |
| **Extra filters** | None |
| **Exit** | Lowest-low stop (`close < low_sl[prev]`), then ATR trailing cross |

Use when you want the **baseline** optimized TTM + KAMA strategy with no regime overlay.

---

## V1b — `BreakoutTTMV1bStrategyBT`

| Aspect | Behavior |
|--------|----------|
| **Entry** | Same as V1 **plus** trend confirmation: `(close > ATR trailing) \| (close > KAMA)` |
| **Extra filters** | Requires price **above** the trailing stop **or** **above** KAMA at the signal bar (avoids entries while price is under the trail / KAMA). |
| **MS regime** | Computed and plotted; **not** part of `buy_signal` in the current implementation. |
| **Exit** | Same as V1 (no SMF-driven exit). |

Use when you want V1 entries only when **price confirms** strength vs. the trail or KAMA.

---

## V1c — `BreakoutTTMV1cStrategyBT`

| Aspect | Behavior |
|--------|----------|
| **Entry** | V1 core **and** **SMF bull regime**: `last_signal == +1` (Smart Money Flow cloud). |
| **Extra filters** | **SMF** must say bull; ignores the V1b-style `(above_trail \| above_kama)` condition. |
| **Exit** | **SMF `switch_down`** closes the position immediately (regime flip), **then** lowest-low stop, **then** ATR trailing. |

Use when you want the same TTM stack but **entries only in SMF bull regime** and **forced exit** when SMF turns bearish.

---

## Quick comparison

| | V1 | V1b | V1c |
|---|----|-----|-----|
| Base TTM + KAMA flat | Yes | Yes | Yes |
| Above ATR trail or KAMA | No | Yes | No |
| SMF bull required | No | No | Yes |
| Exit on SMF switch down | No | No | Yes |
| MS regime in entry | No | No (chart only) | No (chart only) |

---

## Parameter source

TTM/BB/KC/KAMA/exit defaults are aligned with the **V1** optimization note in code (e.g. Return ~417.87%, Sortino ~1.053). V1b and V1c reuse those same class attributes.
