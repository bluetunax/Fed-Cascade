# /services/intelligence.py

from sqlmodel import Session, select
from sqlalchemy import or_
from collections import Counter, defaultdict
import re
import logging
from models import engine, SearchSession, FinancialFlow, ActivityRecord, PinnedRecord

# Import the Federal Normalizer
from services.entity_normalization import normalize_entity_name

logger = logging.getLogger(__name__)

def _advanced_text_match(keyword: str, text: str) -> bool:
    """
    Performs a robust word-boundary search using RegEx.
    This prevents false positives.
    """
    if not text:
        return False
    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
    return bool(pattern.search(text))

def get_global_intelligence():
    """Scans the local database to build a master Factbook of Vendors, States, Sectors, and past searches."""
    with Session(engine) as session:
        activity_records = session.exec(select(ActivityRecord)).all()
        
        # Federal/USAspending Operational Clusters
        target_keywords = [
            "cyber", "software", "cloud", "artificial intelligence", "data center",
            "missile", "munitions", "aerospace", "aviation", "surveillance",
            "research", "development", "laboratory", "clinical", "vaccine",
            "construction", "infrastructure", "highway", "bridge", "transit",
            "environmental", "cleanup", "disaster", "fema", "emergency",
            "education", "training", "logistics", "telecommunications", "security"
        ]
        
        sector_counts = Counter()
        recipient_totals = defaultdict(float)
        recipient_mentions = Counter()
        
        # State metrics
        state_macro = defaultdict(float)
        state_micro = defaultdict(float)
        state_mentions = Counter()
        
        for r in activity_records:
            full_text = f"{r.description} {r.project}"
            for kw in target_keywords:
                if _advanced_text_match(kw, full_text):
                    display_name = kw.title()
                    # Custom standardizations
                    if display_name in ["Cyber", "Software"]: display_name = "Cybersecurity & IT"
                    if display_name in ["Missile", "Munitions"]: display_name = "Defense Systems"
                    if display_name in ["Research", "Development"]: display_name = "R&D"
                    if display_name in ["Construction", "Infrastructure"]: display_name = "Infrastructure"
                    sector_counts[display_name] += 1
                    
            # Catalog Recipients
            recipient_totals[r.recipient_name] += r.amount
            recipient_mentions[r.recipient_name] += 1
            
            # Catalog States
            s_code = r.state_code.upper() if r.state_code else "UNKNOWN"
            state_mentions[s_code] += 1

        # Process Financial Flows
        all_flows = session.exec(select(FinancialFlow)).all()
        
        for f in all_flows:
            s_code = f.state_code.upper()
            state_mentions[s_code] += 1
            
            if f.layer == "Micro":
                recipient_totals[f.recipient] += f.amount_usd
                recipient_mentions[f.recipient] += 1
                state_micro[s_code] += f.amount_usd
            else:
                state_macro[s_code] += f.amount_usd
            
        # Get distinct custom keywords the user has previously searched
        stmt = select(SearchSession.keyword).where(
            SearchSession.search_type == "Activity", 
            SearchSession.keyword != None
        )
        searched_kws = session.exec(stmt).all()
        unique_searches = list(set([k.title() for k in searched_kws if k]))
        
        # Sort Recipients by total funding
        sorted_recipients = [{"name": k, "total": v, "mentions": recipient_mentions[k]} 
                             for k, v in sorted(recipient_totals.items(), key=lambda item: item[1], reverse=True)]
                       
        # Compile and sort states
        all_states = set(list(state_macro.keys()) + list(state_micro.keys()) + list(state_mentions.keys()))
        sorted_states = []
        for s in all_states:
            if s == "ALL" or s == "UNKNOWN": continue 
            total_vol = state_macro[s] + state_micro[s]
            sorted_states.append({
                "name": s,
                "macro": state_macro[s],
                "micro": state_micro[s],
                "total": total_vol,
                "mentions": state_mentions[s]
            })
        sorted_states = sorted(sorted_states, key=lambda x: x['total'], reverse=True)
        
        return {
            "sectors": dict(sector_counts.most_common()),
            "recipients": sorted_recipients, 
            "target_zones": sorted_states, 
            "searched_keywords": unique_searches
        }


