# /routers/version_control.py

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from collections import defaultdict
from typing import Optional
import logging
import json
import asyncio

# Import our database logic
from db_manager import (
    get_all_sessions, 
    delete_session, 
    get_snapshot,
    save_data,
    save_multi_year_data,
    save_activity_search,
    rename_session,
    set_base_session,      
    get_base_session_id,   
    save_ledger_changes    
)

# Import Diff Engine
from services.version_control import compare_ledgers
from data_engine import fetch_single_year_all_sources

# Initialize Router Logger
logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _merge_key(f: dict) -> str:
    """
    Builds a stable per-record merge key for ledger merges.
    
    Prefers the native USAspending source_record_id so that multiple distinct
    awards between the same Agency -> Recipient pair in the same year are 
    preserved as separate records (the old composite key silently collapsed them).
    
    Falls back to a deterministic composite signature (WITHOUT the dollar amount)
    for legacy records or unstable FALLBACK_ hashes, so a modified amount still
    matches its original record instead of duplicating it.
    """
    sig = str(f.get('source_record_id', '') or '')
    
    if sig and sig != "UNKNOWN_ID" and not sig.startswith("FALLBACK_"):
        return sig
        
    return (
        f"AGG_{f.get('year')}|{f.get('source_database')}|{f.get('layer')}|"
        f"{f.get('agency')}|{f.get('recipient')}|{f.get('flow_type')}|{f.get('award_id_piid')}"
    )


@router.get("/ledger_hub")
async def ledger_hub(request: Request, session_id: Optional[int] = None):
    """Renders the Ledger Version Control Hub, grouping sessions into 'Repositories'."""
    raw_sessions = get_all_sessions()
    
    # Group sessions by their 'Target' to act like Git repositories/branches
    repositories = defaultdict(list)
    
    for session in raw_sessions:
        state_code = session.get('state_code', 'ALL')
        stype = session.get('search_type', 'Unknown')
        
        # Format the time horizon cleanly
        if stype == "Timeline" or (stype == "Activity" and session.get('start_year') != session.get('end_year')):
            time_label = f"{session.get('start_year')}-{session.get('end_year')}"
        else:
            time_label = str(session.get('year', 'Unknown'))
            
        # Create a unique repository key (e.g., "UKR | Dashboard | 2023")
        repo_key = f"{state_code}|{stype}|{time_label}"
        
        repositories[repo_key].append(session)
        
    # Convert to a list of dicts for easier Jinja templating
    # Sort repos alphabetically, and ensure the branches inside are sorted by newest first
    sorted_repos = []
    for k, v in sorted(repositories.items()):
        
        # Sort branches by timestamp
        v_sorted = sorted(v, key=lambda x: x['timestamp'], reverse=True)
        
        # Split the key back up for nice UI formatting
        parts = k.split('|')
        
        sorted_repos.append({
            "repo_id": k,
            "state_code": parts[0],
            "type": parts[1],
            "time_label": parts[2],
            "branches": v_sorted
        })
    
    return templates.TemplateResponse(
        request=request,
        name="ledger_hub.html",
        context={
            "repositories": sorted_repos,
            "total_snapshots": len(raw_sessions),
            "session_id": session_id
        }
    )


@router.delete("/squash_repo")
async def squash_repo(state_code: str, stype: str, time_label: str):
    """
    Finds all branches matching this exact repo signature, 
    keeps the newest one, and deletes the rest.
    """
    raw_sessions = get_all_sessions()
    
    # Filter to only sessions that match this specific "Repository"
    matching_sessions = []
    for s in raw_sessions:
        s_state = s.get('state_code', 'ALL')
        s_stype = s.get('search_type', 'Unknown')
        
        if s_stype == "Timeline" or (s_stype == "Activity" and s.get('start_year') != s.get('end_year')):
            s_time = f"{s.get('start_year')}-{s.get('end_year')}"
        else:
            s_time = str(s.get('year', 'Unknown'))
            
        if s_state == state_code and s_stype == stype and s_time == time_label:
            matching_sessions.append(s)
            
    if len(matching_sessions) <= 1:
        return HTMLResponse(status_code=200) # Nothing to squash
        
    # Sort by timestamp (newest first)
    matching_sessions = sorted(matching_sessions, key=lambda x: x['timestamp'], reverse=True)
    
    # Keep the first one (HEAD), delete the rest
    head_session = matching_sessions[0]
    sessions_to_delete = matching_sessions[1:]
    
    for s in sessions_to_delete:
        delete_session(s['id'])
        
    # Return HTMX signal to force the page to reload so the UI updates
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})


