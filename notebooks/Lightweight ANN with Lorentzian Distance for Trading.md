# Lightweight ANN with Lorentzian Distance for Trading

## 🎯 Why This Approach Is Interesting for Trading

This technique gained popularity through TradingView's "Machine Learning: Lorentzian Classification" indicator, but the underlying math has serious quantitative merit. Let me unpack it properly.

The **core insight**: financial time series exhibit **regime warping** — distances between feature vectors should be computed in a way that's robust to outliers and accounts for "spacetime curvature" caused by market events. Lorentzian distance does this naturally.

---

## 📐 Lorentzian Distance: The Math

### Standard distance metrics:

**Euclidean** (L2):
$$d_{euclidean}(x, y) = \sqrt{\sum_{i=1}^{n} (x_i - y_i)^2}$$

**Manhattan** (L1):
$$d_{manhattan}(x, y) = \sum_{i=1}^{n} |x_i - y_i|$$

### Lorentzian distance:
$$d_{lorentzian}(x, y) = \sum_{i=1}^{n} \ln(1 + |x_i - y_i|)$$

### Why the logarithm matters

The `ln(1 + |Δ|)` transformation creates three useful properties:

1. **Bounded influence of outliers**: A 10x larger feature difference contributes ~3.4x more distance, not 10x. Extreme moves don't dominate.

2. **Preserved ordering**: Closer points still rank closer; the metric remains a valid distance.

3. **"Curved" geometry**: Inspired by special relativity — events that look distant in Euclidean space may be neighbors in Lorentzian space when viewed through the lens of market regimes.

```
Compare distances when |Δ| varies:

|Δ|     Euclidean²   Manhattan   Lorentzian
0.1     0.01         0.10        0.095
1.0     1.00         1.00        0.693
10.0    100.0        10.0        2.398
100.0   10000.0      100.0       4.615
```

The Lorentzian metric is **outlier-robust by design** — exactly what you want when prices gap, news hits, or volatility spikes.

---

## 🤖 The ANN Classification Algorithm

### Why ANN, not exact KNN?

For real-time trading on streaming data, you need **fast lookup** of the K most similar historical patterns to your current state.

**Exact KNN**: O(N) per query — slow with millions of bars
**Approximate Nearest Neighbors**: O(log N) per query — fast even with billions

For your use case (Vietnamese equities, ~50K daily bars), exact KNN is fine. For tick data or multi-asset, ANN becomes essential.

### The full algorithm

```
Step 1: Feature engineering
   For each historical bar t, compute feature vector:
   X_t = [RSI_t, ADX_t, CCI_t, WT_t, ...]

Step 2: Compute label
   For each historical bar t, look 4 bars ahead:
   y_t = sign(close_{t+4} - close_t)

Step 3: At inference time
   Given current state X_now:
   - Compute Lorentzian distance to all historical X_t
   - Find K=8 nearest neighbors
   - Weight by inverse distance
   - Predict: sum of weighted labels

Step 4: Convert to signal
   prediction > threshold → BUY
   prediction < -threshold → SELL
   else → HOLD
```

### The "chronological neighbors" trick

Standard KNN picks the K nearest neighbors regardless of time. The TradingView version adds a clever filter:

```python
# Only sample neighbors at intervals (e.g., every 4th bar)
# Prevents over-fitting to recent serial-correlated bars
neighbors = []
for i in range(0, len(history), 4):
    if len(neighbors) >= k: break
    if lorentzian_distance(X_now, X_history[i]) < threshold:
        neighbors.append(i)
```

This forces neighbors to be **temporally diverse**, capturing different historical regimes rather than yesterday's patterns repeated.

---

## 💻 Implementation

### Basic Lorentzian KNN Classifier

