import xgboost as xgb
import lightgbm as lgb
import catboost as cb
from app.core.settings import settings

# Global variables to store models
xgb_model = None
lgb_model = None
catboost_model = None

def load_models():
    """Load all ML models into memory."""
    global xgb_model, lgb_model, catboost_model
    
    try:
        # Load XGBoost model
        xgb_model = xgb.XGBClassifier()
        # xgb_model.load_model(f"{settings.model_path}/xgboost_model_05_19_2025.ubj")
        xgb_model.load_model(settings.xgb_model_path)
        
        # Load LightGBM model
        # lgb_model = lgb.Booster(model_file=f"{settings.model_path}/lightgbm_model_05_19_2025.ubj")
        lgb_model = lgb.Booster(model_file=settings.lgb_model_path)

        # Load CatBoost model
        catboost_model = cb.CatBoostClassifier()
        # catboost_model.load_model(f"{settings.model_path}/catboost_model_05_19_2025.cbm")
        catboost_model.load_model(settings.catboost_model_path)
        
        print("Successfully loaded all ML models")
    except Exception as e:
        print(f"Error loading ML models: {e}")
        raise

def get_models():
    """Get the loaded ML models."""
    if None in (xgb_model, lgb_model, catboost_model):
        load_models()
    return xgb_model, lgb_model, catboost_model
