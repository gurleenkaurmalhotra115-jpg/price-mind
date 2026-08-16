from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from backend.app.database import get_db, Base, engine
from backend.app import crud, models
from backend.app.ml import forecaster

# Create DB tables (if they don't exist yet)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="PriceMind AI - API Server", version="0.1")

# Enable CORS for local cross-origin frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic schemas
class AlertCreate(BaseModel):
    product_id: int
    target_price: float

class AlertResponse(BaseModel):
    id: int
    product_id: int
    target_price: float
    is_active: bool
    created_at: datetime

    class Config:
        orm_mode = True
        from_attributes = True

class SimulateRequest(BaseModel):
    product_id: int
    wait_days: int

@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    products = crud.get_products(db)
    return [
        {
            "id": p.id,
            "name": p.name,
            "brand": p.brand,
            "category": p.category,
            "msrp": p.msrp,
            "current_price": p.current_price
        }
        for p in products
    ]

@app.get("/api/forecast/{product_id}")
def get_forecast(product_id: int, target_price: Optional[float] = None, db: Session = Depends(get_db)):
    result = forecaster.get_forecast_results(db, product_id, target_price)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found or models not trained.")
    return result

@app.post("/api/alerts", response_model=AlertResponse)
def create_alert(alert_data: AlertCreate, db: Session = Depends(get_db)):
    product = crud.get_product(db, alert_data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    db_alert = crud.create_alert(db, alert_data.product_id, alert_data.target_price)
    return db_alert

@app.get("/api/alerts", response_model=List[AlertResponse])
def get_alerts(db: Session = Depends(get_db)):
    return crud.get_active_alerts(db)

@app.post("/api/simulate")
def simulate_wait(req: SimulateRequest, db: Session = Depends(get_db)):
    # Retrieve base forecast results (which gives us current, pred_7, pred_30, pred_60)
    fc = forecaster.get_forecast_results(db, req.product_id)
    if not fc or "forecast" not in fc:
        raise HTTPException(status_code=404, detail="Forecast data unavailable")
        
    current_price = fc["current_price"]
    pred_7 = fc["forecast"]["7"]
    pred_30 = fc["forecast"]["30"]
    pred_60 = fc["forecast"]["60"]
    
    # Validation RMSE errors as standard deviations
    rmse_7 = fc["validation_rmse"]["7"]
    rmse_30 = fc["validation_rmse"]["30"]
    rmse_60 = fc["validation_rmse"]["60"]
    
    # Linear interpolation for price, confidence, and std dev based on target wait_days
    wait_days = max(1, min(90, req.wait_days))
    
    if wait_days <= 7:
        pct = wait_days / 7.0
        predicted_price = current_price + (pred_7 - current_price) * pct
        rmse = rmse_7 * pct # scales uncertainty
    elif wait_days <= 30:
        pct = (wait_days - 7) / 23.0
        predicted_price = pred_7 + (pred_30 - pred_7) * pct
        rmse = rmse_7 + (rmse_30 - rmse_7) * pct
    elif wait_days <= 60:
        pct = (wait_days - 30) / 30.0
        predicted_price = pred_30 + (pred_60 - pred_30) * pct
        rmse = rmse_30 + (rmse_60 - rmse_30) * pct
    else:
        # Extrapolate slightly flat beyond 60 days
        pct = (wait_days - 60) / 30.0
        predicted_price = pred_60
        rmse = rmse_60 * (1.0 + pct * 0.1) # slow uncertainty growth
        
    predicted_price = float(round(predicted_price))
    
    # Expected savings (compared to current price)
    savings = max(0.0, current_price - predicted_price)
    
    # Probability of price drop below current
    p_drop = forecaster.normal_cdf(current_price - 1.0, predicted_price, rmse)
    
    # Risk of price increase (price goes up relative to current)
    p_increase = 1.0 - forecaster.normal_cdf(current_price + 1.0, predicted_price, rmse)
    
    return {
        "wait_days": wait_days,
        "current_price": current_price,
        "expected_price": predicted_price,
        "expected_savings": savings,
        "p_drop": p_drop,
        "risk_p_increase": p_increase,
        "message": f"Simulation for {wait_days} days finished."
    }

@app.post("/api/alerts/check")
def trigger_alert_simulation(req: AlertCreate, db: Session = Depends(get_db)):
    product = crud.get_product(db, req.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    target = req.target_price
    
    # In a real system, we look at the latest price stream.
    # Here, we simulate a drop that drops slightly below the target budget
    simulated_price = target - 701.0
    if simulated_price < product.msrp * 0.5:
        simulated_price = product.msrp * 0.5
        
    simulated_price = float(round(simulated_price))
    savings = float(round(product.current_price - simulated_price))
    
    # Return simulated execution logs
    now_str = datetime.now().strftime("%H:%M:%S")
    logs = [
        f"[{now_str}] Verifying regional retail listing streams...",
        f"[{now_str}] Ingesting supported seller catalogs...",
        f"[{now_str}] Match found: {product.name} on Flipkart.",
        f"🔔 MATCH SIGNAL TRIGGERED! Price dropped: ₹{simulated_price:,.0f} (Target: ₹{target:,.0f})",
        f"[{now_str}] Running secure seller credentials check... ✅ VERIFIED",
        f"[{now_str}] Initiating Autonomous Purchase sequence...",
        f"[{now_str}] Mock API payload submitted successfully.",
        f"🎉 Deal locked! Order completed at ₹{simulated_price:,.0f}. Savings: ₹{savings:,.0f}"
    ]
    
    # In-app deactivate the alert
    alerts = crud.get_alerts_for_product(db, req.product_id)
    for alert in alerts:
        if abs(alert.target_price - target) < 1.0:
            crud.deactivate_alert(db, alert.id)
            
    # Keep the database current price updated to simulated price to show alert effects
    # (Optional, but great for dynamic demos!)
    crud.update_product_price(db, req.product_id, simulated_price)
    
    return {
        "triggered": True,
        "product_name": product.name,
        "simulated_price": simulated_price,
        "savings": savings,
        "logs": logs
    }

from fastapi.responses import HTMLResponse
import os

@app.get("/", response_class=HTMLResponse)
def read_root():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    index_path = os.path.join(base_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "index.html not found"
