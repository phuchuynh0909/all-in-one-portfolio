import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  Link,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { LineChart } from '@mui/x-charts/LineChart';

import { fetchCoveredWarrant, type CoveredWarrantResponse } from '../lib/services/cw';
import { useChartTheme } from '../theme';
import { PageContainer, PageHeader } from '../components/ui';


type Inputs = {
  stockPrice: number;
  warrantPrice: number;
  volatilityPct: number;
  riskFreePct: number;
  daysToExpiry: number;
};

type NullableNumber = number | null;

type ComputedMetrics = {
  optionStyle: 'call' | 'put';
  theoreticalPrice: NullableNumber;
  intrinsicValue: NullableNumber;
  timeValue: NullableNumber;
  delta: NullableNumber;
  gamma: NullableNumber;
  thetaPerDay: NullableNumber;
  vegaPer1PctVol: NullableNumber;
  rhoPer1PctRate: NullableNumber;
  moneynessPct: NullableNumber;
  breakEvenStockPrice: NullableNumber;
  premiumToBreakEvenPct: NullableNumber;
  leverage: NullableNumber;
  effectiveGearing: NullableNumber;
  theoreticalEdgePct: NullableNumber;
  parityPriceRatio: NullableNumber;
  inTheMoney: boolean | null;
  summary: string;
};

type ExpiryScenario = {
  key: string;
  label: string;
  movePct: number | null;
  stockPriceAtExpiry: number;
  payoffPerWarrant: number;
  pnlPerWarrant: number;
  returnPct: number | null;
};

type PayoffPoint = {
  stockPrice: number;
  payoffPerWarrant: number;
  pnlPerWarrant: number;
};

const DEFAULT_SYMBOL = 'CHPG2518';
const VOL_LOOKBACK_DAYS = 90;
const DEFAULT_SCENARIO_MOVES = [5, 3, 0, -2, -5];

const greekDescriptions = {
  theoreticalPrice: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Theoretical Price — Giá lý thuyết</Typography>
      <Typography variant="body2">Theoretical Price là giá trị ước tính của CW theo mô hình định giá Black-Scholes.</Typography>
      <Typography variant="body2">Nó phản ánh giá hợp lý dựa trên giá cổ phiếu cơ sở, giá thực hiện, thời gian còn lại, lãi suất và biến động.</Typography>
      <Typography variant="body2">Nếu giá thị trường thấp hơn nhiều giá lý thuyết, CW có thể đang bị định giá thấp.</Typography>
      <Typography variant="body2">Nếu giá thị trường cao hơn nhiều giá lý thuyết, CW có thể đang bị định giá cao.</Typography>
      <Typography variant="body2">Đây là giá tham chiếu mô hình, không phải giá chắc chắn thị trường sẽ giao dịch.</Typography>
    </>
  ),
  delta: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Delta (Δ) — Độ nhạy giá</Typography>
      <Typography variant="body2">Delta đo lường mức thay đổi giá chứng quyền khi giá tài sản cơ sở thay đổi 1 đơn vị.</Typography>
      <Typography variant="body2">Delta dao động từ 0 đến 1 với call warrant hoặc từ -1 đến 0 với put warrant.</Typography>
      <Typography variant="body2">Ví dụ: Delta = 0.6, giá cổ phiếu tăng 1.000đ thì giá CW tăng khoảng 600đ.</Typography>
      <Typography variant="body2">Delta gần 1: CW deep in-the-money. Delta gần 0: CW deep out-of-the-money.</Typography>
    </>
  ),
  gamma: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Gamma (Γ) — Tốc độ thay đổi của Delta</Typography>
      <Typography variant="body2">Gamma đo lường mức thay đổi của Delta khi giá tài sản cơ sở thay đổi 1 đơn vị.</Typography>
      <Typography variant="body2">Gamma luôn dương với cả call và put warrant.</Typography>
      <Typography variant="body2">Gamma cao nhất khi CW ở trạng thái at-the-money.</Typography>
      <Typography variant="body2">Gamma cao làm Delta đổi nhanh hơn, CW biến động mạnh hơn.</Typography>
      <Typography variant="body2">Càng gần ngày đáo hạn, Gamma càng lớn và rủi ro càng cao.</Typography>
    </>
  ),
  theta: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Theta (Θ) — Sự bào mòn thời gian</Typography>
      <Typography variant="body2">Theta đo lường mức giảm giá trị của CW sau mỗi ngày trôi qua.</Typography>
      <Typography variant="body2">Theta luôn âm, thời gian là yếu tố bất lợi cho người mua CW.</Typography>
      <Typography variant="body2">Ví dụ: Theta = -50 nghĩa là mỗi ngày CW mất khoảng 50đ nếu các yếu tố khác giữ nguyên.</Typography>
      <Typography variant="body2">Theta tăng nhanh khi gần ngày đáo hạn, và CW at-the-money chịu ảnh hưởng mạnh nhất.</Typography>
    </>
  ),
  vega: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Vega (ν) — Độ nhạy với biến động ngầm định</Typography>
      <Typography variant="body2">Vega đo lường mức thay đổi giá CW khi implied volatility thay đổi 1%.</Typography>
      <Typography variant="body2">Vega không phải chữ cái Hy Lạp nhưng vẫn được xếp cùng nhóm Greeks.</Typography>
      <Typography variant="body2">Vega luôn dương với cả call và put warrant.</Typography>
      <Typography variant="body2">IV tăng thì giá CW tăng ngay cả khi giá cổ phiếu đứng yên.</Typography>
      <Typography variant="body2">CW at-the-money và còn nhiều thời gian thường có Vega cao nhất.</Typography>
    </>
  ),
  rho: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Rho (ρ) — Độ nhạy với lãi suất</Typography>
      <Typography variant="body2">Rho đo lường mức thay đổi giá CW khi lãi suất phi rủi ro thay đổi 1%.</Typography>
      <Typography variant="body2">Với call warrant, Rho thường dương: lãi suất tăng thì giá CW có xu hướng tăng.</Typography>
      <Typography variant="body2">Với put warrant, Rho thường âm: lãi suất tăng thì giá CW có xu hướng giảm.</Typography>
      <Typography variant="body2">Rho thường nhỏ hơn Delta và Vega, nên trong ngắn hạn ảnh hưởng thường yếu hơn.</Typography>
      <Typography variant="body2">CW còn nhiều thời gian đến đáo hạn sẽ nhạy với Rho hơn CW sắp hết hạn.</Typography>
    </>
  ),
} as const;