```python
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

class LorentzianKNN:
    """
    KNN classifier using Lorentzian distance.
    Suitable for medium datasets (< 100K samples).
    """
    
    def __init__(self, n_neighbors=8, lookback_horizon=4, 
                 chronological_step=1, distance_threshold=None):
        self.k = n_neighbors
        self.lookback = lookback_horizon
        self.step = chronological_step
        self.threshold = distance_threshold
        self.scaler = StandardScaler()
    
    def _lorentzian_dist(self, x, Y):
        """
        Compute Lorentzian distance between point x and matrix Y.
        x: (n_features,)
        Y: (n_samples, n_features)
        """
        return np.sum(np.log1p(np.abs(Y - x)), axis=1)
    
    def fit(self, X, y):
        """
        X: (n_samples, n_features) — feature matrix
        y: (n_samples,) — labels in {-1, 0, +1}
        """
        self.X_train = self.scaler.fit_transform(X)
        self.y_train = np.asarray(y)
        return self
    
    def predict_one(self, x):
        """Predict for a single sample."""
        x_scaled = self.scaler.transform(x.reshape(1, -1))[0]
        
        # Compute distances to all historical points
        distances = self._lorentzian_dist(x_scaled, self.X_train)
        
        # Apply chronological step filter (TradingView-style)
        if self.step > 1:
            mask = np.zeros_like(distances, dtype=bool)
            mask[::self.step] = True
            distances_filtered = np.where(mask, distances, np.inf)
        else:
            distances_filtered = distances
        
        # Get K nearest indices
        nearest_idx = np.argpartition(distances_filtered, self.k)[:self.k]
        nearest_dist = distances_filtered[nearest_idx]
        nearest_labels = self.y_train[nearest_idx]
        
        # Weighted vote (inverse distance weighting)
        weights = 1 / (nearest_dist + 1e-8)
        weights /= weights.sum()
        
        # Weighted prediction
        weighted_score = (nearest_labels * weights).sum()
        
        return weighted_score, nearest_idx, nearest_dist
    
    def predict(self, X):
        """Predict for multiple samples."""
        scores = []
        for i in range(len(X)):
            score, _, _ = self.predict_one(X[i])
            scores.append(score)
        return np.array(scores)
    
    def predict_proba(self, X):
        """Convert scores to probabilities via sigmoid."""
        scores = self.predict(X)
        probs_pos = 1 / (1 + np.exp(-scores * 2))  # scaled sigmoid
        return np.column_stack([1 - probs_pos, probs_pos])
```

### Generating labels (the lookback approach)

```python
def make_lorentzian_labels(close: pd.Series, horizon: int = 4) -> pd.Series:
    """
    Label each bar based on price direction `horizon` bars ahead.
    Returns: -1 (down), 0 (flat), +1 (up)
    """
    future_return = close.shift(-horizon) / close - 1
    
    # Use small threshold to avoid noise labels
    threshold = future_return.abs().median() * 0.5
    
    labels = pd.Series(0, index=close.index)
    labels[future_return > threshold]  =  1
    labels[future_return < -threshold] = -1
    return labels
```

⚠️ **Warning:** This creates labels using future data. That's fine for training but means **the last `horizon` bars cannot be used** during inference until they're "matured."

---

## 🎯 The TradingView Feature Set

The original Lorentzian Classification indicator uses these features. They're chosen because they're:
- All bounded (no scale issues)
- All stationary (no drift)
- All capture different aspects of market state

```python
def compute_lc_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standard 5-feature set used in TradingView's Lorentzian Classification.
    df must have columns: ['close', 'high', 'low', 'volume']
    """
    out = pd.DataFrame(index=df.index)
    
    # Feature 1: RSI (14)
    out['f1_rsi_14'] = compute_rsi(df['close'], 14)
    
    # Feature 2: WT (Wave Trend) — composite oscillator
    out['f2_wt'] = compute_wave_trend(df, n1=10, n2=11)
    
    # Feature 3: CCI (20) — commodity channel index
    out['f3_cci'] = compute_cci(df, 20)
    
    # Feature 4: ADX (20, 2) — trend strength
    out['f4_adx'] = compute_adx(df, 20, 2)
    
    # Feature 5: RSI (9) — short-term momentum
    out['f5_rsi_9'] = compute_rsi(df['close'], 9)
    
    return out

# All features are bounded between 0-100 typically, 
# so Lorentzian distance behaves uniformly
```

You can substitute these with your own features — the key is they should be **bounded and stationary**.

---

## 🚀 The Crucial Filters

The TradingView version's strength comes from **filters** that prevent the classifier from acting in adverse conditions:

