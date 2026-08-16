import os
import pickle
import math
import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy.orm import Session
from backend.app import models, crud
from backend.app.ml.pipeline import create_features, ARTIFACTS_DIR

def normal_cdf(x: float, mean: float, std: float) -> float:
    """
    Computes the Cumulative Distribution Function (CDF) of a normal distribution.
    Uses math.erf for high performance and zero dependency overhead.
    """
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + math.erf((x - mean) / (std * math.sqrt(2.0))))

def get_forecast_results(db: Session, product_id: int, user_target_price: float = None):
    """
    Loads trained models for a product, extracts the latest features,
    and runs predictions for 7, 30, and 60 days. Returns forecast, confidence,
    probabilities, risk flags, and recommend labels.
    """
    product = crud.get_product(db, product_id)
    if not product:
        return None
        
    records = crud.get_price_history(db, product_id)
    if not records or len(records) < 31:
        return {
            "product_name": product.name,
            "current_price": product.current_price,
            "verdict": "LOW CONFIDENCE",
            "verdict_class": "verdict-avoid",
            "message": "Insufficient historical data to make forecast."
        }
        
    # 1. Prepare features from history
    data = []
    for r in records:
        data.append({
            "date": r.date,
            "price": r.price,
            "discount_pct": r.discount_pct,
            "is_festival_period": r.is_festival_period
        })
    df = pd.DataFrame(data)
    df_features = create_features(df)
    
    # Extract the very last row for current inference features
    latest_features = df_features.tail(1).copy()
    current_price = product.current_price
    
    feature_cols = [
        "price", "discount_pct", "is_festival_period",
        "price_lag_1", "price_lag_7", "price_lag_14", "price_lag_30",
        "rolling_mean_7", "rolling_std_7",
        "rolling_mean_14", "rolling_std_14",
        "rolling_mean_30", "rolling_std_30",
        "day_of_week", "month", "days_since_price_change"
    ]
    
    X_infer = latest_features[feature_cols]
    
    # 2. Load models & predict for 7, 30, 60 days
    predictions = {}
    horizons = [7, 30, 60]
    
    # Load validation metadata (RMSE, MAPE)
    meta_path = os.path.join(ARTIFACTS_DIR, f"metadata_p{product_id}.pickle")
    if not os.path.exists(meta_path):
        return {
            "product_name": product.name,
            "current_price": product.current_price,
            "verdict": "LOW CONFIDENCE",
            "verdict_class": "verdict-avoid",
            "message": "Models not trained. Please run training script first."
        }
        
    with open(meta_path, "rb") as f:
        meta = pickle.load(f)
        
    for h in horizons:
        model_path = os.path.join(ARTIFACTS_DIR, f"model_p{product_id}_h{h}.json")
        if not os.path.exists(model_path):
            return {
                "product_name": product.name,
                "current_price": product.current_price,
                "verdict": "LOW CONFIDENCE",
                "verdict_class": "verdict-avoid",
                "message": f"Model for horizon {h}d is missing."
            }
            
        model = xgb.XGBRegressor()
        model.load_model(model_path)
        
        # XGBoost prediction
        pred_val = float(model.predict(X_infer)[0])
        # Clamp prediction to logical bounds (e.g. between 50% and 110% of current price)
        pred_val = max(current_price * 0.5, min(current_price * 1.1, pred_val))
        pred_val = float(round(pred_val))
        
        # Extract validation errors
        rmse = meta[h]["rmse"]
        mape = meta[h]["mape"]
        
        # Confidence score derived from validation MAPE: max(0, 1 - MAPE)
        confidence = max(0.0, 1.0 - mape) * 100
        
        # Probability that price drops below current price
        # CDF of Normal(predicted, rmse^2) evaluated at current_price
        p_drop_current = normal_cdf(current_price - 1.0, pred_val, rmse)
        
        # Probability that price drops below target
        p_drop_target = 0.0
        if user_target_price:
            p_drop_target = normal_cdf(user_target_price, pred_val, rmse)
            
        # Risk of price increase
        p_increase = 1.0 - normal_cdf(current_price + 1.0, pred_val, rmse)
        
        predictions[h] = {
            "price": pred_val,
            "rmse": rmse,
            "confidence": confidence,
            "p_drop_current": p_drop_current,
            "p_drop_target": p_drop_target,
            "p_increase": p_increase
        }
        
    # 3. Recommendation logic
    # Main indicators (based primarily on the 30-day forecast)
    pred_30 = predictions[30]["price"]
    conf_30 = predictions[30]["confidence"]
    p_drop_30 = predictions[30]["p_drop_current"]
    
    # Savings calculation
    expected_savings = 0.0
    best_horizon = 30
    best_pred_price = current_price
    
    for h in horizons:
        pred_h = predictions[h]["price"]
        if pred_h < best_pred_price:
            best_pred_price = pred_h
            best_horizon = h
            
    if best_pred_price < current_price:
        expected_savings = current_price - best_pred_price
        
    # Price trend calculation
    # We say it is rising if all predictions are >= current_price * 0.99
    is_rising_trend = all(predictions[h]["price"] >= current_price * 0.99 for h in horizons)
    
    # Recommendation rules
    if all(predictions[h]["confidence"] < 60.0 for h in horizons):
        verdict = "LOW CONFIDENCE"
        verdict_class = "verdict-avoid"
        reason = "Validation accuracy is below acceptable threshold."
    elif is_rising_trend and conf_30 >= 70.0 and p_drop_30 < 0.30:
        verdict = "STRONG BUY"
        verdict_class = "verdict-buy"
        reason = "Prices are expected to remain stable or rise. Low risk of waiting benefits."
    elif (current_price - pred_30) / current_price < 0.03:
        verdict = "BUY"
        verdict_class = "verdict-buy"
        reason = "Expected drop is less than 3% in the next 30 days."
    elif (current_price - pred_30) / current_price >= 0.03 and conf_30 >= 60.0:
        verdict = "WAIT"
        verdict_class = "verdict-wait"
        reason = f"Forecast indicates a price drop of ₹{expected_savings:.0f} (~{ (expected_savings/current_price)*100:.1f}%) within {best_horizon} days."
    else:
        verdict = "LOW CONFIDENCE"
        verdict_class = "verdict-avoid"
        reason = "Model confidence insufficient to make recommendation."
        
    # Risk flag if probability of increase is high (> 25% for 30 days)
    risk_flag = predictions[30]["p_increase"] >= 0.25
    
    # Calculate historical average price
    average_historical_price = float(round(df["price"].mean(), 2))
    
    return {
        "product_id": product.id,
        "product_name": product.name,
        "brand": product.brand,
        "current_price": current_price,
        "msrp": product.msrp,
        "average_historical_price": average_historical_price,
        "forecast": {
            "7": predictions[7]["price"],
            "30": predictions[30]["price"],
            "60": predictions[60]["price"]
        },
        "expected_savings": expected_savings,
        "best_horizon": best_horizon,
        "confidence": conf_30,
        "p_drop_30": p_drop_30,
        "p_drop_target_30": predictions[30]["p_drop_target"],
        "risk_p_increase_30": predictions[30]["p_increase"],
        "risk_flag": risk_flag,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "reason": reason,
        "validation_rmse": {
            "7": predictions[7]["rmse"],
            "30": predictions[30]["rmse"],
            "60": predictions[60]["rmse"]
        },
        "validation_mape": {
            "7": meta[7]["mape"],
            "30": meta[30]["mape"],
            "60": meta[60]["mape"]
        },
        "historical_prices": [
            {"date": r.date.isoformat(), "price": r.price}
            for r in records[-30:] # Last 30 observations
        ]
    }
