// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
// Original Pine Script © jdehorty. OpenScript port for this application.
// ====================
// ==== Background ====
// ==================== 

// The filter line is a one-state Kalman estimate of the selected price source.
// Each bar, the filter compares price with its prediction. That difference is
// called the innovation, or "surprise." Larger surprises can cause the filter
// to raise its measurement-noise estimate and give the current price less
// weight.

// This is a price-chart adaptation of innovation-based filtering ideas from
// Sun et al. The paper's navigation model, covariance estimator, and noise
// update are not reproduced exactly here.


// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ⠀estimate
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀↓
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠋⠋⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠛⠟⠟⠟⠟⠟⠟⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀prediction⠀⠀⠀⠀⠀⠀⠀⠀⠀ ⠋⠟⠟⠟⠟⠟⠟⠟⠟⠟⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀price⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀↓⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀↓⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠁⠁⠁⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠁⠁⠁⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠋⠉⠉⠉⠉⠁⠉⠉⠋⠉⠁⠀⠀⠀⠀⠛⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠁⠀⠀⠀⠀⠉⠋⠛⠋⠋⠋⠛⠋⠋⠛⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠋⠉⠁⠁⠁⠁⠉⠉⠁⠁⠁⠁⠁⠋⠉⠁⠀⠛⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠛⠀⠁⠋⠛⠋⠋⠋⠋⠋⠋⠛⠋⠋⠋⠋⠋⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠋⠉⠁⠁⠁⠁⠁⠁⠉⠉⠁⠁⠁⠁⠁⠁⠉⠋⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠛⠋⠋⠋⠋⠋⠋⠋⠋⠛⠋⠋⠋⠋⠋⠋⠋⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠋⠉⠁⠁⠁⠁⠁⠁⠁⠁⠉⠉⠁⠁⠁⠁⠁⠁⠉⠟⠿⠿⠿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠟⠟⠋⠋⠋⠋⠋⠋⠋⠋⠛⠋⠋⠋⠋⠋⠋⠋⠋⠛⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠋⠉⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠉⠉⠁⠁⠁⠁⠁⠉⠟⠿⠟⠟⠿⠿⠿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠟⠟⠟⠿⠿⠋⠋⠋⠋⠋⠋⠋⠛⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠋⠉⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠉⠉⠁⠁⠁⠁⠋⠿⠿⠟⠟⠟⠟⠟⠿⠿⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠟⠟⠟⠟⠟⠟⠟⠿⠛⠋⠋⠋⠋⠋⠛⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠛⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠋⠉⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠉⠉⠁⠁⠉⠟⠿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠿⠟⠟⠟⠿⠿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠟⠋⠋⠋⠋⠛⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠛⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠋⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠉⠉⠉⠛⠿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⡿⡿⡿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠟⠋⠋⠛⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠋⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠉⠋⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠿⡿⡿⡿⡿⡿⠿⠿⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠟⠿⠿⠟⠛⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠛⠋⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
// ⠁⠁⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠁⠉⠉⠉⠋⠛⠛⠟⠟⠿⠿⠿⠟⠟⠟⠟⠟⠿⠿⠿⠿⠿⠿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⠿⠿⠿⠿⠿⠿⠟⠟⠟⠟⠟⠟⠿⠿⠟⠟⠟⠛⠛⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠋⠛⠛

// Figure 1. Calm bar (R = 1.0, K = 0.50). The blue estimate is where the pink prediction and the green price overlap. 
// It moves 50% of the way from the prediction toward price, and it is narrower than both, because two readings that 
// agree make the filter more sure than either one alone.