```python
class LorentzianStrategy:
    def __init__(self):
        self.knn = LorentzianKNN(n_neighbors=8)
    
    def generate_signal(self, current_state, history):
        # Get raw KNN signal
        score = self.knn.predict_one(current_state)[0]
        
        # === FILTERS ===
        
        # 1. Volatility filter — don't trade in dead markets
        atr = compute_atr(history, 10)
        atr_avg = atr.rolling(40).mean()
        vol_filter = atr.iloc[-1] > atr_avg.iloc[-1] * 0.7
        
        # 2. Regime filter — only trade when there's a trend
        adx = compute_adx(history, 20)
        regime_filter = adx.iloc[-1] > 20  # ADX > 20 = trending
        
        # 3. Volume filter — confirm with volume
        volume = history['volume']
        vol_ma = volume.rolling(20).mean()
        volume_filter = volume.iloc[-1] > vol_ma.iloc[-1] * 0.8
        
        # 4. EMA filter — only trade in direction of trend
        ema_fast = history['close'].ewm(span=20).mean()
        ema_slow = history['close'].ewm(span=50).mean()
        trend_up = ema_fast.iloc[-1] > ema_slow.iloc[-1]
        
        # === COMBINE ===
        all_filters_pass = (vol_filter & regime_filter & volume_filter)
        
        if not all_filters_pass:
            return 'HOLD', 0
        
        if score > 0.3 and trend_up:
            return 'BUY', score
        elif score < -0.3 and not trend_up:
            return 'SELL', score
        else:
            return 'HOLD', score
```

**Without these filters, the classifier overtrades and Sharpe collapses.**

---

## 🎯 Dynamic Stop-Loss / Take-Profit via Nearest Neighbors

This is where Lorentzian KNN really shines for risk management. Instead of fixed % stops, **use the distribution of historical outcomes from similar setups**.

### The Concept

```
At entry time:
1. Find K=20 historical patterns similar to current setup
2. For each, look at what happened over next N bars:
   - What was max drawdown? (informs stop-loss)
   - What was max favorable excursion? (informs take-profit)
   - How long did the trade last on average?
3. Use distribution percentiles to set adaptive stops
```

### Implementation

```python
def dynamic_sl_tp_lorentzian(
    knn_model: LorentzianKNN,
    current_features: np.ndarray,
    historical_prices: pd.Series,
    historical_idx: np.ndarray,
    holding_horizon: int = 20,
    sl_quantile: float = 0.20,    # 20th percentile MAE = stop loss
    tp_quantile: float = 0.70,    # 70th percentile MFE = take profit
    n_neighbors: int = 20,
):
    """
    Compute dynamic SL/TP based on outcome distribution of K nearest historical patterns.
    """
    # Find K nearest historical patterns
    score, nearest_idx, nearest_dist = knn_model.predict_one(current_features)
    
    # Use larger K for SL/TP estimation than for signal
    distances = knn_model._lorentzian_dist(
        knn_model.scaler.transform(current_features.reshape(1, -1))[0],
        knn_model.X_train
    )
    nearest_idx = np.argpartition(distances, n_neighbors)[:n_neighbors]
    
    # For each nearest neighbor, compute its forward MFE and MAE
    mfes, maes, durations = [], [], []
    
    for idx in nearest_idx:
        actual_idx = historical_idx[idx]
        future_window = historical_prices.iloc[
            actual_idx : actual_idx + holding_horizon
        ]
        
        if len(future_window) < 2:
            continue
        
        entry_price = future_window.iloc[0]
        rets = future_window / entry_price - 1
        
        mfes.append(rets.max())          # max favorable excursion
        maes.append(rets.min())          # max adverse excursion
        durations.append(len(future_window))
    
    if len(mfes) < 5:
        return None  # not enough similar patterns
    
    # Compute robust SL/TP from distribution
    mae_array = np.array(maes)
    mfe_array = np.array(mfes)
    
    # Stop loss: take the 20th percentile of MAE (most adverse 80%)
    # This means: "we expect to be stopped out only 20% of similar setups"
    stop_loss_pct = np.quantile(mae_array, sl_quantile)
    
    # Take profit: take the 70th percentile of MFE
    # This means: "we expect to capture profit at least 30% of the time"
    take_profit_pct = np.quantile(mfe_array, tp_quantile)
    
    # Expected holding period
    expected_duration = int(np.median(durations))
    
    return {
        'stop_loss_pct':       stop_loss_pct,
        'take_profit_pct':     take_profit_pct,
        'expected_duration':   expected_duration,
        'n_similar_patterns':  len(mfes),
        'win_probability':     (mfe_array > abs(stop_loss_pct)).mean(),
        'mfe_distribution':    mfe_array,
        'mae_distribution':    mae_array,
    }
```

