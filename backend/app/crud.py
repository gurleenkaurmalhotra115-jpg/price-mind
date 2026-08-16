from sqlalchemy.orm import Session
from . import models
from datetime import date

def get_product(db: Session, product_id: int):
    return db.query(models.Product).filter(models.Product.id == product_id).first()

def get_product_by_name(db: Session, name: str):
    return db.query(models.Product).filter(models.Product.name == name).first()

def get_products(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Product).offset(skip).limit(limit).all()

def get_price_history(db: Session, product_id: int, limit: int = 365):
    return db.query(models.PriceRecord)\
             .filter(models.PriceRecord.product_id == product_id)\
             .order_by(models.PriceRecord.date.asc())\
             .limit(limit).all()

def create_product(db: Session, name: str, brand: str, category: str, msrp: float, current_price: float):
    db_product = models.Product(
        name=name,
        brand=brand,
        category=category,
        msrp=msrp,
        current_price=current_price
    )
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

def update_product_price(db: Session, product_id: int, new_price: float):
    db_product = get_product(db, product_id)
    if db_product:
        db_product.current_price = new_price
        db.commit()
        db.refresh(db_product)
    return db_product

def create_price_record(db: Session, product_id: int, obs_date: date, price: float, discount_pct: float, is_festival: bool):
    db_record = models.PriceRecord(
        product_id=product_id,
        date=obs_date,
        price=price,
        discount_pct=discount_pct,
        is_festival_period=is_festival
    )
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record

def create_alert(db: Session, product_id: int, target_price: float):
    db_alert = models.Alert(
        product_id=product_id,
        target_price=target_price,
        is_active=True
    )
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return db_alert

def get_active_alerts(db: Session):
    return db.query(models.Alert).filter(models.Alert.is_active == True).all()

def get_alerts_for_product(db: Session, product_id: int):
    return db.query(models.Alert).filter(models.Alert.product_id == product_id, models.Alert.is_active == True).all()

def deactivate_alert(db: Session, alert_id: int):
    db_alert = db.query(models.Alert).filter(models.Alert.id == alert_id).first()
    if db_alert:
        db_alert.is_active = False
        db.commit()
        db.refresh(db_alert)
    return db_alert
