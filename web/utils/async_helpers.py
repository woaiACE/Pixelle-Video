# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Async helper functions for web UI
"""

import asyncio
import atexit
import sys
import tomllib
from pathlib import Path

from loguru import logger


# ponytail: module-level single event loop reused across run_async calls.
# Previously every call spun up a fresh ProactorEventLoop and tore it down
# (plus close_browser) — which forced HTMLFrameGenerator to relaunch Chromium
# on every script preview / generation since its browser is loop-bound
# (cls._browser_loop is current_loop). One persistent loop lets the shared
# browser and ComfyKit session survive across calls. Streamlit runs user code
# on a single script thread, so no cross-thread loop contention here.
# Upgrade path: per-session loops if concurrent Streamlit threads ever need it.
_LOOP = None


def _get_loop():
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        if sys.platform == "win32":
            # Proactor loop supports subprocess-based libs (Playwright) on Windows;
            # do not depend on the ambient policy Streamlit/Tornado may have set.
            _LOOP = asyncio.ProactorEventLoop()
        else:
            _LOOP = asyncio.new_event_loop()
    return _LOOP


def _teardown_loop():
    global _LOOP
    if _LOOP and not _LOOP.is_closed():
        try:
            from pixelle_video.services.frame_html import HTMLFrameGenerator

            _LOOP.run_until_complete(HTMLFrameGenerator.close_browser())
        except Exception as e:
            logger.debug(f"teardown close_browser failed: {e}")
        _LOOP.close()


atexit.register(_teardown_loop)


def run_async(coro):
    """Run async coroutine in sync context, reusing a persistent event loop"""
    return _get_loop().run_until_complete(coro)


def get_project_version():
    """Get project version from pyproject.toml"""
    try:
        # Get project root (web parent directory)
        web_dir = Path(__file__).resolve().parent.parent
        project_root = web_dir.parent
        pyproject_path = project_root / "pyproject.toml"
        
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                pyproject_data = tomllib.load(f)
                return pyproject_data.get("project", {}).get("version", "Unknown")
    except Exception as e:
        logger.warning(f"Failed to read version from pyproject.toml: {e}")
    return "Unknown"