//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ estimate
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀↓
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀  ⠀prediction⠀⠀ ⠁⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀↘⠀⠉⠟⠟⠟⠟⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠟⠛⠟⠟⠛⠟⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠁⠉⠛⠛⠛⠟⠟⠛⠛⠟⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠉⠋⠟⠟⠟⠛⠟⠟⠛⠛⠛⠟⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠋⠟⠛⠛⠟⠟⠟⠟⠛⠛⠛⠛⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠉⠛⠟⠛⠛⠛⠟⠟⠟⠛⠛⠛⠛⠟⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠋⠉⠉⠉⠋⠟⠟⠛⠛⠛⠛⠟⠟⠛⠛⠛⠛⠛⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠉⠉⠉⠛⠟⠟⠛⠛⠛⠛⠟⠟⠟⠛⠛⠛⠛⠛⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠋⠉⠉⠉⠉⠋⠟⠟⠟⠛⠛⠛⠛⠟⠟⠟⠟⠛⠛⠛⠛⠟⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀price⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠉⠉⠉⠉⠟⠛⠟⠟⠛⠛⠛⠛⠟⠟⠛⠟⠟⠛⠛⠛⠛⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀↓⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠉⠉⠉⠉⠛⠛⠛⠟⠟⠛⠛⠛⠛⠟⠟⠛⠟⠟⠛⠛⠛⠛⠛⠛⠀⠀⠀⠀⠀⠀⠁⠁⠁⠁⠁⠉⠁⠁⠁⠁⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠉⠉⠛⠟⠛⠛⠟⠟⠛⠛⠛⠛⠟⠟⠛⠛⠟⠟⠛⠛⠛⠛⠟⠉⠁⠁⠉⠉⠉⠉⠉⠉⠉⠉⠋⠉⠉⠉⠉⠉⠉⠉⠁⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠉⠉⠋⠟⠛⠛⠛⠟⠟⠛⠛⠛⠛⠟⠟⠛⠛⠛⠟⠟⠛⠟⠟⠟⠟⠋⠉⠉⠉⠉⠉⠉⠉⠉⠉⠋⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠉⠉⠋⠟⠛⠛⠛⠛⠟⠟⠛⠛⠛⠛⠟⠟⠛⠛⠟⠟⠟⠟⠛⠛⠛⠛⠛⠉⠉⠉⠉⠉⠉⠉⠉⠉⠋⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠉⠉⠉⠉⠋⠛⠛⠛⠛⠛⠛⠟⠟⠛⠛⠛⠛⠟⠟⠟⠟⠟⠛⠛⠟⠛⠛⠛⠛⠛⠛⠉⠉⠉⠉⠉⠉⠉⠉⠋⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠉⠉⠉⠛⠟⠛⠛⠛⠛⠛⠛⠟⠟⠟⠟⠟⠟⠟⠟⠛⠛⠛⠛⠛⠛⠟⠟⠛⠛⠛⠛⠛⠋⠉⠉⠉⠉⠉⠉⠋⠁⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀⠀⠀⠀⠀⠀⠀⠁⠉⠉⠉⠉⠉⠉⠋⠛⠛⠛⠛⠛⠛⠟⠟⠟⠟⠟⠟⠛⠛⠛⠟⠟⠛⠛⠛⠛⠛⠛⠛⠛⠟⠟⠛⠛⠛⠛⠛⠉⠉⠉⠉⠉⠋⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
//⠀ ⠁⠁⠉⠉⠉⠉⠉⠋⠋⠛⠛⠟⠟⠟⠟⠟⠟⠟⠟⠟⠛⠛⠟⠟⠛⠛⠛⠛⠟⠟⠛⠛⠛⠛⠛⠛⠛⠛⠛⠛⠟⠟⠟⠟⠛⠟⠛⠋⠋⠉⠋⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁⠁⠁⠁⠁⠁

// *Figure 2.* Gate fired: R inflated by ρ·ν² (R = 6.5, K = 0.13). The price is the same, but this time the jump was big enough to trip the gate. The gate widens the green curve, so price is trusted less on this bar. The estimate moves only 13% of the way toward price and stays close to the prediction.

// ============================
// ==== Reading the Meters ====
// ============================

// The pane shows two meters, each from 0 to 100, on opposite sides of zero:
//
//   Surprise (above 0, blue): the share of recent surprise energy that is
//   above what the filter normally expects. 50 means twice the normal level,
//   75 means four times. It jumps at shocks and fades as they pass.
//
//   Damping (below 0, gray): how much less the line is reacting than it would
//   without the outlier gate. It only rises on bars that fail the gate (the
//   dots), then releases as the filter's noise estimate relaxes.
//
// "Normal" is the noise level the filter would carry if the gate never fired:
// the same slow update as R, without the inflation. Both meters are ratios of
// variances, so the scale is the same on every symbol.
//
// The readout on the price chart names the pattern the meters are showing
// (hover the name for what it means).
//
// Neither meter is a probability that the line is wrong, and neither is a
// trade signal.


