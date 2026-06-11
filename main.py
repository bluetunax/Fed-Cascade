# /main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
from typing import Optional

# Import database initialization
from models import create_db_and_tables

# Import our new modular routers
from routers import dashboard, profiles, activities, intelligence, version_control, changes, user, explorer

# Import the live state manager
from services.state import cascade_state

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the SQLite database on startup
    create_db_and_tables()
    yield

# Initialize the FastAPI application
app = FastAPI(title="Fed Cascade", lifespan=lifespan)

# Mount the static directory for CSS/JS
app.mount("/static", StaticFiles(directory="static"), name="static")

# Plug in all the separated routes
app.include_router(dashboard.router)
app.include_router(activities.router)
app.include_router(profiles.router)
app.include_router(intelligence.router)
app.include_router(version_control.router)
app.include_router(changes.router) # Watchdog Dashboard Router
app.include_router(user.router)    # User Profile & Bookmarking Router
app.include_router(explorer.router) # NEW: Deep Explorer Router

# --- HEALTH CHECK ENDPOINT ---
@app.get("/health", response_class=JSONResponse)
async def health_check():
    """
    Standard health check endpoint for Docker/Kubernetes deployment monitoring.
    Returns 200 OK if the ASGI server is running.
    """
    return {"status": "ok", "app": "Fed Cascade v1.0"}

# --- Live Status Endpoint for HTMX Polling ---
@app.get("/api/status", response_class=HTMLResponse)
async def get_system_status(client_id: Optional[str] = "default"):
    """Returns a live HTML snippet of the backend engine status for a specific client."""
    # Retrieve the state specifically tied to this user's browser session
    user_state = cascade_state.get_state(client_id)
    status = user_state.current_status
    progress = user_state.progress_percent
    
    # Clean, Halo-style HUD HTML snippet
    html_content = f"""
    <div class="text-center font-mono">
        <h2 class="text-3xl font-bold text-white tracking-widest uppercase mb-3 shadow-blue-500/50 drop-shadow-md">Establishing Link...</h2>
        <p class="text-blue-400 text-sm uppercase tracking-widest animate-pulse mb-6">{status}</p>
        
        <div class="w-80 h-1 bg-blue-900/30 mx-auto overflow-hidden rounded relative">
            <div class="h-full bg-blue-500 shadow-[0_0_10px_#3b82f6] transition-all duration-300" style="width: {progress}%"></div>
        </div>
        <div class="text-blue-500/50 text-[10px] text-right w-80 mx-auto mt-1 tracking-widest">{progress}%</div>
    </div>
    """
    return html_content


if __name__ == '__main__':
    import uvicorn
    # Run the server
    uvicorn.run("main:app", host="127.0.0.1", port=5000, reload=True)