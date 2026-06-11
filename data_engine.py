# /data_engine.py
import httpx
import asyncio
import datetime
import logging
import re
import json 
import hashlib
from collections import Counter

# Import our real-time state manager
from services.state import cascade_state

# Import the Entity Normalizer
from services.entity_normalization import normalize_entity_name

# Import database handlers for User Exclusion Rules
from db_manager import get_exclusion_rules, get_or_create_local_user

# Import application settings
from config import settings

# --- Setup Logging ---
logger = logging.getLogger(__name__)
numeric_level = getattr(logging, settings.log_level.upper(), logging.ERROR)
logger.setLevel(numeric_level)

file_handler = logging.FileHandler('dropped_flows.log')
file_handler.setLevel(numeric_level)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Global Semaphore to prevent API Abuse / Rate Limiting. 
API_SEMAPHORE = asyncio.Semaphore(settings.max_concurrent_api_requests)

# --- USAspending Constants ---
# Contracts (Macro level - usually large defense/infrastructure)
CONTRACT_CODES = ["A", "B", "C", "D"] 
# Grants, Loans, Direct Payments (Micro level - usually state gov, universities, NGOs, small biz)
GRANT_CODES = ["02", "03", "04", "05", "06", "07", "08", "09", "10", "11"] 

# Standard Browser Headers to prevent 403 Forbidden Blocks
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _advanced_text_match(keyword: str, text: str) -> bool:
    if not text:
        return False
    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
    return bool(pattern.search(text))

def _loose_entity_match(query: str, entity_name: str) -> bool:
    if not query or not entity_name:
        return False
    return query.lower() in str(entity_name).lower()

def _generate_stable_id(f: dict) -> str:
    dict_str = json.dumps(f, sort_keys=True)
    stable_hash = hashlib.md5(dict_str.encode('utf-8')).hexdigest()
    return f"FALLBACK_{stable_hash}"

def _apply_exclusion_rules(raw_results: list) -> list:
    try:
        user = get_or_create_local_user()
        rules = get_exclusion_rules(user.id)
    except Exception as e:
        logger.error(f"Could not load exclusion rules: {e}")
        return raw_results
        
    if not rules:
        return raw_results
        
    filtered_results = []
    for f in raw_results:
        drop_record = False
        raw_recipient = str(f.get('Recipient Name') or f.get('Sub-Awardee Name', '')).lower()
        raw_desc = str(f.get('Description', '')).lower()
        raw_agency = str(f.get('Funding Agency Name', '')).lower()
        
        for rule in rules:
            target = rule['keyword'].lower()
            rtype = rule['rule_type']
            if rtype == "Recipient" and target in raw_recipient:
                drop_record = True
                break
            elif rtype == "Description" and target in raw_desc:
                drop_record = True
                break
            elif rtype == "Agency" and target in raw_agency:
                drop_record = True
                break
                
        if not drop_record:
            filtered_results.append(f)
    return filtered_results


# ---------------------------------------------------------
# 1. USASPENDING API WORKERS
# ---------------------------------------------------------

class APIFetchError(Exception):
    def __init__(self, message, raw_response=None, payload=None):
        super().__init__(message)
        self.raw_response = raw_response
        self.payload = payload

async def _execute_usaspending_post(client: httpx.AsyncClient, payload: dict, page: int, url: str, state_code: str, year: int) -> list:
    payload["page"] = page
    try:
        response = await client.post(url, json=payload, headers=HEADERS, timeout=45.0)
        if response.status_code == 200:
            data = await asyncio.to_thread(response.json)
            return data.get('results', [])
        else:
            print(f"\n\033[91m{'='*60}\033[0m")
            print(f"\033[91m💥 USASPENDING API REJECTION 💥\033[0m")
            print(f"\033[93mTarget:\033[0m {state_code} | \033[93mYear:\033[0m {year} | \033[93mPage:\033[0m {page}")
            print(f"\033[93mStatus Code:\033[0m {response.status_code}")
            print(f"\033[93mExact Server Response:\033[0m\n{response.text}")
            print(f"\033[93mPayload Sent:\033[0m\n{json.dumps(payload, indent=2)}")
            print(f"\033[91m{'='*60}\033[0m\n")
            logger.error(f"USAspending API Error {response.status_code}: {response.text}")
            raise APIFetchError(f"HTTP {response.status_code}: {response.text[:200]}...", raw_response=response.text, payload=payload)
    except httpx.ReadTimeout:
        raise APIFetchError("Connection timed out (Took > 45 seconds)")
    except httpx.ConnectError as e:
        raise APIFetchError(f"Connection failed: {str(e)}")
    except APIFetchError:
        raise
    except Exception as e:
        logger.error(f"USAspending Connection error for {state_code} ({year}) Page {page}: {e}")
        raise APIFetchError(f"Unexpected Python Error: {str(e)}")

