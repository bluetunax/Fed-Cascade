# /services/entity_normalization.py

import re

# A dictionary mapping common alias patterns or substrings to a single "Cleaned" Entity Name.
# USAspending data is notoriously messy. We have tailored these to encompass Whole-of-Government 
# agencies and major federal prime contractors (Defense, IT, Civilian, Global Health).
ENTITY_ALIASES = {
    # --- Major Federal Agencies ---
    "Department of Defense": ["department of defense", "dod"],
    "Department of State": ["department of state", "dos"],
    "Department of Health and Human Services": ["department of health and human services", "hhs", "health and human services", "centers for disease control and prevention", "cdc", "national institutes of health", "nih"],
    "Department of Energy": ["department of energy", "doe"],
    "Department of Homeland Security": ["department of homeland security", "dhs", "fema", "federal emergency management agency"],
    "Department of Veterans Affairs": ["department of veterans affairs", "va", "veterans affairs"],
    "Department of Justice": ["department of justice", "doj", "fbi", "federal bureau of investigation"],
    "Agency for International Development": ["agency for international development", "usaid", "u.s. agency for international development", "united states agency for international development"],
    "Millennium Challenge Corporation": ["millennium challenge corporation", "mcc"],
    "Peace Corps": ["peace corps"],
    
    # --- Major Defense & Aerospace Primes ---
    "Lockheed Martin": ["lockheed martin", "lockheed martin corporation", "lockheed", "lockheed martin corp"],
    "RTX Corporation (Raytheon)": ["raytheon", "raytheon company", "raytheon technologies", "rtx", "rtx corporation"],
    "General Dynamics": ["general dynamics", "general dynamics corporation", "general dynamics corp", "general dynamics information technology", "gdit"],
    "Boeing": ["boeing", "the boeing company", "boeing company"],
    "Northrop Grumman": ["northrop grumman", "northrop grumman systems corporation", "northrop", "northrop grumman corp"],
    "L3Harris Technologies": ["l3harris", "l3harris technologies", "l-3 communications", "harris corporation"],
    "BAE Systems": ["bae systems", "bae systems inc", "bae systems information and electronic systems integration"],
    "Huntington Ingalls Industries": ["huntington ingalls", "huntington ingalls inc", "hii"],
    "Leidos": ["leidos", "leidos inc", "leidos, inc."],
    "Booz Allen Hamilton": ["booz allen hamilton", "booz allen hamilton inc", "booz allen"],
    
    # --- Major IT, Tech & Professional Services ---
    "Palantir Technologies": ["palantir", "palantir technologies", "palantir usg"],
    "SAIC (Science Applications International Corp)": ["saic", "science applications international corporation"],
    "CACI International": ["caci", "caci inc - federal", "caci international inc"],
    "Peraton": ["peraton", "peraton inc"],
    "Accenture": ["accenture", "accenture federal services", "accenture federal services llc"],
    "Deloitte": ["deloitte", "deloitte consulting", "deloitte consulting llp"],
    "General Electric": ["general electric", "general electric company", "ge"],
    
    # --- Major Civilian / USAID Implementers ---
    "Chemonics International": ["chemonics international", "chemonics", "chemonics inc"],
    "Development Alternatives Inc. (DAI)": ["development alternatives inc", "dai", "dai global", "development alternatives"],
    "Tetra Tech": ["tetra tech", "tetra tech inc", "tetra tech ard", "tetratech"],
    "Palladium": ["palladium", "palladium international", "the palladium group"],
    "RTI International": ["rti international", "research triangle institute", "rti"],
    "John Snow, Inc. (JSI)": ["john snow inc", "jsi", "jsi research & training institute", "john snow"],
    "Abt Global": ["abt associates", "abt global", "abt associates inc"],
    "Creative Associates International": ["creative associates international", "creative associates"],
    "Management Systems International (MSI)": ["management systems international", "msi"],
    "Dexis Consulting Group": ["dexis consulting group", "dexis interactive", "dexis"],
    
    # --- Major Non-Profit Implementers (INGOs) ---
    "World Vision": ["world vision", "world vision inc", "world vision international"],
    "Catholic Relief Services (CRS)": ["catholic relief services", "crs", "catholic relief services - united states conference of catholic bishops"],
    "Mercy Corps": ["mercy corps", "mercy corp"],
    "Save the Children": ["save the children", "save the children federation", "save the children fund"],
    "International Rescue Committee (IRC)": ["international rescue committee", "irc", "the international rescue committee"],
    "CARE": ["cooperative for assistance and relief everywhere", "care", "care usa"],
    "FHI 360": ["fhi 360", "family health international", "fhi360"],
    "Pathfinder International": ["pathfinder international", "pathfinder"],
    "Action Against Hunger": ["action against hunger", "action contre la faim", "acf"],
    "Global Communities": ["global communities", "project concern international", "pci"],
    
    # --- Multilateral / UN Agencies ---
    "United Nations Children's Fund (UNICEF)": ["united nations children's fund", "unicef"],
    "World Food Programme (WFP)": ["world food programme", "wfp", "un world food programme"],
    "World Health Organization (WHO)": ["world health organization", "who"],
    "United Nations Development Programme (UNDP)": ["united nations development programme", "undp"],
    "United Nations High Commissioner for Refugees (UNHCR)": ["united nations high commissioner for refugees", "unhcr", "un refugee agency"],
    "International Organization for Migration (IOM)": ["international organization for migration", "iom"],
    
    # --- Global Health & Research Orgs ---
    "Jhpiego": ["jhpiego", "jhpiego corporation"],
    "Elizabeth Glaser Pediatric AIDS Affiliate": ["elizabeth glaser pediatric aids affiliate", "egpaf"],
    "Management Sciences for Health (MSH)": ["management sciences for health", "msh"],
    "IntraHealth International": ["intrahealth international", "intrahealth"]
}

