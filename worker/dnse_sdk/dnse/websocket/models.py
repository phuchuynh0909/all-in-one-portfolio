"""Data models for market data and private channel updates.

This module provides typed data models for all message types:
- Market data: Trade, Quote, OHLC, ExpectedPrice, TradeExtra, SecurityDefinition
- Private channels: Order, Position, AccountUpdate

All models support parsing from both abbreviated (MessagePack) and full (JSON) field names.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple


def parse_timestamp(v: Any, date_only: bool = False) -> Optional[str]:
    """Parse various timestamp formats into a UTC ISO-8601 string.

    Supports:
    - protobuf: {'Seconds': 1501718400, 'Nanos': 0}
    - ISO string: '2017-08-03T00:00:00Z'
    - Unix int/float: 1501718400 (seconds) or 1501718400000 (milliseconds)

    Returns:
        ISO-8601 with an explicit UTC offset ("2017-08-03T00:00:00+00:00"),
        or "YYYY-MM-DD" when date_only, or None.

    LOCAL PATCH (all-in-one-portfolio/worker) — see dnse_sdk/__init__.py.
    Upstream built every datetime with a bare ``fromtimestamp()`` and then
    formatted with ``strftime``, which dropped any offset. Three inputs naming
    the same instant produced three different naive strings, indistinguishable
    once returned: the protobuf and unix branches came out in the *host's* local
    zone (right on a UTC container, seven hours off on an ICT laptop), while a
    string came out in whatever zone it arrived in. Since the result carried no
    offset, nothing downstream could tell which it had got.

    Now every branch resolves to an aware UTC datetime and the offset is kept.
    A naive *timestamp* string is rejected rather than assumed, because guessing
    its zone silently misplaces the instant; a naive *date* is still accepted
    under date_only, a calendar date being a label rather than an instant.
    """
    try:
        if v is None:
            return None

        if isinstance(v, str):
            dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if not date_only:
                    return None  # ambiguous instant; see the note above
            else:
                dt = dt.astimezone(timezone.utc)

        elif isinstance(v, dict):
            seconds = v.get("Seconds", v.get("seconds", 0))
            nanos = v.get("Nanos", v.get("nanos", 0))
            # An exact integer offset, not ``seconds + nanos / 1e9``: at a 1.8e9
            # epoch a float64 resolves only ~240ns, so the sum rounds away
            # sub-microsecond detail the caller may be relying on.
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
                microseconds=nanos // 1000
            )

        elif isinstance(v, (int, float)):
            # If already in milliseconds (>1e12), convert to seconds
            dt = datetime.fromtimestamp(
                v / 1000.0 if v > 1e12 else float(v), tz=timezone.utc
            )

        else:
            return None

        return dt.strftime("%Y-%m-%d") if date_only else dt.isoformat()
    except Exception:
        return None

@dataclass
class PriceLevel:
    price: float
    quantity: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceLevel":
        return cls(
            price=data.get("price"),
            quantity=data.get("qtty")
        )


@dataclass
class Trade:
    marketId: str
    boardId: str
    isin: str
    symbol: str
    price: float
    quantity: int
    totalVolumeTraded: int
    grossTradeAmount: float
    highestPrice: float
    lowestPrice: float
    openPrice: float
    tradingSessionId: int
    time: Optional[str] = None
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trade":
        return cls(
            marketId=data.get("marketId"),
            boardId=data.get("boardId"),
            isin=data.get("isin"),
            symbol=data.get("symbol"),
            price=data.get("matchPrice"),
            quantity=data.get("matchQtty"),
            totalVolumeTraded=data.get("totalVolumeTraded"),
            grossTradeAmount=data.get("grossTradeAmount"),
            highestPrice=data.get("highestPrice"),
            lowestPrice=data.get("lowestPrice"),
            openPrice=data.get("openPrice"),
            tradingSessionId=data.get("tradingSessionId"),
            time=parse_timestamp(data.get("time")),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class TradeExtra:
    marketId: str
    boardId: str
    isin: str
    symbol: str
    price: float
    quantity: int
    side: int
    avgPrice: float
    totalVolumeTraded: int
    grossTradeAmount: float
    highestPrice: float
    lowestPrice: float
    openPrice: float
    tradingSessionId: int
    time: Optional[str] = None
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeExtra":
        return cls(
            marketId=data.get("marketId"),
            boardId=data.get("boardId"),
            isin=data.get("isin"),
            symbol=data.get("symbol"),
            price=data.get("matchPrice"),
            quantity=data.get("matchQtty"),
            side=data.get("side"),
            avgPrice=data.get("avgPrice"),
            totalVolumeTraded=data.get("totalVolumeTraded"),
            grossTradeAmount=data.get("grossTradeAmount"),
            highestPrice=data.get("highestPrice"),
            lowestPrice=data.get("lowestPrice"),
            openPrice=data.get("openPrice"),
            tradingSessionId=data.get("tradingSessionId"),
            time=parse_timestamp(data.get("time")),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class ForeignInvestor:
    marketId: str
    boardId: str
    tradingSessionId: str
    symbol: str
    transactTime: str
    foreignInvestorTypeCode: str

    sellVolume: int
    sellTradedAmount: int
    buyVolume: int
    buyTradedAmount: int

    totalSellVolume: int
    totalSellTradedAmount: int
    totalBuyVolume: int
    totalBuyTradedAmount: int

    foreignerOrderLimitQuantity: int
    foreignerBuyPossibleQuantity: int
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ForeignInvestor":
        return cls(
            marketId=data.get("marketId"),
            boardId=data.get("boardId"),
            tradingSessionId=data.get("tradingSessionId"),
            symbol=data.get("symbol"),
            transactTime=data.get("transactTime"),
            foreignInvestorTypeCode=data.get("foreignInvestorTypeCode"),
            sellVolume=data.get("sellVolume"),
            sellTradedAmount=data.get("sellTradedAmount"),
            buyVolume=data.get("buyVolume"),
            buyTradedAmount=data.get("buyTradedAmount"),
            totalSellVolume=data.get("totalSellVolume"),
            totalSellTradedAmount=data.get("totalSellTradedAmount"),
            totalBuyVolume=data.get("totalBuyVolume"),
            totalBuyTradedAmount=data.get("totalBuyTradedAmount"),
            foreignerOrderLimitQuantity=data.get("foreignerOrderLimitQuantity"),
            foreignerBuyPossibleQuantity=data.get("foreignerBuyPossibleQuantity"),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class MarketIndex:
    indexName: str

    changedRatio: float
    changedValue: float

    fluctuationSteadinessIssueCount: int
    fluctuationDownIssueCount: int
    fluctuationUpIssueCount: int
    fluctuationLowerLimitIssueCount: int
    fluctuationUpperLimitIssueCount: int

    fluctuationDownIssueVolume: int
    fluctuationUpIssueVolume: int
    fluctuationSteadinessIssueVolume: int

    currencyCode: str
    indexTypeCode: str

    lowestValueIndexes: float
    highestValueIndexes: float
    priorValueIndexes: float
    valueIndexes: float

    contauctAccTrdVal: float
    contauctAccTrdVol: int
    blkTrdAccTrdVal: float
    blkTrdAccTrdVol: int

    grossTradeAmount: float
    totalVolumeTraded: int
    marketIndexClass: int
    marketId: int
    tradingSessionId: int
    transactTime: Optional[str] = None

    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketIndex":
        return cls(
            indexName=data.get("indexName"),
            changedRatio=data.get("changedRatio"),
            changedValue=data.get("changedValue"),
            fluctuationSteadinessIssueCount=data.get("fluctuationSteadinessIssueCount"),
            fluctuationDownIssueCount=data.get("fluctuationDownIssueCount"),
            fluctuationUpIssueCount=data.get("fluctuationUpIssueCount"),
            fluctuationLowerLimitIssueCount=data.get("fluctuationLowerLimitIssueCount"),
            fluctuationUpperLimitIssueCount=data.get("fluctuationUpperLimitIssueCount"),
            fluctuationDownIssueVolume=data.get("fluctuationDownIssueVolume"),
            fluctuationUpIssueVolume=data.get("fluctuationUpIssueVolume"),
            fluctuationSteadinessIssueVolume=data.get("fluctuationSteadinessIssueVolume"),
            currencyCode=data.get("currencyCode"),
            indexTypeCode=data.get("indexTypeCode"),
            lowestValueIndexes=data.get("lowestValueIndexes"),
            highestValueIndexes=data.get("highestValueIndexes"),
            priorValueIndexes=data.get("priorValueIndexes"),
            valueIndexes=data.get("valueIndexes"),
            contauctAccTrdVal=data.get("contauctAccTrdVal"),
            contauctAccTrdVol=data.get("contauctAccTrdVol"),
            blkTrdAccTrdVal=data.get("blkTrdAccTrdVal"),
            blkTrdAccTrdVol=data.get("blkTrdAccTrdVol"),
            grossTradeAmount=data.get("grossTradeAmount"),
            totalVolumeTraded=data.get("totalVolumeTraded"),
            marketIndexClass=data.get("marketIndexClass"),
            marketId=data.get("marketId"),
            tradingSessionId=data.get("tradingSessionId"),
            transactTime=parse_timestamp(data.get("transactTime")),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class EstimatedMarketIndex:
    indexName: str
    changedRatio: float
    changedValue: float
    fluctuationSteadinessIssueCount: float
    fluctuationDownIssueCount: float
    fluctuationUpIssueCount: float
    valueIndexes: float
    grossTradeAmount: float
    totalVolumeTraded: float
    time: Optional[str] = None

    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EstimatedMarketIndex":
        return cls(
            indexName=data.get("indexName"),
            changedRatio=data.get("changedRatio"),
            changedValue=data.get("changedValue"),
            fluctuationSteadinessIssueCount=data.get("fluctuationSteadinessIssueCount"),
            fluctuationDownIssueCount=data.get("fluctuationDownIssueCount"),
            fluctuationUpIssueCount=data.get("fluctuationUpIssueCount"),
            valueIndexes=data.get("valueIndexes"),
            grossTradeAmount=data.get("grossTradeAmount"),
            totalVolumeTraded=data.get("totalVolumeTraded"),
            receivedAt=data.get("_receivedAt"),
            time=data.get("time"),
        )


@dataclass
class ExpectedPrice:
    marketId: str
    boardId: str
    isin: str
    symbol: str
    closePrice: float
    expectedTradePrice: float
    expectedTradeQuantity: int
    time: Optional[str] = None
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExpectedPrice":
        return cls(
            marketId=data.get("marketId"),
            boardId=data.get("boardId"),
            isin=data.get("isin"),
            symbol=data.get("symbol"),
            closePrice=data.get("closePrice"),
            expectedTradePrice=data.get("expectedTradePrice"),
            expectedTradeQuantity=data.get("expectedTradeQuantity"),
            time=parse_timestamp(data.get("time")),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class SecurityDefinition:
    marketId: str
    boardId: str
    symbol: str
    isin: str
    productGrpId: str
    securityGroupId: str
    basicPrice: float
    ceilingPrice: float
    floorPrice: float
    openInterestQuantity: int
    securityStatus: str
    symbolAdminStatusCode: str
    symbolTradingMethodStatusCode: str
    symbolTradingSanctionStatusCode: str
    finalTradeDate: Optional[str]
    listingDate: Optional[str]
    time: Optional[str] = None
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SecurityDefinition":
        return cls(
            symbol=data.get("symbol"),
            marketId=data.get("marketId"),
            boardId=data.get("boardId"),
            isin=data.get("isin"),
            productGrpId=data.get("productGrpId"),
            securityGroupId=data.get("securityGroupId"),
            basicPrice=data.get("basicPrice"),
            ceilingPrice=data.get("ceilingPrice"),
            floorPrice=data.get("floorPrice"),
            openInterestQuantity=data.get("openInterestQuantity"),
            securityStatus=data.get("securityStatus"),
            symbolAdminStatusCode=data.get("symbolAdminStatusCode"),
            symbolTradingMethodStatusCode=data.get("symbolTradingMethodStatusCode"),
            symbolTradingSanctionStatusCode=data.get("symbolTradingSanctionStatusCode"),
            finalTradeDate=parse_timestamp(data.get("finalTradeDate"), date_only=True),
            listingDate=parse_timestamp(data.get("listingDate"), date_only=True),
            time=parse_timestamp(data.get("time")),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class Order:
    id: str
    side: str
    accountNo: str
    symbol: str

    price: float
    priceSecure: float
    averagePrice: float

    quantity: int
    fillQuantity: int
    canceledQuantity: int
    leaveQuantity: int

    orderType: str
    orderStatus: str

    loanPackageId: int
    marketType: str

    transDate: str
    createdDate: str
    modifiedDate: str
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Order":
        return cls(
            id=data.get("id"),
            side=data.get("side"),
            accountNo=data.get("accountNo"),
            symbol=data.get("symbol"),

            price=float(data.get("price")),
            priceSecure=float(data.get("priceSecure")),
            averagePrice=float(data.get("averagePrice")),

            quantity=int(data.get("quantity")),
            fillQuantity=int(data.get("fillQuantity")),
            canceledQuantity=int(data.get("canceledQuantity")),
            leaveQuantity=int(data.get("leaveQuantity")),

            orderType=data.get("orderType"),
            orderStatus=data.get("orderStatus"),

            loanPackageId=int(data.get("loanPackageId")),
            marketType=data.get("marketType"),

            transDate=data.get("transDate"),
            createdDate=data.get("createdDate"),
            modifiedDate=data.get("modifiedDate"),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class Position:
    id: int
    accountNo: str
    symbol: str
    status: str
    loanPackageId: int
    side: str
    accumulateQuantity: int
    tradeQuantity: int
    closedQuantity: int
    costPrice: float
    marketPrice: float
    breakEvenPrice: float
    openQuantity: int
    overNightQuantity: int
    averageClosePrice: float
    marketType: str
    createdDate: str
    modifiedDate: str

    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        return cls(
            id=data.get("id"),
            accountNo=data.get("accountNo"),
            symbol=data.get("symbol"),
            status=data.get("status"),
            loanPackageId=data.get("loanPackageId"),
            side=data.get("side"),
            accumulateQuantity=data.get("accumulateQuantity"),
            tradeQuantity=data.get("tradeQuantity"),
            closedQuantity=data.get("closedQuantity"),
            costPrice=float(data.get("costPrice")),
            marketPrice=float(data.get("marketPrice")),
            breakEvenPrice=float(data.get("breakEvenPrice")),
            openQuantity=data.get("openQuantity"),
            overNightQuantity=data.get("overNightQuantity"),
            averageClosePrice=float(data.get("averageClosePrice")),
            marketType=data.get("marketType"),
            createdDate=data.get("createdDate"),
            modifiedDate=data.get("modifiedDate"),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class Quote:
    marketId: str
    boardId: str
    symbol: str
    isin: str
    bid: List[PriceLevel]
    offer: List[PriceLevel]
    totalOfferQtty: float
    totalBidQtty: float
    time: Optional[str] = None
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Quote":
        # Parse bids array
        bids_data = data.get("bid") or []
        bids = [PriceLevel.from_dict(level) for level in bids_data]

        # Parse asks array
        offer_data = data.get("offer") or []
        offers = [PriceLevel.from_dict(level) for level in offer_data]

        return cls(
            symbol=data.get("symbol"),
            marketId=data.get("marketId"),
            boardId=data.get("boardId"),
            isin=data.get("isin", ""),
            bid=bids,
            offer=offers,
            totalOfferQtty=data.get("totalOfferQtty"),
            totalBidQtty=data.get("totalBidQtty"),
            time=parse_timestamp(data.get("time")),
            receivedAt=data.get("_receivedAt"),
        )

    @property
    def best_bid(self) -> Optional[Tuple[float, int]]:
        if not self.bid:
            return None
        return self.bid[0].price, self.bid[0].quantity

    @property
    def best_ask(self) -> Optional[Tuple[float, int]]:
        if not self.offer:
            return None
        return self.offer[0].price, self.offer[0].quantity

    @property
    def spread(self) -> Optional[float]:
        bid = self.best_bid
        offer = self.best_ask
        if bid and offer:
            return offer[0] - bid[0]
        return None


@dataclass
class Ohlc:
    symbol: str
    resolution: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    time: int
    lastUpdated: int
    type: str
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ohlc":
        # Helper function to round to 2 decimal places (standard rounding)
        def round_value(value) -> float:
            if value is None:
                return 0.0
            return round(float(value), 2)

        return cls(
            symbol=data.get("symbol"),
            resolution=data.get("resolution"),
            open=round_value(data.get("open")),
            high=round_value(data.get("high")),
            low=round_value(data.get("low")),
            close=round_value(data.get("close")),
            volume=data.get("volume"),
            time=data.get("time"),
            type=data.get("type"),
            lastUpdated=data.get("lastUpdated"),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class Session:
    marketId: str
    boardId: str
    eventId: str
    tradingSessionId: int
    tscProdGrpId: str
    time: Optional[str] = None
    receivedAt: Optional[float] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        return cls(
            marketId=data.get("marketId", ""),
            boardId=data.get("boardId", ""),
            eventId=data.get("eventId", ""),
            tradingSessionId=data.get("tradingSessionId", 0),
            tscProdGrpId=data.get("tscProdGrpId", ""),
            time=parse_timestamp(data.get("sendingTime")),
            receivedAt=data.get("_receivedAt"),
        )


@dataclass
class AccountUpdate:
    cash: Decimal
    buyingPower: Decimal
    portfolioValue: Decimal
    equity: Decimal
    timestamp: datetime

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountUpdate":
        """Parse account update from message data.

        Args:
            data: Raw message dict with either abbreviated or full field names

        Returns:
            AccountUpdate instance

        Example:
            >>> AccountUpdate.from_dict({"c": "10000.00", "bp": "20000.00", ...})
        """
        return cls(
            cash=Decimal(str(data.get("cash"))),
            buyingPower=Decimal(str(data.get("buyingPower"))),
            portfolioValue=Decimal(str(data.get("portfolioValue"))),
            equity=Decimal(str(data.get("equity"))),
            timestamp=datetime.fromtimestamp((data.get("timestamp")) / 1000)
        )
