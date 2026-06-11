# /routers/explorer.py

from fastapi import APIRouter, Request, Query
from fastapi.templating import Jinja2Templates
from typing import Optional
import asyncio
import logging

# Data Engine 
from data_engine import fetch_explorer_data, APIFetchError

# Services
from services.state import cascade_state

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/explorer")
async def deep_explorer_page(request: Request):
    """
    Renders the default empty state for the Deep Explorer.
    """
    return templates.TemplateResponse(
        request=request,
        name="explorer.html",
        context={}
    )

@router.get("/explorer/search")
async def execute_deep_explorer(
    request: Request,
    agency_target: str = "ALL",
    psc_code: str = "",
    award_id: str = "",  # Renamed from parent_award_id to be a universal PIID/FAIN/IDIQ lookup
    state_code: str = "ALL",
    year: int = 2023,
    fetch_subawards: bool = False
):
    """
    Executes the advanced Whole-of-Government / Sub-Award query.
    """
    client_id = request.headers.get('X-Client-ID', 'default')
    
    # Clean inputs
    state_code = state_code.upper() if state_code and state_code.strip() != "" else "ALL"
    psc_code = psc_code.strip().upper()
    award_id_clean = award_id.strip()
    agency_target = agency_target.upper()
    
    api_rate_limited = False
    api_error_message = None
    
    prime_data = []
    sub_data = []
    
    try:
        # Hit the advanced orchestrator
        # We pass award_id_clean into the parent_award_id parameter of data_engine 
        # because data_engine injects it into the API's 'keywords' array, which 
        # acts as a direct exact-match lookup for any PIID or FAIN.
        all_macro, all_micro = await fetch_explorer_data(
            agency_target=agency_target,
            psc_code=psc_code,
            parent_award_id=award_id_clean, 
            state_code=state_code,
            year=year,
            fetch_subawards=fetch_subawards,
            client_id=client_id
        )
        
        # Merge macro (Contracts) and micro (Grants) for filtering
        all_data = all_macro + all_micro
        
        for flow in all_data:
            if flow.get("is_subaward"):
                sub_data.append(flow)
            else:
                prime_data.append(flow)
                
    except APIFetchError as e:
        logger.error(f"Explorer API Rejection: {e}")
        api_rate_limited = True
        api_error_message = str(e)
    except Exception as e:
        logger.error(f"Unexpected Explorer Error: {e}")
        api_rate_limited = True
        api_error_message = f"Unexpected backend failure: {str(e)}"
        
    # Build statistics for the UI
    total_prime = sum(f.get("amount_usd", 0) for f in prime_data)
    total_sub = sum(f.get("amount_usd", 0) for f in sub_data)
    
    return templates.TemplateResponse(
        request=request,
        name="explorer.html",
        context={
            "agency_target": agency_target,
            "psc_code": psc_code,
            "award_id": award_id_clean,
            "state_code": state_code,
            "year": year,
            "fetch_subawards": fetch_subawards,
            "prime_data": prime_data,
            "sub_data": sub_data,
            "total_prime": total_prime,
            "total_sub": total_sub,
            "api_rate_limited": api_rate_limited,
            "api_error_message": api_error_message,
            "has_searched": True
        }
    )