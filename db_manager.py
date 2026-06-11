# /db_manager.py

from sqlmodel import Session, select, delete, col
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from models import engine, SearchSession, FinancialFlow, ActivityRecord, LedgerChange, User, PinnedRecord, ExclusionRule, PinnedPipeline
from typing import Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

def _ensure_unique_name(session: Session, name: str) -> str:
    """Helper to auto-increment 'Unnamed Session' (e.g. Unnamed Session 1, 2, 3)"""
    if not name or name.strip() == "" or name.strip() == "Unnamed Session":
        statement = select(SearchSession.session_name).where(SearchSession.session_name.like("Unnamed Session%"))
        results = session.exec(statement).all()
        
        if not results:
            return "Unnamed Session 1"
            
        nums = [0]
        for r in results:
            try:
                num = int(r.replace("Unnamed Session", "").strip())
                nums.append(num)
            except ValueError:
                pass
                
        next_num = max(nums) + 1
        return f"Unnamed Session {next_num}"
    return name


def _deduplicate_flows(flows: list) -> list:
    """
    In-memory deduplication of financial flows before hitting the database.
    Utilizes the native API source_record_id for absolute precision.
    """
    seen_keys = set()
    unique_flows = []
    
    for f in flows:
        # Use native API ID for precise deduplication
        sig = f.get('source_record_id')
        
        # Fallback for mock data or legacy imports missing an ID
        if not sig or sig == "UNKNOWN_ID":
            sig = f"{f.get('state_code')}|{f.get('year')}|{f.get('source_database')}|{f.get('agency')}|{f.get('recipient')}|{f.get('amount_usd')}"
            
        if sig not in seen_keys:
            seen_keys.add(sig)
            unique_flows.append(f)
            
    return unique_flows


def _chunk_list(lst: list, chunk_size: int = 500):
    """
    Yields chunks of a list to prevent SQLite 'too many variables' crashes 
    during massive bulk inserts (SQLite limits variables to ~32,000 per query).
    """
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


def save_data(session_name: str, state_code: str, year: int, macro_data: list, micro_data: list, session_id: Optional[int] = None):
    """
    Saves a single year of Dashboard financial flows utilizing native SQLite INSERT OR IGNORE.
    """
    if not macro_data and not micro_data:
        return None

    # De-duplicate in memory first
    macro_data = _deduplicate_flows(macro_data)
    micro_data = _deduplicate_flows(micro_data)

    with Session(engine) as session:
        if session_id:
            existing_session = session.get(SearchSession, session_id)
            if existing_session:
                # Clean BOTH tables to prevent data leaks when swapping search types
                session.exec(delete(FinancialFlow).where(FinancialFlow.session_id == existing_session.id))
                session.exec(delete(ActivityRecord).where(ActivityRecord.session_id == existing_session.id))
                
                existing_session.state_code = state_code
                existing_session.year = year
                existing_session.start_year = year
                existing_session.end_year = year
                existing_session.timestamp = datetime.now(timezone.utc)
                existing_session.search_type = "Dashboard"
                if session_name and session_name != "Unnamed Session":
                    existing_session.session_name = session_name
                
                new_session = existing_session
            else:
                session_id = None
                
        if not session_id:
            session_name = _ensure_unique_name(session, session_name)
            new_session = SearchSession(
                session_name=session_name, 
                state_code=state_code, 
                year=year,
                start_year=year,
                end_year=year,
                search_type="Dashboard"
            )
            session.add(new_session)
            session.commit() 
            session.refresh(new_session)
        
        # Build homogeneous dictionary arrays via Pydantic model dumping
        flow_dicts = []
        for flow_dict in macro_data + micro_data:
            flow_dict["session_id"] = new_session.id
            obj = FinancialFlow(**flow_dict)
            flow_dicts.append(obj.model_dump(exclude={"id"}))
            
        if flow_dicts:
            for chunk in _chunk_list(flow_dicts):
                stmt = sqlite_insert(FinancialFlow).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["session_id", "source_record_id"])
                session.exec(stmt)
            session.commit()
        
        return new_session.id


