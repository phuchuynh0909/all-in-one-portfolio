## FastAPI + React Migration Scaffold

This repository hosts the restructured app: FastAPI backend and React (Vite+TS) frontend.

### Structure

- `backend/` FastAPI app (routers, services, schemas, db)
- `frontend/` React (Vite + TypeScript)
- `docker-compose.yml` Local dev stack

### Backend

- Run locally:
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```
- Health check: `GET http://localhost:8000/api/v1/health`

### Frontend

- Run locally:
```bash
cd frontend
npm install
npm run dev
```

### Docker
```bash
docker compose up --build
```

### Next steps (migration)
- Identify Streamlit pages and port to React routes:
  - `pages/report.py` → `/report`
  - `pages/sector.py` → `/sector`
  - `pages/my_portfolio.py` → `/portfolio`
  - `pages/backtest.py` → `/backtest`
- Extract data logic in `streamlit_app` into backend services and endpoints
  - Reuse code from `database.py`, `stock_data.py`, `src/stores/*`, `src/strategies/*`
- Define pydantic schemas in `backend/app/schemas/`
- Implement routers in `backend/app/api/v1/routes/`
- Implement services in `backend/app/services/`
- Add persistence in `backend/app/db/`

# all-in-one-portfolio
