# /routers/activities.py

from fastapi import APIRouter, Request, HTTPException
from fastapi.templating import Jinja2Templates
from typing import Optional
import asyncio
import logging

# Database logic
from db_manager import (
    get_all_sessions,
    get_snapshot,
    find_existing_session,
    save_activity_search,
    delete_session
)

# Services
from services.intelligence import get_global_intelligence
from services.charts import create_activity_timeline_chart, create_activity_treemap_chart

# Data Engine
from data_engine import search_activities_by_keyword, generate_bibliography

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/activity")
async def activity_search_page(request: Request, active_session_id: Optional[int] = None):
    """Renders the dedicated Activity / Sector search form. Pre-fills if locked to a session."""
    sessions = get_all_sessions()
    locked_session = None
    
    if active_session_id:
        locked_session = get_snapshot(active_session_id)
        
    intel = get_global_intelligence()
    suggested_keywords = list(set(list(intel['sectors'].keys()) + intel['searched_keywords']))
    suggested_keywords.sort()
        
    return templates.TemplateResponse(
        request=request,
        name="activity_search.html",
        context={
            "sessions": sessions, 
            "active_session_id": active_session_id,
            "locked_session": locked_session,
            "suggested_keywords": suggested_keywords
        }
    )


@router.get("/activity_search")
async def activity_search(
    request: Request, 
    keyword: str, 
    state_code: str = "ALL", 
    start_year: int = 2023,
    end_year: int = 2023,
    session_id: Optional[int] = None,
    session_name: str = "Unnamed Session",
    force_refresh: bool = False
):
    """Executes the search for specific federal activities based on a keyword, supporting Multi-Year horizons."""
    client_id = request.headers.get('X-Client-ID', 'default')
    
    state_code = state_code.upper() if state_code and state_code.strip() != "" else "ALL"
    
    # Ensure chronological order
    if start_year > end_year:
        start_year, end_year = end_year, start_year
    
    api_rate_limited = False
    is_offline = False
    bibliography = {}
    results = []

    # 1. Session check - Match against start_year and end_year
    if session_id and not force_refresh:
        snapshot = get_snapshot(session_id)
        if snapshot and snapshot.get('search_type') == 'Activity' and snapshot.get('state_code') == state_code and snapshot.get('start_year') == start_year and snapshot.get('end_year') == end_year and snapshot.get('keyword') == keyword:
            results = snapshot.get('results', [])
            timestamp = snapshot.get('timestamp')
            session_name = snapshot.get('session_name', session_name)
            is_offline = True
        else:
            # Match the /cascade shape exactly: if parameters changed, drop the ID so we don't accidentally overwrite
            session_id = None
            if snapshot:
                session_name = snapshot.get('session_name', session_name)
                
    # 2. Existing Session Check 
    if not session_id:
        found_id = find_existing_session(state_code, search_type="Activity", start_year=start_year, end_year=end_year, keyword=keyword)
        if found_id:
            session_id = found_id
            
            if not force_refresh:
                snapshot = get_snapshot(session_id)
                if snapshot:
                    results = snapshot.get('results', [])
                    timestamp = snapshot.get('timestamp')
                    session_name = snapshot.get('session_name', session_name)
                    is_offline = True

    # 3. Live Pull
    if not is_offline:
        try:
            task1 = search_activities_by_keyword(keyword, state_code, start_year, end_year, client_id=client_id)
            task2 = generate_bibliography(state_code, start_year, end_year, client_id=client_id)
            
            results, bibliography = await asyncio.gather(task1, task2)
            
            if not results and not bibliography:
                api_rate_limited = True
                
            session_id = save_activity_search(session_name, keyword, state_code, start_year, end_year, results, session_id=session_id)
            timestamp = "Just Now"
        except Exception as e:
            logger.error(f"Error fetching live data for Activity Search: {e}")
            api_rate_limited = True
            results = []
            bibliography = {}
            timestamp = "Failed"

    total_found = sum([item.get('amount', 0) for item in results]) if results else 0
    
    # Generate BOTH charts seamlessly!
    timeline_json = None
    if start_year != end_year:
        timeline_json = create_activity_timeline_chart(results, keyword, start_year, end_year) if results else None
        
    treemap_json = create_activity_treemap_chart(results, keyword) if results else None
    
    return templates.TemplateResponse(
        request=request,
        name="activity_results.html",
        context={
            "session_id": session_id,
            "session_name": session_name,
            "keyword": keyword,
            "state_code": state_code,
            "start_year": start_year,
            "end_year": end_year,
            "results": results,
            "total": total_found,
            "treemap_json": treemap_json,    
            "timeline_json": timeline_json, 
            "bibliography": bibliography,
            "is_offline": is_offline,
            "timestamp": timestamp,
            "api_rate_limited": api_rate_limited
        }
    )


@router.delete("/delete_session/{session_id}")
async def delete_session_route(session_id: int):
    """Deletes a saved session snapshot from the database."""
    success = delete_session(session_id)
    if success:
        return ""
    raise HTTPException(status_code=404, detail="Session not found")