const analysisDescriptions = {
  summary: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>CW Analysis — Tóm tắt nhanh</Typography>
      <Typography variant="body2">Đây là phần tóm tắt định tính của CW theo góc nhìn định giá và payoff.</Typography>
      <Typography variant="body2"><strong>OTM</strong> nghĩa là call CW đang out-of-the-money, giá cổ phiếu cơ sở hiện tại còn thấp hơn giá thực hiện K.</Typography>
      <Typography variant="body2"><strong>Leverage</strong> là đòn bẩy danh nghĩa, cho biết với số tiền mua 1 CW bạn đang kiểm soát tương đương bao nhiêu giá trị cổ phiếu cơ sở.</Typography>
      <Typography variant="body2"><strong>Effective gearing</strong> là đòn bẩy thực tế hơn vì đã tính thêm Delta; nếu cổ phiếu tăng 1% thì giá CW kỳ vọng tăng khoảng mức này, khi các yếu tố khác không đổi.</Typography>
      <Typography variant="body2"><strong>Above theoretical value</strong> nghĩa là giá thị trường hiện tại cao hơn giá lý thuyết theo mô hình, tức CW đang bị định giá đắt theo giả định hiện tại.</Typography>
      <Typography variant="body2"><strong>Break-even premium</strong> là mức % tăng thêm của cổ phiếu cơ sở cần đạt để CW hòa vốn tại ngày đáo hạn.</Typography>
    </>
  ),
  intrinsicValue: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Intrinsic Value — Giá trị nội tại</Typography>
      <Typography variant="body2">Với call CW, đây là giá trị nhận được nếu đáo hạn ngay bây giờ: <code>max(S - K, 0) / conversionRate</code>.</Typography>
      <Typography variant="body2">Nếu chỉ số này bằng 0, CW hiện chưa có giá trị thực nhận tại đáo hạn và toàn bộ giá đang trả là cho kỳ vọng tương lai.</Typography>
    </>
  ),
  timeValue: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Time Value — Giá trị thời gian</Typography>
      <Typography variant="body2">Đây là phần giá CW vượt lên trên giá trị nội tại.</Typography>
      <Typography variant="body2">Nếu intrinsic value bằng 0 mà time value vẫn dương, nghĩa là bạn đang trả tiền cho xác suất cổ phiếu tăng trong tương lai trước ngày đáo hạn.</Typography>
    </>
  ),
  moneyness: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Moneyness — Độ lệch so với strike</Typography>
      <Typography variant="body2">Moneyness cho biết giá cổ phiếu cơ sở hiện tại đang cao hơn hay thấp hơn giá thực hiện.</Typography>
      <Typography variant="body2">Âm nghĩa là call CW đang OTM; dương nghĩa là đã ITM.</Typography>
      <Typography variant="body2">Ví dụ <code>-9.62%</code> nghĩa là cổ phiếu hiện vẫn thấp hơn strike khoảng 9.62%.</Typography>
    </>
  ),
  breakEvenStock: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Break-even Stock — Giá cổ phiếu hòa vốn</Typography>
      <Typography variant="body2">Đây là mức giá cổ phiếu cần đạt tại ngày đáo hạn để nhà đầu tư vừa đủ hòa vốn sau khi trừ chi phí mua CW hiện tại.</Typography>
      <Typography variant="body2">Nếu cổ phiếu kết thúc dưới mức này, vị thế CW vẫn lỗ tại đáo hạn.</Typography>
    </>
  ),
  breakEvenPremium: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Break-even Premium — Premium để hòa vốn</Typography>
      <Typography variant="body2">Đây là tỷ lệ tăng thêm mà cổ phiếu cơ sở phải đạt từ mức hiện tại để lên tới giá hòa vốn tại đáo hạn.</Typography>
      <Typography variant="body2">Số này càng cao thì kỳ vọng tăng giá cần thiết càng lớn.</Typography>
    </>
  ),
  leverage: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Leverage — Đòn bẩy danh nghĩa</Typography>
      <Typography variant="body2">Leverage cho biết với số tiền mua CW, bạn đang kiểm soát tương đương bao nhiêu giá trị cổ phiếu cơ sở.</Typography>
      <Typography variant="body2">Leverage cao không đồng nghĩa chắc chắn tốt; thường nó đi cùng CW giá rẻ nhưng rủi ro cũng cao hơn.</Typography>
    </>
  ),
  effectiveGearing: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Effective Gearing — Đòn bẩy thực tế</Typography>
      <Typography variant="body2">Đây là đòn bẩy đã điều chỉnh theo Delta nên phản ánh sát hơn độ nhạy ngắn hạn của CW.</Typography>
      <Typography variant="body2">Ví dụ effective gearing 5.47x nghĩa là nếu cổ phiếu tăng 1% thì CW có thể tăng khoảng 5.47%, nếu các yếu tố khác giữ nguyên.</Typography>
    </>
  ),
  theoreticalEdge: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Theoretical Edge — Chênh lệch với giá lý thuyết</Typography>
      <Typography variant="body2">Chỉ số này so sánh giá lý thuyết theo mô hình với giá thị trường hiện tại.</Typography>
      <Typography variant="body2">Âm nghĩa là giá lý thuyết thấp hơn giá thị trường, tức CW đang đắt theo giả định hiện tại.</Typography>
      <Typography variant="body2">Dương nghĩa là CW đang rẻ hơn giá lý thuyết.</Typography>
    </>
  ),
  parityRatio: (
    <>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Parity Ratio — Tỷ lệ so với giá trị nội tại</Typography>
      <Typography variant="body2">Tỷ lệ này so sánh giá CW với giá trị nội tại của nó.</Typography>
      <Typography variant="body2">Chỉ số chỉ có ý nghĩa khi CW đã in-the-money.</Typography>
      <Typography variant="body2">Nếu intrinsic value bằng 0 thì parity ratio không tính được và hiển thị N/A.</Typography>
    </>
  ),
} as const;