def save_multi_year_data(session_name: str, state_code: str, start_year: int, end_year: int, macro_data: list, micro_data: list, session_id: Optional[int] = None):
    """
    Saves a multi-year horizon of Timeline flows utilizing native SQLite INSERT OR IGNORE.
    """
    if not macro_data and not micro_data:
        return None

    # De-duplicate in memory first
    macro_data = _deduplicate_flows(macro_data)
    micro_data = _deduplicate_flows(micro_data)

    with Session(engine) as session:
        if session_id:
            existing_session = session.get(SearchSession, session_id)
            if existing_session:
                session.exec(delete(FinancialFlow).where(FinancialFlow.session_id == existing_session.id))
                session.exec(delete(ActivityRecord).where(ActivityRecord.session_id == existing_session.id))
                
                existing_session.state_code = state_code
                existing_session.year = end_year
                existing_session.start_year = start_year
                existing_session.end_year = end_year
                existing_session.timestamp = datetime.now(timezone.utc)
                existing_session.search_type = "Timeline"
                if session_name and session_name != "Unnamed Session":
                    existing_session.session_name = session_name
                    
                new_session = existing_session
            else:
                session_id = None
                
        if not session_id:
            session_name = _ensure_unique_name(session, session_name)
            new_session = SearchSession(
                session_name=session_name, 
                state_code=state_code, 
                year=end_year,
                start_year=start_year,
                end_year=end_year,
                search_type="Timeline"
            )
            session.add(new_session)
            session.commit()
            session.refresh(new_session)
        
        # Build homogeneous dictionary arrays via Pydantic model dumping
        flow_dicts = []
        for flow_dict in macro_data + micro_data:
            flow_dict["session_id"] = new_session.id
            obj = FinancialFlow(**flow_dict)
            flow_dicts.append(obj.model_dump(exclude={"id"}))
            
        if flow_dicts:
            for chunk in _chunk_list(flow_dicts):
                stmt = sqlite_insert(FinancialFlow).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["session_id", "source_record_id"])
                session.exec(stmt)
            session.commit()
        
        return new_session.id


def save_activity_search(session_name: str, keyword: str, state_code: str, start_year: int, end_year: int, results: list, session_id: Optional[int] = None):
    """
    Saves a multi-year keyword-based Activity Search utilizing native SQLite INSERT OR IGNORE.
    """
    if not results:
        return None
        
    with Session(engine) as session:
        if session_id:
            existing_session = session.get(SearchSession, session_id)
            if existing_session:
                session.exec(delete(ActivityRecord).where(ActivityRecord.session_id == existing_session.id))
                session.exec(delete(FinancialFlow).where(FinancialFlow.session_id == existing_session.id))
                
                existing_session.state_code = state_code
                existing_session.year = end_year
                existing_session.start_year = start_year
                existing_session.end_year = end_year
                existing_session.keyword = keyword
                existing_session.timestamp = datetime.now(timezone.utc)
                existing_session.search_type = "Activity"
                if session_name and session_name != "Unnamed Session":
                    existing_session.session_name = session_name
                    
                new_session = existing_session
            else:
                session_id = None
                
        if not session_id:
            session_name = _ensure_unique_name(session, session_name)
            new_session = SearchSession(
                session_name=session_name, 
                state_code=state_code, 
                year=end_year,
                start_year=start_year,
                end_year=end_year,
                keyword=keyword,
                search_type="Activity"
            )
            session.add(new_session)
            session.commit() 
            session.refresh(new_session)
            
        # Memory Deduplication & Build Homogeneous Dictionaries
        seen_keys = set()
        activity_dicts = []
        for res in results:
            sig = res.get('source_record_id')
            if not sig or sig == "UNKNOWN_ID":
                sig = f"{res.get('year')}|{res.get('recipient_name')}|{res.get('agency')}|{res.get('project')}|{res.get('amount')}"
                
            if sig not in seen_keys:
                seen_keys.add(sig)
                
                obj = ActivityRecord(
                    session_id=new_session.id,
                    source_record_id=res.get('source_record_id', 'UNKNOWN_ID'),
                    year=res.get('year'),
                    recipient_name=res.get('recipient_name'),
                    agency=res.get('agency'),
                    amount=res.get('amount'),
                    date=res.get('date'),
                    project=res.get('project'),
                    description=res.get('description'),
                    state_code=res.get('state_code')
                )
                activity_dicts.append(obj.model_dump(exclude={"id"}))
                
        if activity_dicts:
            for chunk in _chunk_list(activity_dicts):
                stmt = sqlite_insert(ActivityRecord).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["session_id", "source_record_id"])
                session.exec(stmt)
            session.commit()
        
        return new_session.id


