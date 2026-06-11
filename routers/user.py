# /routers/user.py

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import html

# Import our database logic
from db_manager import (
    get_or_create_local_user,
    toggle_pinned_record,
    get_user_pins,
    add_exclusion_rule,
    remove_exclusion_rule,
    get_exclusion_rules,
    get_user_pinned_ids,
    toggle_pinned_pipeline,       # NEW
    get_user_pinned_pipelines     # NEW
)

from models import engine, PinnedPipeline
from sqlmodel import Session

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/my_cascade")
async def user_profile_page(request: Request):
    """Renders the User Profile showing pinned awards, pinned pipelines, and exclusion rules."""
    user = get_or_create_local_user()
    
    pins = get_user_pins(user.id)
    pinned_pipelines = get_user_pinned_pipelines(user.id) # Fetch the saved dashboards
    rules = get_exclusion_rules(user.id)
    
    return templates.TemplateResponse(
        request=request,
        name="user_profile.html",
        context={
            "user": user,
            "pins": pins,
            "pinned_pipelines": pinned_pipelines, # Pass to template
            "rules": rules
        }
    )

@router.post("/toggle_pin")
async def toggle_pin(
    request: Request,
    source_record_id: str = Form(...),
    year: int = Form(2023),
    agency: str = Form("Unknown"),
    recipient: str = Form("Unknown"),
    project: str = Form("General Flow"),
    description: str = Form(""),
    amount: float = Form(0.0),
    state_code: str = Form("ALL")
):
    """
    HTMX endpoint to toggle bookmarking a specific award.
    Returns the updated HTML for the pin button container.
    """
    user = get_or_create_local_user()
    
    record_data = {
        "source_record_id": source_record_id,
        "year": year,
        "agency": agency,
        "recipient_name": recipient,
        "project": project,
        "description": description,
        "amount": amount,
        "state_code": state_code
    }
    
    is_pinned = toggle_pinned_record(user.id, record_data)
    
    # Safely escape text for the hidden inputs
    safe_desc = html.escape(description)
    safe_proj = html.escape(project)
    safe_recip = html.escape(recipient)
    safe_agency = html.escape(agency)

    # Determine visual state of the star
    star_icon = "⭐" if is_pinned else "☆"
    color_class = "text-yellow-400" if is_pinned else "text-gray-500 hover:text-yellow-400"
    title_text = "Unpin this award" if is_pinned else "Pin to profile"
    
    # Return the exact same container structure so HTMX swaps it cleanly
    html_content = f"""
    <div class="inline-block pin-container">
        <input type="hidden" name="source_record_id" value="{source_record_id}">
        <input type="hidden" name="year" value="{year}">
        <input type="hidden" name="agency" value="{safe_agency}">
        <input type="hidden" name="recipient" value="{safe_recip}">
        <input type="hidden" name="project" value="{safe_proj}">
        <input type="hidden" name="description" value="{safe_desc}">
        <input type="hidden" name="amount" value="{amount}">
        <input type="hidden" name="state_code" value="{state_code}">
        
        <button type="button" 
                hx-post="/toggle_pin" 
                hx-include="closest .pin-container" 
                hx-target="closest .pin-container"
                hx-swap="outerHTML"
                class="{color_class} text-lg transition-colors focus:outline-none" 
                title="{title_text}">
            {star_icon}
        </button>
    </div>
    """
    
    return HTMLResponse(content=html_content)

@router.post("/toggle_pipeline_pin")
async def toggle_pipeline_pin(
    request: Request,
    state_code: str = Form(...),
    agency: str = Form(...),
    recipient: str = Form(...),
    year: int = Form(...)
):
    """
    HTMX endpoint to toggle bookmarking an entire 5-Year Pipeline Trend Dashboard.
    Returns the updated HTML for the big dashboard button.
    """
    user = get_or_create_local_user()
    
    is_pinned = toggle_pinned_pipeline(user.id, state_code, agency, recipient, year)
    
    # Safely escape text
    safe_agency = html.escape(agency)
    safe_recipient = html.escape(recipient)
    
    # Determine visual state for a nice large button
    star_icon = "⭐ Saved to Profile" if is_pinned else "☆ Save Pipeline"
    color_class = "text-yellow-400 border-yellow-500/50 bg-yellow-900/20" if is_pinned else "text-gray-400 border-gray-600 hover:text-yellow-400 hover:border-yellow-500 hover:bg-yellow-900/10"
    
    html_content = f"""
    <form hx-post="/toggle_pipeline_pin" hx-swap="outerHTML" class="inline-block pipeline-pin-form m-0">
        <input type="hidden" name="state_code" value="{state_code}">
        <input type="hidden" name="agency" value="{safe_agency}">
        <input type="hidden" name="recipient" value="{safe_recipient}">
        <input type="hidden" name="year" value="{year}">
        
        <button type="submit" 
                class="font-bold uppercase tracking-widest text-xs px-4 py-2.5 rounded-lg border transition-colors flex items-center {color_class}">
            {star_icon}
        </button>
    </form>
    """
    
    return HTMLResponse(content=html_content)

@router.post("/add_exclusion_quick")
async def add_exclusion_quick(keyword: str = Form(...), rule_type: str = Form("Recipient")):
    """
    HTMX endpoint for the inline '🚫 Hide' buttons in the tables.
    Returns an empty string, which tells HTMX to delete the row from the DOM.
    """
    user = get_or_create_local_user()
    success = add_exclusion_rule(user.id, keyword, rule_type)
    
    if not success:
        return HTMLResponse(status_code=400, content="Invalid rule")
        
    # Returning empty content with hx-swap="outerHTML" deletes the element from the screen!
    return HTMLResponse(content="")

@router.post("/add_exclusion")
async def add_exclusion_form(keyword: str = Form(...), rule_type: str = Form("Recipient")):
    """Standard form submission from the User Profile page."""
    user = get_or_create_local_user()
    add_exclusion_rule(user.id, keyword, rule_type)
    
    # Force the browser to reload the profile page so the new rule appears
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})

@router.delete("/remove_exclusion/{rule_id}")
async def remove_exclusion(rule_id: int):
    """Deletes a rule from the User Profile page."""
    remove_exclusion_rule(rule_id)
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})

@router.delete("/remove_pinned_pipeline/{pipeline_id}")
async def remove_pinned_pipeline(pipeline_id: int):
    """Deletes a pinned pipeline directly from the User Profile page."""
    with Session(engine) as session:
        pipeline = session.get(PinnedPipeline, pipeline_id)
        if pipeline:
            session.delete(pipeline)
            session.commit()
    return HTMLResponse(status_code=200, headers={"HX-Refresh": "true"})