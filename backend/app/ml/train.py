import sys
import os
import pandas as pd

# Add the project root directory to the python path to resolve package imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.app.database import SessionLocal, engine, Base
from backend.app.ml.generator import seed_database
from backend.app.ml.pipeline import train_and_save_models
from backend.app import models

def main():
    db = SessionLocal()
    try:
        # 1. Initialize and seed database if empty
        seed_database(db)
        
        # 2. Fetch all products
        products = db.query(models.Product).all()
        if not products:
            print("No products found in the database. Seeding failed?")
            return
            
        print(f"Found {len(products)} products. Starting model training...")
        
        # 3. Train models for each product
        for product in products:
            print(f"\n--- Training Models for: {product.name} (ID: {product.id}) ---")
            
            # Retrieve all historical price records for this product
            records = db.query(models.PriceRecord)\
                        .filter(models.PriceRecord.product_id == product.id)\
                        .order_by(models.PriceRecord.date.asc())\
                        .all()
                        
            if not records:
                print(f"No price records found for product ID {product.id}. Skipping.")
                continue
                
            # Load price history records into Pandas DataFrame
            data = []
            for r in records:
                data.append({
                    "date": r.date,
                    "price": r.price,
                    "discount_pct": r.discount_pct,
                    "is_festival_period": r.is_festival_period
                })
            df = pd.DataFrame(data)
            
            # Execute model training
            train_and_save_models(df, product.id, product.name)
            
        print("\nAll models trained and saved successfully.")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
