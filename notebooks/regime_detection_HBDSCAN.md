# HDBSCAN for Market Regime Detection

## 🎯 Why HDBSCAN Specifically for Markets

Market regime detection is fundamentally a **clustering problem**: identify periods where market behavior was *similar*, then label new observations against those historical clusters. HDBSCAN has unique properties that make it well-suited for this — but also some real pitfalls.

Let me give you the honest picture.

---

## 📊 HDBSCAN vs. The Alternatives

Before diving in, here's why HDBSCAN over other regime detection methods:

| Method | Best For | Limitation |
|---|---|---|
| **K-Means** | Known number of regimes, spherical clusters | Forces K, sensitive to outliers |
| **Hidden Markov Models** | Sequential regime transitions | Strong distributional assumptions |
| **GMM** | Soft cluster membership | Assumes Gaussian regimes |
| **DBSCAN** | Variable density, outliers | Single density threshold (ε) — bad for finance |
| **HDBSCAN** | Variable density, no K, robust | Slow at scale, parameter sensitivity |
| **Change Point Detection** | Single-asset structural breaks | Doesn't characterize regime *content* |

**HDBSCAN's killer features for finance:**

1. ✅ **No need to specify K** — finds natural clusters
2. ✅ **Handles variable density** — bull markets are dense, crashes are sparse
3. ✅ **Native outlier detection** — flash crashes get labeled as noise (cluster -1)
4. ✅ **Hierarchical structure** — reveals regime sub-types
5. ✅ **No spherical assumption** — clusters can be any shape

**HDBSCAN's weaknesses:**

1. ❌ **Time-blind by default** — needs careful feature engineering for temporal data
2. ❌ **Lookahead-prone** — easy to leak future info
3. ❌ **Slow on large datasets** — O(N²) memory in worst case
4. ❌ **Hard to validate** — clusters are unsupervised (no ground truth)
5. ❌ **Parameter sensitivity** — `min_cluster_size` matters a lot

---

## 🧠 The Core Concept

HDBSCAN works by:

1. Computing **mutual reachability distance** between points (distance corrected for local density)
2. Building a **minimum spanning tree** based on these distances
3. Constructing a hierarchy by progressively dropping edges
4. Selecting clusters that are **stable** across density levels

For markets, this maps beautifully:
- **Stable regimes** (long bull markets, accumulation phases) → large dense clusters
- **Transitional periods** → cluster boundaries
- **Anomalies** (crashes, flash events) → noise points (label -1)

---

## 🛠️ The Practical Pipeline

### Step 1: Feature Engineering (The Most Important Step)

Regime detection is only as good as your features. Wrong features → meaningless clusters.

**Tier 1: Essential features for regime detection**

```python
import pandas as pd
import numpy as np

def regime_features(close: pd.Series, high: pd.Series, low: pd.Series, 
                     volume: pd.Series, vnindex: pd.Series) -> pd.DataFrame:
    """
    Multi-dimensional features that characterize market regimes.
    All features are stationary (no raw prices/volumes).
    """
    f = pd.DataFrame(index=close.index)
    returns = close.pct_change()
    
    # ============================================
    # VOLATILITY REGIME (the dominant dimension)
    # ============================================
    f['realized_vol_20']  = returns.rolling(20).std() * np.sqrt(252)
    f['realized_vol_60']  = returns.rolling(60).std() * np.sqrt(252)
    f['vol_ratio_20_60']  = f['realized_vol_20'] / f['realized_vol_60']
    f['vol_of_vol']       = f['realized_vol_20'].rolling(20).std()
    
    # Tail risk
    f['skew_60']  = returns.rolling(60).skew()
    f['kurt_60']  = returns.rolling(60).kurt()
    f['downside_vol_60'] = returns[returns < 0].rolling(60).std() * np.sqrt(252)
    
    # ============================================
    # TREND REGIME
    # ============================================
    sma20  = close.rolling(20).mean()
    sma60  = close.rolling(60).mean()
    sma200 = close.rolling(200).mean()
    
    f['close_to_sma200']    = (close / sma200 - 1)
    f['sma_slope_60']       = sma60.pct_change(20)
    f['trend_strength']     = abs(close.pct_change(60))
    
    # ============================================
    # MARKET BREADTH (cross-sectional)
    # ============================================
    f['advances_minus_declines'] = ...
    f['pct_above_sma200']        = ...
    
    # ============================================
    # CORRELATION REGIME
    # ============================================
    vn_returns = vnindex.pct_change()
    f['corr_to_vnindex_60'] = returns.rolling(60).corr(vn_returns)
    
    # ============================================
    # VOLUME REGIME
    # ============================================
    f['volume_zscore_60']   = (volume - volume.rolling(60).mean()) / \
                              volume.rolling(60).std()
    f['dollar_vol_pctile']  = (volume * close).rolling(252).rank(pct=True)
    
    # ============================================
    # DRAWDOWN REGIME
    # ============================================
    f['drawdown_252']       = close / close.rolling(252).max() - 1
    f['runup_252']          = close / close.rolling(252).min() - 1
    
    return f.dropna()
```