@router.post("/rename_session/{session_id}")
async def rename_session_route(session_id: int, request: Request):
    """
    Renames a session via an HTMX prompt.
    NOTE: We intentionally store the RAW string. Jinja2 autoescapes on render,
    so pre-escaping here caused double-escaping (e.g. "O'Brien & Co" displayed
    as "O&#39;Brien &amp; Co" in the UI).
    """
    new_name = request.headers.get("HX-Prompt")
    if new_name:
        rename_session(session_id, new_name.strip())
    
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})


@router.get("/merge_modal/{session_id}")
async def merge_modal(request: Request, session_id: int):
    """Returns an HTMX modal allowing the user to select a target session to merge into."""
    source_snap = get_snapshot(session_id)
    if not source_snap:
        raise HTTPException(status_code=404, detail="Session not found")
        
    all_sessions = get_all_sessions()
    
    # Filter targets: Must be the same search type, but NOT the exact same session
    compatible_targets = [
        s for s in all_sessions 
        if s["search_type"] == source_snap.get("search_type") and s["id"] != session_id
    ]
    
    return templates.TemplateResponse(
        request=request,
        name="components/merge_modal.html",
        context={
            "source_id": session_id,
            "source_name": source_snap.get("session_name", "Unknown"),
            "targets": compatible_targets
        }
    )


@router.get("/merge_review")
async def merge_review(request: Request, target_id: int, source_id: int):
    """Shows a diff preview of merging the source branch INTO the target branch."""
    target_snap = get_snapshot(target_id)
    source_snap = get_snapshot(source_id)
    
    if not target_snap or not source_snap:
        raise HTTPException(status_code=404, detail="Session not found")
        
    stype = target_snap.get("search_type")
    
    if stype != source_snap.get("search_type"):
        logger.error(f"Merge Review Failed: Attempted to merge {stype} into {source_snap.get('search_type')}.")
        raise HTTPException(status_code=400, detail="Cannot merge branches of different search types.")
        
    if stype in ["Dashboard", "Timeline"]:
        target_flows = target_snap.get('macro_data', []) + target_snap.get('micro_data', [])
        source_flows = source_snap.get('macro_data', []) + source_snap.get('micro_data', [])
        
        # FIXED: Wrap in to_thread to prevent event loop blocking
        diff = await asyncio.to_thread(compare_ledgers, target_flows, source_flows)
    else:
        # Activity Search deduplication logic
        t_results = target_snap.get('results', [])
        s_results = source_snap.get('results', [])
        
        t_keys = {f"{r['year']}|{r.get('recipient_name')}|{r.get('project')}|{r.get('description')}" for r in t_results}
        
        added = []
        for r in s_results:
            key = f"{r['year']}|{r.get('recipient_name')}|{r.get('project')}|{r.get('description')}"
            if key not in t_keys:
                added.append(r)
                
        diff = {
            "added": added,
            "modified": [],
            "removed": [],
            "totals": {
                "local_volume": sum(r['amount'] for r in t_results),
                "live_volume": sum(r['amount'] for r in s_results),
                "net_change": sum(r['amount'] for r in added)
            }
        }
        
    return templates.TemplateResponse(
        request=request,
        name="merge_review.html",
        context={
            "target_id": target_id,
            "source_id": source_id,
            "target_name": target_snap.get('session_name', 'Target'),
            "source_name": source_snap.get('session_name', 'Source'),
            "stype": stype,
            "diff": diff
        }
    )


