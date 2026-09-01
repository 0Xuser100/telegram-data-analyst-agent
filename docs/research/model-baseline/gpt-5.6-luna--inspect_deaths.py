import pandas as pd

pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 25)
pd.set_option('display.max_rows', 30)

path = r'C:\Users\ThinkTech\AppData\Local\Temp\baseline-gpt-5_6-luna-u9rf0blt\data\deaths.csv'
df = pd.read_csv(path)
print('SHAPE:', df.shape)
print('\nDTYPES:')
print(df.dtypes.to_string())
print('\nHEAD:')
print(df.head(10).to_string(index=False))
print('\nDESCRIBE:')
if df.shape[1] > 15:
    print(df.describe(include='all').T.head(15).to_string())
else:
    print(df.describe(include='all').to_string())
missing = df.isna().sum()
missing = missing[missing > 0]
print('\nMISSING:')
print(missing.to_string() if len(missing) else 'none')
