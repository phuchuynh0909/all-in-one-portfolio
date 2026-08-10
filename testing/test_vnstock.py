# from vnstock_data import show_api, Reference
from vnstock import Market, Reference, Fundamental

# ref = Reference()
fun = Fundamental()
# df_profile = ref.company("HSG").info()
# `fun.equity(symbol)` returns an EquityFundamental object; call the report
# method (income_statement/balance_sheet/cash_flow/ratio) on THAT, not on
# `fun.equity` itself (that's a bound method, not a namespace).
df_profile = fun.equity("BCM").cash_flow(period="Q")

import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

if len(df_profile) == 1:
    # Single-row profile -> "field : value" lines. (to_string() on the
    # transpose pads every value to the widest one, e.g. a long text field,
    # so short fields get pushed far to the right.)
    row = df_profile.iloc[0]
    width = max(len(c) for c in row.index)
    for col, val in row.items():
        print(f"{col:<{width}} : {val}")
else:
    # Multi-row report (e.g. income_statement) -> print as a normal table.
    print(df_profile.to_string(index=False))
