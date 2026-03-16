import sys
import os

# Add backend/ and agents/ to the path so pytest can find main.py and agent modules
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
