# /routers/changes.py

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, func
from sqlalchemy import desc
from typing import Optional
import csv
import io
from datetime import datetime, timezone

from models import engine, LedgerChange, SearchSession

router = APIRouter()
templates = Jinja2Templates(directory="templates")


class _MissingBaseSession:
    """
    Stand-in for a base SearchSession that has been deleted while its
    LedgerChange rows survived. Prevents the printable report template
    from crashing on `.timestamp.strftime(...)` against None.
    """
    session_name = "Unknown (Deleted Base)"
    
    class _TS:
        @staticmethod
        def strftime(fmt: str) -> str:
            return "Unknown"
            
    timestamp = _TS()


def _build_filtered_changes_stmt(state_code: Optional[str], impact: Optional[str], retroactive: Optional[str]):
    """Shared filter logic for the dashboard, CSV export, and printable report."""
    stmt = select(LedgerChange, SearchSession).join(
        SearchSession, LedgerChange.compare_session_id == SearchSession.id
    )
    
    if state_code and state_code != "ALL":
        stmt = stmt.where(LedgerChange.state_code == state_code)
    if impact and impact != "ALL":
        stmt = stmt.where(LedgerChange.impact_level == impact)
    if retroactive == "TRUE":
        stmt = stmt.where(LedgerChange.is_retroactive == True)
    elif retroactive == "FALSE":
        stmt = stmt.where(LedgerChange.is_retroactive == False)
        
    # Order by absolute dollar impact (biggest changes at the top)
    return stmt.order_by(desc(func.abs(LedgerChange.delta_usd)))


@router.get("/changes")
async def watchdog_dashboard(
    request: Request,
    state_code: Optional[str] = "ALL",
    impact: Optional[str] = "ALL",
    retroactive: Optional[str] = "ALL"
):
    """Renders the Watchdog Volatility Dashboard."""
    with Session(engine) as session:
        stmt = _build_filtered_changes_stmt(state_code, impact, retroactive)
        results = session.exec(stmt).all()
        
        changes_data = []
        total_volatility = 0.0
        retro_count = 0
        high_impact_count = 0
        
        # Cache base session names to avoid redundant DB hits
        base_names_cache = {}
        
        for change, compare_session in results:
            total_volatility += abs(change.delta_usd)
            if change.is_retroactive: 
                retro_count += 1
            if change.impact_level == "HIGH": 
                high_impact_count += 1
            
            if change.base_session_id not in base_names_cache:
                base_sess = session.get(SearchSession, change.base_session_id)
                base_names_cache[change.base_session_id] = base_sess.session_name if base_sess else "Unknown Base"
                
            changes_data.append({
                "change": change,
                "compare_session_name": compare_session.session_name,
                "compare_timestamp": compare_session.timestamp.strftime("%Y-%m-%d"),
                "base_session_name": base_names_cache[change.base_session_id]
            })
            
        # Get unique zones that actually have logged changes for the dropdown filter.
        # Filter out None/empty values which would crash sorted() on mixed types.
        states_stmt = select(LedgerChange.state_code).distinct()
        available_state_codes = sorted([s for s in session.exec(states_stmt).all() if s])
        
    return templates.TemplateResponse(
        request=request,
        name="changes.html",
        context={
            "changes": changes_data,
            "total_volatility": total_volatility,
            "retro_count": retro_count,
            "high_impact_count": high_impact_count,
            "total_changes": len(changes_data),
            "available_state_codes": available_state_codes,
            "current_state_code": state_code,
            "current_impact": impact,
            "current_retro": retroactive
        }
    )


@router.get("/changes/export")
async def export_changes_csv(
    state_code: Optional[str] = "ALL",
    impact: Optional[str] = "ALL",
    retroactive: Optional[str] = "ALL"
):
    """Generates a CSV export of the filtered audit trail."""
    with Session(engine) as session:
        stmt = _build_filtered_changes_stmt(state_code, impact, retroactive)
        results = session.exec(stmt).all()

        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write Headers (Federal taxonomy)
        writer.writerow([
            "Target Zone", "Fiscal Year", "Database", "Agency", "Recipient", 
            "Change Type", "Impact Level", "Is Retroactive", 
            "Old Amount (USD)", "New Amount (USD)", "Delta (USD)", "Delta (%)", 
            "Detected At"
        ])
        
        # Write Data
        for change, compare_session in results:
            writer.writerow([
                change.state_code,
                change.year,
                change.source_database,
                change.agency,
                change.recipient,
                change.change_type,
                change.impact_level,
                "YES" if change.is_retroactive else "NO",
                change.old_amount,
                change.new_amount,
                change.delta_usd,
                round(change.delta_percent, 2),
                change.detected_at.strftime("%Y-%m-%d %H:%M:%S")
            ])
            
    output.seek(0)
    filename = f"fed_cascade_audit_log_{state_code}.csv"
    
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/changes/report")
async def printable_watchdog_report(
    request: Request,
    state_code: Optional[str] = "ALL",
    impact: Optional[str] = "ALL",
    retroactive: Optional[str] = "ALL"
):
    """Generates a clean, printable HTML report of the volatility."""
    with Session(engine) as session:
        stmt = _build_filtered_changes_stmt(state_code, impact, retroactive)
        results = session.exec(stmt).all()
        
        report_data = []
        base_sessions_cache = {}
        total_volatility = 0.0
        
        for change, compare_session in results:
            total_volatility += abs(change.delta_usd)
            
            if change.base_session_id not in base_sessions_cache:
                base_sess = session.get(SearchSession, change.base_session_id)
                # Guard: if the base session was deleted but its LedgerChanges
                # survived, substitute a placeholder so the report template
                # doesn't crash on .timestamp.strftime(...)
                base_sessions_cache[change.base_session_id] = base_sess if base_sess else _MissingBaseSession()
            
            base_session = base_sessions_cache[change.base_session_id]
            
            report_data.append({
                "change": change,
                "compare_session": compare_session,
                "base_session": base_session
            })
            
    return templates.TemplateResponse(
        request=request,
        name="watchdog_report.html",
        context={
            "report_data": report_data,
            "total_volatility": total_volatility,
            "state_code": state_code,
            "impact": impact,
            "retroactive": retroactive,
            "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        }
    )