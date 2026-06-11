# /routers/profiles.py

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from collections import defaultdict
import urllib.parse
import html  
import asyncio  

# Fetching logic
from data_engine import get_recipient_activities, fetch_single_year_all_sources

# Database logic
from db_manager import (
    get_multi_year_data, 
    save_data, 
    find_existing_session,
    get_or_create_local_user,
    is_pipeline_pinned
)
from sqlmodel import Session, select
from models import engine, FinancialFlow, SearchSession

# Services
from services.intelligence import (
    get_recipient_global_dossier,
    get_zone_global_dossier
)
from services.charts import create_mini_trend_chart

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/profile")
async def recipient_profile(
    request: Request, 
    recipient: str = "Unknown Recipient", 
    state_code: str = "ALL", 
    year: int = 2023,
    session_id: Optional[int] = None,
    tab: str = "context" 
):
    """Unified profile view for specific Prime Contractors/Grantees. Supports live single-year context AND global intelligence dossier modes."""
    client_id = request.headers.get('X-Client-ID', 'default')
    
    state_code = state_code.upper()
    
    # Mode 1: Live Snapshot Context (Hits USAspending API)
    activities = []
    total_received = 0.0
    if tab == "context" and state_code not in ["GLOBAL", "ALL", ""]:
        activities = await get_recipient_activities(recipient, state_code, year, client_id=client_id)
        total_received = sum([item.get('amount', 0) for item in activities])
        
    # Mode 2: Global Offline Dossier (Hits Local Cache)
    dossier = get_recipient_global_dossier(recipient)
    
    if tab == "context" and state_code in ["GLOBAL", "ALL", ""]:
        tab = "dossier"

    return templates.TemplateResponse(
        request=request,
        name="recipient.html", # FIX: Point to the correctly renamed HTML template
        context={
            "session_id": session_id, 
            "recipient": recipient, 
            "state_code": state_code,
            "year": year,
            "tab": tab,
            "activities": activities,
            "total": total_received,
            "dossier": dossier
        }
    )


@router.get("/zone")
async def target_zone_dossier(request: Request, state_code: str = "TX", session_id: Optional[int] = None):
    """Global offline intelligence dossier for a specific Target Zone/State."""
    dossier = get_zone_global_dossier(state_code.upper())
    
    return templates.TemplateResponse(
        request=request,
        name="zone.html", 
        context={
            "session_id": session_id,
            "state_code": state_code.upper(),
            "dossier": dossier
        }
    )


@router.get("/pipeline")
async def pipeline_profile(
    request: Request, 
    state_code: str, 
    agency: str,   
    recipient: str, 
    year: int,
    session_id: Optional[int] = None
):
    """Dedicated full-page profile analyzing a specific Agency -> Recipient pipeline trend over 5 years."""
    client_id = request.headers.get('X-Client-ID', 'default')
    
    state_code_upper = state_code.upper()
    api_rate_limited = False
    
    # Check if the user has already pinned this dashboard
    user = get_or_create_local_user()
    is_pinned = is_pipeline_pinned(user.id, state_code_upper, agency, recipient, year)
    
    # Look back 4 years + current year = 5 year horizon
    start_year = year - 4
    macro_data, micro_data = get_multi_year_data(state_code_upper, start_year, year)
    
    all_data = macro_data + micro_data
    
    existing_years = set(f['year'] for f in all_data)
    missing_years = []
    
    for y in range(start_year, year + 1):
        if y not in existing_years:
            if not find_existing_session(state_code_upper, search_type="Dashboard", year=y):
                missing_years.append(y)
                
    if missing_years:
        async def _fetch_and_cache(y):
            try:
                live_macro, live_micro = await fetch_single_year_all_sources(state_code_upper, y, client_id=client_id)
                if live_macro or live_micro:
                    save_data(f"Auto-Cache ({y})", state_code_upper, y, live_macro, live_micro, session_id=None)
                    return live_macro + live_micro
                return []
            except Exception as e:
                return None

        tasks = [_fetch_and_cache(y) for y in missing_years]
        results = await asyncio.gather(*tasks)
        
        for res in results:
            if res is None:
                api_rate_limited = True
            elif res:
                all_data.extend(res)
    
    # 1. Isolate only the exact pipeline flow requested (RAW)
    raw_flow_history = [f for f in all_data if f['agency'] == agency and f['recipient'] == recipient]
    
    # 2. AGGREGATE BY YEAR
    aggregated_flows = defaultdict(float)
    layer = "Unknown"
    source_db = "Unknown"
    
    for f in raw_flow_history:
        aggregated_flows[f['year']] += f['amount_usd']
        layer = f.get('layer', layer)
        source_db = f.get('source_database', source_db)
        
    clean_history = []
    for y in sorted(aggregated_flows.keys()):
        clean_history.append({
            "year": y,
            "amount_usd": aggregated_flows[y],
            "agency": agency, 
            "recipient": recipient,
            "layer": layer,
            "source_database": source_db
        })
    
    chart_json = create_mini_trend_chart(clean_history, start_year, year)
    total_5yr_volume = sum([f['amount_usd'] for f in clean_history])
    target_year_volume = aggregated_flows.get(year, 0.0)

    return templates.TemplateResponse(
        request=request,
        name="pipeline.html",
        context={
            "session_id": session_id,
            "state_code": state_code_upper,
            "target_year": year,
            "start_year": start_year,
            "agency": agency,
            "recipient": recipient,
            "layer": layer,
            "source_db": source_db,
            "total_5yr_volume": total_5yr_volume,
            "target_year_volume": target_year_volume, 
            "flow_history": reversed(raw_flow_history), 
            "chart_json": chart_json,
            "api_rate_limited": api_rate_limited,
            "is_pinned": is_pinned # NEW
        }
    )


@router.get("/source_profile")
async def get_source_profile(
    request: Request, 
    state_code: str, 
    agency: str,   
    recipient: str, 
    year: int,
    amount: float
):
    """HTMX endpoint to return a popup modal containing the exact raw DB record for OSINT verification."""
    
    with Session(engine) as session:
        statement = select(FinancialFlow).where(
            FinancialFlow.state_code == state_code.upper(),
            FinancialFlow.agency == agency,
            FinancialFlow.recipient == recipient,
            FinancialFlow.year == year,
            FinancialFlow.amount_usd >= (amount - 0.01),
            FinancialFlow.amount_usd <= (amount + 0.01)
        )
        record = session.exec(statement).first()

    if not record:
        record = FinancialFlow(
            state_code=state_code.upper(),
            year=year,
            agency=agency,
            recipient=recipient,
            amount_usd=amount,
            source_database="Unknown / Live Memory",
            layer="Unknown",
            flow_type="Unverified"
        )
        
    return templates.TemplateResponse(
        request=request,
        name="components/source_modal.html",
        context={
            "record": record
        }
    )