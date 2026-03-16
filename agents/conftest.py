import sys
import os

# Must be set before any google.genai import or it raises "API key must be set"
os.environ.setdefault("GEMINI_API_KEY", "test-api-key-placeholder")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")
os.environ.setdefault("DEMO_MODE", "true")

# Add agents/ to path so tests can import live_session, root_agent, etc.
sys.path.insert(0, os.path.dirname(__file__))