# Pre-compile the alias mapping for faster O(1) or O(N) lookups
_REVERSE_LOOKUP = {}
for clean_name, aliases in ENTITY_ALIASES.items():
    for alias in aliases:
        _REVERSE_LOOKUP[alias.lower()] = clean_name


def _strip_legal_suffixes(name: str) -> str:
    """
    Removes common corporate or legal suffixes that pollute entity matching.
    """
    suffixes = [
        r"\bLLC\b", r"\bInc\.?\b", r"\bLtd\.?\b", r"\bGmbH\b", 
        r"\bCorp\.?\b", r"\bCorporation\b", r"\bCompany\b", r"\bCo\.?\b",
        r"\bPLC\b"
    ]
    
    cleaned = name
    for suffix in suffixes:
        cleaned = re.sub(suffix, "", cleaned, flags=re.IGNORECASE)
        
    return cleaned.strip()


def normalize_entity_name(raw_name: str) -> str:
    """
    Takes a raw, messy entity name from the USAspending API and attempts 
    to standardize it into a clean, canonical format to prevent fragmenting 
    the same company across 10 different rows in the UI.
    """
    if not raw_name or not isinstance(raw_name, str):
        return "Unknown Entity"
        
    # 1. Clean up whitespace and punctuation
    clean_name = raw_name.strip()
    
    # 2. Check for an exact alias match first (fastest)
    lower_name = clean_name.lower()
    if lower_name in _REVERSE_LOOKUP:
        return _REVERSE_LOOKUP[lower_name]
        
    # 3. Strip legal suffixes to see if that helps it match an alias
    stripped_name = _strip_legal_suffixes(clean_name)
    lower_stripped = stripped_name.lower()
    
    if lower_stripped != lower_name and lower_stripped in _REVERSE_LOOKUP:
        return _REVERSE_LOOKUP[lower_stripped]
        
    # 4. If it's a completely unknown entity, we just return the suffix-stripped version
    return stripped_name if stripped_name else clean_name