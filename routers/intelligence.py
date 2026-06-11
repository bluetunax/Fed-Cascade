# /routers/intelligence.py

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from typing import Optional
import json
import plotly.graph_objects as go
import plotly.utils
import asyncio

from services.intelligence import get_global_intelligence, search_global_entities, search_offline_ledger
from data_engine import execute_live_global_search

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/bibliography")
async def global_bibliography(request: Request, session_id: Optional[int] = None):
    """The Intelligence Dossier route."""
    intel = get_global_intelligence()
    
    sectors = intel['sectors']
    chart_json = None
    
    if sectors:
        y_labels = list(sectors.keys())[::-1] 
        x_values = list(sectors.values())[::-1]
        
        fig = go.Figure(go.Bar(
            x=x_values, y=y_labels, orientation='h',
            marker=dict(color='#a855f7', opacity=0.8)
        ))
        fig.update_layout(
            title="Frequency of Discovered Federal Sectors", 
            template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            height=max(400, len(y_labels)*30), margin=dict(l=0, r=0, t=40, b=0)
        )
        chart_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
        
    return templates.TemplateResponse(
        request=request,
        name="bibliography.html",
        context={
            "sectors": sectors,
            "recipients": intel['recipients'], 
            "target_zones": intel.get('target_zones', []), 
            "chart_json": chart_json,
            "total_recipients": len(intel['recipients']),
            "session_id": session_id
        }
    )


@router.get("/global_search")
async def global_entity_search(request: Request, q: str = "", session_id: Optional[int] = None):
    """A dedicated fuzzy-search interface hitting both local cache and live external APIs."""
    client_id = request.headers.get('X-Client-ID', 'default')
    
    results = []
    api_rate_limited = False
    
    if q and q.strip() != "":
        # 1. Hit the local SQLite cache instantly
        local_results = search_global_entities(q.strip())
        
        # 2. Concurrently hit the Live USAspending API (Single-Pass Sweep)
        try:
            live_raw_results = await execute_live_global_search(q.strip(), client_id=client_id)
        except Exception:
            live_raw_results = []
            api_rate_limited = True
        
        # 3. Merge the Live API results with the local cache results
        merged_entities = {}
        for item in local_results:
            item['roles'] = set(item['roles'])
            item['years'] = set(item['years'])
            item['target_zones'] = set(item['target_zones']) 
            item['data_sources'] = set(item['data_sources'])
            merged_entities[item['name']] = item
        
        if live_raw_results:
            for live_item in live_raw_results:
                name = live_item['name']
                if name not in merged_entities:
                    merged_entities[name] = {
                        "name": name,
                        "roles": set([live_item['role']]),
                        "total_volume": 0.0,
                        "years": set(),
                        "target_zones": set(), 
                        "data_sources": set()
                    }
                
                merged_entities[name]["roles"].add(live_item['role'])
                merged_entities[name]["total_volume"] += live_item['amount']
                merged_entities[name]["years"].add(live_item['year'])
                
                if 'state_code' in live_item:
                    merged_entities[name]["target_zones"].add(live_item['state_code'])
                    
                merged_entities[name]["data_sources"].add(live_item['source'])
                
        for data in merged_entities.values():
            data["roles"] = sorted(list(data["roles"]))
            data["years"] = sorted(list(data["years"]), reverse=True)
            data["target_zones"] = sorted(list(data["target_zones"]))
            data["data_sources"] = sorted(list(data["data_sources"]))
            results.append(data)
            
        results = sorted(results, key=lambda x: x["total_volume"], reverse=True)
        
    return templates.TemplateResponse(
        request=request,
        name="global_search.html",
        context={
            "query": q,
            "results": results,
            "total_found": len(results),
            "api_rate_limited": api_rate_limited,
            "session_id": session_id
        }
    )


@router.get("/ledger_search")
async def offline_ledger_search(request: Request, q: str = "", search_type: str = "ALL", session_id: Optional[int] = None):
    """Searches the local SQLite cache for explicit Award IDs or Text within Descriptions."""
    
    results = []
    
    if q and q.strip() != "":
        results = search_offline_ledger(q.strip(), search_type=search_type)
        
    total_volume = sum([r.get("amount", 0) for r in results]) if results else 0.0
        
    return templates.TemplateResponse(
        request=request,
        name="ledger_search_results.html",
        context={
            "query": q,
            "search_type": search_type,
            "results": results,
            "total_found": len(results),
            "total_volume": total_volume,
            "session_id": session_id
        }
    )