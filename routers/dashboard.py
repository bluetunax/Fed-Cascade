# /routers/dashboard.py

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import asyncio
import json
import logging
import io
from datetime import datetime

# Database logic
from db_manager import (
    get_all_sessions,
    get_snapshot,
    find_existing_session,
    save_data,
    save_multi_year_data,
    get_multi_year_data,
    get_base_session_id,  
    save_ledger_changes,
    export_database_json,   
    import_database_json,   
    wipe_database           
)

# API Fetching logic
from data_engine import (
    fetch_single_year_all_sources,
    fetch_multi_year_flows,
    generate_bibliography,
    APIFetchError 
)

# Services
from services.charts import create_treemap_chart, create_timeline_chart

# Initialize Router Logger
logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def _calc_yoy(curr: float, prev: float) -> str:
    """Helper to calculate percentage change cleanly, protected against ZeroDivisionError."""
    if prev == 0 and curr > 0: return "NEW"
    if prev == 0 and curr == 0: return "0%"
    
    if prev == 0 and curr < 0: return "NEW (Negative)"
    
    delta = ((curr - prev) / abs(prev)) * 100
    
    if delta > 999: return ">+999%" 
    if delta < -999: return "<-999%"
    return f"{delta:+.1f}%"


def _merge_key(f: dict) -> str:
    """
    Builds a stable per-record merge key for the Safe Additive Merge.
    
    Prefers the native USAspending source_record_id so that multiple distinct
    awards between the same Agency -> Recipient pair in the same year are 
    preserved as separate records (previously they silently overwrote each other).
    
    Falls back to a composite signature (WITHOUT the dollar amount) for legacy
    records or unstable FALLBACK_ hashes, so a modified amount still matches
    its original record instead of duplicating it.
    """
    sig = str(f.get('source_record_id', '') or '')
    
    if sig and sig != "UNKNOWN_ID" and not sig.startswith("FALLBACK_"):
        return sig
        
    return (
        f"AGG_{f.get('year')}|{f.get('source_database')}|{f.get('layer')}|"
        f"{f.get('agency')}|{f.get('recipient')}|{f.get('flow_type')}|{f.get('award_id_piid')}"
    )


@router.get("/")
async def home(request: Request, active_session_id: Optional[int] = None):
    """Renders the Dashboard/Timeline search form."""
    sessions = get_all_sessions()
    locked_session = None
    
    if active_session_id:
        locked_session = get_snapshot(active_session_id)
        
    return templates.TemplateResponse(
        request=request,
        name="index.html", 
        context={
            "sessions": sessions, 
            "active_session_id": active_session_id,
            "locked_session": locked_session
        }
    )


@router.get("/unlock_session")
async def unlock_session(return_url: str = "/"):
    """Utility route to clear the active session lock."""
    return RedirectResponse(url=return_url)


