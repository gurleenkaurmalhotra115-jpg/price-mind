import sys
import os
import unittest
import tempfile
import shutil
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.app.database import Base
from backend.app import models, crud
from backend.app.ml import generator, pipeline, forecaster

class TestPriceMindPipeline(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for database and model artifacts
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_pricemind.db")
        
        # Override database configuration for testing
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
        # Create schema
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        
        # Override forecaster's artifacts path to avoid overwriting production artifacts
        self.original_artifacts_dir = pipeline.ARTIFACTS_DIR
        pipeline.ARTIFACTS_DIR = os.path.join(self.test_dir, "artifacts")
        os.makedirs(pipeline.ARTIFACTS_DIR, exist_ok=True)

    def tearDown(self):
        self.db.close()
        # Restore artifacts directory path
        pipeline.ARTIFACTS_DIR = self.original_artifacts_dir
        shutil.rmtree(self.test_dir)

    def test_end_to_end_pipeline(self):
        # 1. Generate price history for a single test product
        spec = {
            "name": "Test iPhone 15 Pro",
            "brand": "Apple",
            "category": "Smartphones",
            "msrp": 134900.0,
            "current_price": 112999.0,
            "depreciation_rate": 0.14
        }
        
        db_product = crud.create_product(
            self.db,
            name=spec["name"],
            brand=spec["brand"],
            category=spec["category"],
            msrp=spec["msrp"],
            current_price=spec["current_price"]
        )
        
        # Check product insertion
        self.assertEqual(db_product.name, "Test iPhone 15 Pro")
        self.assertEqual(db_product.msrp, 134900.0)
        
        # Generate 1 year of price history
        end_date = date(2026, 8, 15)
        start_date = end_date - timedelta(days=365)
        history = generator.generate_price_history(spec, start_date, end_date)
        
        self.assertEqual(len(history), 366) # 365 days + start day
        
        # Insert records into DB
        for record in history:
            crud.create_price_record(
                db=self.db,
                product_id=db_product.id,
                obs_date=record["date"],
                price=record["price"],
                discount_pct=record["discount_pct"],
                is_festival=record["is_festival_period"]
            )
            
        record_count = self.db.query(models.PriceRecord).filter(models.PriceRecord.product_id == db_product.id).count()
        self.assertEqual(record_count, 366)
        
        # 2. Extract data & Train models
        records = self.db.query(models.PriceRecord)\
                         .filter(models.PriceRecord.product_id == db_product.id)\
                         .order_by(models.PriceRecord.date.asc())\
                         .all()
                         
        data = []
        for r in records:
            data.append({
                "date": r.date,
                "price": r.price,
                "discount_pct": r.discount_pct,
                "is_festival_period": r.is_festival_period
            })
        import pandas as pd
        df = pd.DataFrame(data)
        
        # Run training
        metrics = pipeline.train_and_save_models(df, db_product.id, db_product.name)
        
        # Assert model files and metadata exist
        self.assertTrue(os.path.exists(os.path.join(pipeline.ARTIFACTS_DIR, f"model_p{db_product.id}_h7.json")))
        self.assertTrue(os.path.exists(os.path.join(pipeline.ARTIFACTS_DIR, f"model_p{db_product.id}_h30.json")))
        self.assertTrue(os.path.exists(os.path.join(pipeline.ARTIFACTS_DIR, f"model_p{db_product.id}_h60.json")))
        self.assertTrue(os.path.exists(os.path.join(pipeline.ARTIFACTS_DIR, f"metadata_p{db_product.id}.pickle")))
        
        # Check validation metrics structure
        for h in [7, 30, 60]:
            self.assertIn("rmse", metrics[h])
            self.assertIn("mape", metrics[h])
            self.assertGreaterEqual(metrics[h]["rmse"], 0)
            
        # 3. Test Forecaster
        # Inject our test database session and custom artifacts directory path in forecaster
        # For simplicity, forecaster references pipeline.ARTIFACTS_DIR, which we overridden above
        forecast_res = forecaster.get_forecast_results(self.db, db_product.id)
        
        self.assertIsNotNone(forecast_res)
        self.assertEqual(forecast_res["product_name"], "Test iPhone 15 Pro")
        self.assertEqual(forecast_res["current_price"], 112999.0)
        self.assertIn(forecast_res["verdict"], ["STRONG BUY", "BUY", "WAIT", "LOW CONFIDENCE"])
        self.assertIn("forecast", forecast_res)
        self.assertIn("7", forecast_res["forecast"])
        self.assertIn("30", forecast_res["forecast"])
        self.assertIn("60", forecast_res["forecast"])
        
        # Verify probabilities
        self.assertTrue(0.0 <= forecast_res["p_drop_30"] <= 1.0)
        self.assertTrue(0.0 <= forecast_res["risk_p_increase_30"] <= 1.0)
        self.assertGreaterEqual(forecast_res["confidence"], 0.0)
        
        print("End-to-End Pipeline test passed successfully!")

if __name__ == "__main__":
    unittest.main()