async def _fetch_usaspending_year(client: httpx.AsyncClient, state_code: str, year: int, keyword: str = None, recipient_name: str = None):
    url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    start_date = f"{year - 1}-10-01"
    end_date = f"{year}-09-30"

    base_filters = {
        "time_period": [{"start_date": start_date, "end_date": end_date}]
    }
    
    if state_code and state_code.upper() not in ["GLOBAL", "ALL", ""]:
        base_filters["place_of_performance_locations"] = [{"country": state_code.upper()}]
    if keyword:
        base_filters["keywords"] = [keyword]
    if recipient_name:
        base_filters["recipient_search_text"] = [recipient_name]

    all_results = []
    max_pages = 5 
    
    standard_fields = [
        "Award ID", "Generated Internal ID", "Award Amount", 
        "Funding Agency Name", "Awarding Agency Name", "Recipient Name", 
        "Place of Performance Country Code", "Place of Performance State Code", 
        "Award Type", "Start Date", "Description",
        "Parent Award ID", "Product or Service Code", "NAICS Code", "CFDA Number"
    ]
    
    loan_fields = [
        "Award ID", "generated_internal_id", "Loan Value", 
        "Funding Agency", "Awarding Agency", "Recipient Name", 
        "Place of Performance Country Code", "Place of Performance State Code", 
        "Issued Date", "Description"
    ]

    api_code_groups = [
        {"name": "contracts", "codes": ["A", "B", "C", "D"], "sort": "Award Amount", "fields": standard_fields},
        {"name": "grants", "codes": ["02", "03", "04", "05"], "sort": "Award Amount", "fields": standard_fields},
        {"name": "other_financial_assistance", "codes": ["06", "10"], "sort": "Award Amount", "fields": standard_fields},
        {"name": "direct_payments", "codes": ["09", "11"], "sort": "Award Amount", "fields": standard_fields},
        {"name": "loans", "codes": ["07", "08"], "sort": "Loan Value", "fields": loan_fields} 
    ]

    async with API_SEMAPHORE:
        for group in api_code_groups:
            group_filters = base_filters.copy()
            group_filters["award_type_codes"] = group["codes"]
            
            group_payload = {
                "filters": group_filters,
                "fields": group["fields"],
                "limit": 100, 
                "sort": group["sort"],
                "order": "desc"
            }
            
            for page in range(1, max_pages + 1):
                results = await _execute_usaspending_post(client, group_payload, page, url, state_code, year)
                cleaned_results = await asyncio.to_thread(_apply_exclusion_rules, results)
                all_results.extend(cleaned_results)
                if len(results) < 100:
                    break
    return year, all_results

def parse_usaspending_flows(raw_results: list, state_code: str, year: int):
    macro_flows = []  
    micro_flows = []  

    for f in raw_results:
        try:
            amount = f.get('Award Amount') or f.get('Loan Value', 0)
            if not amount or amount <= 0: continue
            flow_id = str(f.get('Generated Internal ID') or f.get('generated_internal_id', _generate_stable_id(f)))
            
            raw_agency = f.get('Funding Agency Name') or f.get('Awarding Agency Name') or f.get('Funding Agency') or f.get('Awarding Agency') or "Unknown Agency"
            
            raw_recipient = f.get('Recipient Name') or "Unknown Recipient"
            record_country = f.get('Place of Performance Country Code', state_code)
            actual_state = record_country if record_country else state_code
            agency = normalize_entity_name(raw_agency)
            recipient = normalize_entity_name(raw_recipient)
            award_type = f.get('Award Type', 'Loan') 
            is_contract = "Contract" in award_type or award_type in CONTRACT_CODES
            
            base_flow = {
                "source_record_id": flow_id,
                "state_code": actual_state.upper() if actual_state else "ALL",
                "year": year,
                "source_database": "Contracts" if is_contract else "Grants",
                "agency": agency,
                "recipient": recipient,
                "amount_usd": amount,
                "flow_type": award_type,
                "is_small_biz": False,
                "is_subaward": False,
                "award_id_piid": str(f.get('Award ID', '')),
                "parent_award_id": str(f.get('Parent Award ID', '')),
                "psc_code": f.get('Product or Service Code'),
                "naics_code": f.get('NAICS Code'),
                "cfda_number": f.get('CFDA Number')
            }
            
            if is_contract:
                base_flow["layer"] = "Macro"
                macro_flows.append(base_flow)
            else:
                base_flow["layer"] = "Micro"
                micro_flows.append(base_flow)
        except Exception as e:
            continue
    return macro_flows, micro_flows


