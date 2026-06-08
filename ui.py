"""IAra — entrada da aplicação e roteador de navegação."""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="IAra",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] > div:first-child { padding-top: 0rem; }
    section[data-testid="stSidebar"] .block-container { padding-top: 0rem; }
    div.stButton > button[kind="primary"] {
        background-color: #1a5c2e;
        border: 1px solid #1a5c2e;
        color: #ffffff;
    }
    div.stButton > button[kind="primary"]:hover {
        background-color: #1a7a3c;
        border: 1px solid #1a7a3c;
        color: #ffffff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.image("src/images/logo_iara.png")
    st.divider()

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