function safeNumber(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function fmtNumber(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  const absValue = Math.abs(value);
  if (absValue > 0 && absValue < Math.pow(10, -digits)) {
    return value.toExponential(4);
  }
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  return `${(value * 100).toFixed(digits)}%`;
}

function fmtSignedPct0(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  const pct = Math.round(value * 100);
  return `${pct > 0 ? '+' : ''}${pct}%`;
}

function fmtQuoteToVnd(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  const vnd = Math.round(value * 1000);
  return `${vnd.toLocaleString('vi-VN')}đ`;
}

function fmtSignedQuoteToVnd(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  const sign = value > 0 ? '+' : '';
  return `${sign}${fmtQuoteToVnd(value)}`;
}

function normalPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

function erfApprox(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return sign * y;
}

function normalCdf(x: number): number {
  return 0.5 * (1 + erfApprox(x / Math.sqrt(2)));
}

function computeMetrics(data: CoveredWarrantResponse | null, inputs: Inputs): ComputedMetrics | null {
  if (!data) {
    return null;
  }

  const optionStyle = data.greeks.option_style;
  const strike = data.detail.exercise_price;
  const conversionRate = data.detail.conversion_rate;
  const stockPrice = inputs.stockPrice > 0 ? inputs.stockPrice : null;
  const warrantPrice = inputs.warrantPrice > 0 ? inputs.warrantPrice : null;
  const volatility = inputs.volatilityPct > 0 ? inputs.volatilityPct / 100 : null;
  const riskFreeRate = inputs.riskFreePct / 100;
  const timeToExpiry = Math.max(inputs.daysToExpiry / 365, 1 / 365);

  let theoreticalPrice: NullableNumber = null;
  let intrinsicValue: NullableNumber = null;
  let timeValue: NullableNumber = null;
  let delta: NullableNumber = null;
  let gamma: NullableNumber = null;
  let thetaPerDay: NullableNumber = null;
  let vegaPer1PctVol: NullableNumber = null;
  let rhoPer1PctRate: NullableNumber = null;
  let moneynessPct: NullableNumber = null;
  let breakEvenStockPrice: NullableNumber = null;
  let premiumToBreakEvenPct: NullableNumber = null;
  let leverage: NullableNumber = null;
  let effectiveGearing: NullableNumber = null;
  let theoreticalEdgePct: NullableNumber = null;
  let parityPriceRatio: NullableNumber = null;
  let inTheMoney: boolean | null = null;

  if (stockPrice && strike && strike > 0) {
    if (optionStyle === 'call') {
      intrinsicValue = Math.max(stockPrice - strike, 0);
      inTheMoney = stockPrice > strike;
      moneynessPct = stockPrice / strike - 1;
    } else {
      intrinsicValue = Math.max(strike - stockPrice, 0);
      inTheMoney = stockPrice < strike;
      moneynessPct = strike / stockPrice - 1;
    }

    if (conversionRate && conversionRate > 0) {
      intrinsicValue /= conversionRate;
    }
  }

  if (
    stockPrice &&
    strike &&
    conversionRate &&
    stockPrice > 0 &&
    strike > 0 &&
    conversionRate > 0 &&
    volatility &&
    volatility > 0
  ) {
    const sqrtT = Math.sqrt(timeToExpiry);
    const d1 = (
      Math.log(stockPrice / strike) +
      (riskFreeRate + 0.5 * volatility * volatility) * timeToExpiry
    ) / (volatility * sqrtT);
    const d2 = d1 - volatility * sqrtT;

    if (optionStyle === 'call') {
      const sharePrice = stockPrice * normalCdf(d1) - strike * Math.exp(-riskFreeRate * timeToExpiry) * normalCdf(d2);
      theoreticalPrice = sharePrice / conversionRate;
      delta = normalCdf(d1) / conversionRate;
      thetaPerDay = (
        (-(stockPrice * normalPdf(d1) * volatility) / (2 * sqrtT)) -
        riskFreeRate * strike * Math.exp(-riskFreeRate * timeToExpiry) * normalCdf(d2)
      ) / conversionRate / 365;
      rhoPer1PctRate = strike * timeToExpiry * Math.exp(-riskFreeRate * timeToExpiry) * normalCdf(d2) / conversionRate / 100;
    } else {
      const sharePrice = strike * Math.exp(-riskFreeRate * timeToExpiry) * normalCdf(-d2) - stockPrice * normalCdf(-d1);
      theoreticalPrice = sharePrice / conversionRate;
      delta = (normalCdf(d1) - 1) / conversionRate;
      thetaPerDay = (
        (-(stockPrice * normalPdf(d1) * volatility) / (2 * sqrtT)) +
        riskFreeRate * strike * Math.exp(-riskFreeRate * timeToExpiry) * normalCdf(-d2)
      ) / conversionRate / 365;
      rhoPer1PctRate = -strike * timeToExpiry * Math.exp(-riskFreeRate * timeToExpiry) * normalCdf(-d2) / conversionRate / 100;
    }

    gamma = normalPdf(d1) / (stockPrice * volatility * sqrtT) / conversionRate;
    vegaPer1PctVol = stockPrice * normalPdf(d1) * sqrtT / conversionRate / 100;
  }

  if (warrantPrice && intrinsicValue !== null) {
    timeValue = warrantPrice - intrinsicValue;
    if (intrinsicValue > 0) {
      parityPriceRatio = warrantPrice / intrinsicValue;
    }
  }

  if (warrantPrice && conversionRate && strike) {
    if (optionStyle === 'call') {
      breakEvenStockPrice = strike + warrantPrice * conversionRate;
      if (stockPrice) {
        premiumToBreakEvenPct = breakEvenStockPrice / stockPrice - 1;
      }
    } else {
      breakEvenStockPrice = strike - warrantPrice * conversionRate;
      if (stockPrice) {
        premiumToBreakEvenPct = 1 - breakEvenStockPrice / stockPrice;
      }
    }
  }

  if (stockPrice && warrantPrice && conversionRate && warrantPrice > 0 && conversionRate > 0) {
    leverage = stockPrice / (warrantPrice * conversionRate);
    if (delta !== null) {
      effectiveGearing = Math.abs(delta) * stockPrice / warrantPrice;
    }
    if (theoreticalPrice !== null) {
      theoreticalEdgePct = theoreticalPrice / warrantPrice - 1;
    }
  }

  const summaryParts: string[] = [];
  if (inTheMoney !== null) {
    summaryParts.push(inTheMoney ? 'ITM' : 'OTM');
  }
  if (leverage !== null) {
    summaryParts.push(`leverage ${leverage.toFixed(2)}x`);
  }
  if (effectiveGearing !== null) {
    summaryParts.push(`effective gearing ${effectiveGearing.toFixed(2)}x`);
  }
  if (theoreticalEdgePct !== null) {
    if (theoreticalEdgePct > 0.05) {
      summaryParts.push('below theoretical value');
    } else if (theoreticalEdgePct < -0.05) {
      summaryParts.push('above theoretical value');
    } else {
      summaryParts.push('near theoretical value');
    }
  }
  if (premiumToBreakEvenPct !== null) {
    summaryParts.push(`break-even premium ${(premiumToBreakEvenPct * 100).toFixed(2)}%`);
  }

  return {
    optionStyle,
    theoreticalPrice,
    intrinsicValue,
    timeValue,
    delta,
    gamma,
    thetaPerDay,
    vegaPer1PctVol,
    rhoPer1PctRate,
    moneynessPct,
    breakEvenStockPrice,
    premiumToBreakEvenPct,
    leverage,
    effectiveGearing,
    theoreticalEdgePct,
    parityPriceRatio,
    inTheMoney,
    summary: summaryParts.join(', ') || 'Insufficient pricing inputs for full CW analysis',
  };
}

function GreekTitle(props: { label: string; tooltip: React.ReactNode }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        {props.label}
      </Typography>
      <Tooltip
        title={
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, maxWidth: 360, p: 0.5 }}>
            {props.tooltip}
          </Box>
        }
        arrow
      >
        <InfoOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary', cursor: 'help' }} />
      </Tooltip>
    </Box>
  );
}