@router.post("/commit_local_merge")
async def commit_local_merge(target_id: int = Form(...), source_id: int = Form(...)):
    """Actually performs the merge, updates the target session, and deletes the source session."""
    target_snap = get_snapshot(target_id)
    source_snap = get_snapshot(source_id)
    
    if not target_snap or not source_snap:
        raise HTTPException(status_code=404, detail="Session not found")
        
    stype = target_snap.get("search_type")
    
    if stype in ["Dashboard", "Timeline"]:
        target_flows = target_snap.get('macro_data', []) + target_snap.get('micro_data', [])
        source_flows = source_snap.get('macro_data', []) + source_snap.get('micro_data', [])
        
        # Keyed on source_record_id (with stable fallback) so distinct awards
        # between the same Agency -> Recipient pair survive the merge.
        # On collision, the higher reported volume wins.
        merged_dict = {}
        for f in target_flows + source_flows:
            f.pop('id', None)
            f.pop('session_id', None)
            
            key = _merge_key(f)
            if key not in merged_dict:
                merged_dict[key] = f
            else:
                if float(f.get('amount_usd', 0)) > float(merged_dict[key].get('amount_usd', 0)):
                    merged_dict[key] = f
                    
        final_macro = [f for f in merged_dict.values() if f['layer'] == 'Macro']
        final_micro = [f for f in merged_dict.values() if f['layer'] == 'Micro']
        
        if stype == "Dashboard":
            save_data(target_snap['session_name'], target_snap['state_code'], target_snap['year'], final_macro, final_micro, session_id=target_id)
        else:
            save_multi_year_data(target_snap['session_name'], target_snap['state_code'], target_snap['start_year'], target_snap['end_year'], final_macro, final_micro, session_id=target_id)
            
    else:
        t_results = target_snap.get('results', [])
        s_results = source_snap.get('results', [])
        
        merged_results = {}
        for r in t_results + s_results:
            r.pop('id', None)
            r.pop('session_id', None)
            
            key = f"{r['year']}|{r.get('recipient_name')}|{r.get('project')}|{r.get('description')}"
            if key not in merged_results:
                merged_results[key] = r
            else:
                if float(r.get('amount', 0)) > float(merged_results[key].get('amount', 0)):
                    merged_results[key] = r
                    
        final_results = list(merged_results.values())
        
        save_activity_search(
            target_snap['session_name'], target_snap['keyword'], 
            target_snap['state_code'], target_snap['start_year'], target_snap['end_year'], 
            final_results, session_id=target_id
        )
        
    delete_session(source_id)
    return RedirectResponse(url="/ledger_hub", status_code=303)


# --- Ledger Synchronization (Version Control) Endpoints ---

