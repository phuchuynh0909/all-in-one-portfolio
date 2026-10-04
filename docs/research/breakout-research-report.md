Below is the full research report, cleaned up and organized for practical use.

# Volume-Based Breakout Watchlist Research Report

## 1. Executive summary

The strongest conclusion is that **volume is better used as a confirmation and conditioning variable than as a stand-alone breakout predictor**.

Academic research supports three broad ideas:

- volume contains information not fully captured by price,
- abnormal trading activity can be associated with subsequent returns,
- and volume interacts with momentum and technical structure.

But there is **no universal academic rule** that says something like “RVOL ≥ 1.5 means the breakout will succeed.”

That kind of threshold is mainly a practitioner heuristic and should be tested rather than treated as a law. Research on abnormal volume, price-volume interaction, and technical-pattern recognition supports the logic of combining price structure with volume, but not one fixed breakout recipe. 

For a practical watchlist, the most defensible architecture is:

**Compression / accumulation → preparation → breakout confirmation**

A stock becomes interesting before breakout when it has:

- a relatively tight 20–60 day base,
- contracting volatility,
- often contracting volume,
- price approaching resistance,
- constructive OBV / A-D / CMF,
- and adequate liquidity.

It becomes a breakout candidate only when price clears resistance with stronger participation, typically measured by RVOL or a similar normalized volume metric. Fidelity explicitly notes that rising OBV or A/D in a trading range can warn of an upside breakout, and positive CMF can help confirm a resistance break. 

A useful research default is:

| Feature | Starting rule |
|---|---:|
| Consolidation | 20–60 sessions |
| Base width | roughly ≤8–15% |
| Distance to resistance | preferably ≤3% |
| CMF(21) | >0 |
| OBV slope | positive |
| A/D slope | non-negative or positive |
| Volume during base | stable to contracting |
| Breakout buffer | max(0.5% of resistance, 0.25×ATR14) |
| Breakout RVOL20 | ≥1.5 |
| Conservative confirmation | second close or successful retest |

The most important design insight is:

> **Do not reward huge volume too early.**

A good pre-breakout setup may have **quiet volume but constructive accumulation**. Large relative volume should become more important when price is actually testing or clearing resistance.

---

# 2. First principle: what “buy volume vs sell volume” really means

Every executed trade has both a buyer and a seller.

So raw “number of shares bought” versus “number of shares sold” is not meaningful: they are equal by definition.

What traders usually mean is:

- **buyer-initiated / aggressive volume**: trades crossing the ask,
- **seller-initiated / aggressive volume**: trades crossing the bid.

Define:

\[
V^{buy}_t
=
\sum_i q_i I(\text{buyer-initiated})
\]

\[
V^{sell}_t
=
\sum_i q_i I(\text{seller-initiated})
\]

Then:

\[
DeltaVolume_t
=
V^{buy}_t - V^{sell}_t
\]

and:

\[
BuyRatio_t
=
\frac{V^{buy}_t}
{V^{buy}_t+V^{sell}_t}
\]

A research threshold such as:

\[
BuyRatio>55\%
\]

can be tested, but only if the underlying trade-direction classification is reliable.

The Lee–Ready framework is foundational here: trade direction must be inferred from trade and quote data and classification is imperfect. 

This matters because many charting platforms show something called “up volume/down volume” that is **not true aggressor-side buy/sell volume**.

TradingView states that parts of its Volume Profile implementation classify lower-timeframe volume based on bar direction rather than literal order initiation. 

For Vietnam, FiinTrade exposes categories such as Buy Up, Sell Down, and Unidentified, which are more relevant for this purpose. 

---

# 3. Why volume may help

## 3.1 Information-quality channel

Blume, Easley, and O'Hara developed a theoretical framework in which volume reveals information about the quality or precision of signals beyond what price alone communicates.

That supports the intuition that:

\[
\text{price breakout + strong volume}
\]

may contain more information than:

\[
\text{price breakout alone}
\] 


## 3.2 Attention / participation channel

Gervais, Kaniel, and Mingelgrin found that stocks with unusually high trading activity relative to their own history subsequently behaved differently from normal-volume stocks over roughly the following month.

The important point is that the signal was **abnormal volume relative to the stock's own history**, not raw absolute volume. 

That directly supports using RVOL or a percentile-based abnormal-volume measure.

