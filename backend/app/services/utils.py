import numpy as np
from typing import List, Optional

def convert_nans(arr: np.ndarray) -> List[Optional[float]]:
    """Convert numpy array with NaNs to list of floats with None for NaN values."""
    return np.where(np.isnan(arr), None, arr.astype(float)).tolist()