def get_recipient_global_dossier(recipient_name: str):
    """Fetches all known intelligence for a specific Recipient across the entire local cache."""
    normalized_target = normalize_entity_name(recipient_name).lower()
    raw_target = recipient_name.lower()
    
    with Session(engine) as session:
        all_activities = session.exec(select(ActivityRecord)).all()
        all_flows = session.exec(select(FinancialFlow)).all()
        
        activities = []
        for a in all_activities:
            db_name = str(a.recipient_name).lower()
            if (normalized_target in db_name or raw_target in db_name or 
               (len(db_name) > 4 and db_name in raw_target)):
                activities.append(a)
        
        flows = []
        for f in all_flows:
            db_recip = str(f.recipient).lower()
            if (normalized_target in db_recip or raw_target in db_recip or 
               (len(db_recip) > 4 and db_recip in raw_target)):
                flows.append(f)
        
        states = set()
        years = set()
        agencies = set()
        
        unique_projects = {}
        for a in activities:
            states.add(a.state_code)
            years.add(a.year)
            agencies.add(a.agency)
            
            proj_key = f"{a.year}-{a.project}-{a.description}"
            if proj_key not in unique_projects:
                unique_projects[proj_key] = {
                    "year": a.year,
                    "state_code": a.state_code, 
                    "agency": a.agency,       
                    "project": a.project,
                    "description": a.description,
                    "amount": a.amount,
                    "date": a.date
                }
                
        flow_records = []
        total_flow_funding = 0.0
        for f in flows:
            total_flow_funding += f.amount_usd
            states.add(f.state_code)
            years.add(f.year)
            agencies.add(f.agency)
            
            flow_records.append({
                "year": f.year,
                "state_code": f.state_code, 
                "agency": f.agency,       
                "amount": f.amount_usd,
                "source_db": f.source_database
            })
            
        total_activity_funding = sum([p['amount'] for p in unique_projects.values()])
        captured_volume = max(total_flow_funding, total_activity_funding)
        
        return {
            "recipient_name": recipient_name, 
            "captured_volume": captured_volume,
            "operating_zones": sorted(list(states)), 
            "active_years": sorted(list(years), reverse=True),
            "known_agencies": sorted(list(agencies)),      
            "projects": sorted(list(unique_projects.values()), key=lambda x: (x['year'], x['date']), reverse=True),
            "flows": sorted(flow_records, key=lambda x: (x['year'], x['amount']), reverse=True)
        }


def get_zone_global_dossier(state_code: str):
    """Fetches all known intelligence for a specific Target Zone/US State across the entire local cache."""
    with Session(engine) as session:
        act_stmt = select(ActivityRecord).where(ActivityRecord.state_code == state_code)
        activities = session.exec(act_stmt).all()
        
        flow_stmt = select(FinancialFlow).where(FinancialFlow.state_code == state_code)
        flows = session.exec(flow_stmt).all()
        
        recipients = set()
        years = set()
        agencies = set()
        
        unique_projects = {}
        for a in activities:
            recipients.add(a.recipient_name)
            years.add(a.year)
            agencies.add(a.agency)
            
            proj_key = f"{a.year}-{a.recipient_name}-{a.project}-{a.description}"
            if proj_key not in unique_projects:
                unique_projects[proj_key] = {
                    "year": a.year,
                    "recipient_name": a.recipient_name, 
                    "agency": a.agency,            
                    "project": a.project,
                    "description": a.description,
                    "amount": a.amount,
                    "date": a.date
                }
                
        macro_flows = []
        micro_flows = []
        total_macro = 0.0
        total_micro = 0.0
        
        for f in flows:
            years.add(f.year)
            agencies.add(f.agency)
            
            flow_record = {
                "year": f.year,
                "agency": f.agency,           
                "recipient": f.recipient,    
                "amount": f.amount_usd,
                "source_db": f.source_database,
                "flow_type": f.flow_type
            }
            
            if f.layer == "Macro":
                total_macro += f.amount_usd
                macro_flows.append(flow_record)
            else:
                total_micro += f.amount_usd
                micro_flows.append(flow_record)
                recipients.add(f.recipient)
        
        total_activity_funding = sum([p['amount'] for p in unique_projects.values()])
        captured_micro = max(total_micro, total_activity_funding)
        captured_total = total_macro + captured_micro
        
        return {
            "state_code": state_code, 
            "captured_volume": captured_total,
            "macro_volume": total_macro,
            "micro_volume": captured_micro,
            "operating_recipients": sorted(list(recipients)), 
            "active_years": sorted(list(years), reverse=True),
            "known_agencies": sorted(list(agencies)),     
            "projects": sorted(list(unique_projects.values()), key=lambda x: (x['year'], x['date']), reverse=True),
            "macro_flows": sorted(macro_flows, key=lambda x: (x['year'], x['amount']), reverse=True),
            "micro_flows": sorted(micro_flows, key=lambda x: (x['year'], x['amount']), reverse=True)
        }


