# Requirements for the Streamlit webapp (pip reads any filename via -r):
#
#     pip install -r requirements.py
#
# Pulls the agent's own requirements (pydocs-mcp + langgraph + adapters)
# and adds the UI layer.
-r requirements.txt
streamlit>=1.36