## 3.3 Momentum interaction

Lee and Swaminathan found that past turnover helped explain the magnitude and persistence of momentum.

But the relationship is not simply:

> more volume = more bullish.

High-volume momentum stocks can behave differently over longer horizons and may also be closer to reversal or exhaustion phases. 

## 3.4 Technical pattern information

Lo, Mamaysky, and Wang systematically formalized chart-pattern detection and found that some technical patterns contained incremental return information.

The key lesson for your project is not that a specific “cup and handle” or “box breakout” always works.

It is that:

> chart structures should be defined quantitatively rather than visually whenever possible. 


---

# 4. Operationally defining a consolidation

Instead of relying only on visual interpretation, define a base mathematically.

For a lookback \(L\):

\[
Resistance_t
=
\max_{i=t-L}^{t-1} High_i
\]

\[
Support_t
=
\min_{i=t-L}^{t-1} Low_i
\]

Then:

\[
BaseWidth_t
=
\frac{Resistance_t-Support_t}
{(Resistance_t+Support_t)/2}
\]

A useful starting range:

\[
L = 20\text{ to }60
\]

and:

\[
BaseWidth \le 8\%\text{ to }15\%
\]

These are research parameters, not universal truths.

For volatile stocks, a better alternative is to normalize base width by ATR.

For example:

\[
NormalizedWidth
=
\frac{Resistance-Support}
{ATR_{14}}
\]

This lets you compare different volatility regimes more fairly.

---

# 5. Defining breakout properly

A breakout should not be defined as merely:

\[
High_t > Resistance
\]

because an intraday wick can briefly cross resistance and reverse.

A stronger definition is:

\[
Close_t > Resistance + Buffer_t
\]

with:

\[
Buffer_t
=
\max(
0.005 \times Resistance,
0.25 \times ATR_{14}
)
\]

This is not an academically validated optimum; it is a sensible research starting point.

The objective is to reduce false positives from trivial price penetration.

---

# 6. Relative volume

Define:

\[
RVOL_{N,t}
=
\frac{Volume_t}
{SMA_N(Volume)_t}
\]

For example:

\[
RVOL20 = 1.5
\]

means today's volume is 150% of the recent 20-day average.

Why use RVOL?

Because raw volume is not comparable across stocks.

10 million shares may be enormous for one company and routine for another.

The abnormal-volume literature supports this kind of relative framing. 

A useful test grid is:

- 1.25×
- 1.5×
- 2.0×

rather than assuming 1.5 is optimal.

IBD's practitioner framework has historically emphasized breakout volume roughly 40–50% above average, while Fidelity and Schwab more generally emphasize expanding / above-average volume. 

---

# 7. Best volume indicators for the waitlist problem

Different indicators answer different questions.

## OBV

\[
OBV_t=
\begin{cases}
OBV_{t-1}+V_t & C_t>C_{t-1}\\
OBV_{t-1}-V_t & C_t<C_{t-1}\\
OBV_{t-1} & C_t=C_{t-1}
\end{cases}
\]

Best use:

**detect accumulation when price itself is still range-bound.**

Fidelity explicitly notes that rising OBV during a trading range may indicate accumulation and warn of an upward breakout. 

Weakness:

All daily volume is assigned to one direction depending on whether the close rose or fell.

So OBV is a crude proxy.

---

## Accumulation / Distribution line

Money Flow Multiplier:

\[
MFM=
\frac{2C-H-L}{H-L}
\]

Then:

\[
ADL_t
=
ADL_{t-1} + MFM_tV_t
\]

This asks where the close occurred inside the day's range.

A strong close near the top of the bar contributes positively.

Fidelity notes that a rising A/D line in a trading range can indicate accumulation. 

Weakness:

It can misread gap behavior because it evaluates the close relative to the current bar, not relative to the previous close.

---

## Chaikin Money Flow

\[
CMF_n
=
\frac{\sum_{i=1}^n MFM_iV_i}
{\sum_{i=1}^n V_i}
\]

Typical setting:

\[
n=20\text{ or }21
\]

Useful interpretation:

- CMF > 0: buying pressure proxy
- CMF < 0: selling pressure proxy

Fidelity uses 21 periods as a standard setting and notes positive CMF can help confirm an upside resistance break. 

Suggested tests:

- CMF > 0
- CMF > 0.05
- CMF > 0.10

