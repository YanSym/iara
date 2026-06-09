"""IAra — entrada da aplicação e roteador de navegação."""

from __future__ import annotations

import base64

import streamlit as st

st.set_page_config(
    page_title="IAra",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Encode logo as base64 so CSS can render it at any size without Streamlit constraints
with open("src/images/logo_iara.png", "rb") as _f:
    _logo_b64 = base64.b64encode(_f.read()).decode()

st.markdown(
    f"""
    <style>
    section[data-testid="stSidebar"] > div:first-child {{ padding-top: 0rem; }}
    section[data-testid="stSidebar"] .block-container {{ padding-top: 0rem; }}

    /* Render full-size logo as background of the header slot */
    [data-testid="stSidebarHeader"] {{
        background-image: url("data:image/png;base64,{_logo_b64}");
        background-size: contain;
        background-repeat: no-repeat;
        background-position: center;
        min-height: 200px !important;
        height: 200px !important;
        max-height: none !important;
        padding: 0 1rem !important;
    }}
    /* Hide the tiny img injected by st.logo() */
    [data-testid="stSidebarHeader"] img {{
        display: none !important;
    }}
    [data-testid="stSidebarHeader"] a {{
        display: block;
        width: 100%;
        height: 100%;
    }}

    div.stButton > button[kind="primary"] {{
        background-color: #1a5c2e;
        border: 1px solid #1a5c2e;
        color: #ffffff;
    }}
    div.stButton > button[kind="primary"]:hover {{
        background-color: #1a7a3c;
        border: 1px solid #1a7a3c;
        color: #ffffff;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# st.logo() keeps the slot above the nav — the CSS above overrides its tiny img
st.logo("src/images/logo_iara.png", size="large")

pg = st.navigation([
    st.Page("pages/0_Home.py",     title="Home",     default=True),
    st.Page("pages/1_Chat.py",     title="Chat"),
    st.Page("pages/2_Webhooks.py", title="Webhooks"),
])

with st.sidebar:
    st.divider()
    st.text_input("API URL",    value="http://localhost:8000", key="base_url")
    st.text_input("Tenant Key", value="test_tenant_001",       key="tenant_key")

pg.run()
