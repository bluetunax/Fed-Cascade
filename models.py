# /models.py

from sqlmodel import Field, SQLModel, create_engine
from sqlalchemy import UniqueConstraint, event
from typing import Optional
from datetime import datetime, timezone

# Import the new global settings
from config import settings

# 1. Table for tracking Search History 
class SearchSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_name: Optional[str] = Field(default="Unnamed Session")
    
    search_type: str = Field(default="Dashboard") # "Dashboard", "Timeline", "Activity", or "Explorer"
    keyword: Optional[str] = Field(default=None)  
    
    state_code: str = Field(index=True) # USA State Code (e.g., TX, CA, NY) or "GLOBAL"
    
    year: int = Field(default=2023, index=True) # Represents US Fiscal Year
    
    start_year: int = Field(default=2023, index=True)
    end_year: int = Field(default=2023, index=True)
    
    # NEW: Explorer Targeting Fields
    agency_target: Optional[str] = Field(default=None) # e.g. "USAID", "DOD", "STATE", "ALL"
    parent_award_id: Optional[str] = Field(default=None) # For searching specific IDIQ Vehicles
    psc_code: Optional[str] = Field(default=None) # For searching specific Service Codes
    
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Watchdog Mode Anchor Flag
    is_base_session: bool = Field(default=False, index=True)

# 2. Table for the actual money moving (Dashboard/Timeline)
class FinancialFlow(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "session_id", "source_record_id",
            name="unique_flow_per_session"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="searchsession.id", ondelete="CASCADE", index=True)
    
    # The native stable ID from the USAspending API (Generated Internal ID)
    source_record_id: str = Field(default="UNKNOWN_ID", index=True)
    
    state_code: str = Field(index=True)
    year: int = Field(index=True)
    source_database: str = Field(default="USAspending") 
    
    agency: str    
    recipient: str 
    amount_usd: float
    
    layer: str     # Macro (Contracts) or Micro (Grants)
    flow_type: str = Field(default="Grant") # Specific Award Type
    is_small_biz: bool = Field(default=False) 
    
    # NEW: Federal Taxonomy & Hierarchy Tracking
    is_subaward: bool = Field(default=False, index=True)
    award_id_piid: Optional[str] = Field(default=None, index=True) # The PIID (Contract) or FAIN (Grant)
    parent_award_id: Optional[str] = Field(default=None, index=True) # The IDIQ/Master Contract ID
    
    psc_code: Optional[str] = Field(default=None)
    naics_code: Optional[str] = Field(default=None)
    cfda_number: Optional[str] = Field(default=None)

# 3. Table for the text-heavy award descriptions
class ActivityRecord(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "session_id", "source_record_id",
            name="unique_activity_per_session"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="searchsession.id", ondelete="CASCADE")
    
    # Native API ID
    source_record_id: str = Field(default="UNKNOWN_ID", index=True)
    
    year: int = Field(default=2023, index=True) 
    recipient_name: str 
    agency: str         
    amount: float
    date: str
    project: str        # e.g., Award ID or PIID
    description: str
    state_code: str     

# 4. Table for the Persistent Watchdog Change Log
class LedgerChange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # The two snapshots being compared
    base_session_id: int = Field(foreign_key="searchsession.id", ondelete="CASCADE", index=True)
    compare_session_id: int = Field(foreign_key="searchsession.id", ondelete="CASCADE", index=True)
    
    # Tracking the exact API record that changed (Provenance Layer)
    source_record_id: str = Field(default="UNKNOWN_ID", index=True)
    
    state_code: str = Field(index=True)
    year: int = Field(index=True)
    source_database: str
    
    agency: str
    recipient: str
    
    change_type: str # 'ADDED', 'REMOVED', 'MODIFIED'
    
    old_amount: float
    new_amount: float
    delta_usd: float
    delta_percent: float
    
    is_retroactive: bool = Field(default=False)
    impact_level: str = Field(default="LOW") # 'HIGH', 'MEDIUM', 'LOW'
    
    # NEW: Watchdog tracking for Subawards
    is_subaward: bool = Field(default=False)
    
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# 5. User Authentication / Local Profile
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# 6. Saved/Pinned Awards (Bookmarking specific awards)
class PinnedRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    
    source_record_id: str = Field(index=True)
    
    year: int
    agency: str
    recipient: str
    project_title: str
    description: str
    amount_usd: float
    state_code: str
    
    notes: Optional[str] = Field(default=None)
    pinned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# 7. The Noise Exclusion Engine
class ExclusionRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    
    keyword: str = Field(index=True)
    rule_type: str = Field(default="Recipient") # "Recipient", "Description", "Agency"
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# 8. Pinned Pipeline Dashboards (Bookmarking the Pipeline Trend page)
class PinnedPipeline(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    
    state_code: str = Field(index=True)
    agency: str = Field(index=True)
    recipient: str = Field(index=True)
    year: int = Field(index=True)
    
    pinned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Use the dynamic database URL from our .env config
engine = create_engine(
    settings.database_url, 
    echo=False, 
    connect_args={"check_same_thread": False}
)

# Turn on SQLite Write-Ahead Logging (WAL) mode
# This completely eliminates the dreaded "database is locked" SQLite crash 
# when background tasks and users try to read/write concurrently.
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)