import MetaTrader5 as mt5
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import joblib

mt5.initialize()

symbol = "XAUUSD"

rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 3000)

df = pd.DataFrame(rates)

df['returns'] = df['close'].pct_change()
df['ma'] = df['close'].rolling(20).mean()
df['momentum'] = df['close'] - df['close'].shift(5)

df.dropna(inplace=True)

df['target'] = (df['close'].shift(-1) > df['close']).astype(int)

X = df[['returns', 'ma', 'momentum']]
y = df['target']

model = RandomForestClassifier(n_estimators=200)
model.fit(X, y)

joblib.dump(model, "ai_model.pkl")

print("✅ AI TRAINED")