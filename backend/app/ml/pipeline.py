import os
import pickle
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import mean_squared_error, mean_absolute_percentage_error

# Define directory for saving trained ML artifacts
ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
os.makedirs(ARTIFACTS_DIR, exist_ok=True)

def create_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Engineers tabular time-series features for XGBoost forecasting.
    Input df must contain: date, price, discount_pct, is_festival_period
    """
    df = df.copy()
    df = df.sort_values("date").reset_index(drop=True)
    
    # 1. Lags
    df["price_lag_1"] = df["price"].shift(1)
    df["price_lag_7"] = df["price"].shift(7)
    df["price_lag_14"] = df["price"].shift(14)
    df["price_lag_30"] = df["price"].shift(30)
    
    # 2. Rolling window features (avoiding lookahead bias by shifting by 1 day)
    df["rolling_mean_7"] = df["price"].shift(1).rolling(window=7).mean()
    df["rolling_std_7"] = df["price"].shift(1).rolling(window=7).std()
    
    df["rolling_mean_14"] = df["price"].shift(1).rolling(window=14).mean()
    df["rolling_std_14"] = df["price"].shift(1).rolling(window=14).std()
    
    df["rolling_mean_30"] = df["price"].shift(1).rolling(window=30).mean()
    df["rolling_std_30"] = df["price"].shift(1).rolling(window=30).std()
    
    # 3. Calendar Features
    df["date_dt"] = pd.to_datetime(df["date"])
    df["day_of_week"] = df["date_dt"].dt.dayofweek
    df["month"] = df["date_dt"].dt.month
    
    # 4. Momentum: Days since last price change
    is_change = df["price"].diff() != 0
    is_change.iloc[0] = True # Initialize first element
    change_groups = is_change.cumsum()
    df["days_since_price_change"] = df.groupby(change_groups).cumcount()
    
    # Clean up temporary column
    df = df.drop(columns=["date_dt"])
    
    return df

def train_and_save_models(df: pd.DataFrame, product_id: int, product_name: str):
    """
    Trains and saves 3 separate XGBoost regression models (7, 30, and 60 days lead time)
    for a given product. Includes chronological validation backtesting.
    """
    df_features = create_features(df)
    
    # Drop rows with NaNs in features (first 30 rows due to 30-day lag/rolling windows)
    df_clean = df_features.dropna(subset=[
        "price_lag_1", "price_lag_7", "price_lag_14", "price_lag_30",
        "rolling_mean_7", "rolling_std_7",
        "rolling_mean_14", "rolling_std_14",
        "rolling_mean_30", "rolling_std_30"
    ]).copy()
    
    feature_cols = [
        "price", "discount_pct", "is_festival_period",
        "price_lag_1", "price_lag_7", "price_lag_14", "price_lag_30",
        "rolling_mean_7", "rolling_std_7",
        "rolling_mean_14", "rolling_std_14",
        "rolling_mean_30", "rolling_std_30",
        "day_of_week", "month", "days_since_price_change"
    ]
    
    horizons = [7, 30, 60]
    results = {}
    
    for h in horizons:
        # Define target variable (price in h days)
        df_clean[f"target_{h}"] = df_clean["price"].shift(-h)
        
        # Supervised rows (exclude the last h rows which do not have a target yet)
        df_supervised = df_clean.dropna(subset=[f"target_{h}"]).copy()
        
        X = df_supervised[feature_cols]
        y = df_supervised[f"target_{h}"]
        
        # Chronological Split (last 60 days for validation, minimum 120 days for training)
        val_size = min(60, len(df_supervised) // 3)
        train_idx = len(df_supervised) - val_size
        
        X_train, y_train = X.iloc[:train_idx], y.iloc[:train_idx]
        X_val, y_val = X.iloc[train_idx:], y.iloc[train_idx:]
        
        # Setup XGBoost Regressor
        model = xgb.XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42
        )
        
        # Train on training set
        model.fit(X_train, y_train)
        
        # Predict on validation set
        y_pred = model.predict(X_val)
        
        # Evaluate model error (RMSE and MAPE)
        rmse = np.sqrt(mean_squared_error(y_val, y_pred))
        mape = mean_absolute_percentage_error(y_val, y_pred)
        
        # Retrain on full supervised dataset for production/demo deployment
        final_model = xgb.XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42
        )
        final_model.fit(X, y)
        
        # Save final model binary
        model_filename = f"model_p{product_id}_h{h}.json"
        model_path = os.path.join(ARTIFACTS_DIR, model_filename)
        final_model.save_model(model_path)
        
        # Store metrics
        results[h] = {
            "rmse": float(rmse),
            "mape": float(mape),
            "model_path": model_path
        }
        
    # Save validation metadata (RMSE, MAPE) for forecasting interval logic
    meta_path = os.path.join(ARTIFACTS_DIR, f"metadata_p{product_id}.pickle")
    with open(meta_path, "wb") as f:
        pickle.dump(results, f)
        
    print(f"Product {product_name} (ID: {product_id}) models trained successfully.")
    for h in horizons:
        print(f"  Horizon {h}d: Val RMSE = Rs. {results[h]['rmse']:.2f}, MAPE = {results[h]['mape']*100:.2f}%")
        
    return results