**Why these specific features:**
- All **stationary** (won't drift over time)
- All **bounded or naturally scaled** (Lorentzian-friendly if you want to combine)
- Cover the **5 main regime dimensions**: vol, trend, breadth, correlation, drawdown
- All have **clear economic interpretation** (you can label clusters meaningfully)

### Step 2: Apply HDBSCAN with Proper Configuration

```python
import hdbscan
from sklearn.preprocessing import StandardScaler

def detect_regimes(features: pd.DataFrame, 
                    min_cluster_size_pct: float = 0.05,
                    min_samples: int = None) -> tuple:
    """
    Apply HDBSCAN to regime features.
    
    min_cluster_size_pct: minimum cluster size as % of data
                         (5% = a regime must last at least 5% of history)
    """
    # Critical: scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(features)
    
    # Set min_cluster_size based on data length
    n_samples = len(features)
    min_cluster_size = max(int(n_samples * min_cluster_size_pct), 30)
    
    if min_samples is None:
        min_samples = min_cluster_size // 2
    
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_method='eom',  # 'eom' or 'leaf'
        metric='euclidean',
        prediction_data=True,             # enables soft clustering on new data
    )
    
    labels = clusterer.fit_predict(X_scaled)
    
    return clusterer, labels, scaler
```

### Step 3: Characterize the Regimes

This is where you turn cluster labels into actionable regime descriptions:

```python
def characterize_regimes(features: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """
    Compute centroid statistics for each cluster.
    Translates clusters into human-readable regimes.
    """
    df = features.copy()
    df['cluster'] = labels
    
    # Cluster summary
    summary = df.groupby('cluster').agg({
        'realized_vol_20':  'mean',
        'vol_ratio_20_60':  'mean',
        'close_to_sma200':  'mean',
        'sma_slope_60':     'mean',
        'corr_to_vnindex_60': 'mean',
        'drawdown_252':     'mean',
        'skew_60':          'mean',
    }).round(3)
    
    # Add cluster size and frequency
    summary['n_periods'] = df.groupby('cluster').size()
    summary['pct_time']  = summary['n_periods'] / len(df)
    
    # Auto-label regimes based on characteristics
    def label_regime(row):
        vol = row['realized_vol_20']
        trend = row['sma_slope_60']
        dd = row['drawdown_252']
        
        if dd < -0.15:
            return 'CRISIS / BEAR'
        elif vol < 0.15 and trend > 0:
            return 'CALM BULL'
        elif vol > 0.30:
            return 'HIGH VOLATILITY'
        elif trend > 0.05:
            return 'TRENDING UP'
        elif trend < -0.05:
            return 'TRENDING DOWN'
        else:
            return 'CHOPPY / RANGE'
    
    summary['regime_label'] = summary.apply(label_regime, axis=1)
    return summary
```

### Step 4: Visualize the Regime Timeline

```python
import matplotlib.pyplot as plt
import seaborn as sns

def plot_regime_timeline(prices: pd.Series, labels: np.ndarray, 
                          dates: pd.DatetimeIndex):
    fig, ax = plt.subplots(figsize=(15, 5))
    
    # Plot price
    ax.plot(dates, prices, color='black', linewidth=0.5)
    
    # Color background by regime
    unique_labels = sorted(set(labels))
    cmap = sns.color_palette("husl", len(unique_labels))
    
    for label, color in zip(unique_labels, cmap):
        if label == -1:
            color = 'gray'  # outliers
        mask = labels == label
        for start, end in find_contiguous(mask):
            ax.axvspan(dates[start], dates[end], 
                       alpha=0.3, color=color, 
                       label=f'Regime {label}' if start == np.where(mask)[0][0] else '')
    
    ax.set_title('Market Regimes Over Time')
    ax.legend(loc='upper left', bbox_to_anchor=(1, 1))
    plt.tight_layout()
    plt.show()
```

---

## 🚨 The Critical Look-Ahead Bias Problem

This is where most regime detection projects fail catastrophically. Let me explain.

### The Problem

```python
# WRONG: Standard HDBSCAN application
features = compute_features(full_history)  # uses all historical data
clusterer.fit(features)                     # clusters based on ENTIRE history
labels = clusterer.fit_predict(features)    # labels at time t use info from t+1, t+2, ...
```

**Why this is wrong**: The clustering algorithm sees data from June 2024 when it's deciding what cluster January 2024 belongs to. You can't trade on this — at January 2024 you don't know June will happen.

### The Two Correct Approaches

**Approach A: Walk-Forward Re-clustering**

```python
def walk_forward_regime_detection(features: pd.DataFrame, 
                                    initial_train_size: int = 504,
                                    refit_frequency: int = 21):
    """
    Re-cluster periodically using only past data.
    """
    labels = pd.Series(index=features.index, dtype=int)
    
    for t in range(initial_train_size, len(features), refit_frequency):
        # Use ONLY past data
        train_data = features.iloc[:t]
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(train_data)
        
        # Cluster historical data
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=int(len(X_train_scaled) * 0.05),
            prediction_data=True,
        ).fit(X_train_scaled)
        
        # Predict for the next chunk
        end_t = min(t + refit_frequency, len(features))
        new_data = features.iloc[t:end_t]
        X_new_scaled = scaler.transform(new_data)
        
        # Use approximate_predict for new points
        new_labels, _ = hdbscan.approximate_predict(clusterer, X_new_scaled)
        labels.iloc[t:end_t] = new_labels
    
    return labels
```

**Approach B: Static Clustering on Training Period Only**

```python
def static_regime_detection(features: pd.DataFrame, 
                             train_end_date: str):
    """
    Cluster once on training period, classify rest.
    Simpler but less adaptive.
    """
    train_mask = features.index <= train_end_date
    
    # Fit on training period only
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(features[train_mask])
    
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=int(train_mask.sum() * 0.05),
        prediction_data=True,
    ).fit(X_train_scaled)
    
    # Classify all data using fitted clusterer
    X_all_scaled = scaler.transform(features)
    labels, _ = hdbscan.approximate_predict(clusterer, X_all_scaled)
    
    return clusterer, labels, scaler
```

**Trade-offs:**

| Aspect | Walk-Forward | Static |
|---|---|---|
| Look-ahead | ✅ None | ✅ None on test |
| Adaptive to new regimes | ✅ Yes | ❌ No |
| Computational cost | ❌ Heavy | ✅ Cheap |
| Cluster stability | ❌ Cluster IDs may change | ✅ Stable |
| Best for | Live trading | Research / backtest |

---

## 🎯 Using Regimes in Your Strategy

The whole point of regime detection is to **change behavior based on regime**. Here's how:

### Use Case 1: Regime-Conditional Position Sizing

```python
def regime_position_size(base_size: float, current_regime: int, 
                          regime_stats: pd.DataFrame) -> float:
    """
    Scale position size based on regime characteristics.
    Lower vol + trending → larger size
    High vol or crisis  → smaller size or zero
    """
    if current_regime == -1:  # outlier/unknown
        return 0.0  # don't trade in unknown regimes
    
    regime_label = regime_stats.loc[current_regime, 'regime_label']
    
    multipliers = {
        'CALM BULL':       1.5,
        'TRENDING UP':     1.2,
        'CHOPPY / RANGE':  0.7,
        'TRENDING DOWN':   0.5,
        'HIGH VOLATILITY': 0.3,
        'CRISIS / BEAR':   0.0,
    }
    
    return base_size * multipliers.get(regime_label, 0.5)
```

### Use Case 2: Regime-Specific Strategy Selection

```python
def select_strategy(current_regime: int, regime_stats: pd.DataFrame):
    """
    Different strategies work in different regimes.
    """
    label = regime_stats.loc[current_regime, 'regime_label']
    
    strategy_map = {
        'CALM BULL':       'breakout',         # trends extend
        'TRENDING UP':     'breakout',
        'CHOPPY / RANGE':  'mean_reversion',   # reversion works
        'TRENDING DOWN':   'short_breakouts',
        'HIGH VOLATILITY': 'vol_arb',
        'CRISIS / BEAR':   'flat',             # stand aside
    }
    
    return strategy_map.get(label, 'flat')
```

### Use Case 3: Regime as Meta-Feature

This is what I'd recommend for your VN equity work — **add regime as a feature in your meta-labeling model**:

```python
# In your feature pipeline
features['market_regime']         = regime_labels
features['days_in_current_regime'] = compute_days_in_regime(regime_labels)
features['regime_avg_winrate']    = compute_historical_regime_winrate(...)
features['regime_avg_volatility'] = regime_centroid_vol[regime_labels]
```

The XGBoost meta-model then learns "in regime X, breakouts work; in regime Y, they don't" automatically.

### Use Case 4: Regime-Conditional Stop Losses

```python
def regime_stop_loss(base_atr_mult: float, current_regime: int) -> float:
    """
    Wider stops in high-vol regimes, tighter in calm ones.
    """
    regime_vol = regime_stats.loc[current_regime, 'realized_vol_20']
    median_vol = regime_stats['realized_vol_20'].median()
    
    vol_adjustment = regime_vol / median_vol
    return base_atr_mult * vol_adjustment
```

---

## ⚠️ Common Pitfalls

### Pitfall 1: Too Many Features

HDBSCAN suffers from curse of dimensionality. With > 15 features, distance metrics become meaningless.

**Fix:** Stick to 5-10 carefully chosen features, or use PCA/UMAP first:

```python
from umap import UMAP
reducer = UMAP(n_components=5, random_state=42)
X_reduced = reducer.fit_transform(X_scaled)
clusterer.fit(X_reduced)
```

### Pitfall 2: Over-Interpreting Tiny Clusters

HDBSCAN sometimes finds clusters of 50-100 points. Don't trust them as regimes — they're often noise.

**Fix:** Set `min_cluster_size` to at least **5% of data** (preferably 10%).

### Pitfall 3: Labels Aren't Stable

Each refit can produce different cluster IDs. Cluster 0 in May might be cluster 2 in June.

**Fix:** Use **regime characteristics** (vol, trend) for decisions, not raw cluster IDs:

```python
# Don't do this:
if regime_label == 0:
    do_strategy_a()

# Do this:
if regime_stats.loc[current_regime, 'realized_vol_20'] > 0.30:
    do_strategy_a()
```

### Pitfall 4: Confusing Outliers with Regime Changes

Cluster -1 (outliers) could mean:
- Crash period (genuinely anomalous)
- New regime emerging (not yet enough data)
- Just noise

**Fix:** Track outlier persistence:

```python
# If 5+ consecutive days are outliers, regime is shifting
consecutive_outliers = (labels == -1).rolling(5).sum()
if consecutive_outliers.iloc[-1] >= 5:
    print("⚠️ Possible regime shift - reduce risk")
```

### Pitfall 5: Single-Asset Regimes Don't Generalize

Vietnamese equity regimes ≠ global regimes. Your features must reflect VN market conditions specifically.

**Fix:** Always use VN-specific signals (VNINDEX, foreign flows, USD/VND, SBV rates).

### Pitfall 6: Treating It as a Crystal Ball

HDBSCAN tells you about **past and current** regime, not future. It doesn't predict regime changes — only identifies them after the fact.

**Fix:** Use regime detection for **risk management**, not market timing. The lag is OK if you're sizing positions based on current vol, not predicting tomorrow.

---

## 🔬 Validation: Is Your Regime Detection Actually Useful?

Don't just look at pretty charts — measure whether regime knowledge improves outcomes.

### Test 1: Regime Stability

```python
def measure_regime_stability(labels: np.ndarray) -> dict:
    """
    Good regimes are persistent. Day-to-day flipping = bad.
    """
    transitions = (np.diff(labels) != 0).sum()
    avg_regime_length = len(labels) / max(transitions, 1)
    
    return {
        'transitions':       transitions,
        'avg_regime_length': avg_regime_length,
        'transitions_per_year': transitions / (len(labels) / 252),
    }

# Healthy: 4-12 regime changes per year (regimes last 1-3 months)
# Unhealthy: 50+ changes per year (just noise)
```

### Test 2: Out-of-Cluster Performance

```python
def regime_performance_separation(returns: pd.Series, 
                                    labels: pd.Series) -> pd.DataFrame:
    """
    Different regimes should produce different return distributions.
    If they don't, your clustering isn't capturing meaningful structure.
    """
    summary = pd.DataFrame({
        'regime': labels,
        'returns': returns,
    })
    
    return summary.groupby('regime').agg(
        mean_return=('returns', 'mean'),
        sharpe=('returns', lambda x: x.mean() / (x.std() + 1e-9) * np.sqrt(252)),
        win_rate=('returns', lambda x: (x > 0).mean()),
        downside_vol=('returns', lambda x: x[x<0].std() * np.sqrt(252) if (x<0).any() else 0),
    ).round(3)

# Healthy: Sharpe across regimes spans -1 to +2
# Unhealthy: All regimes show similar Sharpe (~ 0.5)
```

### Test 3: ANOVA / Permutation Test

```python
from scipy.stats import f_oneway

def test_regime_significance(returns: pd.Series, labels: pd.Series):
    """
    Statistical test: are regime returns significantly different?
    """
    groups = [returns[labels == k].values for k in labels.unique() if k != -1]
    f_stat, p_value = f_oneway(*groups)
    
    print(f"F-statistic: {f_stat:.3f}")
    print(f"p-value:     {p_value:.4f}")
    print("Regimes are statistically distinct" if p_value < 0.05 
          else "Regimes are NOT distinct - clustering is not meaningful")
```

---

## 🇻🇳 VN Equity-Specific Recommendations

For Vietnamese market regime detection, here's my opinionated setup:

### Feature Set (8 features)

```python
def vn_regime_features(close: pd.Series, vnindex: pd.Series, 
                        vix_proxy: pd.Series) -> pd.DataFrame:
    """
    VN-tuned regime features.
    Focus on what matters for VN: VNINDEX direction, vol regime, foreign flows.
    """
    f = pd.DataFrame(index=close.index)
    vn_returns = vnindex.pct_change()
    
    # 1-2: VNINDEX volatility regime
    f['vnindex_vol_20']  = vn_returns.rolling(20).std() * np.sqrt(252)
    f['vnindex_vol_pct'] = f['vnindex_vol_20'].rolling(252).rank(pct=True)
    
    # 3-4: VNINDEX trend regime
    f['vnindex_trend_60']  = vnindex.pct_change(60)
    f['vnindex_drawdown']  = vnindex / vnindex.rolling(252).max() - 1
    
    # 5: Above/below long-term MA
    sma200 = vnindex.rolling(200).mean()
    f['vnindex_above_sma200'] = (vnindex / sma200 - 1)
    
    # 6: Tail risk (downside vol)
    f['downside_vol_60'] = vn_returns[vn_returns < 0].rolling(60).std() * np.sqrt(252)
    
    # 7: Skewness (regime asymmetry)
    f['vn_skew_60'] = vn_returns.rolling(60).skew()
    
    # 8: Market correlation regime (single-stock idiosyncrasy)
    stock_returns = close.pct_change()
    f['corr_to_vnindex'] = stock_returns.rolling(60).corr(vn_returns)
    
    return f.dropna()
```

### Recommended HDBSCAN Parameters for VN

```python
clusterer = hdbscan.HDBSCAN(
    min_cluster_size=50,           # ~10% of 1-year of trading days
    min_samples=15,
    cluster_selection_method='eom',
    cluster_selection_epsilon=0.5,  # merge clusters very close to each other
    metric='euclidean',
    prediction_data=True,
)
```

### Expected Regimes for VN Equities

Based on 2014-2024 VN market history, you should see roughly:

1. **Calm Bull** (long, low-vol periods) — 2017-2019, 2020-2021
2. **Crash Recovery** (high-vol with drawdown) — early 2020 (COVID), 2022 (correction)
3. **Choppy Range** (sideways with elevated vol) — 2023
4. **Trending Bull** (strong trend, normal vol) — late 2017, mid-2021
5. **Crisis** (extreme drawdown) — March 2020, September 2022

If your clusters don't roughly map to these known periods, your features need adjustment.

---

## 📋 A Complete Integration Recipe

Here's how I'd integrate HDBSCAN regimes into your existing pipeline:

```python
class RegimeAwareMetaLabeler:
    """
    Wraps your existing meta-labeling pipeline with regime awareness.
    """
    
    def __init__(self, base_meta_model, regime_features_func):
        self.regime_models = {}  # one model per regime
        self.regime_clusterer = None
        self.regime_features_func = regime_features_func
        
    def fit(self, X_train, y_train, returns_train, dates_train):
        # 1. Detect regimes on training data
        regime_X = self.regime_features_func(...)
        scaler = StandardScaler()
        regime_X_scaled = scaler.fit_transform(regime_X)
        
        self.regime_clusterer = hdbscan.HDBSCAN(
            min_cluster_size=50,
            prediction_data=True,
        ).fit(regime_X_scaled)
        self.regime_scaler = scaler
        
        train_regimes = self.regime_clusterer.labels_
        
        # 2. Train one meta-model per regime
        for regime in np.unique(train_regimes):
            if regime == -1: continue  # skip outliers
            
            mask = train_regimes == regime
            if mask.sum() < 100: continue  # need enough data
            
            self.regime_models[regime] = clone(self.base_model)
            self.regime_models[regime].fit(X_train[mask], y_train[mask])
    
    def predict_proba(self, X_new, regime_features_new):
        # Determine regime
        regime_X_scaled = self.regime_scaler.transform(regime_features_new)
        regime, _ = hdbscan.approximate_predict(self.regime_clusterer, regime_X_scaled)
        
        # Use regime-specific model
        if regime[0] in self.regime_models:
            return self.regime_models[regime[0]].predict_proba(X_new)
        else:
            # Fallback for unknown regimes
            return np.array([[0.5, 0.5]])  # uncertain → don't trade
```

This approach **trains different meta-models for different regimes**, which often dramatically improves OOS performance because breakout strategies behave differently in bull vs choppy markets.

---

## 🎯 TL;DR — When to Use HDBSCAN for Regimes

### ✅ Good fit for:

- Detecting structural market regimes from multi-dimensional features
- Identifying **outlier periods** (cluster -1) like crashes
- **Risk management** — adapt position sizing to regime
- **Feature engineering** — regime as a feature in your XGBoost
- Research / backtesting historical periods

### ❌ Not great for:

- **Predicting** regime changes (it's lagging)
- **Real-time decisions** in milliseconds (too slow)
- Markets with too few data points (need 5+ years minimum)
- Very high-frequency data (computational cost)