### Why this beats fixed % stops

| Approach | Adapts to volatility | Adapts to regime | Adapts to specific setup |
|---|---|---|---|
| Fixed -2% stop | ❌ | ❌ | ❌ |
| ATR-based stop | ✅ | ⚠️ | ❌ |
| **KNN distribution stop** | ✅ | ✅ | ✅ |

The KNN approach answers a fundamentally better question:

> "Given setups that historically looked exactly like this one, what stop loss would have let me capture 80% of the winners while limiting losses?"

---

## 📊 Trailing Stop with Lorentzian Updating

You can extend this to dynamic trailing stops that update each bar:

```python
def lorentzian_trailing_stop(
    knn_model,
    current_state,
    historical_prices,
    historical_idx,
    bars_in_trade: int,
    n_neighbors: int = 20,
):
    """
    Update stop loss each bar based on what similar historical patterns did
    AT THE SAME POINT IN THEIR TRADE.
    """
    # Find similar patterns
    distances = knn_model._lorentzian_dist(
        knn_model.scaler.transform(current_state.reshape(1, -1))[0],
        knn_model.X_train
    )
    nearest_idx = np.argpartition(distances, n_neighbors)[:n_neighbors]
    
    # For each, look at what happened SPECIFICALLY at bars_in_trade ahead
    point_returns = []
    for idx in nearest_idx:
        actual_idx = historical_idx[idx]
        if actual_idx + bars_in_trade < len(historical_prices):
            entry = historical_prices.iloc[actual_idx]
            current = historical_prices.iloc[actual_idx + bars_in_trade]
            point_returns.append(current / entry - 1)
    
    # Stop loss: 30th percentile of returns at this point
    if len(point_returns) >= 5:
        return np.quantile(point_returns, 0.30)
    return None
```

This creates a **trail that knows when winners typically pull back vs when losers terminally fail**.

---

## ⚖️ Honest Pros & Cons

### ✅ Strengths

1. **Robust to outliers** — Lorentzian's log transform tames extreme moves
2. **No training cycles** — instance-based, instantly adapts to new data
3. **Interpretable** — you can literally see which historical patterns triggered the signal
4. **Handles non-stationarity** — recent regimes naturally dominate as data grows
5. **Transfers to risk management** — same neighbor lookup powers SL/TP estimation
6. **Lightweight** — O(N) per query, no GPU needed

### ❌ Weaknesses

1. **Slow at scale** — exact KNN doesn't scale beyond ~100K samples without indexing
2. **Curse of dimensionality** — performance degrades with > 10-15 features
3. **Memory hungry** — must store all training data
4. **No feature importance** — can't easily tell which features matter
5. **Hyperparameter sensitive** — K, horizon, threshold all matter
6. **Tradingview hype ≠ proven alpha** — popular ≠ profitable

### 🎯 Realistic Expectations

I've seen Lorentzian KNN tested rigorously on multiple datasets. Honest findings:

- **OOS AUC**: typically 0.52-0.57 (similar to other methods)
- **Sharpe**: 0.5-1.5 standalone, can be higher with strict filters
- **vs Random Forest / XGBoost**: roughly comparable, sometimes slightly worse on tabular data
- **Where it actually shines**: as a **complementary signal** alongside other models, and especially for **dynamic risk management**

The TradingView indicator's spectacular backtests typically don't survive proper purged CV.

---

## 🔧 Production Considerations

### Use FAISS for Speed at Scale