# ---------------------------------------------------------
# 2. ORCHESTRATORS (Dashboard & Timeline)
# ---------------------------------------------------------

async def fetch_single_year_all_sources(state_code: str, year: int, client_id: str = "default"):
    cascade_state.update(f"Executing USAspending pull for {state_code} (FY{year})...", 20, client_id=client_id)
    async with httpx.AsyncClient(timeout=90.0) as client:
        _, raw_flows = await _fetch_usaspending_year(client, state_code, year)
        cascade_state.update(f"Payload secured. Offloading JSON to background threads...", 60, client_id=client_id)
        macro, micro = await asyncio.to_thread(parse_usaspending_flows, raw_flows, state_code, year)
        cascade_state.update(f"Aggregating and sorting financial ledgers...", 90, client_id=client_id)
        return macro, micro

async def fetch_multi_year_flows(state_code: str, start_year: int, end_year: int, client_id: str = "default"):
    total_years = end_year - start_year + 1
    cascade_state.update(f"Spawning {total_years} concurrent API workers...", 5, client_id=client_id)
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [_fetch_usaspending_year(client, state_code, y) for y in range(start_year, end_year + 1)]
        cascade_state.update(f"Awaiting multi-year HTTP payloads from Treasury Dept...", 20, client_id=client_id)
        results = await asyncio.gather(*tasks)
    
    all_macro = []
    all_micro = []
    for idx, (year, raw_flows) in enumerate(results):
        progress = 30 + int((idx / total_years) * 60)
        cascade_state.update(f"Parsing raw JSON matrix for FY{year}...", progress, client_id=client_id)
        macro, micro = await asyncio.to_thread(parse_usaspending_flows, raw_flows, state_code, year)
        all_macro.extend(macro)
        all_micro.extend(micro)
        
    cascade_state.update("Synthesizing multi-year federal ledgers...", 95, client_id=client_id)
    return all_macro, all_micro