def search_global_entities(query: str):
    """
    Performs a fuzzy search against all cached entities (Agencies, Vendors, Grantees)
    and aggregates them into unified search results entirely offline.
    """
    if not query or query.strip() == "":
        return []
        
    query = query.strip()
    normalized_query = normalize_entity_name(query)
    
    query_str1 = f"%{query}%"
    query_str2 = f"%{normalized_query}%"
    
    with Session(engine) as session:
        # Upgraded to ilike for case-insensitivity
        flow_stmt = select(FinancialFlow).where(
            (FinancialFlow.agency.ilike(query_str1)) | 
            (FinancialFlow.recipient.ilike(query_str1)) |
            (FinancialFlow.agency.ilike(query_str2)) | 
            (FinancialFlow.recipient.ilike(query_str2))
        )
        flows = session.exec(flow_stmt).all()
        
        # Upgraded to ilike for case-insensitivity
        act_stmt = select(ActivityRecord).where(
            (ActivityRecord.agency.ilike(query_str1)) | 
            (ActivityRecord.recipient_name.ilike(query_str1)) |
            (ActivityRecord.agency.ilike(query_str2)) | 
            (ActivityRecord.recipient_name.ilike(query_str2))
        )
        activities = session.exec(act_stmt).all()
        
    entities = {}
    
    def _add_to_entity(name, role, amount, year, state, source):
        if not name or name.strip() == "": return
        if name not in entities:
            entities[name] = {
                "name": name,
                "roles": set(),
                "total_volume": 0.0,
                "years": set(),
                "target_zones": set(), 
                "data_sources": set()
            }
        
        entities[name]["roles"].add(role)
        entities[name]["total_volume"] += amount
        entities[name]["years"].add(year)
        entities[name]["target_zones"].add(state)
        entities[name]["data_sources"].add(source)

    query_lower = query.lower()
    norm_lower = normalized_query.lower()
    
    for f in flows:
        if query_lower in str(f.agency).lower() or norm_lower in str(f.agency).lower():
            _add_to_entity(f.agency, "Federal Agency", f.amount_usd, f.year, f.state_code, f.source_database)
        if query_lower in str(f.recipient).lower() or norm_lower in str(f.recipient).lower():
            role = "Grantee" if f.layer == "Micro" else "Contractor"
            _add_to_entity(f.recipient, role, f.amount_usd, f.year, f.state_code, f.source_database)
            
    for a in activities:
        if query_lower in str(a.agency).lower() or norm_lower in str(a.agency).lower():
            _add_to_entity(a.agency, "Federal Agency", a.amount, a.year, a.state_code, "Activities Scan")
        if query_lower in str(a.recipient_name).lower() or norm_lower in str(a.recipient_name).lower():
            _add_to_entity(a.recipient_name, "Grantee/Contractor", a.amount, a.year, a.state_code, "Activities Scan")

    results = []
    for data in entities.values():
        data["roles"] = sorted(list(data["roles"]))
        data["years"] = sorted(list(data["years"]), reverse=True)
        data["target_zones"] = sorted(list(data["target_zones"]))
        data["data_sources"] = sorted(list(data["data_sources"]))
        results.append(data)
        
    return sorted(results, key=lambda x: x["total_volume"], reverse=True)