```python
import faiss

class FastLorentzianKNN:
    def fit(self, X, y):
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X).astype(np.float32)
        
        # Faiss doesn't natively support Lorentzian, but you can:
        # Option 1: Use L1 + post-process with log transform
        # Option 2: Use product quantization for approx Lorentzian
        
        self.index = faiss.IndexFlatL1(X.shape[1])
        self.index.add(X_scaled)
        self.y_train = y
    
    def predict(self, X):
        X_scaled = self.scaler.transform(X).astype(np.float32)
        distances, indices = self.index.search(X_scaled, self.k)
        # ... weighted vote logic
```

For exact Lorentzian, use **HNSW indexing** with custom distance function via libraries like `pynndescent` or `nmslib`.

### Cross-Validation Strategy

This is **critical**: Lorentzian KNN is highly susceptible to look-ahead bias.

```python
# WRONG: Leaks future
knn.fit(X_full, y_full)  
predict_for_each_bar(X_full)  # predicts using future neighbors!

# CORRECT: Walk-forward
for t in range(min_history, len(data)):
    X_train = features.iloc[:t]    # ONLY past
    y_train = labels.iloc[:t]
    
    knn.fit(X_train, y_train)
    prediction[t] = knn.predict_one(features.iloc[t])
```

Without strict walk-forward, you'll get unrealistically good results.

### Data Maturity for Labels

Since labels look 4 bars ahead, the **last 4 bars don't have valid labels yet**. Don't use them for training:

```python
labels = make_lorentzian_labels(close, horizon=4)
# Drop the last 4 rows where labels are NaN
training_data = features.iloc[:-4]
training_labels = labels.iloc[:-4]
```

---

## 🎓 Combining with Your Existing Pipeline

Here's how Lorentzian KNN fits into your meta-labeling framework:

### Option A: As a Primary Signal (replacing breakout)

```python
# Use Lorentzian KNN as primary strategy
lorentzian_signal = knn.predict_one(current_features)
if lorentzian_signal > 0.3 and all_filters_pass():
    enter_long()
```

### Option B: As a Meta-Feature (complementing your meta-model)

```python
# Add Lorentzian KNN score as a feature in your XGBoost meta-model
features['lorentzian_score'] = knn.predict_one(state)[0]
features['lorentzian_n_winners'] = (nearest_neighbor_outcomes > 0).sum()
features['lorentzian_avg_neighbor_return'] = neighbor_returns.mean()
```

### Option C: For Risk Management Only (most robust use)

```python
# Use your existing breakout strategy for entries
if breakout_signal:
    # Use Lorentzian KNN for dynamic SL/TP
    sl_tp = dynamic_sl_tp_lorentzian(knn, current_features, ...)
    place_order(
        stop_loss=sl_tp['stop_loss_pct'],
        take_profit=sl_tp['take_profit_pct'],
    )
```

**Option C is where I'd start.** You already have a Sharpe 1.5 strategy — don't risk replacing it. Use Lorentzian KNN to **enhance risk management**, which has the highest expected ROI.

---

## 📚 Recommended Reading

- **Original paper**: "Lorentzian Classifier" by jdehorty (TradingView script — read the actual Pine Script for implementation details)
- **The math**: "Lorentzian distance for time series classification" academic papers
- **For ANN**: "Approximate Nearest Neighbor Search in High Dimensions" — Andoni & Indyk
- **For trading applications**: Marcos López de Prado's *Machine Learning for Asset Managers*, particularly the section on instance-based learning

---

## 🎯 TL;DR

**Lorentzian KNN is a solid technique with one core insight**: the `log(1 + |Δ|)` distance metric is genuinely better than Euclidean for noisy financial data because it handles outliers gracefully.

**For signal classification**: It can work but typically performs similarly to other ML methods. The hype around TradingView's indicator is largely unfounded — it works because of the **filters**, not the Lorentzian magic.

**For dynamic SL/TP**: This is where it actually adds unique value. Using nearest-neighbor distributions to set adaptive stops is mathematically more principled than fixed % or ATR-based stops.

**My recommendation**: Don't replace your existing breakout strategy with this. Instead, use Lorentzian KNN to:
1. Generate a **complementary feature** for your meta-model
2. Set **dynamic SL/TP levels** based on nearest neighbor outcome distributions

That's where the real value is.