function InlineTooltipLabel(props: { label: string; tooltip: React.ReactNode }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.75 }}>
      <strong>{props.label}</strong>
      <Tooltip
        title={
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, maxWidth: 360, p: 0.5 }}>
            {props.tooltip}
          </Box>
        }
        arrow
      >
        <InfoOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary', cursor: 'help' }} />
      </Tooltip>
    </Box>
  );
}

function StatCard(props: { title: React.ReactNode; value: string; subtitle?: string }) {
  return (
    <Paper
      sx={{
        p: 2,
        height: '100%',
        bgcolor: 'surface.default',
        border: 1,
        borderColor: 'line.subtle',
      }}
    >
      <Box sx={{ mb: 1 }}>
        {props.title}
      </Box>
      <Typography variant="h5" sx={{ fontWeight: 700, lineHeight: 1.1 }}>
        {props.value}
      </Typography>
      {props.subtitle ? (
        <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 1 }}>
          {props.subtitle}
        </Typography>
      ) : null}
    </Paper>
  );
}

function buildExpiryScenarios(
  data: CoveredWarrantResponse | null,
  inputs: Inputs,
  fixedPriceInputs: string[],
): ExpiryScenario[] {
  if (!data) {
    return [];
  }

  const stockPrice = inputs.stockPrice > 0 ? inputs.stockPrice : null;
  const warrantPrice = inputs.warrantPrice > 0 ? inputs.warrantPrice : null;
  const strike = data.detail.exercise_price;
  const conversionRate = data.detail.conversion_rate;
  const baseSymbol = data.detail.base_stock_code || 'CPCS';
  const optionStyle = data.greeks.option_style;

  if (!stockPrice || !warrantPrice || !strike || !conversionRate || conversionRate <= 0) {
    return [];
  }

  const moveScenarios = DEFAULT_SCENARIO_MOVES.map((movePct) => {
    const stockPriceAtExpiry = stockPrice * (1 + movePct / 100);
    const intrinsicShareValue = optionStyle === 'call'
      ? Math.max(stockPriceAtExpiry - strike, 0)
      : Math.max(strike - stockPriceAtExpiry, 0);
    const payoffPerWarrant = intrinsicShareValue / conversionRate;
    const pnlPerWarrant = payoffPerWarrant - warrantPrice;
    return {
      key: `move_${movePct}`,
      label: movePct > 0 ? `${baseSymbol} +${movePct}%` : movePct < 0 ? `${baseSymbol} ${movePct}%` : `${baseSymbol} giữ nguyên`,
      movePct,
      stockPriceAtExpiry,
      payoffPerWarrant,
      pnlPerWarrant,
      returnPct: warrantPrice > 0 ? pnlPerWarrant / warrantPrice : null,
    };
  });

  const fixedPriceScenarios = fixedPriceInputs
    .map((rawValue, index) => ({ rawValue, index, price: Number(rawValue) }))
    .filter((item) => Number.isFinite(item.price) && item.price > 0)
    .map((item) => {
      const stockPriceAtExpiry = item.price;
      const intrinsicShareValue = optionStyle === 'call'
        ? Math.max(stockPriceAtExpiry - strike, 0)
        : Math.max(strike - stockPriceAtExpiry, 0);
      const payoffPerWarrant = intrinsicShareValue / conversionRate;
      const pnlPerWarrant = payoffPerWarrant - warrantPrice;
      return {
        key: `fixed_${item.index}_${stockPriceAtExpiry}`,
        label: `${baseSymbol} giá nhập tay`,
        movePct: stockPrice > 0 ? (stockPriceAtExpiry / stockPrice - 1) * 100 : null,
        stockPriceAtExpiry,
        payoffPerWarrant,
        pnlPerWarrant,
        returnPct: warrantPrice > 0 ? pnlPerWarrant / warrantPrice : null,
      };
    });

  const atmPayoff = 0;
  const atmPnl = -warrantPrice;
  const atmScenario: ExpiryScenario = {
    key: 'atm',
    label: `${baseSymbol} về K (ATM)`,
    movePct: stockPrice > 0 ? (strike / stockPrice - 1) * 100 : null,
    stockPriceAtExpiry: strike,
    payoffPerWarrant: atmPayoff,
    pnlPerWarrant: atmPnl,
    returnPct: warrantPrice > 0 ? atmPnl / warrantPrice : null,
  };

  return [...moveScenarios, ...fixedPriceScenarios, atmScenario];
}