def find_existing_session(state_code: str, search_type: str = "Dashboard", year: Optional[int] = None, start_year: Optional[int] = None, end_year: Optional[int] = None, keyword: Optional[str] = None) -> Optional[int]:
    """
    Checks if we already have a saved snapshot for these exact parameters.
    Returns the most recent match.
    """
    with Session(engine) as session:
        statement = select(SearchSession).where(
            SearchSession.state_code == state_code,
            SearchSession.search_type == search_type
        )
        
        if search_type == "Dashboard" and year:
            statement = statement.where(SearchSession.year == year)
        elif search_type in ["Timeline", "Activity"] and start_year and end_year:
            statement = statement.where(SearchSession.start_year == start_year, SearchSession.end_year == end_year)
            
        if search_type == "Activity" and keyword:
            statement = statement.where(SearchSession.keyword == keyword)
            
        statement = statement.order_by(SearchSession.id.desc())
        
        result = session.exec(statement).first()
        if result:
            return result.id
        return None


def get_snapshot(session_id: int):
    """
    Reconstructs data payloads based on search type, strictly bound to the session_id.
    """
    with Session(engine) as session:
        db_session = session.get(SearchSession, session_id)
        if not db_session:
            return None
            
        if db_session.search_type == "Activity":
            statement = select(ActivityRecord).where(ActivityRecord.session_id == db_session.id).order_by(ActivityRecord.date.desc())
            results = session.exec(statement).all()
            
            activities = [r.model_dump() for r in results]
            
            return {
                "session_id": db_session.id, 
                "session_name": db_session.session_name,
                "search_type": db_session.search_type,
                "state_code": db_session.state_code,
                "year": db_session.year,
                "start_year": db_session.start_year,
                "end_year": db_session.end_year,
                "keyword": db_session.keyword,
                "timestamp": db_session.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "results": activities,
                "is_offline": True
            }
        else:
            # Dashboard / Timeline behavior
            statement = select(FinancialFlow).where(
                FinancialFlow.session_id == db_session.id
            ).order_by(FinancialFlow.amount_usd.desc())
            
            all_flows = session.exec(statement).all()
            
            macro_data = [f.model_dump() for f in all_flows if f.layer == "Macro"]
            micro_data = [f.model_dump() for f in all_flows if f.layer == "Micro"]
            
            return {
                "session_id": db_session.id, 
                "session_name": db_session.session_name,
                "search_type": db_session.search_type,
                "state_code": db_session.state_code,
                "year": db_session.year,
                "start_year": db_session.start_year,
                "end_year": db_session.end_year,
                "timestamp": db_session.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "macro_data": macro_data,
                "micro_data": micro_data,
                "is_offline": True
            }


def get_multi_year_data(state_code: str, start_year: int, end_year: int):
    """
    Retrieves all flows within a time range, ordered chronologically.
    Used largely for aggregate profile views.
    """
    with Session(engine) as session:
        statement = select(FinancialFlow).where(
            FinancialFlow.state_code == state_code,
            FinancialFlow.year >= start_year,
            FinancialFlow.year <= end_year
        ).order_by(FinancialFlow.year.asc(), FinancialFlow.amount_usd.desc())
        
        all_flows = session.exec(statement).all()
        
        macro_data = [f.model_dump() for f in all_flows if f.layer == "Macro"]
        micro_data = [f.model_dump() for f in all_flows if f.layer == "Micro"]
        
        return macro_data, micro_data


def get_all_sessions():
    """
    Retrieves a list of all saved sessions for the homepage history table.
    Hides background Auto-Cache sessions.
    """
    with Session(engine) as session:
        # Ignore Auto-Cache items so they don't flood the UI
        statement = select(SearchSession).where(
            ~col(SearchSession.session_name).like("Auto-Cache%")
        ).order_by(SearchSession.id.desc())
        
        results = session.exec(statement).all()
        
        sessions = []
        for row in results:
            sessions.append({
                "id": row.id,
                "session_name": row.session_name,
                "search_type": row.search_type,
                "keyword": row.keyword,
                "state_code": row.state_code,
                "year": row.year,
                "start_year": row.start_year,
                "end_year": row.end_year,
                "timestamp": row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "is_base_session": row.is_base_session 
            })
        return sessions


def delete_session(session_id: int):
    """
    Deletes a specific session from the SearchSession table.
    (ActivityRecords and FinancialFlows will cascade delete automatically)
    """
    with Session(engine) as session:
        db_session = session.get(SearchSession, session_id)
        if db_session:
            # Manually delete related records just in case SQLite foreign key constraints are off
            if db_session.search_type == "Activity":
                session.exec(delete(ActivityRecord).where(ActivityRecord.session_id == db_session.id))
            else:
                session.exec(delete(FinancialFlow).where(FinancialFlow.session_id == db_session.id))
                
            session.delete(db_session)
            session.commit()
            return True
        return False


