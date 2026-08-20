# ── GKYZ defaults ─────────────────────────────────────────────────────────────
GKYZ_WINDOW = 21
GKYZ_UPPER  = 0.8
GKYZ_LOWER  = 0.2

# ── YZ vol-ratio defaults ────────────────────────────────────────────────────
YZ_SHORT_WINDOW    = 20
YZ_LONG_WINDOW     = 120
YZ_RISK_OFF_THRESH = 1.0   # vol_ratio below this → risk_off (sticky)
YZ_RISK_ON_THRESH  = 1.0   # vol_ratio above this → risk_on  (sticky, elevated)


def compute_gkyz_regime(
    open_s, high_s, low_s, close_s,
    window=GKYZ_WINDOW, upper=GKYZ_UPPER, lower=GKYZ_LOWER,
) -> tuple:
    """Hysteresis regime: risk_on=True when GKYZ crosses above upper; False below lower."""
    gkyz_arr = calculate_gkyz_volatility(
        open_s.values.astype(np.float64), high_s.values.astype(np.float64),
        low_s.values.astype(np.float64),  close_s.values.astype(np.float64),
        window=window, normalize=True,
    )
    n = len(gkyz_arr)
    risk_on = np.zeros(n, dtype=bool)
    state   = False
    for i in range(n):
        v = gkyz_arr[i]
        if np.isnan(v):
            risk_on[i] = state; continue
        if not state and v > upper: state = True
        elif state and v < lower:  state = False
        risk_on[i] = state
    idx = close_s.index
    return pd.Series(gkyz_arr, index=idx, name='gkyz'),            pd.Series(risk_on,  index=idx, name='risk_on')


def compute_yz_regime(
    open_s, high_s, low_s, close_s,
    short_window=YZ_SHORT_WINDOW, long_window=YZ_LONG_WINDOW,
    risk_off_threshold=YZ_RISK_OFF_THRESH, risk_on_threshold=YZ_RISK_ON_THRESH,
) -> tuple:
    """
    Vol-ratio regime: YZ(short) / YZ(long).
    Zones: <0.75 risk_on (calm) | 0.75-1.0 neutral | >1.0 risk_off (elevated)
    Hysteresis:
      ratio > risk_on_threshold  → risk_on  = True  (sticky)
      ratio < risk_off_threshold → risk_on  = False (sticky)
    Returns (vol_ratio series, risk_on bool series).
    """
    def _yz(window):
        lst = calculate_yz_volatility(
            open_s.values.astype(np.float64), high_s.values.astype(np.float64),
            low_s.values.astype(np.float64),  close_s.values.astype(np.float64),
            window=window,
        )
        s = pd.Series(np.array(lst, dtype=np.float64), index=close_s.index)
        return s.replace(0, np.nan)

    yz_s      = _yz(short_window)
    yz_l      = _yz(long_window)
    vol_ratio = (yz_s / yz_l).replace([np.inf, -np.inf], np.nan)

    ratio_arr = vol_ratio.values
    n         = len(ratio_arr)
    risk_on   = np.zeros(n, dtype=bool)
    state     = False
    for i in range(n):
        v = ratio_arr[i]
        if np.isnan(v):
            risk_on[i] = state
            continue
        if state and v < risk_off_threshold:
            state = False
        elif not state and v > risk_on_threshold:
            state = True
        risk_on[i] = state

    return vol_ratio, pd.Series(risk_on, index=close_s.index, name='risk_on')


def compute_gkyz_regime_all(open_df, high_df, low_df, close_df,
                             window=GKYZ_WINDOW, upper=GKYZ_UPPER, lower=GKYZ_LOWER):
    """GKYZ risk-on boolean for every symbol. Returns (T, N) bool DataFrame."""
    result = pd.DataFrame(False, index=close_df.index, columns=close_df.columns)
    for sym in close_df.columns:
        _, ron = compute_gkyz_regime(open_df[sym], high_df[sym], low_df[sym], close_df[sym],
                                     window=window, upper=upper, lower=lower)
        result[sym] = ron.values
    return result


def compute_yz_regime_all(open_df, high_df, low_df, close_df,
                           short_window=YZ_SHORT_WINDOW, long_window=YZ_LONG_WINDOW,
                           risk_off_threshold=YZ_RISK_OFF_THRESH,
                           risk_on_threshold=YZ_RISK_ON_THRESH):
    """Vol-ratio YZ risk-on boolean for every symbol. Returns (T, N) bool DataFrame."""
    result = pd.DataFrame(False, index=close_df.index, columns=close_df.columns)
    for sym in close_df.columns:
        _, ron = compute_yz_regime(open_df[sym], high_df[sym], low_df[sym], close_df[sym],
                                   short_window=short_window, long_window=long_window,
                                   risk_off_threshold=risk_off_threshold,
                                   risk_on_threshold=risk_on_threshold)
        result[sym] = ron.values
    return result


def compute_regime(open_s, high_s, low_s, close_s, regime_version='v1', **kwargs):
    """Dispatch to GKYZ (v1) or YZ midline (v2) regime for a single series."""
    if regime_version == 'v1':
        return compute_gkyz_regime(open_s, high_s, low_s, close_s, **kwargs)
    return compute_yz_regime(open_s, high_s, low_s, close_s, **kwargs)


def compute_regime_all(open_df, high_df, low_df, close_df, regime_version='v1', **kwargs):
    """Dispatch to GKYZ (v1) or YZ midline (v2) regime for all symbols."""
    if regime_version == 'v1':
        return compute_gkyz_regime_all(open_df, high_df, low_df, close_df, **kwargs)
    return compute_yz_regime_all(open_df, high_df, low_df, close_df, **kwargs)


def apply_gkyz_entry_filter(entries, risk_on_vnindex, risk_on_symbols, entry_version):
    """
    Gate entries by regime state.
    v3    : enter only when VNINDEX AND symbol are both risk-on  (True)
    v1/v2 : enter only when VNINDEX AND symbol are both risk-off (False)
    """
    vn_mat = pd.DataFrame(
        np.tile(risk_on_vnindex.values[:, None], (1, len(entries.columns))),
        index=entries.index, columns=entries.columns,
    )
    gate = (vn_mat & risk_on_symbols) if entry_version == 'v3' else (~vn_mat & ~risk_on_symbols)
    return entries & gate


# Compute VNINDEX baseline regime (v1 / GKYZ)
gkyz_vnindex, risk_on_vnindex = compute_gkyz_regime(
    open_['VNINDEX'], high['VNINDEX'], low['VNINDEX'], close['VNINDEX'],
)
n_risk_on  = risk_on_vnindex.sum()
n_risk_off = (~risk_on_vnindex).sum()
print(f'VNINDEX  risk-on  bars: {n_risk_on}  ({n_risk_on / len(risk_on_vnindex) * 100:.1f}%)')
print(f'VNINDEX  risk-off bars: {n_risk_off} ({n_risk_off / len(risk_on_vnindex) * 100:.1f}%)')
