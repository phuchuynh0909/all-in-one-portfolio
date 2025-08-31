import numpy as np
from numba import njit, prange
import numba as nb

@njit(parallel=True)
def directional_change_nb(price, theta):
    T = np.full(price.shape, np.nan, dtype=np.int64)
    TMV = np.full(price.shape, np.nan, dtype=np.float64)
    Colors = np.full(price.shape, 0, dtype=np.int64)  # 1: Upward DCC, 2: Upward Overshoot, -1: Downward DCC, -2: Downward Overshoot
    Events = np.full(price.shape, 0, dtype=np.int64)  # 1: Upward DCC, 2: Upward Overshoot, -1: Downward DCC, -2: Downward Overshoot

    for col in nb.prange(price.shape[1]):
        # Set the initial event variable value
        ext_point_n = price[0, col]
        curr_event_max = price[0, col]
        curr_event_min = price[0, col]
        time_point_max = 0
        time_point_min = 0
        trend_status = 'up'
        curr_T = 0

        for t in range(0, price.shape[0]):
            TMV[t, col] = (price[t, col] - ext_point_n) / ext_point_n * theta
            T[t, col] = curr_T
            curr_T += 1

            if trend_status == 'up':
                Colors[t, col] = 2
                Events[t, col] = 2

                if price[t, col] < ((1 - theta) * curr_event_max):
                    trend_status = 'down'
                    curr_event_min = price[t, col]
                    ext_point_n = curr_event_max
                    curr_T = t - time_point_max
                    num_points_change = t - time_point_max
                    for j in range(1, num_points_change + 1):
                        Colors[t - j, col] = -1
                        Events[t - j, col] = -1
                else:
                    if price[t, col] > curr_event_max:
                        curr_event_max = price[t, col]
                        time_point_max = t
            else:
                Colors[t, col] = -2
                Events[t, col] = -2

                if price[t, col] > ((1 + theta) * curr_event_min):
                    trend_status = 'up'
                    curr_event_max = price[t, col]
                    ext_point_n = curr_event_min
                    curr_T = t - time_point_min
                    num_points_change = t - time_point_min
                    for j in range(1, num_points_change + 1):
                        Colors[t - j, col] = 1
                        Events[t - j, col] = 1
                else:
                    if price[t, col] < curr_event_min:
                        curr_event_min = price[t, col]
                        time_point_min = t
    
    return TMV, T