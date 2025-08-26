from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.portfolio import (
    Position,
    PositionCreate,
    Transaction,
    TransactionCreate,
    InvestmentAmount,
    InvestmentAmountCreate,
    PortfolioSummary,
    OptimizationRequest,
    OptimizationResult,
    ClosePositionRequest,
    ClosePositionResponse,
)
from app.services import portfolio_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.post("/positions", response_model=Position)
def create_position(
    position: PositionCreate,
    db: Session = Depends(get_db),
) -> Position:
    return portfolio_service.create_position(db, position)


@router.get("/positions", response_model=List[Position])
async def list_positions(db: Session = Depends(get_db)) -> List[Position]:
    return await portfolio_service.get_positions(db)


@router.get("/positions/{position_id}", response_model=Position)
def get_position(position_id: int, db: Session = Depends(get_db)) -> Position:
    position = portfolio_service.get_position(db, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.put("/positions/{position_id}", response_model=Position)
def update_position(
    position_id: int,
    position: PositionCreate,
    db: Session = Depends(get_db),
) -> Position:
    updated = portfolio_service.update_position(db, position_id, position)
    if not updated:
        raise HTTPException(status_code=404, detail="Position not found")
    return updated


@router.delete("/positions/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db)) -> dict:
    if not portfolio_service.delete_position(db, position_id):
        raise HTTPException(status_code=404, detail="Position not found")
    return {"status": "success"}


@router.post("/positions/close", response_model=ClosePositionResponse)
def close_position(
    request: ClosePositionRequest,
    db: Session = Depends(get_db),
) -> ClosePositionResponse:
    """
    Close a position (partially or fully) and create a corresponding sell transaction.
    
    This endpoint:
    - Validates the position exists and has sufficient quantity
    - Calculates realized P/L
    - Creates a sell transaction
    - Updates or deletes the position based on remaining quantity
    """
    try:
        return portfolio_service.close_position(db, request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error closing position: {str(e)}")


@router.post("/transactions", response_model=Transaction)
def create_transaction(
    transaction: TransactionCreate,
    db: Session = Depends(get_db),
) -> Transaction:
    return portfolio_service.create_transaction(db, transaction)


@router.get("/transactions", response_model=List[Transaction])
def list_transactions(db: Session = Depends(get_db)) -> List[Transaction]:
    return portfolio_service.get_transactions(db)


@router.get("/transactions/{transaction_id}", response_model=Transaction)
def get_transaction(
    transaction_id: int, db: Session = Depends(get_db)
) -> Transaction:
    transaction = portfolio_service.get_transaction(db, transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)) -> dict:
    if not portfolio_service.delete_transaction(db, transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"status": "success"}


@router.post("/investment", response_model=InvestmentAmount)
def set_investment_amount(
    investment: InvestmentAmountCreate,
    db: Session = Depends(get_db),
) -> InvestmentAmount:
    return portfolio_service.set_investment_amount(db, investment)


@router.get("/investment", response_model=InvestmentAmount)
def get_investment_amount(db: Session = Depends(get_db)) -> InvestmentAmount:
    investment = portfolio_service.get_investment_amount(db)
    if not investment:
        raise HTTPException(
            status_code=404, detail="No investment amount set"
        )
    return investment


@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(db: Session = Depends(get_db)) -> PortfolioSummary:
    return await portfolio_service.get_portfolio_summary(db)


@router.post("/optimize", response_model=OptimizationResult)
def optimize_portfolio(
    body: OptimizationRequest,
    db: Session = Depends(get_db),
) -> OptimizationResult:
    return portfolio_service.optimize_portfolio(db, body)
