import xgboost as xgb
import lightgbm as lgb
import catboost as cb
import pickle
import os
from app.core.settings import settings

# Global variables to store models
xgb_model = None
lgb_model = None
catboost_model = None


class XGBBoosterWrapper:
    """Wrapper to make xgb.Booster compatible with sklearn-style predict_proba."""
    
    def __init__(self, booster: xgb.Booster):
        self.booster = booster
    
    def predict_proba(self, X):
        """Return probabilities in sklearn format [P(0), P(1)]."""
        import numpy as np
        dmatrix = xgb.DMatrix(X)
        proba_1 = self.booster.predict(dmatrix)
        proba_0 = 1 - proba_1
        return np.column_stack([proba_0, proba_1])
    
    def predict(self, X):
        """Return class predictions."""
        import numpy as np
        proba = self.predict_proba(X)
        return (proba[:, 1] > 0.5).astype(int)


def load_models():
    """Load all ML models into memory."""
    global xgb_model, lgb_model, catboost_model
    
    try:
        # Load XGBoost model - try multiple methods
        xgb_path = settings.xgb_model_path
        print(f"Loading XGBoost model from: {xgb_path}")
        
        if xgb_path.endswith('.pkl') or xgb_path.endswith('.pickle'):
            # Load pickle format
            with open(xgb_path, 'rb') as f:
                xgb_model = pickle.load(f)
        else:
            # Try loading as Booster first (most reliable for .ubj, .json, .bin)
            try:
                booster = xgb.Booster()
                booster.load_model(xgb_path)
                xgb_model = XGBBoosterWrapper(booster)
                print("Loaded XGBoost as Booster with wrapper")
            except Exception as e1:
                print(f"Booster load failed: {e1}, trying XGBClassifier...")
                # Fallback to XGBClassifier
                xgb_model = xgb.XGBClassifier()
                xgb_model.load_model(xgb_path)
        
        # Load LightGBM model
        print(f"Loading LightGBM model from: {settings.lgb_model_path}")
        lgb_model = lgb.Booster(model_file=settings.lgb_model_path)

        # Load CatBoost model
        print(f"Loading CatBoost model from: {settings.catboost_model_path}")
        catboost_model = cb.CatBoostClassifier()
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