---

## Volume Oscillator

\[
VO
=
\frac{SMA_{short}(V)-SMA_{long}(V)}
{SMA_{long}(V)} \times 100
\]

Possible settings:

- 5 / 20
- 10 / 30
- 12 / 26

Useful question:

> Is volume participation accelerating?

Weakness:

Rising volume does not itself say whether the flow is bullish or bearish. 


---

## VWAP and Anchored VWAP

\[
VWAP
=
\frac{\sum Price_i \times Volume_i}
{\sum Volume_i}
\]

For swing breakout analysis, Anchored VWAP is often more useful than regular session VWAP.

Potential anchors:

- base low
- major gap
- earnings date
- previous breakout
- capitulation low

If price remains above an AVWAP anchored to the base low, that can suggest that the average participant since the low is profitable.

TradingView supports time- and event-based anchoring approaches. 

---

## Volume Profile

Volume Profile answers:

> At which prices did trading activity concentrate?

Useful concepts:

- POC — Point of Control
- VAH — Value Area High
- VAL — Value Area Low
- HVN — High Volume Node
- LVN — Low Volume Node

A practical application:

If resistance sits immediately beneath a large overhead HVN, price may face significant supply.

If it breaks through a thin LVN above the base, price can sometimes travel faster because there was relatively little historical acceptance there.

TradingView notes that value area calculations often use about 70% of total profile volume. 

---

# 8. The ideal volume pattern before breakout

The strongest candidate is often **not** the one with constant huge volume.

A constructive sequence is:

### Phase A — early base

- heavier activity after previous move
- wide ranges
- both buyers and sellers active

### Phase B — consolidation matures

- price range narrows
- ATR contracts
- total volume declines
- selling pressure becomes less aggressive
- OBV / A-D stop deteriorating

### Phase C — accumulation becomes visible

- CMF turns positive
- OBV begins rising despite sideways price
- A/D rises
- down days occur on lighter volume
- up days begin showing slightly stronger participation

### Phase D — resistance test

- price pushes into top 10–25% of base
- volume begins to rise modestly
- failed pullbacks become shallower
- price holds above short-term VWAP / AVWAP

### Phase E — breakout

- close above resistance
- RVOL expands sharply
- ideally CMF remains positive
- OBV/A-D print fresh local highs
- optional intraday BuyRatio becomes strongly positive

Fidelity's educational material also describes volume contraction during common consolidation patterns followed by increasing volume at breakout. 

---

# 9. What genuine accumulation may look like

A useful accumulation signature is:

\[
Price \approx flat
\]

while:

\[
OBV \uparrow
\]

and/or:

\[
ADL \uparrow
\]

and:

\[
CMF > 0
\]

This implies price is not making obvious progress yet, but volume is occurring under relatively constructive conditions.

A stronger version is:

- price makes equal highs,
- price makes higher lows,
- OBV makes higher highs,
- volume on down days contracts,
- volume on tests of support dries up.

The logic is:

**supply is being absorbed without price needing to fall materially.**

---

# 10. How to distinguish accumulation from distribution or churn

This is where many simple volume systems fail.

High volume by itself is ambiguous.

## Constructive behavior

Look for:

- price closes near daily highs
- down-volume decreases over time
- pullbacks occur on lighter volume
- support tests recover quickly
- CMF remains positive
- OBV/A-D trend upward
- price holds above AVWAP
- resistance retests become tighter
- breakout attempts produce higher lows rather than breakdowns

## Potential distribution

Warning signs:

- very high volume but repeated closes in lower half of candle
- large upper shadows at resistance
- OBV diverges downward
- CMF deteriorates
- A/D falls while price remains near highs
- large volume spikes fail to produce price progress
- repeated breakout failures

## Churn

A common churn pattern is:

- extremely high volume
- narrow real body
- little net price progress
- near major resistance

That can mean buyers and sellers are both highly active.

Churn is not automatically bearish, but it should not be interpreted as clean accumulation.

---

# 11. Volume dry-up

An often useful pre-breakout feature is:

\[
\frac{SMA_{10}(Volume)}
{SMA_{50}(Volume)}
<1
\]

A stronger test might be:

\[
0.7 \le
\frac{Vol10}{Vol50}
\le 0.9
\]

Why?

A mature base often sees participation contract because fewer holders are willing to sell at current prices.