version 1
study("Innovation Adaptive Kalman Filter Oscillator", precision = 0, range = [-100, 100])

smoothingPeriod = input(20, "Trend smoothness", min = 2, max = 200, step = 1)
noiseLookback = input(20, "Noise lookback", min = 2, max = 200, step = 1)
anomalyMultiple = input(2.05, "Unusual-move threshold", min = 1, max = 10, step = 0.05)
outlierRejection = input(0.05, "Damping strength", min = 0, max = 1, step = 0.05)

noiseDecay = 0.9
shiftStart = 30
anomalyThreshold = pow(anomalyMultiple, 2)
processNoiseRatio = pow(2 / (smoothingPeriod + 1), 2)

var estimate = none
var estimateVariance = 1
var measurementNoise = 0
var calmNoise = 0
var meanSurprise = 0
var surpriseVariance = 0
var processNoise = 0

if isNone(estimate)
    estimate = close
    firstMove = bar.index == 0 ? 0.0001 : pow(close - close[1], 2)
    seedNoise = firstMove > 0 ? firstMove : 0.0001
    surpriseVariance = seedNoise
    meanSurprise = 0
    measurementNoise = seedNoise
    calmNoise = seedNoise
    estimateVariance = seedNoise
    processNoise = seedNoise * processNoiseRatio

predictedEstimate = estimate
predictedVariance = estimateVariance + processNoise
surprise = close - predictedEstimate
centeredSurprise = surprise - meanSurprise
surpriseEnergy = pow(centeredSurprise, 2)
testStatistic = surpriseVariance > 0 ? surpriseEnergy / surpriseVariance : 0
isAnomaly = testStatistic > anomalyThreshold

statAlpha = 2 / (noiseLookback + 1)
meanSurprise = (1 - statAlpha) * meanSurprise + statAlpha * surprise
surpriseVariance = (1 - statAlpha) * surpriseVariance + statAlpha * surpriseEnergy

updatedNoise = measurementNoise
if isAnomaly
    updatedNoise = measurementNoise + outlierRejection * surpriseEnergy
else
    updatedNoise = (1 - noiseDecay) * surpriseVariance + noiseDecay * measurementNoise
measurementNoise = updatedNoise
calmNoise = (1 - noiseDecay) * surpriseVariance + noiseDecay * calmNoise

kalmanGain = predictedVariance / (predictedVariance + updatedNoise)
estimate = predictedEstimate + kalmanGain * surprise
estimateVariance = (1 - kalmanGain) * predictedVariance
processNoise = surpriseVariance * processNoiseRatio

surpriseRatio = surpriseVariance / max(calmNoise, 0.000000000001)
surpriseMeter = 100 * max(1 - 1 / surpriseRatio, 0)
calmGain = predictedVariance / (predictedVariance + calmNoise)
dampingMeter = 100 * max(1 - kalmanGain / calmGain, 0)

isUpsideAnomaly = isAnomaly and centeredSurprise > 0
isDownsideAnomaly = isAnomaly and centeredSurprise < 0

surpriseArea = plot(surpriseMeter, "Surprise Area", fade(#2AB2FF, 100), width = 1)
dampingArea = plot(-dampingMeter, "Damping Area", fade(#8B9194, 100), width = 1)
zeroLine = plot(0, "Zero", fade(#8B9194, 60), width = 1)
fill(surpriseArea, zeroLine, fade(#2AB2FF, 82))
fill(zeroLine, dampingArea, fade(#8B9194, 74))

plot(100, "Ceiling", fade(gray, 100), width = 1)
plot(-100, "Floor", fade(gray, 100), width = 1)
plot(shiftStart, "Shift Level (Surprise)", fade(#2AB2FF, 55), width = 1)
plot(-shiftStart, "Shift Level (Damping)", fade(#8B9194, 45), width = 1)
plot(surpriseMeter > 0 ? surpriseMeter : none, "Surprise Glow", fade(#2AB2FF, 82), width = 7)
plot(surpriseMeter, "Surprise", #2AB2FF, width = 2)
plot(-dampingMeter, "Damping", #8B9194, width = 2)

if isUpsideAnomaly
    signal("", #009988, at = "price", shape = "circle")
if isDownsideAnomaly
    signal("", #CC3311, at = "price", shape = "circle")
