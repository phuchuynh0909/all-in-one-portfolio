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

# Meta-label model globals
_meta_xgb_model = None
_meta_lgb_model = None
_meta_catboost_model = None
_meta_scaler = None
_meta_feature_columns = None
_meta_prod_scale_pos_weight = None
_meta_prod_class_ratio = None
_meta_ensemble_weights = None
_meta_best_ensemble_name = None


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


def load_meta_models() -> None:
    """Load meta-label models (trained by train_meta_label_models.py)."""
    global _meta_xgb_model, _meta_lgb_model, _meta_catboost_model
    global _meta_scaler, _meta_feature_columns
    global _meta_prod_scale_pos_weight, _meta_prod_class_ratio
    global _meta_ensemble_weights, _meta_best_ensemble_name
    import json
    import joblib
    from pathlib import Path

    models_dir = Path(settings.model_path)

    def _latest(pattern):
        matches = sorted(models_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            raise FileNotFoundError(f"No file matching '{pattern}' in {models_dir}")
        return matches[0]

    xgb_path = _latest('xgboost_meta_*.ubj')
    lgb_path = _latest('lightgbm_meta_*.txt')
    cat_path = _latest('catboost_meta_*.cbm')
    scaler_path = _latest('meta_label_scaler_*.joblib')
    meta_path = _latest('meta_label_training_metadata_*.json')

    print(f"Loading meta XGBoost from {xgb_path.name}")
    _meta_xgb_model = xgb.XGBClassifier()
    _meta_xgb_model.load_model(str(xgb_path))

    print(f"Loading meta LightGBM from {lgb_path.name}")
    _meta_lgb_model = lgb.Booster(model_file=str(lgb_path))

    print(f"Loading meta CatBoost from {cat_path.name}")
    _meta_catboost_model = cb.CatBoostClassifier()
    _meta_catboost_model.load_model(str(cat_path))

    _meta_scaler = joblib.load(scaler_path)

    with open(meta_path) as f:
        metadata = json.load(f)
    _meta_feature_columns = metadata['feature_columns']
    _meta_prod_scale_pos_weight = metadata.get('production_scale_pos_weight', 1.0)
    _meta_prod_class_ratio = metadata.get('production_class_ratio', 1.0)
    _meta_ensemble_weights = metadata.get('ensemble_weights', {'XGBoost': 1/3, 'LightGBM': 1/3, 'CatBoost': 1/3})
    _meta_best_ensemble_name = metadata.get('best_ensemble_name', 'Ensemble: Val-AUC Weighted')

    print("Meta label models loaded successfully")


def get_meta_models():
    """Get meta-label models, scaler, feature columns, calibration params, and ensemble config."""
    if _meta_xgb_model is None:
        load_meta_models()
    return (
        _meta_xgb_model,
        _meta_lgb_model,
        _meta_catboost_model,
        _meta_scaler,
        _meta_feature_columns,
        _meta_prod_scale_pos_weight,
        _meta_prod_class_ratio,
        _meta_ensemble_weights,
    )