def rename_session(session_id: int, new_name: str) -> bool:
    """Renames a specific search session."""
    with Session(engine) as session:
        db_session = session.get(SearchSession, session_id)
        if db_session and new_name and new_name.strip():
            db_session.session_name = new_name.strip()
            session.add(db_session)
            session.commit()
            return True
        return False


# --- WATCHDOG MODE CONTROLLERS ---

def set_base_session(session_id: int) -> bool:
    """Sets a specific session as the Base Anchor, removing the flag from others in the same repo."""
    with Session(engine) as session:
        target = session.get(SearchSession, session_id)
        if not target:
            return False
            
        # Un-set any existing base session for this exact context
        stmt = select(SearchSession).where(
            SearchSession.state_code == target.state_code,
            SearchSession.search_type == target.search_type,
            SearchSession.start_year == target.start_year,
            SearchSession.end_year == target.end_year,
            SearchSession.is_base_session == True
        )
        existing_bases = session.exec(stmt).all()
        for b in existing_bases:
            b.is_base_session = False
            session.add(b)
            
        # Set the new base
        target.is_base_session = True
        session.add(target)
        session.commit()
        return True


def get_base_session_id(state_code: str, search_type: str, start_year: int, end_year: int) -> Optional[int]:
    """Finds the current Base Anchor ID for a given repository signature."""
    with Session(engine) as session:
        stmt = select(SearchSession.id).where(
            SearchSession.state_code == state_code,
            SearchSession.search_type == search_type,
            SearchSession.start_year == start_year,
            SearchSession.end_year == end_year,
            SearchSession.is_base_session == True
        )
        return session.exec(stmt).first()


def save_ledger_changes(base_session_id: int, new_session_id: int, diff_results: dict):
    """Commits the Watchdog audit trail to the persistent database utilizing chunked SQLite inserts."""
    with Session(engine) as session:
        
        # Delete any existing comparisons between these two specific commits to prevent duplicates
        session.exec(delete(LedgerChange).where(
            LedgerChange.base_session_id == base_session_id,
            LedgerChange.compare_session_id == new_session_id
        ))
        
        all_changes = diff_results.get("added", []) + diff_results.get("removed", []) + diff_results.get("modified", [])
        
        change_dicts = []
        for chg in all_changes:
            obj = LedgerChange(
                base_session_id=base_session_id,
                compare_session_id=new_session_id,
                source_record_id=chg.get("source_record_id", "UNKNOWN_ID"),
                state_code=chg["state_code"],
                year=chg["year"],
                source_database=chg["source_database"],
                agency=chg["agency"],
                recipient=chg["recipient"],
                change_type=chg["change_type"],
                old_amount=chg["old_amount"],
                new_amount=chg["new_amount"],
                delta_usd=chg["delta"],
                delta_percent=chg["delta_percent"],
                is_retroactive=chg["is_retroactive"],
                impact_level=chg["impact_level"]
            )
            change_dicts.append(obj.model_dump(exclude={"id"}))
            
        if change_dicts:
            for chunk in _chunk_list(change_dicts):
                session.exec(sqlite_insert(LedgerChange).values(chunk))
            session.commit()


# --- COLLABORATION & DATABASE MANAGEMENT ---

def wipe_database() -> bool:
    """Permanently deletes all data across all tables."""
    with Session(engine) as session:
        session.exec(delete(PinnedPipeline))
        session.exec(delete(PinnedRecord))
        session.exec(delete(ExclusionRule))
        session.exec(delete(LedgerChange))
        session.exec(delete(FinancialFlow))
        session.exec(delete(ActivityRecord))
        session.exec(delete(SearchSession))
        session.commit()
    return True

def export_database_json() -> dict:
    """Exports the entire active database into a clean, portable JSON dictionary."""
    def _clean_dict(d):
        clean = {}
        for k, v in d.items():
            # Standardize datetime objects so they can be parsed cleanly via JSON
            if isinstance(v, datetime):
                clean[k] = v.isoformat()
            else:
                clean[k] = v
        return clean

    with Session(engine) as session:
        sessions = session.exec(select(SearchSession)).all()
        flows = session.exec(select(FinancialFlow)).all()
        activities = session.exec(select(ActivityRecord)).all()
        changes = session.exec(select(LedgerChange)).all()

        return {
            "metadata": {
                "export_date": datetime.now(timezone.utc).isoformat(),
                "version": "1.0",
                "app": "Fed Cascade OSINT Engine"
            },
            "sessions": [_clean_dict(s.model_dump()) for s in sessions],
            "flows": [_clean_dict(f.model_dump()) for f in flows],
            "activities": [_clean_dict(a.model_dump()) for a in activities],
            "ledger_changes": [_clean_dict(c.model_dump()) for c in changes]
        }

