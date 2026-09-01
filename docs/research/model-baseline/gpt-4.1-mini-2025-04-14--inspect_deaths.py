import pandas as pd

# Set display options for pandas
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

# Load the dataset
file_path = "./data/deaths.csv"
df = pd.read_csv(file_path)

# Inspect shape, dtypes, head, describe
print("Shape:", df.shape)
print("Data types:\n", df.dtypes)
print("Head (10 rows):\n", df.head(10))
print("Describe:\n", df.describe(include='all').transpose())

# Check missing values
missing = df.isnull().sum()
print("Missing values per column (if any):\n", missing[missing > 0])