Then, when new demand appears at resistance, volume expands sharply.

This creates a useful contrast:

\[
LowVolumeBase
\rightarrow
HighVolumeBreakout
\]

This is probably more informative than simply demanding high volume throughout the entire base.

---

# 12. Screening architecture

I recommend a state-based architecture.

## State 0 — no valid base

Reject if:

- too volatile
- insufficient liquidity
- no bounded price structure
- large recent breakdown

## State 1 — valid consolidation

Requirements:

- 20–60 day range
- controlled base width
- decreasing ATR or stable ATR
- no repeated high-volume breakdowns

## State 2 — WAITLIST

Add to watchlist when:

- price is close to resistance
- CMF positive
- OBV / A-D constructive
- base volume stable to contracting
- liquidity sufficient

## State 3 — BREAKOUT CANDIDATE

Trigger when:

\[
Close > Resistance + Buffer
\]

and:

\[
RVOL20 \ge threshold
\]

## State 4 — CONFIRMED

Optional conservative confirmation:

- second daily close above resistance
- or retest succeeds
- or first-hour order flow confirms demand

## State 5 — INVALIDATED

Remove if:

- price closes materially back into base
- support breaks
- CMF / A-D deterioration becomes significant
- breakout fails on large selling volume

---

# 13. Proposed Preparation Score

A practical 100-point prototype:

\[
Score=
20I(DistanceToResistance\le3\%)
\]

\[
+15I(BaseWidth\le12\%)
\]

\[
+15I(CMF21>0)
\]

\[
+15I(OBVSlope>0)
\]

\[
+10I(ADLSlope>0)
\]

\[
+10I(Vol10/Vol50<0.9)
\]

\[
+15I(LiquidityPass)
\]

Then:

- <50: reject
- 50–69: monitor
- ≥70: waitlist candidate

These cutoffs are engineering defaults, not validated optima.

A more continuous scoring system is even better.

For example:

\[
RankScore=
0.20ProximityScore
+0.15CompressionScore
+0.15OBVScore
+0.15CMFScore
+0.10ADScore
+0.10VolumeDryUpScore
+0.15LiquidityScore
\]

This produces smoother rankings rather than binary rules.

---

# 14. Separate breakout score

Once the breakout happens, use a different score.

For example:

\[
TriggerScore=
0.35BreakStrength
+
0.30RVOLScore
+
0.15CMFScore
+
0.10OBVConfirmation
+
0.10OrderFlow
\]

Why separate the two scores?

Because the best pre-breakout candidate may have **low volume**, while the best breakout session should show **high volume**.

That is a major conceptual distinction.

---

# 15. Practical watchlist rules

A strong first-pass implementation:

| Variable | Core criterion |
|---|---|
| Base duration | 20–60 days |
| Base width | 8–15% |
| Price location | within 3% of resistance |
| ATR trend | contracting or stable |
| Vol10/Vol50 | <0.9 preferred |
| CMF21 | >0 |
| OBV | positive 10–20d slope |
| A/D | positive or flat-positive slope |
| Liquidity | sufficient for intended order size |
| Overhead supply | no obvious major HVN immediately above |
| News/event risk | flagged separately |

---

# 16. Breakout trigger

A balanced starting rule:

\[
Breakout =
[Close > Resistance + Buffer]
\]

AND

\[
[RVOL20 \ge 1.5]
\]

AND

\[
[CMF21 > 0]
\]

Optional strict variant:

\[
OBV_t >
\max(OBV_{t-20:t-1})
\]

Optional intraday confirmation:

\[
BuyRatio_{first60m}>55\%
\]

Fidelity's CMF and volume material supports the logic of positive money flow plus expanding participation when resistance breaks. 

---

# 17. False-breakout controls

The best filters are usually structural.

| Problem | Suggested control |
|---|---|
| Intraday wick | require daily close above resistance |
| Weak participation | RVOL ≥1.25–1.5 |
| News spike | require next-day hold or retest |
| Excess extension | reject if >~2 ATR above breakout |
| Illiquid stock | median traded-value filter |
| Broad-market weakness | add index / sector regime filter |
| Auction/block distortion | inspect intraday volume distribution |
| Poor buy/sell data | remove order-flow condition |

High volume can represent:

- real demand,
- short covering,
- forced liquidations,
- index rebalancing,
- block trades,
- earnings repricing,
- distribution,
- capitulation.