def import_database_json(data: dict) -> tuple[int, int]:
    """
    Safely merges an imported JSON export into the active local database.
    Re-maps Foreign Keys to prevent ID collisions and utilizes SQLite native chunked bulk inserts. 
    """
    sessions_added = 0
    records_added = 0
    
    with Session(engine) as session:
        session_id_map = {}
        
        # 1. Import Search Sessions (These still need to be row-by-row to map new PK IDs)
        for old_sess in data.get("sessions", []):
            original_name = old_sess.get("session_name", "Unnamed")
            new_name = f"[Import] {original_name}"
            
            raw_ts = old_sess.get("timestamp")
            if isinstance(raw_ts, str):
                try:
                    parsed_ts = datetime.fromisoformat(raw_ts)
                except ValueError:
                    parsed_ts = datetime.now(timezone.utc)
            else:
                parsed_ts = datetime.now(timezone.utc)
            
            new_sess = SearchSession(
                session_name=_ensure_unique_name(session, new_name),
                search_type=old_sess.get("search_type"),
                keyword=old_sess.get("keyword"),
                state_code=old_sess.get("state_code", "ALL"),
                year=old_sess.get("year"),
                start_year=old_sess.get("start_year"),
                end_year=old_sess.get("end_year"),
                timestamp=parsed_ts,
                is_base_session=False 
            )
            session.add(new_sess)
            session.commit()
            session.refresh(new_sess)
            
            session_id_map[old_sess.get("id")] = new_sess.id
            sessions_added += 1
            
        # 2. Bulk Import Financial Flows
        flow_dicts = []
        for flow in data.get("flows", []):
            old_s_id = flow.get("session_id")
            if old_s_id in session_id_map:
                flow["session_id"] = session_id_map[old_s_id]
                flow.pop("id", None)
                obj = FinancialFlow(**flow)
                flow_dicts.append(obj.model_dump(exclude={"id"}))
                
        if flow_dicts:
            for chunk in _chunk_list(flow_dicts):
                stmt = sqlite_insert(FinancialFlow).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["session_id", "source_record_id"])
                session.exec(stmt)
            session.commit()
            records_added += len(flow_dicts)
                    
        # 3. Bulk Import Activity Records
        activity_dicts = []
        for act in data.get("activities", []):
            old_s_id = act.get("session_id")
            if old_s_id in session_id_map:
                act["session_id"] = session_id_map[old_s_id]
                act.pop("id", None)
                obj = ActivityRecord(**act)
                activity_dicts.append(obj.model_dump(exclude={"id"}))
                
        if activity_dicts:
            for chunk in _chunk_list(activity_dicts):
                stmt = sqlite_insert(ActivityRecord).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["session_id", "source_record_id"])
                session.exec(stmt)
            session.commit()
            records_added += len(activity_dicts)
                    
        # 4. Bulk Import Ledger Changes
        change_dicts = []
        for lc in data.get("ledger_changes", []):
            old_base_id = lc.get("base_session_id")
            old_comp_id = lc.get("compare_session_id")
            
            if old_base_id in session_id_map and old_comp_id in session_id_map:
                lc["base_session_id"] = session_id_map[old_base_id]
                lc["compare_session_id"] = session_id_map[old_comp_id]
                lc.pop("id", None)
                
                raw_dt = lc.get("detected_at")
                if isinstance(raw_dt, str):
                    try:
                        lc["detected_at"] = datetime.fromisoformat(raw_dt)
                    except ValueError:
                        lc["detected_at"] = datetime.now(timezone.utc)
                        
                obj = LedgerChange(**lc)
                change_dicts.append(obj.model_dump(exclude={"id"}))
                
        if change_dicts:
            for chunk in _chunk_list(change_dicts):
                session.exec(sqlite_insert(LedgerChange).values(chunk))
            session.commit()
            records_added += len(change_dicts)
                    
    return sessions_added, records_added


# --- USER PROFILE, BOOKMARKING & EXCLUSION LOGIC ---