@router.get("/load_session/{session_id}")
async def load_session(session_id: int):
    """Dynamically routes saved snapshots to the correct UI view."""
    snapshot = get_snapshot(session_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Session not found")
        
    stype = snapshot.get("search_type", "Dashboard")
    state_code = snapshot.get("state_code", "ALL")
    year = snapshot.get("year", 2023)
    start_year = snapshot.get("start_year", year)
    end_year = snapshot.get("end_year", year)
    
    if stype == "Activity":
        keyword = snapshot.get("keyword", "")
        return RedirectResponse(url=f"/activity_search?session_id={session_id}&keyword={keyword}&state_code={state_code}&start_year={start_year}&end_year={end_year}")
    elif stype == "Timeline":
        return RedirectResponse(url=f"/timeline?session_id={session_id}&state_code={state_code}&start_year={start_year}&end_year={end_year}")
    else:
        return RedirectResponse(url=f"/cascade?session_id={session_id}&state_code={state_code}&year={year}")


@router.get("/settings")
async def settings_page(request: Request, active_session_id: Optional[int] = None):
    return templates.TemplateResponse(name="settings.html", request=request, context={"session_id": active_session_id})


@router.get("/api/export_db")
async def export_db():
    data = export_database_json()
    json_str = json.dumps(data, indent=2)
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"fedcascade_export_{date_str}.json"
    
    return StreamingResponse(
        io.StringIO(json_str),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/api/import_db")
async def import_db(request: Request, file: UploadFile = File(...)):
    if not file.filename.endswith('.json'):
        return templates.TemplateResponse(request=request, name="settings.html", context={"import_error": "File must be a JSON payload."})
        
    try:
        content = await file.read()
        data = json.loads(content)
        if "metadata" not in data or "sessions" not in data:
            return templates.TemplateResponse(request=request, name="settings.html", context={"import_error": "Invalid JSON format."})
            
        sessions_added, records_added = import_database_json(data)
        return templates.TemplateResponse(request=request, name="settings.html", context={"import_success": f"Successfully merged {sessions_added} sessions and {records_added} data records."})
    except Exception as e:
        logger.error(f"Failed database import: {e}")
        return templates.TemplateResponse(request=request, name="settings.html", context={"import_error": f"Import failed: {str(e)}"})


@router.post("/api/wipe_db")
async def wipe_db_route(request: Request):
    try:
        wipe_database()
        return templates.TemplateResponse(request=request, name="settings.html", context={"wipe_success": "Database has been completely wiped."})
    except Exception as e:
        return templates.TemplateResponse(request=request, name="settings.html", context={"import_error": f"Wipe failed: {str(e)}"})


@router.get("/cascade")
async def cascade(
    request: Request, 
    session_id: Optional[int] = None, 
    session_name: str = "Unnamed Session", 
    state_code: str = "ALL", 
    year: int = 2023,
    db_filter: str = "all",
    force_refresh: bool = False
):
    client_id = request.headers.get('X-Client-ID', 'default')
    state_code = state_code.upper()
    api_rate_limited = False
    api_error_message = None 
    is_offline = False
    bibliography = {}
    
    if session_id and not force_refresh:
        snapshot = get_snapshot(session_id)
        if snapshot and snapshot.get('search_type') == 'Dashboard' and snapshot.get('state_code') == state_code and snapshot.get('year') == year:
            macro_data = snapshot.get('macro_data', [])
            micro_data = snapshot.get('micro_data', [])
            timestamp = snapshot.get('timestamp')
            session_name = snapshot.get('session_name', session_name)
            is_offline = True
        else:
            session_id = None
            if snapshot:
                session_name = snapshot.get('session_name', session_name)
    
    if not session_id:
        found_id = find_existing_session(state_code, search_type="Dashboard", year=year)
        if found_id:
            session_id = found_id
            
            if not force_refresh:
                snapshot = get_snapshot(session_id)
                if snapshot:
                    macro_data = snapshot.get('macro_data', [])
                    micro_data = snapshot.get('micro_data', [])
                    timestamp = snapshot.get('timestamp')
                    session_name = snapshot.get('session_name', session_name)
                    is_offline = True

    if not is_offline:
        try:
            task1 = fetch_single_year_all_sources(state_code=state_code, year=year, client_id=client_id)
            task_prev = fetch_single_year_all_sources(state_code=state_code, year=year-1, client_id=client_id)
            task2 = generate_bibliography(state_code, year, year, client_id=client_id)
            
            (live_macro, live_micro), (prev_macro, prev_micro), bibliography = await asyncio.gather(task1, task_prev, task2)
            
            if not live_macro and not live_micro:
                api_rate_limited = True
                api_error_message = "USAspending returned an empty payload for this query. The data may be restricted, or the pipeline may not exist for this year."
                macro_data, micro_data = [], []
                timestamp = "API Failed"
            else:
                local_macro = []
                local_micro = []
                if session_id:
                    local_snap = get_snapshot(session_id)
                    if local_snap:
                        local_macro = local_snap.get('macro_data', [])
                        local_micro = local_snap.get('micro_data', [])
                
                local_combined = local_macro + local_micro
                live_combined = live_macro + live_micro
                
                # SAFE ADDITIVE MERGE
                # Keyed on the native source_record_id (per-award precision) instead of the old
                # agency|recipient composite, which collapsed multiple distinct awards between
                # the same two entities into a single record and silently dropped the rest.
                merged_dict = {}
                for f in local_combined:
                    if isinstance(f, dict):
                        f.pop('id', None)
                        f.pop('session_id', None)
                        merged_dict[_merge_key(f)] = f

                diff_to_log = {"added": [], "modified": [], "removed": []}
                
                for lf in live_combined:
                    if isinstance(lf, dict):
                        key = _merge_key(lf)
                        
                        if key not in merged_dict:
                            merged_dict[key] = lf
                            diff_to_log["added"].append({
                                **lf, "old_amount": 0.0, "new_amount": lf.get("amount_usd", 0),
                                "delta": lf.get("amount_usd", 0), "delta_percent": 100.0,
                                "is_retroactive": False, "impact_level": "LOW", "change_type": "ADDED"
                            })
                        else:
                            local_amt = merged_dict[key].get('amount_usd', 0)
                            live_amt = lf.get('amount_usd', 0)
                            delta = live_amt - local_amt
                            
                            if abs(delta) > 1.0:
                                delta_percent = (delta / abs(local_amt)) * 100 if local_amt != 0 else 100.0
                                diff_to_log["modified"].append({
                                    **lf, "old_amount": local_amt, "new_amount": live_amt,
                                    "delta": delta, "delta_percent": delta_percent,
                                    "is_retroactive": False, "impact_level": "MEDIUM", "change_type": "MODIFIED"
                                })
                                merged_dict[key] = lf # Update cache with new live value

                # Ensure output lists are flat, purely un-nested dictionaries
                macro_data = [f for f in merged_dict.values() if f.get('layer') == 'Macro']
                micro_data = [f for f in merged_dict.values() if f.get('layer') == 'Micro']

                session_id = save_data(session_name, state_code, year, macro_data, micro_data, session_id=session_id)
                
                # BUG FIX: Only log to the Watchdog if there is an explicit base session, 
                # AND that base session is NOT the current session!
                log_base_id = get_base_session_id(state_code, "Dashboard", year, year)
                if log_base_id and log_base_id != session_id:
                    if diff_to_log["added"] or diff_to_log["modified"]:
                        save_ledger_changes(log_base_id, session_id, diff_to_log)
                
                if prev_macro or prev_micro:
                    existing_prev = find_existing_session(state_code, search_type="Dashboard", year=year-1)
                    if not existing_prev:
                        save_data("Auto-Cache (YoY)", state_code, year-1, prev_macro, prev_micro, session_id=None)
                
                timestamp = "Just Now"

        except APIFetchError as e:
            logger.error(f"USAspending Explicit Rejection in /cascade: {e}")
            api_rate_limited = True
            api_error_message = str(e)
            macro_data, micro_data = [], []
            prev_macro, prev_micro = [], []
            timestamp = "API Failed"
        except Exception as e:
            logger.error(f"Error fetching live data for Dashboard /cascade: {e}")
            api_rate_limited = True
            api_error_message = f"Unexpected backend failure: {str(e)}"
            macro_data, micro_data = [], []
            prev_macro, prev_micro = [], []
            timestamp = "API Failed"
    else:
        prev_macro, prev_micro = get_multi_year_data(state_code, year - 1, year - 1)
        
    if db_filter == "contracts":
        macro_data = [f for f in macro_data if f.get('source_database') == "Contracts"]
        micro_data = [f for f in micro_data if f.get('source_database') == "Contracts"]
        prev_macro = [f for f in prev_macro if f.get('source_database') == "Contracts"]
        prev_micro = [f for f in prev_micro if f.get('source_database') == "Contracts"]
    elif db_filter == "grants":
        macro_data = [f for f in macro_data if f.get('source_database') == "Grants"]
        micro_data = [f for f in micro_data if f.get('source_database') == "Grants"]
        prev_macro = [f for f in prev_macro if f.get('source_database') == "Grants"]
        prev_micro = [f for f in prev_micro if f.get('source_database') == "Grants"]
    
    # YoY remains an (agency, recipient) AGGREGATE comparison by design:
    # individual award IDs change year-to-year, so the pipeline-level sum is
    # the only meaningful basis for a year-over-year trend.
    prev_macro_dict = {}
    for f in prev_macro:
        k = (f.get('agency'), f.get('recipient'))
        prev_macro_dict[k] = prev_macro_dict.get(k, 0) + f.get('amount_usd', 0)
        
    for f in macro_data:
        prev_amt = prev_macro_dict.get((f.get('agency'), f.get('recipient')), 0)
        f['yoy'] = _calc_yoy(f.get('amount_usd', 0), prev_amt)

    prev_micro_dict = {}
    for f in prev_micro:
        k = (f.get('agency'), f.get('recipient'))
        prev_micro_dict[k] = prev_micro_dict.get(k, 0) + f.get('amount_usd', 0)
        
    for f in micro_data:
        prev_amt = prev_micro_dict.get((f.get('agency'), f.get('recipient')), 0)
        f['yoy'] = _calc_yoy(f.get('amount_usd', 0), prev_amt)

    total_funding = sum([item.get('amount_usd', 0) for item in macro_data]) + sum([item.get('amount_usd', 0) for item in micro_data])
    
    # Total portfolio YoY badge (dashboard.html already renders this when present)
    prev_total_funding = sum([f.get('amount_usd', 0) for f in prev_macro]) + sum([f.get('amount_usd', 0) for f in prev_micro])
    total_yoy = _calc_yoy(total_funding, prev_total_funding) if (total_funding or prev_total_funding) else None
    
    # FIX 1: Pass ALL currently filtered data to the Treemap chart, not just Contracts.
    chart_json = create_treemap_chart(macro_data + micro_data)
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html", 
        context={
            "session_id": session_id,  
            "session_name": session_name,
            "state_code": state_code, 
            "year": year, 
            "db_filter": db_filter,
            "flows": macro_data,
            "micro_flows": micro_data,
            "total": total_funding,
            "total_yoy": total_yoy,
            "api_rate_limited": api_rate_limited,
            "api_error_message": api_error_message, 
            "chart_json": chart_json,
            "bibliography": bibliography,
            "is_offline": is_offline,
            "timestamp": timestamp
        }
    )


@router.get("/api/trend_modal")
async def get_trend_modal(request: Request, state_code: str, agency: str, recipient: str, year: int):
    start_year = year - 4
    macro_data, micro_data = get_multi_year_data(state_code, start_year, year)
    
    all_data = macro_data + micro_data
    
    flow_history = [f for f in all_data if f.get('agency') == agency and f.get('recipient') == recipient]
    flow_history = sorted(flow_history, key=lambda x: x.get('year', 0))
    
    from services.charts import create_mini_trend_chart
    chart_json = create_mini_trend_chart(flow_history, start_year, year)
    
    return templates.TemplateResponse(
        request=request,
        name="components/trend_modal.html",
        context={
            "agency": agency, 
            "recipient": recipient,
            "chart_json": chart_json
        }
    )


# NOTE: /sync_ledger and /commit_merge were REMOVED from this router.
# They duplicated routes in routers/version_control.py, and because this router
# is registered first in main.py, these older copies were shadowing the newer,
# more complete implementations (which handle base-session diffing, merge
# preservation, and Watchdog logging correctly). The canonical versions now
# live exclusively in routers/version_control.py.


@router.get("/timeline")
async def timeline(
    request: Request,
    session_id: Optional[int] = None, 
    session_name: str = "Unnamed Session",
    state_code: str = "ALL",
    start_year: int = 2014,
    end_year: int = 2024,
    db_filter: str = "all",
    force_refresh: bool = False 
):
    client_id = request.headers.get('X-Client-ID', 'default')
    state_code = state_code.upper()
    api_rate_limited = False
    api_error_message = None 
    is_offline = False
    bibliography = {}

    if session_id and not force_refresh:
        snapshot = get_snapshot(session_id)
        if snapshot and snapshot.get('search_type') == 'Timeline' and snapshot.get('state_code') == state_code and snapshot.get('start_year') == start_year and snapshot.get('end_year') == end_year:
            macro_data = snapshot.get('macro_data', [])
            micro_data = snapshot.get('micro_data', [])
            is_offline = True
            timestamp = snapshot.get('timestamp')
            session_name = snapshot.get('session_name', session_name)
    
    if not is_offline and not session_id:
        found_id = find_existing_session(state_code, search_type="Timeline", start_year=start_year, end_year=end_year)
        if found_id and not force_refresh:
            session_id = found_id
            snapshot = get_snapshot(session_id)
            macro_data = snapshot.get('macro_data', [])
            micro_data = snapshot.get('micro_data', [])
            is_offline = True
            timestamp = snapshot.get('timestamp')
            session_name = snapshot.get('session_name', session_name)

    if not is_offline:
        try:
            task1 = fetch_multi_year_flows(state_code, start_year, end_year, client_id=client_id)
            task2 = generate_bibliography(state_code, start_year, end_year, client_id=client_id)
            
            (live_macro, live_micro), bibliography = await asyncio.gather(task1, task2)
            
            macro_data = live_macro
            micro_data = live_micro
            
            session_id = save_multi_year_data(session_name, state_code, start_year, end_year, macro_data, micro_data)
            timestamp = "Just Now"
        except APIFetchError as e:
            logger.error(f"Error fetching timeline: {e}")
            api_rate_limited = True
            api_error_message = str(e)
            macro_data, micro_data = [], []
            timestamp = "API Failed"
        except Exception as e:
            logger.error(f"Error fetching timeline: {e}")
            api_rate_limited = True
            api_error_message = f"Unexpected backend failure: {str(e)}"
            macro_data, micro_data = [], []
            timestamp = "API Failed"
            
    if db_filter == "contracts":
        macro_data = [f for f in macro_data if f.get('source_database') == "Contracts"]
        micro_data = [f for f in micro_data if f.get('source_database') == "Contracts"]
    elif db_filter == "grants":
        macro_data = [f for f in macro_data if f.get('source_database') == "Grants"]
        micro_data = [f for f in micro_data if f.get('source_database') == "Grants"]
    
    all_data = macro_data + micro_data
    total_decade_funding = sum([item.get('amount_usd', 0) for item in all_data])
    
    timeline_json = create_timeline_chart(all_data, start_year, end_year)
    
    return templates.TemplateResponse(
        request=request,
        name="timeline.html",
        context={
            "session_id": session_id,
            "session_name": session_name,
            "state_code": state_code, 
            "start_year": start_year,
            "end_year": end_year,
            "db_filter": db_filter,
            "total": total_decade_funding,
            "timeline_json": timeline_json,
            "flows": macro_data,
            "bibliography": bibliography,
            "timestamp": timestamp,
            "is_offline": is_offline,
            "api_rate_limited": api_rate_limited,
            "api_error_message": api_error_message 
        }
    )