Therefore:

\[
HighVolume \neq AutomaticallyBullish
\]

The academic evidence supports information content, not a universally positive interpretation. 

---

# 18. Breakout quality versus entry quality

These are different concepts.

A stock can have a spectacular breakout:

- +10%
- RVOL 4×

but still offer a poor entry because:

- it is far above resistance,
- stop distance is large,
- gap risk is high,
- risk/reward is unattractive.

Therefore the model should calculate:

\[
ExtensionATR
=
\frac{Close-Resistance}
{ATR_{14}}
\]

A useful rule to test:

\[
ExtensionATR < 1.5\text{ to }2.0
\]

for fresh entries.

Stocks beyond that can remain **confirmed breakout** but not necessarily **entry eligible**.

---

# 19. Risk management

Position sizing should be based on risk per trade.

\[
RiskBudget
=
AccountEquity \times RiskPercent
\]

Then:

\[
Shares
=
\frac{RiskBudget}
{Entry-Stop}
\]

Example:

Account:

\[
1,000,000,000 \text{ VND}
\]

Risk per trade:

\[
0.5\%
\]

So:

\[
RiskBudget=5,000,000
\]

Entry:

\[
50,000
\]

Stop:

\[
48,000
\]

Then:

\[
Shares
=
5,000,000/2,000
=
2,500
\]

before adjusting for:

- lot size
- liquidity
- gap risk
- portfolio concentration

CME similarly frames position sizing around risk amount and stop distance. 

Useful stop variants to test:

\[
Stop_A = Resistance - 1ATR
\]

or:

\[
Stop_B
=
RetestLow - 0.25ATR
\]

or below the last higher low inside the base.

Also note that stop orders do not guarantee execution at the stop price. The SEC explains that stop-market orders may fill materially away from the trigger in fast markets, while stop-limit orders may fail to execute. 

---

# 20. Backtesting design

The biggest methodological risk is not picking the wrong indicator.

It is overfitting.

White's Reality Check and later work on the Probability of Backtest Overfitting and Deflated Sharpe Ratio all address the danger of searching many models and then reporting only the best-looking one. 

A defensible backtest should include:

- point-in-time universe membership
- delisted stocks where possible
- corporate-action-adjusted data
- no future information
- realistic execution
- commissions / fees / slippage
- liquidity constraints
- chronological train / validation / test splits

---

# 21. Signal timing

For daily data:

- build indicators using completed bar \(t\)
- generate signal at close \(t\)
- execute at \(t+1\) open or a pre-defined next-session condition

Avoid calculating the signal using the close and then assuming you bought at that same close unless you explicitly model a valid market-on-close workflow.

---

# 22. Success definition

A classification approach:

\[
Success =
\begin{cases}
1 & +2R \text{ occurs before } -1R \\
0 & otherwise
\end{cases}
\]

within:

- 10 sessions
- 20 sessions
- 40 sessions

Then calculate:

\[
Precision=
\frac{TP}{TP+FP}
\]

\[
Recall=
\frac{TP}{TP+FN}
\]

Also report:

- hit rate
- average return
- median return
- max drawdown
- Sharpe
- profit factor
- average winner
- average loser
- expected value
- turnover
- exposure

A high hit rate alone is meaningless if losses are much larger than gains.

---

# 23. Recommended parameter grid

Rather than optimizing hundreds of values, test a small economically sensible grid.

| Parameter | Values |
|---|---|
| Base length | 20 / 30 / 40 / 60 |
| Base width | 8 / 10 / 12 / 15% |
| Distance to resistance | 1 / 2 / 3 / 5% |
| RVOL breakout | 1.25 / 1.5 / 2.0 |
| CMF | >0 / >0.05 / >0.10 |
| Volume dry-up | Vol10/Vol50 <0.7 / 0.8 / 0.9 |
| Confirmation | close / next-day hold / retest |
| Holding horizon | 10 / 20 / 40 days |
| Stop | structural / 1 ATR / 1.5 ATR |

Then evaluate out-of-sample stability.

Do not simply pick the highest Sharpe in sample.

---

# 24. Scanner pseudocode

