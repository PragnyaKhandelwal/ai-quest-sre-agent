"""
conftest.py (repo root)

Ensures the test suite is fully deterministic and never depends on
whatever LLM provider credentials happen to be present in a developer's
local .env file. These tests assert against the deterministic
local-simulation fallback (agents/*_agent.py's `_simulate()` functions),
not arbitrary real-LLM judgment -- a real Groq/OpenAI/Lyzr response can
legitimately differ from the fixed expectations here (e.g. a different
but still-reasonable severity), and has no visibility into local,
in-process state like the triage dedup cache. So real credentials must
never leak into a pytest run.

This must run before any agents.* module is imported (the moment a test
module does `from agents import ...`), since agents/config.py reads these
env vars once at import time and derives LYZR_ENABLED/LLM_PROVIDER/etc.
from them. pytest always loads the rootdir conftest.py before collecting
or importing test modules, which guarantees this ordering.
"""
import os

# Set to empty string rather than removing the keys: agents/config.py calls
# python-dotenv's load_dotenv() at import time with its default
# override=False, which only skips a key that is *already present* in
# os.environ -- an empty string counts as present and is left alone, but a
# genuinely absent (popped/never-set) key would still get filled in from a
# developer's local .env, defeating the point of this file.
for _key in ("LYZR_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
    os.environ[_key] = ""