def get_or_create_local_user() -> User:
    """Ensures a default user exists in the database to attach pins and rules to."""
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "local_analyst")).first()
        if not user:
            user = User(username="local_analyst")
            session.add(user)
            session.commit()
            session.refresh(user)
        return user

def toggle_pinned_record(user_id: int, record_data: dict) -> bool:
    source_id = record_data.get('source_record_id')
    if not source_id or source_id == "UNKNOWN_ID":
        return False
        
    with Session(engine) as session:
        existing = session.exec(select(PinnedRecord).where(
            PinnedRecord.user_id == user_id, 
            PinnedRecord.source_record_id == source_id
        )).first()
        
        if existing:
            session.delete(existing)
            session.commit()
            return False
        else:
            new_pin = PinnedRecord(
                user_id=user_id,
                source_record_id=source_id,
                year=record_data.get('year', 2023),
                agency=record_data.get('agency', 'Unknown'),
                recipient=record_data.get('recipient_name') or record_data.get('recipient', 'Unknown'),
                project_title=record_data.get('project', 'General Flow'),
                description=record_data.get('description', ''),
                amount_usd=record_data.get('amount') or record_data.get('amount_usd', 0.0),
                state_code=record_data.get('state_code', 'ALL')
            )
            session.add(new_pin)
            session.commit()
            return True

def get_user_pins(user_id: int) -> list:
    with Session(engine) as session:
        pins = session.exec(select(PinnedRecord).where(PinnedRecord.user_id == user_id).order_by(PinnedRecord.pinned_at.desc())).all()
        return [p.model_dump() for p in pins]
        
def get_user_pinned_ids(user_id: int) -> set:
    with Session(engine) as session:
        ids = session.exec(select(PinnedRecord.source_record_id).where(PinnedRecord.user_id == user_id)).all()
        return set(ids)

def add_exclusion_rule(user_id: int, keyword: str, rule_type: str = "Recipient") -> bool:
    if not keyword or not keyword.strip():
        return False
    with Session(engine) as session:
        existing = session.exec(select(ExclusionRule).where(
            ExclusionRule.user_id == user_id,
            ExclusionRule.keyword == keyword.strip(),
            ExclusionRule.rule_type == rule_type
        )).first()
        
        if not existing:
            rule = ExclusionRule(user_id=user_id, keyword=keyword.strip(), rule_type=rule_type)
            session.add(rule)
            session.commit()
        return True

def remove_exclusion_rule(rule_id: int):
    with Session(engine) as session:
        rule = session.get(ExclusionRule, rule_id)
        if rule:
            session.delete(rule)
            session.commit()

def get_exclusion_rules(user_id: int) -> list:
    with Session(engine) as session:
        rules = session.exec(select(ExclusionRule).where(ExclusionRule.user_id == user_id).order_by(ExclusionRule.created_at.desc())).all()
        return [r.model_dump() for r in rules]


# --- PIPELINE TREND BOOKMARKING ---

def toggle_pinned_pipeline(user_id: int, state_code: str, agency: str, recipient: str, year: int) -> bool:
    with Session(engine) as session:
        existing = session.exec(select(PinnedPipeline).where(
            PinnedPipeline.user_id == user_id,
            PinnedPipeline.state_code == state_code,
            PinnedPipeline.agency == agency,
            PinnedPipeline.recipient == recipient,
            PinnedPipeline.year == year
        )).first()
        
        if existing:
            session.delete(existing)
            session.commit()
            return False
        else:
            new_pin = PinnedPipeline(
                user_id=user_id,
                state_code=state_code,
                agency=agency,
                recipient=recipient,
                year=year
            )
            session.add(new_pin)
            session.commit()
            return True

def get_user_pinned_pipelines(user_id: int) -> list:
    with Session(engine) as session:
        pins = session.exec(select(PinnedPipeline).where(PinnedPipeline.user_id == user_id).order_by(PinnedPipeline.pinned_at.desc())).all()
        return [p.model_dump() for p in pins]

def is_pipeline_pinned(user_id: int, state_code: str, agency: str, recipient: str, year: int) -> bool:
    with Session(engine) as session:
        existing = session.exec(select(PinnedPipeline).where(
            PinnedPipeline.user_id == user_id,
            PinnedPipeline.state_code == state_code,
            PinnedPipeline.agency == agency,
            PinnedPipeline.recipient == recipient,
            PinnedPipeline.year == year
        )).first()
        return bool(existing)