```python
for stock in point_in_time_universe(date=t):

    bars = history(
        stock,
        end=t,
        lookback=120,
        adjusted=True
    )

    if not liquidity_pass(bars):
        continue

    atr14 = ATR(bars, 14)
    cmf21 = CMF(bars, 21)
    obv = OBV(bars)
    adl = accumulation_distribution(bars)

    base = bars[-41:-1]

    resistance = base.high.max()
    support = base.low.min()

    midpoint = (resistance + support) / 2

    base_width = (
        resistance - support
    ) / midpoint

    distance_to_resistance = (
        resistance - bars.close[-1]
    ) / resistance

    vol10 = bars.volume[-11:-1].mean()
    vol20 = bars.volume[-21:-1].mean()
    vol50 = bars.volume[-51:-1].mean()

    obv_slope = linear_slope(obv[-20:])
    adl_slope = linear_slope(adl[-20:])

    waitlist = (
        base_width <= 0.12
        and 0 <= distance_to_resistance <= 0.03
        and cmf21[-1] > 0
        and obv_slope > 0
        and adl_slope >= 0
        and vol10 / vol50 <= 0.90
    )

    breakout_buffer = max(
        0.005 * resistance,
        0.25 * atr14[-1]
    )

    rvol20 = (
        bars.volume[-1] / vol20
    )

    breakout = (
        bars.close[-1]
        > resistance + breakout_buffer
        and rvol20 >= 1.50
        and cmf21[-1] > 0
    )

    if waitlist:
        add_to_watchlist(stock)

    if breakout:
        mark_breakout_candidate(stock)
```

In production, use a state machine rather than requiring `waitlist=True` on the breakout day because price will already be above resistance.

---

# 25. Better state-machine logic

```text
STATE 0
No valid base

        ↓

STATE 1
Valid consolidation

        ↓

STATE 2
WAITLIST
Near resistance
CMF positive
OBV/A-D constructive
Volume contracting

        ↓

STATE 3
BREAKOUT
Close > resistance + buffer
RVOL ≥ threshold

        ↓

STATE 4
CONFIRMED
Second close or retest holds

        ↓

STATE 5
MANAGE / INVALIDATE
```

This is cleaner than trying to make one formula handle all stages.

---

# 26. Historical examples

These are illustrative examples, not proof of profitability.

Choosing famous winners after the fact creates selection bias.

## NVIDIA — May 2023

The historical series shows Nvidia moving sharply higher on May 25, 2023, with approximately 1.544 billion shares traded and volume several times the recent average. 

The lesson is not simply “high volume = buy.”

The lesson is:

- breakout participation was enormous,
- but the stock also became very extended,
- creating significant gap and stop-distance risk.

So **breakout quality was high, but entry quality was more debatable**.

---

## Meta Platforms — February 2024

META moved from roughly 391 on February 1 to about 470 on February 2, while reported volume rose from roughly 29.7 million to about 84.7 million shares. 

Again:

- participation clearly confirmed the repricing,
- but a rule requiring entry close to the breakout line would likely classify the stock as overextended.

---

## Alphabet / Google — April 2015

This example is closer to the ideal workflow.

GOOG traded below roughly 27.05 for much of April.

On April 23, it closed just above that area on roughly 2.45× relative volume, then followed through the next session with continued high participation. 

This resembles:

**WAITLIST → BREAKOUT → FOLLOW-THROUGH**

and is exactly the kind of pattern a conservative breakout engine should study.

---

## FPT — Vietnam, November 2024

An FPTS technical report described FPT consolidating in approximately the 130,000–140,000 VND range, followed by a breakout accompanied by liquidity rising to the highest level in more than three months. 

FPTS also discussed the former resistance region around 140,000 as a possible retest/support area afterward. 

That sequence is highly relevant:

\[
Consolidation
\rightarrow
VolumeExpansion
\rightarrow
Breakout
\rightarrow
Retest
\]

---

# 27. Vietnam-specific implementation

For Vietnam, a practical architecture is:

```text
Official exchange / vendor data
        ↓
Daily OHLCV history
        ↓
Liquidity + corporate-action QA
        ↓
Base detector
        ↓
Preparation Score
        ↓
Watchlist
        ↓
Intraday monitoring
        ↓
Buy Up / Sell Down / RVOL confirmation
        ↓
Alert
```

Potential sources include:

- HOSE official trading statistics 
- DNSE OpenAPI for programmatic data / automation 
- FiinTrade for Time & Sales and Buy Up / Sell Down validation 

