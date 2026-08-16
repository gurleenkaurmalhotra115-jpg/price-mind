import random
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from backend.app.database import engine, Base, SessionLocal
from backend.app import models, crud

# Configured smartphone catalog
SMARTPHONES = [
    {
        "name": "Samsung Galaxy S24 Ultra",
        "brand": "Samsung",
        "category": "Smartphones",
        "msrp": 139999.0,
        "current_price": 129999.0,
        "depreciation_rate": 0.07  # 7% standard depreciation over the year
    },
    {
        "name": "iPhone 15 Pro",
        "brand": "Apple",
        "category": "Smartphones",
        "msrp": 134900.0,
        "current_price": 112999.0,
        "depreciation_rate": 0.14  # 14% standard depreciation over the year
    },
    {
        "name": "OnePlus 12",
        "brand": "OnePlus",
        "category": "Smartphones",
        "msrp": 69999.0,
        "current_price": 64999.0,
        "depreciation_rate": 0.08  # 8% standard depreciation over the year
    },
    {
        "name": "Google Pixel 8 Pro",
        "brand": "Google",
        "category": "Smartphones",
        "msrp": 109999.0,
        "current_price": 95999.0,
        "depreciation_rate": 0.12  # 12% standard depreciation over the year
    }
]

def generate_price_history(product_spec, start_date: date, end_date: date):
    history = []
    days_total = (end_date - start_date).days
    
    msrp = product_spec["msrp"]
    target_price = product_spec["current_price"]
    dep_rate = product_spec["depreciation_rate"]
    
    # Calculate baseline starting price so we land exactly on target_price at the end
    # We want base_price(end) = target_price.
    # At t=days_total, baseline is msrp * (1 - dep_rate).
    # We add an adjustment offset so that the final price lands exactly on target_price
    final_baseline = msrp * (1.0 - dep_rate)
    offset = target_price - final_baseline
    
    # Seed random for reproducibility
    random.seed(42 + hash(product_spec["name"]) % 1000)
    
    # Define sale periods (diwali/autumn sale, year-end sale, spring sale, summer sale)
    # Using day-of-year ranges (roughly)
    festival_periods = [
        (280, 295), # Autumn Festival / Big Billion Days (October 10 to October 25)
        (350, 366), # Year End Sale (December 15 to December 31)
        (70, 80),   # Spring Sale (March 10 to March 20)
        (155, 168), # Summer Sale (June 5 to June 18)
    ]
    
    flash_sale_days = set(random.sample(range(10, days_total - 10), 10)) # 10 random flash sales
    
    for t in range(days_total + 1):
        obs_date = start_date + timedelta(days=t)
        day_of_year = obs_date.timetuple().tm_yday
        
        # 1. Base depreciation trend
        progress = t / days_total
        baseline = msrp * (1.0 - progress * dep_rate) + offset
        
        # 2. Festival period check
        is_festival = False
        festival_dip = 0.0
        for start_doy, end_doy in festival_periods:
            if start_doy <= day_of_year <= end_doy:
                is_festival = True
                festival_dip = 0.12 # 12% drop
                break
        
        # 3. Weekly cycle (weekends are slightly cheaper)
        is_weekend = obs_date.weekday() in [4, 5, 6] # Fri, Sat, Sun
        weekend_dip = 0.015 if is_weekend else 0.0
        
        # 4. Flash sale dip
        is_flash = t in flash_sale_days
        flash_dip = 0.05 if is_flash else 0.0
        
        # 5. Normal noise
        noise = random.normalvariate(0, 0.005) # 0.5% standard deviation noise
        
        # Calculate combined multipliers
        total_dip = festival_dip + weekend_dip + flash_dip
        sim_price = baseline * (1.0 - total_dip + noise)
        
        # Clamp price to be maximum MSRP and minimum 60% MSRP
        sim_price = max(msrp * 0.6, min(msrp, sim_price))
        
        # Force final date to match target current price exactly
        if t == days_total:
            sim_price = target_price
            is_festival = False
        
        # Round to nearest integer (rupee)
        sim_price = float(round(sim_price))
        
        discount_pct = float(round(((msrp - sim_price) / msrp) * 100, 1))
        
        history.append({
            "date": obs_date,
            "price": sim_price,
            "discount_pct": discount_pct,
            "is_festival_period": is_festival
        })
        
    return history

def seed_database(db: Session):
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    
    # Check if products already exist
    existing_products = db.query(models.Product).count()
    if existing_products > 0:
        print("Database already seeded. Skipping generation.")
        return
        
    print("Seeding database...")
    end_date = date(2026, 8, 15) # Match current date constraint
    start_date = end_date - timedelta(days=365)
    
    for spec in SMARTPHONES:
        print(f"Generating data for {spec['name']}...")
        db_product = crud.create_product(
            db=db,
            name=spec["name"],
            brand=spec["brand"],
            category=spec["category"],
            msrp=spec["msrp"],
            current_price=spec["current_price"]
        )
        
        history = generate_price_history(spec, start_date, end_date)
        
        for record in history:
            crud.create_price_record(
                db=db,
                product_id=db_product.id,
                obs_date=record["date"],
                price=record["price"],
                discount_pct=record["discount_pct"],
                is_festival=record["is_festival_period"]
            )
            
    print("Database seeding completed successfully!")

if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