def search_offline_ledger(query: str, search_type: str = "ALL"):
    """
    Searches the local SQLite cache for specific Award IDs or text within Descriptions.
    search_type: 'AWARD_ID', 'DESCRIPTION', or 'ALL'
    """
    if not query or query.strip() == "":
        return []
        
    query_clean = query.strip()
    query_str = f"%{query_clean}%"
    seen_signatures = set()
    results = []
    
    print(f"\n\033[96m{'='*60}\033[0m")
    print(f"\033[96m🔍 OFFLINE LEDGER OMNI-SEARCH INITIATED\033[0m")
    print(f"\033[93mTarget Text:\033[0m '{query_clean}'")
    print(f"\033[93mSQL wildcard string:\033[0m '{query_str}'")
    print(f"\033[93mSearch Mode:\033[0m {search_type}")
    print(f"\033[96m{'='*60}\033[0m")
    
    with Session(engine) as session:
        # 1. Search FinancialFlow (Dashboard/Timeline cache)
        if search_type in ["AWARD_ID", "ALL"]:
            print(f"\033[94m[1/3] Executing query against 'FinancialFlow' table...\033[0m")
            
            if search_type == "ALL":
                flow_stmt = select(FinancialFlow).where(
                    (FinancialFlow.award_id_piid.ilike(query_str)) |
                    (FinancialFlow.parent_award_id.ilike(query_str)) |
                    (FinancialFlow.source_record_id.ilike(query_str)) |
                    (FinancialFlow.agency.ilike(query_str)) |
                    (FinancialFlow.recipient.ilike(query_str)) |
                    (FinancialFlow.state_code.ilike(query_str))
                )
            else:
                flow_stmt = select(FinancialFlow).where(
                    (FinancialFlow.award_id_piid.ilike(query_str)) |
                    (FinancialFlow.parent_award_id.ilike(query_str)) |
                    (FinancialFlow.source_record_id.ilike(query_str))
                )
                
            flows = session.exec(flow_stmt).all()
            print(f"      -> SQLite returned \033[92m{len(flows)}\033[0m matching rows.")
            
            for f in flows:
                sig = f"FLOW|{f.id}"
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    award_id_str = f.award_id_piid if f.award_id_piid else (f.parent_award_id or f.source_record_id or "Unknown PIID")
                    
                    results.append({
                        "record_type": "Financial Flow",
                        "year": f.year,
                        "state_code": f.state_code,
                        "agency": f.agency,
                        "recipient": f.recipient,
                        "award_id": award_id_str,
                        "description": f"Mechanism: {f.flow_type} | Database: {f.source_database} | Source ID: {f.source_record_id}",
                        "amount": f.amount_usd
                    })
        else:
            print(f"\033[90m[1/3] Skipping 'FinancialFlow' table due to search_type filter.\033[0m")

        # 2. Search ActivityRecord (Activity Search cache)
        if search_type in ["AWARD_ID", "DESCRIPTION", "ALL"]:
            print(f"\033[94m[2/3] Executing query against 'ActivityRecord' table...\033[0m")
            
            conditions = []
            if search_type in ["AWARD_ID", "ALL"]:
                conditions.append(ActivityRecord.project.ilike(query_str))
                conditions.append(ActivityRecord.source_record_id.ilike(query_str))
            
            if search_type in ["DESCRIPTION", "ALL"]:
                conditions.append(ActivityRecord.description.ilike(query_str))
                
            if search_type == "ALL":
                conditions.append(ActivityRecord.agency.ilike(query_str))
                conditions.append(ActivityRecord.recipient_name.ilike(query_str))
                conditions.append(ActivityRecord.state_code.ilike(query_str))
                
            act_stmt = select(ActivityRecord).where(or_(*conditions))
            activities = session.exec(act_stmt).all()
            
            print(f"      -> SQLite returned \033[92m{len(activities)}\033[0m matching rows.")
            
            for a in activities:
                sig = f"ACT|{a.id}"
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    results.append({
                        "record_type": "Activity Description",
                        "year": a.year,
                        "state_code": a.state_code,
                        "agency": a.agency,
                        "recipient": a.recipient_name,
                        "award_id": a.project,
                        "description": a.description,
                        "amount": a.amount
                    })
        else:
             print(f"\033[90m[2/3] Skipping 'ActivityRecord' table due to search_type filter.\033[0m")

        # 3. Search PinnedRecord (Saved Bookmarks Cache)
        if search_type in ["AWARD_ID", "DESCRIPTION", "ALL"]:
            print(f"\033[94m[3/3] Executing query against 'PinnedRecord' table (Bookmarked Awards)...\033[0m")
            
            conditions = []
            if search_type in ["AWARD_ID", "ALL"]:
                conditions.append(PinnedRecord.project_title.ilike(query_str))
                conditions.append(PinnedRecord.source_record_id.ilike(query_str))
                
            if search_type in ["DESCRIPTION", "ALL"]:
                conditions.append(PinnedRecord.description.ilike(query_str))
                
            if search_type == "ALL":
                conditions.append(PinnedRecord.agency.ilike(query_str))
                conditions.append(PinnedRecord.recipient.ilike(query_str))
                conditions.append(PinnedRecord.state_code.ilike(query_str))
                
            pin_stmt = select(PinnedRecord).where(or_(*conditions))
            pinned_awards = session.exec(pin_stmt).all()
            
            print(f"      -> SQLite returned \033[92m{len(pinned_awards)}\033[0m matching rows.")
            
            for p in pinned_awards:
                # Use source_record_id for the signature check to prevent dupes 
                # if the user pinned something that is ALSO in FinancialFlow/ActivityRecord
                sig = f"PIN|{p.source_record_id}"
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    results.append({
                        "record_type": "Pinned/Saved Award",
                        "year": p.year,
                        "state_code": p.state_code,
                        "agency": p.agency,
                        "recipient": p.recipient,
                        "award_id": p.project_title,
                        "description": p.description,
                        "amount": p.amount_usd
                    })
        else:
            print(f"\033[90m[3/3] Skipping 'PinnedRecord' table due to search_type filter.\033[0m")

    print(f"\n\033[92m✅ Omni-Search Complete. Returning {len(results)} deduplicated matches to the UI.\033[0m\n")
    
    return sorted(results, key=lambda x: x["amount"], reverse=True)