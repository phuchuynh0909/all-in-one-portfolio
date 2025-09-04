"""
Data Crawler API routes

Provides endpoints for crawling financial data from external sources
and importing them into the database.
"""

import json
import time
import requests
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.base import get_db
from app.db.models.financial import Company, ItemValue
from app.services.financial_data_importer import FinancialDataImporter
from app.utils.wichart import getToken, getNonce, getSign, getHeaders, decrypt

router = APIRouter(prefix="/crawler", tags=["data-crawler"])

# TODO: Move these to environment variables or config
WICHART_BASE_URL = "https://wichart.vn/wichartapi/wichart/company/fs"
SIGN_TOKEN = "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX"

@router.post("/crawl-symbol/{symbol}")
async def crawl_symbol_data(
    symbol: str,
    background_tasks: BackgroundTasks,
    quarter: Optional[int] = 1,
    db: Session = Depends(get_db)
):
    """Crawl financial data for a specific symbol from wichart.vn"""
    
    symbol = symbol.upper()
    
    # Check if company exists
    company = db.query(Company).filter(Company.ticker == symbol).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company {symbol} not found in database")
    
    # Check if data already exists
    existing_data = db.query(ItemValue).filter(ItemValue.company_id == company.company_id).first()
    if existing_data:
        return {
            "status": "skipped",
            "message": f"Data already exists for {symbol}",
            "company": {"ticker": company.ticker, "name": company.name}
        }
    
    try:
        # Start crawling in background
        crawl_and_import_data(symbol, quarter, db)
        
        return {
            "status": "started",
            "message": f"Started crawling financial data for {symbol}",
            "company": {"ticker": company.ticker, "name": company.name}
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start crawling: {str(e)}")

def crawl_and_import_data(symbol: str, quarter: int, db: Session):
    """Background task to crawl and import financial data"""
    
    try:
        print(f"Starting to crawl data for {symbol}")
        
        # Get authentication details
        token = getToken()
        nonce = getNonce()
        stime = int(time.time() * 1000)
        
        # Prepare query parameters
        query_params = {
            "code": symbol,
            "page": 1,
            "type": "quarter",
            "unit": "ty",
            "currency": "vnd",
            "quarter": quarter,
        }
        
        # Prepare signature data
        sign_data = {
            'code': symbol,
            'currency': query_params['currency'],
            'nonce': nonce,
            'page': query_params['page'],
            'quarter': query_params['quarter'],
            'sign-token': SIGN_TOKEN,
            'stime': stime,
            'type': query_params['type'],
            'unit': query_params['unit'],
            'v': 'v1',
        }
        
        # Generate signature and make request
        hash_code = getSign(sign_data)
        headers = getHeaders(token, nonce, hash_code, stime)
        
        print(f"Making request to wichart API for {symbol}")
        response = requests.get(WICHART_BASE_URL, params=query_params, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}")
        
        # Decrypt and parse response
        enc = response.json().get('enc')
        if not enc:
            raise Exception("No encrypted data in response")
        
        decrypted_data = decrypt(enc)
        data = json.loads(decrypted_data)
        
        # Import data using financial data importer
        print(f"Importing financial data for {symbol}")
        importer = FinancialDataImporter(db)
        importer.import_financial_data(data, symbol)
        
        db.commit()
        print(f"Successfully imported financial data for {symbol}")
        
    except Exception as e:
        print(f"Error crawling/importing data for {symbol}: {e}")
        db.rollback()
        raise

@router.get("/available-symbols")
async def get_available_symbols(db: Session = Depends(get_db)):
    """Get list of companies that can be crawled (companies without financial data)"""
    
    query = """
    SELECT c.ticker, c.name, 
           CASE WHEN iv.company_id IS NULL THEN 0 ELSE COUNT(iv.item_value_id) END as data_count
    FROM company c
    LEFT JOIN item_value iv ON c.company_id = iv.company_id
    GROUP BY c.company_id, c.ticker, c.name
    ORDER BY data_count ASC, c.ticker
    """
    
    result = db.execute(text(query))
    
    return [
        {
            "ticker": row[0],
            "name": row[1],
            "data_count": row[2],
            "needs_crawling": row[2] == 0
        }
        for row in result.fetchall()
    ]

@router.get("/crawl-status/{symbol}")
async def get_crawl_status(symbol: str, db: Session = Depends(get_db)):
    """Check crawling status for a symbol"""
    
    symbol = symbol.upper()
    
    # Check if company exists
    company = db.query(Company).filter(Company.ticker == symbol).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company {symbol} not found")
    
    # Check data count
    data_count = db.query(ItemValue).filter(ItemValue.company_id == company.company_id).count()
    
    return {
        "symbol": symbol,
        "company_name": company.name,
        "data_count": data_count,
        "has_data": data_count > 0,
        "status": "completed" if data_count > 0 else "pending"
    }
