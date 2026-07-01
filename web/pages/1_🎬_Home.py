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
Home Page - Main video generation interface
"""

import sys
from pathlib import Path

# Add project root to sys.path
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st

# Import state management
from web.state.session import init_session_state, init_i18n, get_pixelle_video

# Import components
from web.components.header import render_header
from web.components.settings import render_advanced_settings
from web.components.faq import render_faq_sidebar

# Page config
st.set_page_config(
    page_title="Home - Pixelle-Video",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def main():
    """Main UI entry point"""
    # Initialize session state and i18n
    init_session_state()
    init_i18n()
    
    # Render header (title + language selector)
    render_header()
    
    # Render FAQ in sidebar
    render_faq_sidebar()
    
    # Initialize Pixelle-Video
    pixelle_video = get_pixelle_video()
    
    # Render system configuration (LLM + ComfyUI)
    render_advanced_settings()
    
    # ========================================================================
    # Pipeline Selection & Delegation
    # ========================================================================
    from web.pipelines import get_all_pipeline_uis

    # Get all registered pipelines
    pipelines = get_all_pipeline_uis()

    # ponytail: 用 selectbox 选 pipeline + 只渲染选中那个，替代 st.tabs 全渲染。
    # st.tabs 每次 rerun 渲染所有 5 个 tab（各含几十个 widget），是创建/打开项目时
    # 卡顿的根因。selectbox 只渲染 1 个，rerun 成本降到 1/5。
    # 历史页"打开项目"通过 query param 指定 story_illustration。
    qp_pipeline = st.query_params.get("pipeline")
    pipeline_keys = [p.name for p in pipelines]
    default_idx = pipeline_keys.index(qp_pipeline) if qp_pipeline in pipeline_keys else 0
    if qp_pipeline:
        del st.query_params["pipeline"]

    st.session_state.setdefault("_home_pipeline_idx", default_idx)
    cur_idx = st.session_state["_home_pipeline_idx"]
    labels = [f"{p.icon} {p.display_name}" for p in pipelines]
    picked = st.selectbox("选择创作模式", labels, index=cur_idx, label_visibility="collapsed")
    st.session_state["_home_pipeline_idx"] = labels.index(picked)

    pipeline = pipelines[st.session_state["_home_pipeline_idx"]]
    if pipeline.description:
        st.caption(pipeline.description)
    pipeline.render(pixelle_video)


if __name__ == "__main__":
    main()

