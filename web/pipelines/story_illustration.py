# Copyright (C) 2025 AIDC-AI
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""
Story Illustration Pipeline UI — 项目级管理 + 4 阶段工作区。

进入页面 → 项目列表（新建/继续）。点击项目 → 工作区（故事/资产库/分镜/生成），
阶段可来回切换，编辑自动落盘到 output/projects/{id}/，重进恢复。

详见 SPEC_story_project.md。
"""

import os
from typing import Any

import streamlit as st
from loguru import logger

from web.i18n import tr, get_language
from web.pipelines.base import PipelineUI, register_pipeline_ui
from web.utils.async_helpers import run_async
from web.components.digital_tts_config import render_style_config as render_tts_config
from pixelle_video.config import config_manager
from pixelle_video.models.progress import ProgressEvent
from pixelle_video.prompts.story_prompts import (
    build_story_scenecut_prompt,
    build_story_panel_image_batch_prompt,
    assets_to_text,
)
from pixelle_video.utils.content_generators import _parse_json
from pixelle_video.services.project_service import ProjectService

# session_state keys
_CUR_PROJECT = "story_cur_project_id"   # 当前打开的项目 id
_STAGE = "story_stage"                  # story | assets | storyboard | video
_ASSET_LIB = "story_asset_lib"
_STYLE_PARAMS = "story_style_params"


def _ss(key, default=None):
    return st.session_state.get(key, default)


def _tr(key, default=None):
    """tr() with fallback. tr() returns the key itself when missing (no raise),
    so we pass fallback through and detect miss by equality."""
    result = tr(key, fallback=default)
    return result if result != key else (default if default is not None else key)


def _has_tr(key: str) -> bool:
    """仅用于 progress 等需要布尔判定的场景。"""
    return tr(key) != key


def _busy() -> bool:
    """生成/长任务进行中。触发按钮据此 disabled，防止阻塞期间缓冲的重复点击在任务结束后触发。"""
    return bool(st.session_state.get("story_generating"))


class StoryIllustrationPipelineUI(PipelineUI):
    """绘本故事插图视频 —— 项目级管理 UI。"""

    name = "story_illustration"
    icon = "📖"

    @property
    def display_name(self):
        return _tr("pipeline.story_illustration.name", "故事插图视频")

    @property
    def description(self):
        return _tr("pipeline.story_illustration.description",
                   "输入故事，AI 提取角色场景、分镜、生成一致性插图视频")

    def render(self, pixelle_video: Any):
        # 历史页"打开项目"通过 query param 传入 project_id
        qp = st.query_params.get("story_project")
        if qp and not _ss(_CUR_PROJECT):
            st.session_state[_CUR_PROJECT] = qp
            st.session_state.setdefault(_STAGE, "story")
            # 清掉 query param，避免刷新重复触发
            del st.query_params["story_project"]
            project = ProjectService.load_project(qp)
            if project:
                self._restore_project_to_session(project)
            st.rerun()
            return

        project_id = _ss(_CUR_PROJECT)
        if not project_id:
            self._render_project_list()
        else:
            project = ProjectService.load_project(project_id)
            if project is None:
                # 项目被删/丢失，退回列表
                st.session_state.pop(_CUR_PROJECT, None)
                st.rerun()
                return
            self._render_workspace(pixelle_video, project)

    # ==================== 项目列表 ====================

    def _render_project_list(self):
        st.markdown(f"**{_tr('section.story_projects', '我的故事项目')}**")

        col_new, _ = st.columns([1, 3])
        with col_new:
            with st.popover("➕ 新建项目", width="stretch"):
                title = st.text_input("项目标题", key="new_project_title", placeholder="如：小兔子的发光种子")
                if st.button("创建", type="primary", width="stretch"):
                    if not title.strip():
                        st.error("请输入标题")
                        st.stop()
                    project = ProjectService.create_project(title.strip())
                    st.session_state[_CUR_PROJECT] = project.project_id
                    st.session_state[_STAGE] = "story"
                    st.rerun()

        projects = ProjectService.list_projects()
        if not projects:
            st.info("还没有故事项目，点击「新建项目」开始创作。")
            return

        # 卡片网格
        cols = st.columns(3)
        for i, p in enumerate(projects):
            with cols[i % 3]:
                with st.container(border=True):
                    # 缩略图
                    if p.cover_path and os.path.exists(p.cover_path):
                        st.image(p.cover_path, width="stretch")
                    else:
                        st.markdown("🎬")
                    st.markdown(f"**{p.title}**")
                    # 进度阶段点
                    stages = ["story", "assets", "storyboard", "video"]
                    labels = ["故事", "资产", "分镜", "视频"]
                    dots = " ".join(
                        "🟢" if p.stages_ready.get(s) else "⚪" for s in stages
                    )
                    st.caption(f"{dots}　{'·'.join(labels)}")
                    st.caption(f"📅 {p.updated_at[:16] if p.updated_at else ''}")
                    if st.button("打开", key=f"open_{p.project_id}", width="stretch"):
                        st.session_state[_CUR_PROJECT] = p.project_id
                        st.session_state[_STAGE] = p.current_stage or "story"
                        self._restore_project_to_session(p)
                        st.rerun()
                    # 删除
                    with st.popover("🗑", width="stretch"):
                        st.write(f"确认删除「{p.title}」？此操作不可撤销。")
                        if st.button("确认删除", key=f"del_{p.project_id}", type="primary"):
                            ProjectService.delete_project(p.project_id)
                            st.rerun()

    def _restore_project_to_session(self, project):
        """从磁盘恢复项目内容到 session_state，供工作区表单填充。"""
        pid = project.project_id
        st.session_state["story_text"] = ProjectService.load_story(pid)
        st.session_state["story_title"] = project.title
        st.session_state["story_n_scenes"] = project.n_scenes
        st.session_state["story_auto_scenes"] = project.auto_scenes
        st.session_state["story_provider"] = project.story_provider
        st.session_state["story_art_style_key"] = project.art_style_key
        if project.prompt_prefix:
            st.session_state["story_art_style"] = project.prompt_prefix
        if project.frame_template:
            st.session_state["story_frame_template"] = project.frame_template
        if project.brand:
            st.session_state["story_brand"] = project.brand
        st.session_state["story_style_params"] = project.style_params or {}
        st.session_state[_ASSET_LIB] = ProjectService.load_assets(pid)
        scenes = ProjectService.load_scenes(pid)
        st.session_state["story_scenes"] = scenes
        # 摄影字段从 scenes 拆出（供生成阶段直接使用）
        st.session_state["story_final_narrations"] = [s.get("narration", "") for s in scenes]
        st.session_state["story_final_compositions"] = [s.get("composition", "") for s in scenes]
        st.session_state["story_final_shots"] = [s.get("shot_type", "") for s in scenes]
        st.session_state["story_final_cameras"] = [s.get("camera_move", "") for s in scenes]
        st.session_state["story_final_lightings"] = [s.get("lighting", "") for s in scenes]

    # ==================== 工作区 ====================

    def _render_workspace(self, pixelle_video, project):
        # 顶栏：返回 + 标题
        col_back, col_title = st.columns([1, 4])
        with col_back:
            if st.button("⬅️ 项目列表", width="stretch"):
                st.session_state.pop(_CUR_PROJECT, None)
                st.rerun()
        with col_title:
            st.markdown(f"### 📖 {project.title}")

        # 阶段导航
        stages = ["story", "assets", "storyboard", "video"]
        labels = ["1️⃣ 故事", "2️⃣ 资产库", "3️⃣ 分镜", "4️⃣ 生成"]
        st.session_state.setdefault(_STAGE, "story")
        cur = _ss(_STAGE, "story")
        cols = st.columns(4)
        for i, (s, label) in enumerate(zip(stages, labels)):
            with cols[i]:
                ready = project.stages_ready.get(s, False)
                marker = "🟢" if s == cur else ("✅" if ready else "⚪")
                # 故事阶段始终可进；后续阶段需前一阶段就绪，避免空 story 调 LLM
                prev_ready = True if s == "story" else project.stages_ready.get(stages[i - 1], False)
                if st.button(f"{marker} {label}", key=f"stage_{s}", width="stretch",
                             disabled=(not prev_ready and s != cur) or _busy()):
                    st.session_state[_STAGE] = s
                    st.rerun()

        st.divider()

        if cur == "story":
            self._stage_story(pixelle_video, project)
        elif cur == "assets":
            self._stage_assets(pixelle_video, project)
        elif cur == "storyboard":
            self._stage_storyboard(pixelle_video, project)
        else:
            self._stage_video(pixelle_video, project)

    def _persist_project_meta(self, project):
        """把 session_state 里的故事配置写回 project.json。"""
        project.title = _ss("story_title") or project.title
        project.story_provider = _ss("story_provider")
        project.art_style_key = _ss("story_art_style_key")
        project.prompt_prefix = _ss("story_art_style", "")
        project.style_params = _ss(_STYLE_PARAMS, {}) or {}
        project.auto_scenes = _ss("story_auto_scenes", True)
        project.n_scenes = _ss("story_n_scenes")
        project.frame_template = _ss("story_frame_template")
        project.brand = _ss("story_brand")
        project.current_stage = _ss(_STAGE, "story")
        ProjectService._finalize(project)

    # ==================== 阶段 1：故事 ====================

    def _stage_story(self, pixelle_video, project):
        st.markdown(f"**{_tr('section.story_input', '故事输入')}**")

        story = st.text_area(
            "故事文本", value=_ss("story_text", ""), height=200,
            key="story_text_input",
            placeholder="输入一段故事…例如：小兔子白白在森林里捡到一颗发光的种子…",
        )
        title = st.text_input(
            "视频标题（可选，留空则 AI 自动生成）",
            value=_ss("story_title") or "",
            key="story_title_input",
        )
        auto_scenes = st.checkbox(
            "🎨 AI 自动决定分镜数量（推荐）",
            value=_ss("story_auto_scenes", True), key="story_auto_scenes_input",
        )
        if auto_scenes:
            n_scenes = None
            st.caption("将根据故事长度与节奏由 AI 推理分镜数。")
        else:
            n_scenes = st.number_input(
                "分镜数量", min_value=2, max_value=12,
                value=_ss("story_n_scenes", 6), key="story_n_scenes_input",
            )

        from pixelle_video.prompts.image_generation import IMAGE_STYLE_PRESETS
        from web.pipelines.api_workflows import list_api_media_workflows
        style_keys = list(IMAGE_STYLE_PRESETS.keys())
        _style_zh = {k: IMAGE_STYLE_PRESETS[k].get("name", k) for k in style_keys}
        col_a, col_b = st.columns(2)
        with col_a:
            # ponytail: 首次无值时设默认 watercolor；有值（恢复项目）时不传 index，
            # 避免 widget 同时有 session_state 值和 default index 冲突
            _saved_style = _ss("story_art_style_key")
            if not _saved_style or _saved_style not in style_keys:
                st.session_state["story_art_style_key"] = (
                    "watercolor" if "watercolor" in style_keys else style_keys[0]
                )
            art_style_key = st.selectbox(
                "画风预设", style_keys,
                format_func=lambda k: _style_zh[k], key="story_art_style_key",
            )
        with col_b:
            _img_wfs = list_api_media_workflows(pixelle_video, "image")
            _img_displays = [w["display_name"] for w in _img_wfs] or ["（未配置图片模型）"]
            _img_keys = [w["key"] for w in _img_wfs] or [None]
            _saved = _ss("story_provider")
            _preferred = "gemini-3.1-flash-image"
            _def_idx = (
                _img_keys.index(_saved) if _saved in _img_keys
                else next((i for i, k in enumerate(_img_keys) if k and _preferred in k), None)
                if any(k and _preferred in k for k in _img_keys)
                else next((i for i, k in enumerate(_img_keys) if k and "gemini" in k), 0)
            )
            _sel = st.selectbox(
                "图片生成 Provider", _img_displays, index=_def_idx,
                key="story_provider_input",
                help="资产图与插图用同一 provider。Gemini 的 img2img 契约最干净。",
            )
            provider = _img_keys[_img_displays.index(_sel)]

        # 故事专用模板选择（扫描 templates/*/story_*.html）
        from pixelle_video.utils.os_util import list_resource_files
        _story_templates = []
        for size_dir in ("1080x1920",):
            for f in list_resource_files("templates", size_dir):
                if f.startswith("story_") and f.endswith(".html"):
                    _story_templates.append(f"{size_dir}/{f}")
        _tpl_names = {
            "story_classic.html": "经典绘本（米黄四角）",
            "story_comic.html": "漫画风（黑边对话框）",
            "story_warm.html": "温暖风（柔光圆角）",
            "story_fairy.html": "童话故事（梦幻星月）",
            "story_bedtime.html": "睡前故事（深蓝夜空）",
        }
        _saved_tpl = _ss("story_frame_template")
        if not _saved_tpl or _saved_tpl not in _story_templates:
            st.session_state["story_frame_template"] = _story_templates[0] if _story_templates else "1080x1920/story_classic.html"
        st.selectbox(
            "故事模板", _story_templates,
            format_func=lambda k: _tpl_names.get(k.split("/")[-1], k.split("/")[-1]),
            key="story_frame_template",
            help="字幕样式由模板的旁白区决定，选择不同模板即不同字幕风格。",
        )
        # 右下角署名（覆盖模板 {{brand=Pixelle-Video}} 默认值）
        _saved_brand = _ss("story_brand")
        if _saved_brand is None:
            st.session_state["story_brand"] = "Pixelle-Video"
        st.text_input("右下角署名", key="story_brand", help="显示在每页右下角的署名文字")

        # 模板预览：调 HTMLFrameGenerator 用示例数据生成预览帧
        if st.button("🔍 预览模板", key="preview_story_template", width="stretch", disabled=_busy()):
            from pixelle_video.services.frame_html import HTMLFrameGenerator
            from pixelle_video.utils.template_util import resolve_template_path
            tpl_path = resolve_template_path(_ss("story_frame_template"))
            # 占位图：浅灰 SVG data URI
            _ph = "data:image/svg+xml;base64," + __import__("base64").b64encode(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"><rect width="1024" height="1024" fill="#e8d8b8"/><text x="512" y="512" font-size="60" text-anchor="middle" fill="#a08060" dominant-baseline="middle">故事插图</text></svg>'.encode()
            ).decode()
            with st.spinner("生成模板预览…"):
                try:
                    gen = HTMLFrameGenerator(tpl_path)
                    out = run_async(gen.generate_frame(
                        title="小兔子的发光种子",
                        text="白白在森林里捡到一颗发光的种子。",
                        image=_ph,
                        ext={"index": 1, "page_count": 8, "brand": _ss("story_brand") or "Pixelle-Video"},
                    ))
                    if out and os.path.exists(out):
                        st.image(out, width="stretch")
                    else:
                        st.warning("预览生成失败")
                except Exception as e:
                    st.error(f"预览失败：{e}")

        st.markdown("---")
        st.markdown(f"**{_tr('section.tts', '🎤 配音合成')}**")
        style_params = render_tts_config(pixelle_video, key_prefix="story_")
        st.session_state[_STYLE_PARAMS] = style_params
        st.session_state["story_art_style"] = IMAGE_STYLE_PRESETS[art_style_key]["prefix"]
        st.session_state["story_provider"] = provider
        st.session_state["story_n_scenes"] = int(n_scenes) if n_scenes else None
        st.session_state["story_title"] = title.strip() or None
        st.session_state["story_auto_scenes"] = auto_scenes
        st.session_state["story_text"] = story

        # 落盘故事 + 配置
        # 两阶段：点按钮 → 存 story + set busy + rerun → rerun 时执行三次 LLM 提取
        if _busy() and _ss("_pending_extract"):
            with st.spinner("AI 正在分类型提取角色/场景/道具…"):
                try:
                    from pixelle_video.prompts.story_prompts import (
                        build_story_character_prompt,
                        build_story_location_prompt,
                        build_story_prop_prompt,
                    )
                    extract_story = _ss("story_text", "")
                    lib = {"characters": [], "scenes": [], "props": []}
                    for kind, builder in (
                        ("characters", build_story_character_prompt),
                        ("scenes", build_story_location_prompt),
                        ("props", build_story_prop_prompt),
                    ):
                        resp = run_async(pixelle_video.llm(
                            builder(extract_story), temperature=0.7, max_tokens=None
                        ))
                        items = _parse_json(resp).get(kind, [])
                        for it in items:
                            it["image_path"] = None
                        lib[kind] = items
                    st.session_state[_ASSET_LIB] = lib
                    ProjectService.save_assets(project.project_id, lib)
                    for k in list(st.session_state.keys()):
                        if k.startswith("asset_") and (k.endswith("_name") or k.endswith("_desc")):
                            del st.session_state[k]
                    st.session_state[_STAGE] = "assets"
                    st.success(f"✅ 提取完成：角色 {len(lib.get('characters', []))} / 场景 {len(lib.get('scenes', []))} / 道具 {len(lib.get('props', []))}")
                except Exception as e:
                    logger.exception(e)
                    st.error(f"提取失败：{e}")
                finally:
                    st.session_state["story_generating"] = False
                    st.session_state["_pending_extract"] = False
                    st.rerun()
        elif st.button("💾 保存故事并提取资产", type="primary", width="stretch", disabled=_busy()):
            if not story.strip():
                st.error("请先输入故事文本")
                st.stop()
            ProjectService.save_story(
                project.project_id, story, title=title.strip() or project.title,
            )
            self._persist_project_meta(project)
            st.session_state["_pending_extract"] = True
            st.session_state["story_generating"] = True
            st.rerun()

    # ==================== 阶段 2：资产库 ====================

    def _queue_asset(self, target):
        """把待办资产生成存入 session_state + set busy + rerun，两阶段防重复点击。
        target: "all" 或 (kind, idx)。"""
        st.session_state["_pending_asset"] = target
        st.session_state["story_generating"] = True
        st.rerun()

    def _stage_assets(self, pixelle_video, project):
        lib = _ss(_ASSET_LIB, {"characters": [], "scenes": [], "props": []})
        st.markdown(f"**{_tr('section.asset_library', '资产库编辑')}**")
        st.caption("可编辑名称/描述，可新增/删除，生成参考图。资产图将作为插图参考图，保证角色跨分镜一致。")

        # 两阶段：busy 时执行待办资产生成，按钮全 disabled，防缓冲点击重复触发。
        # 每生成一张就 rerun，实时刷新资产库图片；队列空才清 busy。
        if _busy() and _ss("_pending_asset"):
            pending = _ss("_pending_asset")
            if pending == "all":
                # 重建待办队列（跳过已有图），取第一张跑，余下下轮 rerun 继续
                todo = [(k, i) for k in ("characters", "scenes", "props")
                        for i, it in enumerate(lib.get(k, [])) if not it.get("image_path")]
                if todo:
                    k, idx = todo[0]
                    with st.spinner(f"正在生成资产图… ({len(todo)} 项待生成)"):
                        self._gen_one_asset(pixelle_video, lib[k][idx], k)
                    ProjectService.save_assets(project.project_id, lib)
                    st.rerun()  # 刷新图片，继续下一张
                else:
                    st.session_state["story_generating"] = False
                    st.session_state["_pending_asset"] = None
                    st.rerun()
                return
            else:
                kind, idx = pending
                items = lib.get(kind, [])
                if idx < len(items):
                    with st.spinner(f"正在生成 {items[idx].get('name') or '资产'}…"):
                        self._gen_one_asset(pixelle_video, items[idx], kind)
                ProjectService.save_assets(project.project_id, lib)
                st.session_state["story_generating"] = False
                st.session_state["_pending_asset"] = None
                st.rerun()
                return

        kind_label = {"characters": "🧑 角色", "scenes": "🏞️ 场景", "props": "🎒 道具"}
        for kind in ("characters", "scenes", "props"):
            items = lib.setdefault(kind, [])
            with st.expander(f"{kind_label[kind]}（{len(items)}）", expanded=(kind == "characters")):
                for idx, it in enumerate(items):
                    c1, c2, c3 = st.columns([2, 3, 1])
                    with c1:
                        it["name"] = st.text_input("名称", value=it.get("name", ""), key=f"asset_{kind}_{idx}_name")
                    with c2:
                        it["description"] = st.text_area("描述", value=it.get("description", ""), height=68, key=f"asset_{kind}_{idx}_desc")
                    with c3:
                        if it.get("image_path"):
                            st.image(it["image_path"], width="stretch")
                            if st.button("重生", key=f"asset_{kind}_{idx}_regen", disabled=_busy()):
                                self._queue_asset(kind, idx)
                        else:
                            st.write("—")
                            if st.button("生成", key=f"asset_{kind}_{idx}_gen", disabled=_busy()):
                                self._queue_asset(kind, idx)
                        if st.button("🗑", key=f"asset_{kind}_{idx}_del", help="删除此项", disabled=_busy()):
                            items.pop(idx)
                            st.rerun()
                        if it.get("_error"):
                            st.error(f"❌ {it['_error']}")
                if st.button("➕ 新增", key=f"asset_{kind}_add", disabled=_busy()):
                    items.append({"name": "", "description": "", "image_path": None})
                    st.rerun()

        st.markdown("---")
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("⬅️ 故事", width="stretch", disabled=_busy()):
                st.session_state[_STAGE] = "story"
                st.rerun()
        with col2:
            if st.button("🎨 生成全部资产图", type="primary", width="stretch", disabled=_busy()):
                self._queue_asset("all")
        with col3:
            n_ready = sum(1 for k in ("characters", "scenes", "props") for it in lib.get(k, []) if it.get("image_path"))
            n_total = sum(len(lib.get(k, [])) for k in ("characters", "scenes", "props"))
            if st.button(f"下一步：分镜 ➡️  ({n_ready}/{n_total} 已生成，可跳过)", width="stretch", disabled=_busy()):
                ProjectService.save_assets(project.project_id, lib)
                self._persist_project_meta(project)
                # 进入分镜前清旧分镜
                st.session_state.pop("story_scenes", None)
                st.session_state[_STAGE] = "storyboard"
                st.rerun()

    def _gen_one_asset(self, pixelle_video, it: dict, kind: str = "props"):
        """生成单项资产图。kind=characters 用四视角模板；其余 1:1。"""
        try:
            import tempfile, httpx
            from pixelle_video.utils.prompt_helper import build_image_prompt
            from pixelle_video.prompts.story_prompts import build_character_sheet_prompt

            base = it.get("description", "")
            if kind == "characters":
                base = build_character_sheet_prompt(base)
            prefix = _ss("story_art_style", "")
            prompt = build_image_prompt(base, prefix)
            provider = _ss("story_provider")
            asset_w = asset_h = 1280 if kind == "characters" else 1024
            media_result = run_async(pixelle_video.media(
                prompt=prompt, workflow=provider, media_type="image",
                width=asset_w, height=asset_h,
            ))
            if not media_result.is_image:
                it["image_path"] = None
                it["_error"] = "provider 未返回图片（检查 provider 配置或额度）"
                return
            url = media_result.url
            if url.startswith("file://") or os.path.exists(url):
                it["image_path"] = url[7:] if url.startswith("file://") else url
                it["_error"] = None
                return
            import re
            _slug = re.sub(r"[^\w]+", "_", (it.get("name") or "asset")).strip("_")[:40] or "asset"
            out = os.path.join(tempfile.gettempdir(), f"story_asset_{kind}_{_slug}.png")
            async def _dl():
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    with open(out, "wb") as f:
                        f.write(r.content)
            run_async(_dl())
            it["image_path"] = out
            it["_error"] = None
        except Exception as e:
            logger.warning(f"asset gen failed for {it.get('name')}: {e}")
            it["image_path"] = None
            it["_error"] = str(e)

    # ==================== 阶段 3：分镜 ====================

    def _asset_lookup(self, lib: dict):
        """构建资产名 → {path, desc} 索引，供绑定资产取参考图/描述。"""
        idx = {}
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                if it.get("name"):
                    idx[it["name"]] = {"path": it.get("image_path"), "desc": it.get("description", "")}
        return idx

    def _collect_refs_by_binding(self, sc: dict, lib: dict) -> list:
        """按分镜绑定的角色/场景/道具名收集参考图 image_path（去重，过滤未生成）。"""
        idx = self._asset_lookup(lib)
        names = list(sc.get("characters", [])) + [sc.get("scene", "")] + list(sc.get("props", []))
        refs, seen = [], set()
        for name in names:
            info = idx.get(name)
            if info and info["path"] and info["path"] not in seen:
                refs.append(info["path"])
                seen.add(info["path"])
        return refs

    def _build_panel_for_prompt(self, sc: dict, lib: dict) -> dict:
        """构建单镜 panel dict（含绑定资产视觉描述）供图片 prompt 生成。"""
        idx = self._asset_lookup(lib)
        asset_desc = {}
        for name in list(sc.get("characters", [])) + [sc.get("scene", "")] + list(sc.get("props", [])):
            if name and name in idx:
                asset_desc[name] = idx[name]["desc"]
        return {
            "narration": sc.get("narration", ""),
            "composition": sc.get("composition", ""),
            "shot_type": sc.get("shot_type", ""),
            "camera_move": sc.get("camera_move", ""),
            "lighting": sc.get("lighting", ""),
            "characters": sc.get("characters", []),
            "scene": sc.get("scene", ""),
            "props": sc.get("props", []),
            "asset_descriptions": asset_desc,
        }

    def _gen_panel_image(self, pixelle_video, sc: dict, lib: dict, prompt_prefix: str):
        """用已生成的 prompt + 绑定资产参考图调 provider 生图，写回 sc['image_path']。"""
        try:
            import tempfile, httpx
            from pixelle_video.utils.prompt_helper import build_image_prompt
            prompt = build_image_prompt(sc.get("image_prompt", ""), prompt_prefix)
            refs = self._collect_refs_by_binding(sc, lib)
            provider = _ss("story_provider")
            media_result = run_async(pixelle_video.media(
                prompt=prompt, workflow=provider, media_type="image",
                width=1024, height=1024,
                image_paths=refs if refs else None,
            ))
            if not media_result.is_image:
                sc["image_path"] = None
                sc["_error"] = "provider 未返回图片"
                return
            url = media_result.url
            if url.startswith("file://") or os.path.exists(url):
                sc["image_path"] = url[7:] if url.startswith("file://") else url
                sc["_error"] = None
                return
            import re
            _slug = re.sub(r"[^\w]+", "_", sc.get("scene", "") or "panel").strip("_")[:30] or "panel"
            out = os.path.join(tempfile.gettempdir(), f"story_panel_{_slug}_{abs(hash(sc.get('narration','')))%99999}.png")
            async def _dl():
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    with open(out, "wb") as f:
                        f.write(r.content)
            run_async(_dl())
            sc["image_path"] = out
            sc["_error"] = None
        except Exception as e:
            logger.warning(f"panel image gen failed: {e}")
            sc["image_path"] = None
            sc["_error"] = str(e)

    def _queue_panel(self, target):
        """待办分镜生图存入 session_state + set busy + rerun。target: idx / 'all' / 'regen_prompts'。"""
        st.session_state["_pending_panel"] = target
        st.session_state["story_generating"] = True
        st.rerun()

    def _stage_storyboard(self, pixelle_video, project):
        st.markdown(f"**{_tr('section.scenecut', '分镜预览')}**")
        story = _ss("story_text", "")
        n_scenes = _ss("story_n_scenes")
        lib = _ss(_ASSET_LIB, {})

        def _run_scenecut():
            try:
                resp = run_async(pixelle_video.llm(
                    build_story_scenecut_prompt(story, n_scenes, assets_to_text(lib)),
                    temperature=0.8, max_tokens=None,
                ))
                if not resp or not resp.strip():
                    return False, "LLM 返回空响应（检查 LLM 配置/额度/网络，或缩短故事与资产库）"
                scenes = _parse_json(resp).get("scenes", [])
                if not scenes:
                    return False, "分镜返回为空"
                st.session_state["story_scenes"] = scenes
                return True, None
            except Exception as e:
                logger.exception(e)
                return False, str(e)

        def _gen_all_panel_prompts():
            """批量生成所有分镜的图片 prompt（一次 LLM 调用，保证衔接一致）。"""
            scenes = st.session_state.get("story_scenes", [])
            panels = [self._build_panel_for_prompt(s, lib) for s in scenes]
            resp = run_async(pixelle_video.llm(
                build_story_panel_image_batch_prompt(panels),
                temperature=0.7, max_tokens=None,
            ))
            if not resp or not resp.strip():
                logger.warning("panel prompt LLM returned empty, leaving prompts blank")
                return
            prompts = _parse_json(resp).get("image_prompts", [])
            if len(prompts) < len(scenes):
                prompts += [""] * (len(scenes) - len(prompts))
            prompts = prompts[:len(scenes)]
            for s, p in zip(scenes, prompts):
                s["image_prompt"] = p

        # 首次进入：分镜 + 批量生成图片 prompt
        if "story_scenes" not in st.session_state or not st.session_state.get("story_scenes"):
            st.session_state["story_generating"] = True
            with st.spinner("AI 正在分镜 + 生成图片提示词…"):
                ok, err = _run_scenecut()
                if ok:
                    try:
                        _gen_all_panel_prompts()
                    except Exception as e:
                        logger.warning(f"batch panel prompt failed: {e}")
            st.session_state["story_generating"] = False
            if ok:
                # 落盘分镜（含图片 prompt），退出重进不丢
                ProjectService.save_scenes(project.project_id, st.session_state["story_scenes"])
                st.rerun()
            else:
                st.error(f"分镜失败：{err}")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("🔄 重试分镜", type="primary", disabled=_busy()):
                        st.rerun()
                with col2:
                    if st.button("⬅️ 返回资产库", disabled=_busy()):
                        st.session_state[_STAGE] = "assets"
                        st.rerun()
                return

        # 两阶段：busy 时执行待办（单镜生图 / 重新生成提示词 / 全部生图）
        # 注意 _pending_panel=0（第一镜）也是有效值，用 is not None 判断
        if _busy() and _ss("_pending_panel") is not None:
            pending = _ss("_pending_panel")
            scenes = st.session_state["story_scenes"]
            prompt_prefix = _ss("story_art_style", "")
            # 先把 widget 编辑值同步回 scenes（用户编辑了旁白/摄影后再触发）
            for i, sc in enumerate(scenes):
                sc["narration"] = st.session_state.get(f"scene_narr_{i}", sc.get("narration", ""))
                sc["shot_type"] = st.session_state.get(f"scene_shot_{i}", sc.get("shot_type", ""))
                sc["camera_move"] = st.session_state.get(f"scene_cam_{i}", sc.get("camera_move", ""))
                sc["lighting"] = st.session_state.get(f"scene_light_{i}", sc.get("lighting", ""))
            if pending == "regen_prompts":
                with st.spinner("重新生成所有图片提示词…"):
                    _gen_all_panel_prompts()
            elif pending == "all":
                for i, sc in enumerate(scenes):
                    if not sc.get("image_path"):
                        with st.spinner(f"生成分镜图… ({i+1}/{len(scenes)})"):
                            self._gen_panel_image(pixelle_video, sc, lib, prompt_prefix)
            else:
                idx = pending
                if idx < len(scenes):
                    with st.spinner(f"生成分镜 {idx+1} 图像…"):
                        self._gen_panel_image(pixelle_video, scenes[idx], lib, prompt_prefix)
            ProjectService.save_scenes(project.project_id, scenes)
            st.session_state["story_generating"] = False
            st.session_state["_pending_panel"] = None
            st.rerun()
            return

        scenes = st.session_state["story_scenes"]
        shot_opts = ["", "特写", "近景", "中景", "全景", "远景"]
        camera_opts = ["", "固定", "推", "拉", "横移", "跟随", "仰俯"]
        light_opts = ["", "顺光", "逆光", "侧光", "顶光", "暖光", "冷光"]

        edited = []
        for i, sc in enumerate(scenes):
            with st.container(border=True):
                st.markdown(f"**镜头 {i+1}**")
                left, mid, right = st.columns([1.2, 1.5, 1])
                with left:
                    n = st.text_area("旁白（原文逐字，可编辑）", value=sc.get("narration", ""), height=80, key=f"scene_narr_{i}")
                    sc_shot = st.selectbox("景别", shot_opts,
                        index=shot_opts.index(sc.get("shot_type", "")) if sc.get("shot_type", "") in shot_opts else 0,
                        key=f"scene_shot_{i}")
                    sc_cam = st.selectbox("运镜", camera_opts,
                        index=camera_opts.index(sc.get("camera_move", "")) if sc.get("camera_move", "") in camera_opts else 0,
                        key=f"scene_cam_{i}")
                    sc_light = st.selectbox("光照", light_opts,
                        index=light_opts.index(sc.get("lighting", "")) if sc.get("lighting", "") in light_opts else 0,
                        key=f"scene_light_{i}")
                with mid:
                    st.caption("🖼️ 图片提示词（LLM 生成）")
                    st.text_area("prompt", value=sc.get("image_prompt", ""), height=160,
                                 key=f"scene_iprompt_{i}", label_visibility="collapsed")
                    binding = []
                    for name in sc.get("characters", []):
                        binding.append(f"🧑 {name}")
                    if sc.get("scene"):
                        binding.append(f"🏞️ {sc['scene']}")
                    for name in sc.get("props", []):
                        binding.append(f"🎒 {name}")
                    st.caption("绑定资产：" + ("  ".join(binding) if binding else "无"))
                with right:
                    if sc.get("image_path") and os.path.exists(sc["image_path"]):
                        st.image(sc["image_path"], width="stretch")
                        if st.button("重生", key=f"scene_regen_{i}", width="stretch", disabled=_busy()):
                            self._queue_panel(i)
                    else:
                        st.write("—")
                        if st.button("🎨 生成图像", key=f"scene_gen_{i}", type="primary", width="stretch", disabled=_busy()):
                            self._queue_panel(i)
                    if sc.get("_error"):
                        st.error(f"❌ {sc['_error']}")
                edited.append({
                    "narration": n,
                    "shot_type": sc_shot, "camera_move": sc_cam, "lighting": sc_light,
                    "characters": sc.get("characters", []), "scene": sc.get("scene", ""),
                    "props": sc.get("props", []), "source_text": sc.get("source_text", ""),
                    "image_prompt": sc.get("image_prompt", ""), "image_path": sc.get("image_path"),
                })

        st.markdown("---")
        col1, col2, col3, col4 = st.columns([1, 1, 1, 1.5])
        with col1:
            if st.button("⬅️ 资产库", width="stretch", disabled=_busy()):
                st.session_state[_STAGE] = "assets"
                st.rerun()
        with col2:
            if st.button("🔄 重新分镜", width="stretch", help="丢弃当前分镜，重新调用 AI", disabled=_busy()):
                st.session_state.pop("story_scenes", None)
                st.rerun()
        with col3:
            if st.button("🔄 重新生成提示词", width="stretch", help="编辑分镜后重新批量生成图片 prompt", disabled=_busy()):
                self._queue_panel("regen_prompts")
        with col4:
            if st.button("🎨 生成全部图像", type="primary", width="stretch", disabled=_busy()):
                self._queue_panel("all")
            if st.button("✅ 确认分镜，去生成视频", type="primary", width="stretch", disabled=_busy()):
                st.session_state["story_final_narrations"] = [s["narration"].strip() for s in edited]
                st.session_state["story_final_compositions"] = [s["composition"].strip() for s in edited]
                st.session_state["story_final_shots"] = [s["shot_type"] for s in edited]
                st.session_state["story_final_cameras"] = [s["camera_move"] for s in edited]
                st.session_state["story_final_lightings"] = [s["lighting"] for s in edited]
                st.session_state["story_final_image_paths"] = [s.get("image_path") for s in edited]
                ProjectService.save_scenes(project.project_id, edited)
                self._persist_project_meta(project)
                st.session_state[_STAGE] = "video"
                st.rerun()

    # ==================== 阶段 4：生成 ====================

    def _stage_video(self, pixelle_video, project):
        st.markdown(f"**{_tr('section.generate', '生成视频')}**")
        style_params = _ss(_STYLE_PARAMS, {}) or {}
        lib = _ss(_ASSET_LIB, {})
        narrations = _ss("story_final_narrations", [])

        col1, col2 = st.columns([1, 2])
        with col1:
            if st.button("⬅️ 分镜", width="stretch", key="story_video_back"):
                st.session_state[_STAGE] = "storyboard"
                st.rerun()
        with col2:
            st.caption(
                f"故事 {len(narrations)} 个分镜 · "
                f"资产库 {sum(len(lib.get(k, [])) for k in ('characters', 'scenes', 'props'))} 项 · "
                f"画风 {_ss('story_art_style_key', '-')}"
            )

        # 历次 run
        runs = ProjectService.list_runs(project.project_id)
        if runs:
            with st.expander(f"📚 历史版本（{len(runs)}）", expanded=False):
                for r in runs:
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        st.caption(f"{r['run_id']} · {os.path.basename(r['video_path'])}")
                    with col_b:
                        st.video(r["video_path"])

        with st.container(border=True):
            if not config_manager.validate():
                st.warning(_tr("settings.not_configured", "系统未配置，请先在设置中完成 LLM/Provider 配置"))

            # 两阶段生成：点按钮 → set generating + 存 params + rerun → rerun 时执行生成。
            # 生成期间所有按钮 disabled，阻塞期间缓冲的重复点击在结束 rerun 时被丢弃。
            if _busy() and _ss("_pending_video_params"):
                video_params = _ss("_pending_video_params")
                progress_bar = st.progress(0)
                status_text = st.empty()

                def update_progress(event: ProgressEvent):
                    key = f"progress.{event.event_type}"
                    msg = tr(key) if _has_tr(key) else event.event_type
                    if event.frame_current and event.frame_total:
                        msg = f"{msg} ({event.frame_current}/{event.frame_total})"
                    if event.extra_info:
                        msg = f"{msg} - {event.extra_info}"
                    status_text.text(msg)
                    progress_bar.progress(min(int(event.progress * 100), 99))

                video_params["progress_callback"] = update_progress
                try:
                    result = run_async(pixelle_video.generate_video(**video_params))
                    progress_bar.progress(100)
                    status_text.text(_tr("status.success", "✅ 完成"))
                    st.success(f"✅ 视频已生成：{result.video_path}")
                    if os.path.exists(result.video_path):
                        st.video(result.video_path)
                        with open(result.video_path, "rb") as vf:
                            st.download_button(
                                label="⬇️ 下载视频" if get_language() == "zh_CN" else "⬇️ Download",
                                data=vf.read(),
                                file_name=os.path.basename(result.video_path),
                                mime="video/mp4", width="stretch", key="story_dl_video",
                            )
                except Exception as e:
                    progress_bar.empty()
                    status_text.empty()
                    st.error(f"生成失败：{e}")
                    logger.exception(e)
                finally:
                    st.session_state["story_generating"] = False
                    st.session_state["_pending_video_params"] = None
                    st.rerun()  # 刷新历史版本列表
            elif st.button("🎬 生成视频", type="primary", width="stretch", key="story_btn_generate"):
                if not narrations:
                    st.error("没有分镜旁白，请返回分镜阶段确认")
                    st.stop()
                st.session_state["_pending_video_params"] = {
                    "pipeline": self.name,
                    "project_id": project.project_id,
                    "text": _ss("story_text", ""),
                    "mode": "generate",
                    "title": _ss("story_title"),
                    "n_scenes": _ss("story_n_scenes") or len(narrations),
                    "narrations": narrations,
                    "scene_compositions": _ss("story_final_compositions") or [],
                    "scene_shots": _ss("story_final_shots") or [],
                    "scene_cameras": _ss("story_final_cameras") or [],
                    "scene_lightings": _ss("story_final_lightings") or [],
                    "scene_image_paths": _ss("story_final_image_paths") or [],
                    "asset_library": lib,
                    "asset_provider": _ss("story_provider"),
                    "prompt_prefix": _ss("story_art_style", ""),
                    "frame_template": _ss("story_frame_template") or "1080x1920/story_classic.html",
                    "media_workflow": _ss("story_provider"),
                    "template_params": {"brand": _ss("story_brand") or "Pixelle-Video"},
                    **style_params,
                }
                st.session_state["story_generating"] = True
                st.rerun()


# Register self
register_pipeline_ui(StoryIllustrationPipelineUI)