async def get_recipient_activities(recipient_name: str, state_code: str, year: int, client_id: str = "default"):
    cascade_state.update(f"Fetching raw pipeline to isolate {recipient_name}...", 20, client_id=client_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        _, raw_flows = await _fetch_usaspending_year(client, state_code, year, recipient_name=recipient_name)
    cascade_state.update(f"Scanning texts for activity records...", 70, client_id=client_id)
    
    def _parse(flows, name):
        activities = []
        for f in flows:
            try:
                raw_recip = f.get('Recipient Name', '')
                destination = normalize_entity_name(raw_recip)
                if _loose_entity_match(name, destination):
                    amount = f.get('Award Amount') or f.get('Loan Value', 0)
                    if not amount or amount <= 0: continue
                    flow_id = str(f.get('Generated Internal ID') or f.get('generated_internal_id', _generate_stable_id(f)))
                    
                    raw_agency = f.get('Funding Agency Name') or f.get('Awarding Agency Name') or f.get('Funding Agency') or f.get('Awarding Agency')
                    if not raw_agency or raw_agency.strip() == "":
                        raw_agency = "Unknown Agency"
                        
                    agency = normalize_entity_name(raw_agency)
                    date = str(f.get('Start Date') or f.get('Issued Date', 'Unknown'))[:10]
                    description = f.get('Description', 'No specific activity description provided.')
                    project_name = f.get('Award ID', 'General Funding')
                    activities.append({
                        "source_record_id": flow_id, 
                        "agency": agency, "amount": amount, "date": date,
                        "project": project_name, "description": description
                    })
            except Exception:
                continue
        return sorted(activities, key=lambda x: x['date'], reverse=True)

    result = await asyncio.to_thread(_parse, raw_flows, recipient_name)
    cascade_state.update(f"Isolation complete.", 95, client_id=client_id)
    return result


# ---------------------------------------------------------
# 3. DEEP EXPLORER API WORKERS (Whole-of-Gov & Subawards)
# ---------------------------------------------------------

async def _fetch_advanced_usaspending(client: httpx.AsyncClient, agency_target: str, psc_code: str, parent_award_id: str, state_code: str, year: int, is_subaward: bool = False):
    """
    A highly advanced dynamic fetcher capable of targeting DoD, State, or whole-of-gov,
    filtering by specific PSC codes, or hitting the distinct Sub-Award Treasury endpoint.
    """
    url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    
    start_date = f"{year - 1}-10-01"
    end_date = f"{year}-09-30"

    filters = {
        "time_period": [{"start_date": start_date, "end_date": end_date}]
    }
    
    if state_code and state_code.upper() not in ["GLOBAL", "ALL", ""]:
        filters["place_of_performance_locations"] = [{"country": state_code.upper()}]
        
    agency_map = {
        "USAID": "Agency for International Development",
        "DOD": "Department of Defense",
        "STATE": "Department of State"
    }
    
    if agency_target and agency_target.upper() in agency_map:
        filters["agencies"] = [{"type": "funding", "tier": "toptier", "name": agency_map[agency_target.upper()]}]
        
    if psc_code and psc_code.strip():
        filters["psc_codes"] = [psc_code.strip()]
        
    if parent_award_id and parent_award_id.strip():
        filters["keywords"] = [parent_award_id.strip()]

    all_results = []
    max_pages = 5

    # Define fields based on prime vs subaward
    if is_subaward:
        fields = [
            "Sub-Award ID", "Sub-Awardee Name", "Sub-Award Amount", 
            "Funding Agency Name", "Awarding Agency Name", "Prime Award ID", 
            "Place of Performance Country Code"
        ]
    else:
        fields = [
            "Award ID", "Generated Internal ID", "Award Amount", 
            "Funding Agency Name", "Awarding Agency Name", "Recipient Name", 
            "Place of Performance Country Code", "Place of Performance State Code", 
            "Award Type", "Start Date", "Description",
            "Parent Award ID", "Product or Service Code", "NAICS Code", "CFDA Number"
        ]
        
    loan_fields = [
        "Award ID", "generated_internal_id", "Loan Value", 
        "Funding Agency", "Awarding Agency", "Recipient Name", 
        "Place of Performance Country Code", "Place of Performance State Code", 
        "Issued Date", "Description"
    ]

    api_code_groups = [
        {"name": "contracts", "codes": ["A", "B", "C", "D"]},
        {"name": "grants", "codes": ["02", "03", "04", "05"]},
        {"name": "other_financial_assistance", "codes": ["06", "10"]},
        {"name": "direct_payments", "codes": ["09", "11"]},
        {"name": "loans", "codes": ["07", "08"]}
    ]
    
    async with API_SEMAPHORE:
        # We loop over code groups for BOTH prime and subawards to satisfy API schema requirements
        for group in api_code_groups:
            group_filters = filters.copy()
            group_filters["award_type_codes"] = group["codes"]
            
            if is_subaward:
                current_fields = fields
                current_sort = "Sub-Award Amount"
            else:
                current_fields = loan_fields if group["name"] == "loans" else fields
                current_sort = "Loan Value" if group["name"] == "loans" else "Award Amount"
            
            group_payload = {
                "filters": group_filters,
                "fields": current_fields,
                "limit": 100, 
                "sort": current_sort,
                "order": "desc"
            }
            
            if is_subaward:
                group_payload["subawards"] = True
            
            for page in range(1, max_pages + 1):
                results = await _execute_usaspending_post(client, group_payload, page, url, state_code, year)
                cleaned = await asyncio.to_thread(_apply_exclusion_rules, results)
                all_results.extend(cleaned)
                if len(results) < 100:
                    break
                        
    return year, all_results


def parse_explorer_flows(raw_results: list, state_code: str, year: int, is_subaward: bool = False):
    macro_flows = []
    micro_flows = []
    
    for f in raw_results:
        try:
            if is_subaward:
                amount = f.get('Sub-Award Amount', 0)
                if not amount or amount <= 0: continue
                flow_id = str(f.get('Sub-Award ID') or f.get('subaward_number') or _generate_stable_id(f))
                
                raw_agency = f.get('Funding Agency Name') or f.get('Awarding Agency Name') or "Unknown Agency"
                
                raw_recipient = f.get('Sub-Awardee Name') or "Unknown Sub-Awardee"
                award_type = "Sub-Contract" if f.get('award_type') == 'procurement' else "Sub-Grant"
                parent_id = f.get('Prime Award ID', None)
                piid = f.get('Sub-Award ID', None) 
                record_country = f.get('Place of Performance Country Code') or state_code
                psc_code, naics_code, cfda_number = None, None, None
            else:
                amount = f.get('Award Amount') or f.get('Loan Value', 0)
                if not amount or amount <= 0: continue
                flow_id = str(f.get('Generated Internal ID') or f.get('generated_internal_id', _generate_stable_id(f)))
                
                raw_agency = f.get('Funding Agency Name') or f.get('Awarding Agency Name') or f.get('Funding Agency') or f.get('Awarding Agency') or "Unknown Agency"
                
                raw_recipient = f.get('Recipient Name') or "Unknown Recipient"
                award_type = f.get('Award Type', 'Grant')
                parent_id = f.get('Parent Award ID', None)
                piid = f.get('Award ID', None)
                record_country = f.get('Place of Performance Country Code') or state_code
                psc_code = f.get('Product or Service Code')
                naics_code = f.get('NAICS Code')
                cfda_number = f.get('CFDA Number')
                
            actual_state = record_country if record_country else state_code
            agency = normalize_entity_name(raw_agency)
            recipient = normalize_entity_name(raw_recipient)
            is_contract = "Contract" in award_type or award_type in CONTRACT_CODES or award_type == "Sub-Contract"
            
            base_flow = {
                "source_record_id": flow_id,
                "state_code": actual_state.upper() if actual_state else "ALL",
                "year": year,
                "source_database": "Contracts" if is_contract else "Grants",
                "agency": agency,
                "recipient": recipient,
                "amount_usd": amount,
                "flow_type": award_type,
                "is_small_biz": False,
                "is_subaward": is_subaward,
                "award_id_piid": str(piid) if piid else None,
                "parent_award_id": str(parent_id) if parent_id else None,
                "psc_code": str(psc_code) if psc_code else None,
                "naics_code": str(naics_code) if naics_code else None,
                "cfda_number": str(cfda_number) if cfda_number else None
            }
            
            if is_contract:
                base_flow["layer"] = "Macro"
                macro_flows.append(base_flow)
            else:
                base_flow["layer"] = "Micro"
                micro_flows.append(base_flow)
        except Exception as e:
            continue
            
    return macro_flows, micro_flows


async def fetch_explorer_data(
    agency_target: str, psc_code: str, parent_award_id: str, 
    state_code: str, year: int, fetch_subawards: bool, client_id: str = "default"
):
    target_label = agency_target if agency_target and agency_target != "ALL" else "Whole-of-Government"
    cascade_state.update(f"Executing Deep Explorer query for {target_label}...", 10, client_id=client_id)
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [
            _fetch_advanced_usaspending(client, agency_target, psc_code, parent_award_id, state_code, year, is_subaward=False)
        ]
        
        if fetch_subawards:
            cascade_state.update(f"Spawning concurrent Sub-Award tracking nodes...", 15, client_id=client_id)
            tasks.append(_fetch_advanced_usaspending(client, agency_target, psc_code, parent_award_id, state_code, year, is_subaward=True))
            
        results = await asyncio.gather(*tasks)
        
        cascade_state.update(f"Extracting Prime Award matrices...", 50, client_id=client_id)
        prime_year, prime_raw = results[0]
        macro_prime, micro_prime = await asyncio.to_thread(parse_explorer_flows, prime_raw, state_code, year, False)
        
        macro_sub, micro_sub = [], []
        if fetch_subawards and len(results) > 1:
            cascade_state.update(f"Extracting Sub-Award network graphs...", 75, client_id=client_id)
            sub_year, sub_raw = results[1]
            macro_sub, micro_sub = await asyncio.to_thread(parse_explorer_flows, sub_raw, state_code, year, True)
            
        cascade_state.update("Synthesizing multi-tier federal intelligence...", 95, client_id=client_id)
        return macro_prime + macro_sub, micro_prime + micro_sub


# ---------------------------------------------------------
# 4. ACTIVITY SEARCH & BIBLIOGRAPHY
# ---------------------------------------------------------

def _parse_activities_from_raw(raw_flows, year, state_code, keyword):
    matched_activities = []
    for f in raw_flows:
        try:
            description = f.get('Description', 'No description')
            project_name = f.get('Award ID', '')
            full_text = f"{description} {project_name}"
            
            if _advanced_text_match(keyword, full_text):
                flow_id = str(f.get('Generated Internal ID') or f.get('generated_internal_id', _generate_stable_id(f)))
                
                raw_agency = f.get('Funding Agency Name') or f.get('Awarding Agency Name') or f.get('Funding Agency') or f.get('Awarding Agency')
                if not raw_agency or raw_agency.strip() == "":
                    raw_agency = "Unknown Agency"
                    
                agency = normalize_entity_name(raw_agency)
                
                raw_recipient = f.get('Recipient Name', 'Unknown Recipient')
                recipient_name = normalize_entity_name(raw_recipient)
                
                record_country = f.get('Place of Performance Country Code', state_code)
                actual_state = record_country if record_country else state_code
                
                matched_activities.append({
                    "source_record_id": flow_id,
                    "year": year,
                    "recipient_name": recipient_name,
                    "agency": agency,
                    "amount": f.get('Award Amount') or f.get('Loan Value', 0),
                    "date": str(f.get('Start Date') or f.get('Issued Date', 'Unknown'))[:10],
                    "project": project_name,
                    "description": description,
                    "state_code": actual_state.upper() if actual_state else "ALL"
                })
        except Exception as e:
            continue
    return matched_activities

async def search_activities_by_keyword(keyword: str, state_code: str, start_year: int, end_year: int, client_id: str = "default"):
    cascade_state.update(f"Downloading raw descriptions to scan for '{keyword}'...", 10, client_id=client_id)
    async with httpx.AsyncClient(timeout=90.0) as client:
        tasks = [_fetch_usaspending_year(client, state_code, y, keyword=keyword) for y in range(start_year, end_year + 1)]
        results = await asyncio.gather(*tasks)
    
    matched_activities = []
    total = len(results)
    for idx, (year, raw_flows) in enumerate(results):
        progress = 40 + int((idx / total) * 50)
        cascade_state.update(f"Deep-scanning contract descriptions for {year}...", progress, client_id=client_id)
        year_matches = await asyncio.to_thread(_parse_activities_from_raw, raw_flows, year, state_code, keyword)
        matched_activities.extend(year_matches)
            
    cascade_state.update(f"Sorting matched activities chronologically...", 95, client_id=client_id)
    return sorted(matched_activities, key=lambda x: x['date'], reverse=True)

def _parse_bibliography_from_raw(raw_flows, target_keywords):
    found_sectors = Counter()
    for f in raw_flows:
        try:
            description = f.get('Description', '')
            project_name = f.get('Award ID', '')
            full_text = f"{description} {project_name}"
            for kw in target_keywords:
                if _advanced_text_match(kw, full_text):
                    display_name = kw.title()
                    if display_name in ["Cyber", "Software"]: display_name = "Cybersecurity & IT"
                    if display_name in ["Missile", "Munitions"]: display_name = "Defense Systems"
                    if display_name in ["Research", "Development"]: display_name = "R&D"
                    if display_name in ["Construction", "Infrastructure"]: display_name = "Infrastructure"
                    found_sectors[display_name] += 1
        except Exception:
            continue
    return found_sectors

async def generate_bibliography(state_code: str, start_year: int, end_year: int, client_id: str = "default"):
    cascade_state.update(f"Fetching global activity corpus for bibliography...", 10, client_id=client_id)
    async with httpx.AsyncClient(timeout=90.0) as client:
        tasks = [_fetch_usaspending_year(client, state_code, y) for y in range(start_year, end_year + 1)]
        results = await asyncio.gather(*tasks)
    
    target_keywords = [
        "cyber", "software", "cloud", "artificial intelligence", "data center",
        "missile", "munitions", "aerospace", "aviation", "surveillance",
        "research", "development", "laboratory", "clinical", "vaccine",
        "construction", "infrastructure", "highway", "bridge", "transit",
        "environmental", "cleanup", "disaster", "fema", "emergency",
        "education", "training", "logistics", "telecommunications", "security"
    ]
    master_counter = Counter()
    total = len(results)
    for idx, (year, raw_flows) in enumerate(results):
        progress = 30 + int((idx / total) * 60)
        cascade_state.update(f"Classifying operational clusters for FY{year}...", progress, client_id=client_id)
        year_counter = await asyncio.to_thread(_parse_bibliography_from_raw, raw_flows, target_keywords)
        master_counter.update(year_counter)
    return dict(master_counter.most_common())

# ---------------------------------------------------------
# 5. LIVE GLOBAL SEARCH WORKERS 
# ---------------------------------------------------------

async def execute_live_global_search(query: str, client_id: str = "default"):
    cascade_state.update(f"Initiating single-pass federal sweep for '{query}'...", 20, client_id=client_id)
    
    # 1. FIX: Step back 1 year to bypass the Treasury's 90-day DoD reporting lag
    # This guarantees we are hitting a dense, fully-populated dataset.
    target_year = datetime.datetime.now().year - 1
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 2. FIX: Fire two concurrent queries. One specifically targeting Recipient Names, 
        # and one targeting general Keywords (to catch Federal Agencies).
        task_recip = _fetch_usaspending_year(client, state_code="ALL", year=target_year, recipient_name=query)
        task_kw = _fetch_usaspending_year(client, state_code="ALL", year=target_year, keyword=query)
        
        # Run both simultaneously
        api_results = await asyncio.gather(task_recip, task_kw, return_exceptions=True)
        
        raw_flows = []
        for res in api_results:
            if not isinstance(res, Exception):
                _, flows = res
                raw_flows.extend(flows)
        
    cascade_state.update("Merging federal intelligence streams...", 70, client_id=client_id)
    
    def _process_sweep(flows, q):
        sweep_res = []
        seen_entities = set() # Prevent duplicate spam if they have 500 contracts
        
        for f in flows:
            try:
                amount = f.get('Award Amount') or f.get('Loan Value', 0)
                if not amount or amount <= 0: continue
                
                raw_agency = f.get('Funding Agency Name') or f.get('Awarding Agency Name') or f.get('Funding Agency') or f.get('Awarding Agency')
                if not raw_agency or raw_agency.strip() == "":
                    raw_agency = "Unknown Agency"
                    
                agency = normalize_entity_name(raw_agency)
                record_country = f.get('Place of Performance Country Code', 'GLOBAL')
                
                # Check if it matches an Agency
                if _loose_entity_match(q, agency):
                    entity_key = f"AGENCY_{agency}"
                    if entity_key not in seen_entities:
                        seen_entities.add(entity_key)
                        sweep_res.append({
                            "name": agency, "role": "Federal Agency",
                            "amount": amount, "year": target_year,
                            "state_code": record_country, "source": "USAspending (Live)"
                        })
                    
                # Check if it matches a Prime Contractor / Grantee
                raw_recipient = f.get('Recipient Name', 'Unknown')
                recipient = normalize_entity_name(raw_recipient)
                
                if _loose_entity_match(q, recipient):
                    award_type = f.get('Award Type', 'Loan')
                    is_contract = "Contract" in award_type or award_type in CONTRACT_CODES
                    
                    entity_key = f"RECIP_{recipient}"
                    if entity_key not in seen_entities:
                        seen_entities.add(entity_key)
                        sweep_res.append({
                            "name": recipient, "role": "Contractor" if is_contract else "Grantee",
                            "amount": amount, "year": target_year,
                            "state_code": record_country, "source": "USAspending (Live)"
                        })
            except Exception:
                continue
                
        return sweep_res

    results = await asyncio.to_thread(_process_sweep, raw_flows, query)
    cascade_state.update("Finalizing global intelligence matrix...", 95, client_id=client_id)
    return results