function buildPayoffCurve(data: CoveredWarrantResponse | null, inputs: Inputs): PayoffPoint[] {
  if (!data) {
    return [];
  }

  const stockPrice = inputs.stockPrice > 0 ? inputs.stockPrice : null;
  const warrantPrice = inputs.warrantPrice > 0 ? inputs.warrantPrice : null;
  const strike = data.detail.exercise_price;
  const conversionRate = data.detail.conversion_rate;
  const optionStyle = data.greeks.option_style;

  if (!stockPrice || !warrantPrice || !strike || !conversionRate || conversionRate <= 0) {
    return [];
  }

  const minPrice = Math.max(0.01, Math.min(stockPrice * 0.75, strike * 0.85));
  const maxPrice = Math.max(stockPrice * 1.25, strike * 1.15);
  const steps = 48;
  const points: PayoffPoint[] = [];

  for (let i = 0; i <= steps; i += 1) {
    const stockPriceAtExpiry = minPrice + ((maxPrice - minPrice) * i) / steps;
    const intrinsicShareValue = optionStyle === 'call'
      ? Math.max(stockPriceAtExpiry - strike, 0)
      : Math.max(strike - stockPriceAtExpiry, 0);
    const payoffPerWarrant = intrinsicShareValue / conversionRate;
    points.push({
      stockPrice: stockPriceAtExpiry,
      payoffPerWarrant,
      pnlPerWarrant: payoffPerWarrant - warrantPrice,
    });
  }

  return points;
}

