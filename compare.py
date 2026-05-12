import pandas as pd

df1 = pd.read_excel("cherrypick_list - Copy.xlsx")
df2 = pd.read_excel("cherrypick_list.xlsx")

if df1.shape != df2.shape:
    print(f"DIFFERENT: shapes differ - Copy={df1.shape}, Current={df2.shape}")
else:
    diff = (df1.fillna("") != df2.fillna(""))
    if diff.any().any():
        for col in diff.columns[diff.any()]:
            rows = diff.index[diff[col]].tolist()
            for r in rows:
                v1 = df1.at[r, col]
                v2 = df2.at[r, col]
                print(f"DIFF row {r}, col [{col}]: Copy=[{v1}] vs Current=[{v2}]")
    else:
        print("IDENTICAL: No differences found.")