@router.get("/sync_ledger")
async def sync_ledger(request: Request, session_id: int, state_code: str, year: int):
    client_id = request.headers.get('X-Client-ID', 'default')
    
    snapshot = get_snapshot(session_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Session not found")
        
    local_macro = snapshot.get('macro_data', [])
    local_micro = snapshot.get('micro_data', [])
    local_combined = local_macro + local_micro
    
    try:
        live_macro, live_micro = await fetch_single_year_all_sources(state_code, year, client_id=client_id)
        live_combined = live_macro + live_micro
        
        # FIXED: Wrap in to_thread to prevent event loop blocking
        diff_results = await asyncio.to_thread(compare_ledgers, local_combined, live_combined)
        
    except Exception as e:
        logger.error(f"Error generating sync diff for /sync_ledger: {e}")
        raise HTTPException(status_code=503, detail="Unable to reach external APIs to sync ledger. Please try again later.")
    
    return templates.TemplateResponse(
        request=request,
        name="diff.html",
        context={
            "session_id": session_id,
            "session_name": snapshot.get('session_name', 'Unnamed Session'),
            "state_code": state_code,
            "year": year,
            "diff": diff_results,
            "live_macro_json": json.dumps(live_macro),
            "live_micro_json": json.dumps(live_micro)
        }
    )

@router.post("/commit_merge")
async def commit_merge(
    session_id: int = Form(...),
    state_code: str = Form(...),
    year: int = Form(...),
    live_macro_json: str = Form(...),
    live_micro_json: str = Form(...)
):
    try:
        live_macro = json.loads(live_macro_json)
        live_micro = json.loads(live_micro_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload provided")
        
    snapshot = get_snapshot(session_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Session not found")
        
    local_macro = snapshot.get('macro_data', [])
    local_micro = snapshot.get('micro_data', [])
    local_combined = local_macro + local_micro
    live_combined = live_macro + live_micro

    # Safe Additive Merge: keyed on source_record_id (with stable fallback)
    # so distinct awards between the same pair are preserved. Live values
    # overwrite local on conflict only if higher (protects against partial
    # live payloads silently shrinking the cache).
    merged_dict = {}
    for f in local_combined:
        f.pop('id', None)
        f.pop('session_id', None)
        merged_dict[_merge_key(f)] = f
        
    for f in live_combined:
        f.pop('id', None)
        f.pop('session_id', None)
        key = _merge_key(f)
        if key not in merged_dict:
            merged_dict[key] = f
        else:
            if float(f.get('amount_usd', 0)) > float(merged_dict[key].get('amount_usd', 0)):
                merged_dict[key] = f

    final_macro = [f for f in merged_dict.values() if f['layer'] == 'Macro']
    final_micro = [f for f in merged_dict.values() if f['layer'] == 'Micro']
    final_combined = final_macro + final_micro

    base_id = get_base_session_id(state_code, "Dashboard", year, year)
    log_base_id = base_id if base_id else session_id
    
    diff_to_log = None
    if log_base_id:
        base_snap = get_snapshot(log_base_id)
        if base_snap:
            base_flows = base_snap.get('macro_data', []) + base_snap.get('micro_data', [])
            # FIXED: Wrap in to_thread to prevent event loop blocking
            diff_to_log = await asyncio.to_thread(compare_ledgers, base_flows, final_combined)

    session_name = snapshot.get('session_name', 'Unnamed Session') if snapshot else 'Unnamed Session'
    save_data(session_name, state_code, year, final_macro, final_micro, session_id=session_id)

    if log_base_id and log_base_id != session_id and diff_to_log and (diff_to_log.get("added") or diff_to_log.get("modified")):
        save_ledger_changes(log_base_id, session_id, diff_to_log)
    
    return RedirectResponse(url=f"/cascade?session_id={session_id}&state_code={state_code}&year={year}", status_code=303)


# --- WATCHDOG MODE CONTROLLERS ---

@router.post("/set_base_session/{session_id}")
async def set_base_session_route(session_id: int):
    success = set_base_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    target_snap = get_snapshot(session_id)
    if target_snap:
        all_sessions = get_all_sessions()
        for s in all_sessions:
            if s['id'] != session_id and \
               s['state_code'] == target_snap['state_code'] and \
               s['search_type'] == target_snap['search_type'] and \
               s['start_year'] == target_snap['start_year'] and \
               s['end_year'] == target_snap['end_year']:
               
                compare_snap = get_snapshot(s['id'])
                if compare_snap and target_snap['search_type'] in ["Dashboard", "Timeline"]:
                    base_flows = target_snap.get('macro_data', []) + target_snap.get('micro_data', [])
                    compare_flows = compare_snap.get('macro_data', []) + compare_snap.get('micro_data', [])
                    
                    # FIXED: Wrap in to_thread to prevent event loop blocking
                    diff = await asyncio.to_thread(compare_ledgers, base_flows, compare_flows)
                    save_ledger_changes(session_id, s['id'], diff)
                    
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})


@router.post("/trigger_audit/{session_id}")
async def trigger_audit(session_id: int):
    target_snap = get_snapshot(session_id)
    if not target_snap:
        raise HTTPException(status_code=404, detail="Session not found")
        
    base_id = get_base_session_id(
        target_snap['state_code'], target_snap['search_type'], 
        target_snap['start_year'], target_snap['end_year']
    )
    
    if not base_id or base_id == session_id:
        return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})
        
    base_snap = get_snapshot(base_id)
    
    if base_snap and target_snap['search_type'] in ["Dashboard", "Timeline"]:
        base_flows = base_snap.get('macro_data', []) + base_snap.get('micro_data', [])
        compare_flows = target_snap.get('macro_data', []) + target_snap.get('micro_data', [])
        
        # FIXED: Wrap in to_thread to prevent event loop blocking
        diff = await asyncio.to_thread(compare_ledgers, base_flows, compare_flows)
        save_ledger_changes(base_id, session_id, diff)
        
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})