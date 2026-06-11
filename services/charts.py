# /services/charts.py

import pandas as pd
import plotly.graph_objects as go
import plotly.utils
import json
from collections import defaultdict

def create_treemap_chart(flows):
    """
    Creates a proportional Treemap tailored for Federal Operations.
    Hierarchy: Category (Contracts/Grants) -> Flow Type -> Recipient.
    This handles the extreme power-law distribution of US Federal spending perfectly.
    """
    if not flows: return None
    
    # Sort and get top 60 flows to prevent SVG rendering issues while maintaining depth
    top_flows = sorted(flows, key=lambda x: x.get('amount_usd', 0), reverse=True)[:60]
    
    root_name = "Federal Portfolio"
    
    layer_totals = defaultdict(float)
    type_totals = defaultdict(float)
    recip_totals = defaultdict(float)
    
    total_volume = 0.0
    
    for f in top_flows:
        amt = f.get('amount_usd', 0)
        if amt <= 0: continue # Treemaps cannot process zero or negative areas
        
        # 1. Layer (Contracts vs Grants)
        layer = f.get('source_database', 'Unknown')
        if layer == "Contracts": 
            layer_label = "Federal Contracts"
        elif layer == "Grants": 
            layer_label = "Grants & Assistance"
        else: 
            layer_label = str(layer)
            
        # 2. Flow Type (Definitive Contract, Project Grant, etc)
        flow_type = f.get('flow_type', 'Unknown Award Type')
        
        # 3. Recipient
        recipient = f.get('recipient', 'Unknown Recipient')
        
        total_volume += amt
        layer_totals[layer_label] += amt
        
        # Compound keys prevent collisions if "World Vision" gets both a Contract and a Grant
        type_key = f"{layer_label}|{flow_type}"
        type_totals[type_key] += amt
        
        recip_key = f"{type_key}|{recipient}"
        recip_totals[recip_key] += amt
        
    if total_volume <= 0: return None

    ids = [root_name]
    labels = [root_name]
    parents = [""]
    values = [total_volume]
    
    # Add Level 1: Layers
    for layer_label, amt in layer_totals.items():
        ids.append(layer_label)
        labels.append(layer_label)
        parents.append(root_name)
        values.append(amt)
        
    # Add Level 2: Flow Types
    for type_key, amt in type_totals.items():
        # Bounded split: layer labels never contain '|', so split once.
        # Guards against flow_type strings that contain a literal '|'.
        layer_label, flow_type = type_key.split('|', 1)
        
        ids.append(type_key)
        labels.append(flow_type)
        parents.append(layer_label)
        values.append(amt)
        
    # Add Level 3: Recipients
    for recip_key, amt in recip_totals.items():
        # Bounded split from the LEFT twice: parts = [layer, flow_type, recipient].
        # Recipient names (most likely to contain junk characters) are never split.
        parts = recip_key.split('|', 2)
        type_key = f"{parts[0]}|{parts[1]}"
        recipient = parts[2]
        
        ids.append(recip_key)
        labels.append(recipient)
        parents.append(type_key)
        values.append(amt)

    fig = go.Figure(go.Treemap(
        ids=ids,
        labels=labels,
        parents=parents,
        values=values,
        branchvalues="total",
        textinfo="label+value+percent parent",
        texttemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percentParent:.1%}",
        marker=dict(
            line=dict(color='#121212', width=2) # Match dark UI border
        ),
        pathbar=dict(visible=True, textfont=dict(color='#9ca3af')),
        hovertemplate="<b>%{label}</b><br>Amount: $%{value:,.0f}<extra></extra>"
    ))

    fig.update_layout(
        margin=dict(t=30, l=10, r=10, b=10),
        height=500,
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family="Inter, sans-serif")
    )
    
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def create_timeline_chart(flows, start_year, end_year):
    """Aggregates multi-year data into a Stacked Area Chart by Source, padding empty years with zero."""
    if not flows: return None
    
    df = pd.DataFrame(flows)
    grouped = df.groupby(['year', 'source_database'])['amount_usd'].sum().reset_index()
    
    # Create a dataframe of all years in the horizon to prevent X-axis clipping
    all_years = pd.DataFrame({'year': range(start_year, end_year + 1)})
    
    fig = go.Figure()
    
    # 1. Plot Contracts Data (Blue)
    contract_data = grouped[grouped['source_database'] == 'Contracts']
    if not contract_data.empty:
        # Merge to inject zeros where data is missing
        c_merged = pd.merge(all_years, contract_data, on='year', how='left').fillna({'amount_usd': 0})
        fig.add_trace(go.Scatter(
            x=c_merged['year'], 
            y=c_merged['amount_usd'],
            mode='lines+markers',
            stackgroup='one',
            line=dict(color="#3b82f6", width=2),
            name="Federal Contracts"
        ))
        
    # 2. Plot Grants Data (Purple)
    grant_data = grouped[grouped['source_database'] == 'Grants']
    if not grant_data.empty:
        # Merge to inject zeros where data is missing
        g_merged = pd.merge(all_years, grant_data, on='year', how='left').fillna({'amount_usd': 0})
        fig.add_trace(go.Scatter(
            x=g_merged['year'], 
            y=g_merged['amount_usd'],
            mode='lines+markers',
            stackgroup='one',
            line=dict(color="#a855f7", width=2), 
            name="Grants & Financial Assistance"
        ))
    
    fig.update_layout(
        title_text="Multi-Year Federal Obligation Timeline",
        font_size=12,
        height=400,
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        # Force the axis bounds so the user explicitly sees their horizon parameters were accepted
        xaxis=dict(tickmode='linear', dtick=1, range=[start_year, end_year]),
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def create_activity_timeline_chart(activities, keyword, start_year, end_year):
    """Generates an Area Chart specifically for Multi-Year Activity Searches, padding empty years with zero."""
    if not activities: return None
    
    df = pd.DataFrame(activities)
    if 'year' not in df.columns:
        return None
        
    grouped = df.groupby('year')['amount'].sum().reset_index()
    
    # Create a dataframe of all years in the horizon to prevent X-axis clipping
    all_years = pd.DataFrame({'year': range(start_year, end_year + 1)})
    merged = pd.merge(all_years, grouped, on='year', how='left').fillna({'amount': 0})
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=merged['year'], 
        y=merged['amount'],
        mode='lines+markers',
        stackgroup='one',
        line=dict(color="#a855f7", width=2), # Purple for activity theme
        name=f"'{keyword.title()}' Funding"
    ))
    
    fig.update_layout(
        title_text=f"Surge Timeline: {keyword.title()}",
        font_size=12,
        height=350,
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        # Force the axis bounds so the user explicitly sees their horizon parameters were accepted
        xaxis=dict(tickmode='linear', dtick=1, range=[start_year, end_year]),
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def create_activity_treemap_chart(activities, keyword):
    """Generates a hierarchical Treemap for Activity Searches (Zone -> Agency -> Recipient)."""
    if not activities: return None
    
    # Aggregate flows to avoid massive SVGs
    pipeline = defaultdict(float)
    total_vol = 0.0
    
    for act in activities:
        # Prevent empty strings from breaking Plotly IDs
        state = str(act.get('state_code') or 'Unknown Zone').strip()
        agency = str(act.get('agency') or 'Unknown Agency').strip()
        recip = str(act.get('recipient_name') or 'Unknown Recipient').strip()
        amt = float(act.get('amount') or 0)
        
        if amt > 0:
            pipeline[(state, agency, recip)] += amt
            total_vol += amt
            
    if total_vol <= 0: return None

    root_name = f"'{keyword.title()}' Portfolio"
    ids = [root_name]
    labels = [root_name]
    parents = [""]
    values = [total_vol]

    state_totals = defaultdict(float)
    agency_totals = defaultdict(float)
    
    for (state, agency, recip), amt in pipeline.items():
        state_totals[state] += amt
        agency_key = f"Zone: {state}|{agency}"
        agency_totals[agency_key] += amt

    # Tier 1: Target Zones (States/Countries)
    for state, amt in state_totals.items():
        ids.append(f"Zone: {state}")
        labels.append(f"Zone: {state}")
        parents.append(root_name)
        values.append(amt)
        
    # Tier 2: Agencies
    for agency_key, amt in agency_totals.items():
        # Bounded split: zone codes never contain '|', so split once.
        # Guards against agency names containing a literal '|'.
        state_id, agency = agency_key.split('|', 1)
        ids.append(agency_key)
        labels.append(agency)
        parents.append(state_id)
        values.append(amt)
        
    # Tier 3: Recipients
    # BUG FIX: a stray `values.append(agency)` here previously injected a STRING
    # into the values array, desynchronizing ids/labels/parents/values and
    # breaking the treemap render entirely.
    for (state, agency, recip), amt in pipeline.items():
        agency_key = f"Zone: {state}|{agency}"
        recip_key = f"{agency_key}|{recip}"
        ids.append(recip_key)
        labels.append(recip)
        parents.append(agency_key)
        values.append(amt)

    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values,
        branchvalues="total",
        textinfo="label+value+percent parent",
        texttemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percentParent:.1%}",
        marker=dict(line=dict(color='#121212', width=2)),
        pathbar=dict(visible=True, textfont=dict(color='#9ca3af')),
    ))

    fig.update_layout(
        title_text=f"Aggregated Pipeline: {keyword.title()}",
        margin=dict(t=40, l=10, r=10, b=10),
        height=400,
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)'
    )
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def create_mini_trend_chart(flow_history, start_year, end_year):
    """Generates a small, clean line chart for the YoY pop-up modal."""
    if not flow_history: return None
    
    df = pd.DataFrame(flow_history)
    # Ensure all years in the horizon are represented, even if volume is 0
    all_years = pd.DataFrame({'year': range(start_year, end_year + 1)})
    
    if 'amount_usd' in df.columns:
        # Aggregate multiple same-year records BEFORE merging. Some callers
        # (e.g. /api/trend_modal) pass raw flow history with multiple awards
        # per year; merging unaggregated rows duplicated x-axis points and
        # drew sawtooth artifacts.
        yearly = df.groupby('year', as_index=False)['amount_usd'].sum()
        merged = pd.merge(all_years, yearly, on='year', how='left').fillna({'amount_usd': 0})
    else:
        # Fallback if empty
        merged = all_years.copy()
        merged['amount_usd'] = 0
        
    # Determine the line color and fully qualified RGBA fill color
    line_color = "#4ade80" 
    fill_color = "rgba(74, 222, 128, 0.2)" # Green transparent
    
    if flow_history:
        source_db = flow_history[-1].get('source_database', 'Grants')
        if source_db == 'Grants':
            line_color = "#a855f7" # Purple
            fill_color = "rgba(168, 85, 247, 0.2)"
        elif source_db == 'Contracts':
            line_color = "#3b82f6" # Blue
            fill_color = "rgba(59, 130, 246, 0.2)"
            
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=merged['year'], 
        y=merged['amount_usd'],
        mode='lines+markers',
        line=dict(color=line_color, width=3),
        marker=dict(size=8, color="#1e1e1e", line=dict(width=2, color=line_color)),
        fill='tozeroy',
        fillcolor=fill_color 
    ))
    
    fig.update_layout(
        font_size=10,
        height=200,
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(
            tickmode='linear', 
            dtick=1, 
            range=[start_year, end_year],
            showgrid=False,
            zeroline=False
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor='#374151',
            zeroline=False
        ),
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False
    )
    
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)