export default function CWPage() {
  const ct = useChartTheme();
  const [symbolInput, setSymbolInput] = useState(DEFAULT_SYMBOL);
  const [currentSymbol, setCurrentSymbol] = useState(DEFAULT_SYMBOL);
  const [data, setData] = useState<CoveredWarrantResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inputs, setInputs] = useState<Inputs>({
    stockPrice: 0,
    warrantPrice: 0,
    volatilityPct: 0,
    riskFreePct: 4.5,
    daysToExpiry: 0,
  });
  const [fixedPriceInputs, setFixedPriceInputs] = useState<string[]>(['']);

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const response = await fetchCoveredWarrant(currentSymbol);
        setData(response);
        setInputs({
          stockPrice: safeNumber(response.assumptions.stock_price),
          warrantPrice: safeNumber(response.assumptions.warrant_price),
          volatilityPct: safeNumber(response.assumptions.annual_volatility) * 100,
          riskFreePct: safeNumber(response.assumptions.risk_free_rate) * 100,
          daysToExpiry: response.assumptions.days_to_expiry,
        });
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load CW detail');
        setData(null);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [currentSymbol]);

  const metrics = computeMetrics(data, inputs);
  const expiryScenarios = buildExpiryScenarios(data, inputs, fixedPriceInputs);
  const payoffCurve = buildPayoffCurve(data, inputs);
  const expiryDateLabel = data?.detail.last_trading_date
    ? new Date(data.detail.last_trading_date).toLocaleDateString('vi-VN')
    : 'N/A';

  const handleAnalyze = () => {
    const normalized = symbolInput.trim().toUpperCase();
    if (!normalized) {
      return;
    }
    setCurrentSymbol(normalized);
  };

  const updateInput = (key: keyof Inputs) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const next = Number(event.target.value);
    setInputs((prev) => ({
      ...prev,
      [key]: Number.isFinite(next) ? next : 0,
    }));
  };

  const updateFixedPriceInput = (index: number) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const nextValue = event.target.value;
    setFixedPriceInputs((prev) => prev.map((value, idx) => (idx === index ? nextValue : value)));
  };

  const addFixedPriceRow = () => {
    setFixedPriceInputs((prev) => [...prev, '']);
  };

  const removeFixedPriceRow = (index: number) => {
    setFixedPriceInputs((prev) => {
      if (prev.length === 1) {
        return [''];
      }
      return prev.filter((_, idx) => idx !== index);
    });
  };

  return (
    <PageContainer>
      <PageHeader
        title="Warrants"
        description="Fetch CW contract details from DNSE, pull the underlying close from the backend, estimate realized volatility, then compute Black-Scholes greeks and warrant metrics under editable assumptions."
        actions={
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'stretch', sm: 'center' }}>
              <TextField
                label="CW Symbol"
                value={symbolInput}
                onChange={(event) => setSymbolInput(event.target.value.toUpperCase())}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    handleAnalyze();
                  }
                }}
                sx={{ minWidth: 220 }}
              />
              <Button variant="contained" onClick={handleAnalyze} sx={{ minWidth: 140 }}>
                Analyze
              </Button>
          </Stack>
        }
      />

      <Stack spacing={2}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress />
          </Box>
        ) : null}

        {error ? <Alert severity="error">{error}</Alert> : null}

        {data && metrics ? (
          <>
            <Grid container spacing={2}>
              <Grid item xs={12} md={8}>
                <Paper sx={{ p: 3, height: '100%' }}>
                  <Stack spacing={2}>
                    <Box>
                      <Typography variant="h5" sx={{ fontWeight: 700 }}>
                        {data.detail.symbol}
                      </Typography>
                      <Typography variant="body1" sx={{ color: 'text.secondary', mt: 0.5 }}>
                        {data.detail.stock_name || 'Covered warrant'}
                      </Typography>
                    </Box>
                    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                      <Chip label={`Base stock: ${data.detail.base_stock_code || 'N/A'}`} color="primary" variant="outlined" />
                      <Chip label={`Style: ${metrics.optionStyle.toUpperCase()}`} color={metrics.optionStyle === 'call' ? 'success' : 'warning'} />
                      <Chip label={`CW type: ${data.detail.cw_stock_type || 'N/A'}`} variant="outlined" />
                      <Chip label={`Days to expiry: ${inputs.daysToExpiry}`} variant="outlined" />
                    </Stack>
                    <Divider />
                    <Grid container spacing={2}>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title="Exercise Price" value={fmtNumber(data.detail.exercise_price)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title="Conversion Rate" value={fmtNumber(data.detail.conversion_rate, 4)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title="Base Stock Price" value={fmtNumber(inputs.stockPrice)} subtitle={data.assumptions.underlying_price_source} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title="Warrant Price" value={fmtNumber(inputs.warrantPrice)} subtitle={data.assumptions.warrant_price_source} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard
                          title="IV (Used)"
                          value={`${fmtNumber(inputs.volatilityPct)}%`}
                          subtitle={data.assumptions.volatility_source}
                        />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard
                          title={`Hist Vol (${VOL_LOOKBACK_DAYS}d)`}
                          value={
                            typeof data.assumptions.hist_vol === 'number' && Number.isFinite(data.assumptions.hist_vol)
                              ? `${fmtNumber(data.assumptions.hist_vol * 100)}%`
                              : 'N/A'
                          }
                          subtitle={
                            typeof data.assumptions.hist_vol === 'number' && Number.isFinite(data.assumptions.hist_vol) && inputs.volatilityPct > 0
                              ? (() => {
                                  const diff = inputs.volatilityPct / 100 - data.assumptions.hist_vol;
                                  const sign = diff > 0 ? '+' : '';
                                  return `IV ${sign}${fmtNumber(diff * 100)}% vs hist`;
                                })()
                              : undefined
                          }
                        />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title="Risk-Free Rate" value={`${fmtNumber(inputs.riskFreePct)}%`} />
                      </Grid>
                    </Grid>
                  </Stack>
                </Paper>
              </Grid>
              <Grid item xs={12} md={4}>
                <Paper sx={{ p: 3, height: '100%' }}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                    Contract Detail
                  </Typography>
                  <Stack spacing={1.25}>
                    <Typography variant="body2"><strong>Underlying:</strong> {data.detail.base_stock_name || 'N/A'}</Typography>
                    <Typography variant="body2"><strong>Issuer:</strong> {data.detail.issuer_name || 'N/A'}</Typography>
                    <Typography variant="body2"><strong>Period:</strong> {data.detail.period || 'N/A'}</Typography>
                    <Typography variant="body2"><strong>Listing:</strong> {data.detail.listing_date ? new Date(data.detail.listing_date).toLocaleDateString() : 'N/A'}</Typography>
                    <Typography variant="body2"><strong>Last Trading:</strong> {data.detail.last_trading_date ? new Date(data.detail.last_trading_date).toLocaleDateString() : 'N/A'}</Typography>
                    <Typography variant="body2"><strong>Total Volume:</strong> {fmtNumber(data.detail.total_vol, 0)}</Typography>
                    <Typography variant="body2"><strong>Total Value:</strong> {fmtNumber(data.detail.total_val, 0)}</Typography>
                    {data.detail.source_url ? (
                      <Link href={data.detail.source_url} target="_blank" rel="noreferrer" underline="hover">
                        Open DNSE source
                      </Link>
                    ) : null}
                  </Stack>
                </Paper>
              </Grid>
            </Grid>

            <Paper sx={{ p: 3 }}>
              <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                Editable Assumptions
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6} md={2.4}>
                  <TextField fullWidth label="Stock Price" type="number" value={inputs.stockPrice} onChange={updateInput('stockPrice')} />
                </Grid>
                <Grid item xs={12} sm={6} md={2.4}>
                  <TextField fullWidth label="CW Price" type="number" value={inputs.warrantPrice} onChange={updateInput('warrantPrice')} />
                </Grid>
                <Grid item xs={12} sm={6} md={2.4}>
                  <TextField fullWidth label="Volatility %" type="number" value={inputs.volatilityPct} onChange={updateInput('volatilityPct')} />
                </Grid>
                <Grid item xs={12} sm={6} md={2.4}>
                  <TextField fullWidth label="Risk-Free %" type="number" value={inputs.riskFreePct} onChange={updateInput('riskFreePct')} />
                </Grid>
                <Grid item xs={12} sm={6} md={2.4}>
                  <TextField fullWidth label="Days to Expiry" type="number" value={inputs.daysToExpiry} onChange={updateInput('daysToExpiry')} />
                </Grid>
                <Grid item xs={12}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Fixed Price Scenarios At Expiry
                  </Typography>
                  <Stack spacing={1.5}>
                    {fixedPriceInputs.map((value, index) => (
                      <Stack key={`fixed-price-${index}`} direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
                        <TextField
                          fullWidth
                          label={`Fixed Price Row ${index + 1}`}
                          type="number"
                          value={value}
                          onChange={updateFixedPriceInput(index)}
                          helperText="Enter underlying stock price in the same quote unit, e.g. 30.975"
                        />
                        <Stack direction="row" spacing={1}>
                          <Button variant="outlined" onClick={addFixedPriceRow}>
                            Add Row
                          </Button>
                          <Button
                            variant="outlined"
                            color="error"
                            onClick={() => removeFixedPriceRow(index)}
                            disabled={fixedPriceInputs.length === 1}
                          >
                            Remove
                          </Button>
                        </Stack>
                      </Stack>
                    ))}
                  </Stack>
                </Grid>
              </Grid>
            </Paper>

            <Grid container spacing={2}>
              <Grid item xs={12} lg={7}>
                <Paper sx={{ p: 3, height: '100%' }}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                    Greeks
                  </Typography>
                  <Grid container spacing={2}>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title={<GreekTitle label="Theoretical Price" tooltip={greekDescriptions.theoreticalPrice} />} value={fmtNumber(metrics.theoreticalPrice, 6)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title={<GreekTitle label="Delta" tooltip={greekDescriptions.delta} />} value={fmtNumber(metrics.delta, 6)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title={<GreekTitle label="Gamma" tooltip={greekDescriptions.gamma} />} value={fmtNumber(metrics.gamma, 8)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title={<GreekTitle label="Theta / Day" tooltip={greekDescriptions.theta} />} value={fmtNumber(metrics.thetaPerDay, 6)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title={<GreekTitle label="Vega / 1% Vol" tooltip={greekDescriptions.vega} />} value={fmtNumber(metrics.vegaPer1PctVol, 6)} />
                      </Grid>
                      <Grid item xs={12} sm={6} md={4}>
                        <StatCard title={<GreekTitle label="Rho / 1% Rate" tooltip={greekDescriptions.rho} />} value={fmtNumber(metrics.rhoPer1PctRate, 6)} />
                      </Grid>
                    </Grid>
                </Paper>
              </Grid>
              <Grid item xs={12} lg={5}>
                <Paper sx={{ p: 3, height: '100%' }}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                    CW Analysis
                  </Typography>
                  <Stack spacing={1.25}>
                    <Typography variant="body1" sx={{ color: 'text.secondary' }}>
                      <InlineTooltipLabel label="Summary" tooltip={analysisDescriptions.summary} />: {metrics.summary}
                    </Typography>
                    <Divider />
                    <Typography variant="body2"><InlineTooltipLabel label="Intrinsic Value" tooltip={analysisDescriptions.intrinsicValue} />: {fmtNumber(metrics.intrinsicValue)}</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Time Value" tooltip={analysisDescriptions.timeValue} />: {fmtNumber(metrics.timeValue)}</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Moneyness" tooltip={analysisDescriptions.moneyness} />: {fmtPct(metrics.moneynessPct)}</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Break-even Stock" tooltip={analysisDescriptions.breakEvenStock} />: {fmtNumber(metrics.breakEvenStockPrice)}</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Break-even Premium" tooltip={analysisDescriptions.breakEvenPremium} />: {fmtPct(metrics.premiumToBreakEvenPct)}</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Leverage" tooltip={analysisDescriptions.leverage} />: {fmtNumber(metrics.leverage)}x</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Effective Gearing" tooltip={analysisDescriptions.effectiveGearing} />: {fmtNumber(metrics.effectiveGearing)}x</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Theoretical Edge" tooltip={analysisDescriptions.theoreticalEdge} />: {fmtPct(metrics.theoreticalEdgePct)}</Typography>
                    <Typography variant="body2"><InlineTooltipLabel label="Parity Ratio" tooltip={analysisDescriptions.parityRatio} />: {fmtNumber(metrics.parityPriceRatio, 3)}</Typography>
                  </Stack>
                </Paper>
              </Grid>
            </Grid>

            {expiryScenarios.length > 0 ? (
              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                  Kịch bản tại đáo hạn {expiryDateLabel}
                </Typography>
                <Stack spacing={1.5}>
                  {expiryScenarios.map((scenario) => (
                    <Paper
                      key={scenario.key}
                      sx={{
                        px: 2,
                        py: 1.5,
                        bgcolor: 'surface.default',
                        border: 1,
                        borderColor: 'line.subtle',
                      }}
                    >
                      <Stack
                        direction={{ xs: 'column', md: 'row' }}
                        spacing={1.5}
                        justifyContent="space-between"
                        alignItems={{ xs: 'flex-start', md: 'center' }}
                      >
                        <Box>
                          <Typography variant="body1" sx={{ fontWeight: 700 }}>
                            {scenario.label} → {fmtQuoteToVnd(scenario.stockPriceAtExpiry)}
                          </Typography>
                          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                            Giá trị CW tại đáo hạn: {fmtQuoteToVnd(scenario.payoffPerWarrant)}
                            {scenario.movePct !== null ? ` • ${scenario.movePct > 0 ? '+' : ''}${scenario.movePct.toFixed(2)}% vs spot` : ''}
                          </Typography>
                        </Box>
                        <Stack direction="row" spacing={3} alignItems="center">
                          <Typography
                            variant="h6"
                            sx={{
                              fontWeight: 800,
                              color: (scenario.returnPct ?? 0) >= 0 ? 'success.main' : 'error.main',
                              minWidth: 78,
                              textAlign: 'right',
                            }}
                          >
                            {fmtSignedPct0(scenario.returnPct)}
                          </Typography>
                          <Typography
                            variant="h6"
                            sx={{
                              fontWeight: 700,
                              color: scenario.pnlPerWarrant >= 0 ? 'success.light' : 'error.light',
                              minWidth: 96,
                              textAlign: 'right',
                            }}
                          >
                            {fmtSignedQuoteToVnd(scenario.pnlPerWarrant)}
                          </Typography>
                        </Stack>
                      </Stack>
                    </Paper>
                  ))}
                </Stack>
              </Paper>
            ) : null}

            {payoffCurve.length > 0 ? (
              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                  Payoff Chart At Expiry
                </Typography>
                <Typography variant="body2" sx={{ color: 'text.secondary', mb: 2 }}>
                  Curve shows CW expiry value and PnL per warrant across a range of underlying prices.
                </Typography>
                <Box sx={{ width: '100%', overflowX: 'auto' }}>
                  <LineChart
              sx={ct.xChartsSx}
                    height={360}
                    xAxis={[
                      {
                        data: payoffCurve.map((point) => point.stockPrice),
                        valueFormatter: (value: number | null) => fmtQuoteToVnd(value ?? 0),
                        label: `${data.detail.base_stock_code || 'Underlying'} price at expiry`,
                      },
                    ]}
                    yAxis={[
                      {
                        valueFormatter: (value: number | null) => fmtQuoteToVnd(value ?? 0),
                      },
                    ]}
                    series={[
                      {
                        id: 'payoff',
                        label: 'CW payoff',
                        data: payoffCurve.map((point) => point.payoffPerWarrant),
                        color: ct.seriesColor(5),
                        showMark: false,
                      },
                      {
                        id: 'pnl',
                        label: 'PnL',
                        data: payoffCurve.map((point) => point.pnlPerWarrant),
                        color: ct.accent,
                        showMark: false,
                      },
                    ]}
                    margin={{ left: 72, right: 24, top: 24, bottom: 48 }}
                    grid={{ horizontal: true, vertical: true }}
                    slotProps={{
                      legend: {
                        position: { vertical: 'top', horizontal: 'end' },
                      },
                    }}
                  />
                </Box>
              </Paper>
            ) : null}
          </>
        ) : null}
      </Stack>
    </PageContainer>
  );
}