For efficiency:

- weekly data → market regime
- daily data → base detection and ranking
- 5–15 minute data → only for top watchlist names

You do **not** need full intraday data for every stock all the time.

---

# 28. Recommended Vietnam watchlist specification

A strong first implementation for HOSE/HNX:

### Hard filters

- median 20-day traded value above a minimum liquidity threshold
- no suspended / stale-price names
- base duration 20–60 sessions
- base width ≤12%
- close ≤3% below resistance
- ATR20 / price declining or stable
- CMF21 >0
- OBV20 slope >0

### Bonus features

- A/D slope >0
- Vol10 / Vol50 <0.9
- price above AVWAP anchored at base low
- repeated support tests on low volume
- sector relative strength positive
- weekly chart not directly under major resistance

### Breakout trigger

\[
Close > R + max(0.5\%R,0.25ATR)
\]

AND:

\[
RVOL20\ge1.5
\]

AND:

\[
CMF21>0
\]

Optional intraday:

\[
BuyRatio>55\%
\]

---

# 29. Suggested output table

Your scanner should produce something like:

| Ticker | Prep Score | Base Days | Width | Dist. to R | Vol10/50 | CMF21 | OBV slope | RVOL | State |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAA | 86 | 38 | 8.4% | 1.2% | 0.74 | +0.11 | strong + | 0.91 | WAITLIST |
| BBB | 79 | 31 | 10.2% | 2.1% | 0.81 | +0.07 | + | 1.05 | WAITLIST |
| CCC | 92 | 44 | 7.9% | -0.4% | 0.88 | +0.13 | new high | 1.84 | BREAKOUT |
| DDD | 54 | 23 | 11.7% | 1.9% | 1.31 | -0.03 | flat | 1.12 | MONITOR |
| EEE | 31 | 51 | 9.0% | 2.7% | 0.69 | -0.08 | negative | 0.77 | REJECT |

The ranking should help you focus manually on the top 10–30 names rather than scan hundreds of charts.

---

# 30. Features I would prioritize

If building the first version, I would start with only these:

### Price structure

1. Base length
2. Base width
3. Distance to resistance
4. ATR contraction

### Volume / accumulation

5. Vol10 / Vol50
6. RVOL20
7. CMF21
8. OBV slope

### Liquidity

9. median traded value

### Breakout

10. close above resistance + ATR buffer

This is enough to build a useful first scanner.

Only after validating this should you add:

- A/D
- AVWAP
- Volume Profile
- sector strength
- buy/sell delta
- intraday microstructure

---

# 31. What I would not do

Avoid these common mistakes:

- using green-candle volume as “buy volume”
- demanding huge volume throughout the base
- chasing every RVOL spike
- scanning low-liquidity penny stocks
- treating breakout volume as bullish regardless of candle structure
- ignoring gap extension
- using today's stock universe to backtest the past
- optimizing dozens of parameters on one historical sample
- assuming volume on earnings days behaves like ordinary technical volume
- using same-day closing information and assuming same-close execution

---

# 32. Research conclusions

The evidence supports this broader framework:

\[
\boxed{
TightBase
+
NearResistance
+
ConstructiveAccumulation
+
VolumeDryUp
}
\]

followed by:

\[
\boxed{
PriceBreakout
+
RVOLExpansion
+
MoneyFlowConfirmation
+
HoldOrRetest
}
\]

Academic research supports the idea that volume contains information and interacts with momentum and technical patterns. 

Practitioner evidence supports expanding volume as an important breakout-confirmation variable. 

But the exact combination:

> 40-day base + CMF > 0 + OBV rising + RVOL20 >1.5

should be treated as a **hypothesis to backtest**, not a proven universal system.

The biggest edge is likely not finding one magical indicator. It is combining:

**structure + compression + accumulation + normalized volume + confirmation + risk control.**

For your specific goal—finding stocks **before** they break out—the most important variables are probably:

1. distance to resistance,
2. base tightness,
3. volatility contraction,
4. volume dry-up,
5. OBV / A-D divergence,
6. positive CMF,
7. liquidity.

Then, once price attacks resistance, shift emphasis toward:

1. RVOL,
2. breakout strength,
3. order-flow imbalance,
4. follow-through,
5. retest behavior.

That separation between **Preparation Score** and **Trigger Score** is the core design I would use for the system.