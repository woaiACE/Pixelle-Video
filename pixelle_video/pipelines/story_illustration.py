# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Story Illustration Pipeline

绘本故事插图视频：故事文本 → 资产库(角色/场景/道具) → LLM 分镜 → 一致性插图(img2img) → TTS → 拼接。

继承 StandardPipeline，复用其 setup/resume/checkpoint/produce_assets/post_production/finalize 全部逻辑，
只覆写两步：
- generate_content: 故事 → 分镜（narration + composition），而非 topic 展开或正则切分。
- plan_visuals: 每场景插图 prompt + 选定该场景引用的资产图（写入 frame.reference_image_paths，
  FrameProcessor 透传 image_paths 给 img2img）。

资产库的提取与生图在 plan_visuals 内完成（若 UI 未预生成）。img2img 适配层已就绪，不改 provider。
"""

from pathlib import Path
from typing import List, Optional

from loguru import logger

from pixelle_video.pipelines.standard import StandardPipeline
from pixelle_video.pipelines.linear import PipelineContext
from pixelle_video.utils.content_generators import generate_image_prompts, _parse_json
from pixelle_video.utils.prompt_helper import build_image_prompt
from pixelle_video.prompts.story_prompts import (
    build_story_extraction_prompt,
    build_story_scenecut_prompt,
    assets_to_text,
)
from pixelle_video.utils.os_util import get_task_frame_path


class StoryIllustrationPipeline(StandardPipeline):
    """绘本故事插图视频管线。"""

    # ==================== Step 2: 分镜 ====================

    async def generate_content(self, ctx: PipelineContext):
        """故事 → 分镜（narration + composition）。"""
        # UI 预确认的旁白直接用（向导 Step 3 确认后传入）
        predefined = ctx.params.get("narrations")
        if predefined and isinstance(predefined, list) and predefined:
            ctx.narrations = predefined
            logger.info(f"✅ Using {len(ctx.narrations)} pre-defined narrations")
            return

        self._report_progress(ctx.progress_callback, "story_scenecut", 0.05)
        story = ctx.input_text
        n_scenes = ctx.params.get("n_scenes", 6)
        asset_library = ctx.params.get("asset_library") or {}
        assets_text = assets_to_text(asset_library)

        prompt = build_story_scenecut_prompt(
            story=story,
            n_scenes=n_scenes,
            assets=assets_text,
            min_words=ctx.params.get("min_narration_words", 5),
            max_words=ctx.params.get("max_narration_words", 30),
        )
        response = await self.llm(prompt, temperature=0.8, max_tokens=4000)
        result = _parse_json(response)
        scenes = result.get("scenes", [])
        if not scenes:
            raise ValueError("story scenecut returned no scenes")

        # 旁白 + 构图说明都存到 ctx，构图说明在 plan_visuals 用于选资产
        ctx.narrations = [s.get("narration", "").strip() for s in scenes]
        ctx.params["_scene_compositions"] = [s.get("composition", "") for s in scenes]
        if len(ctx.narrations) > n_scenes:
            ctx.narrations = ctx.narrations[:n_scenes]
            ctx.params["_scene_compositions"] = ctx.params["_scene_compositions"][:n_scenes]
        logger.info(f"✅ Story split into {len(ctx.narrations)} scenes")

    # ==================== Step 4: 插图 prompt + 资产参考图 ====================

    async def plan_visuals(self, ctx: PipelineContext):
        """每场景：插图 prompt + 选定引用的资产图路径（img2img 参考）。"""
        frame_template = ctx.params.get("frame_template") or "1080x1920/default.html"
        template_name = Path(frame_template).name
        from pixelle_video.utils.template_util import get_template_type
        template_type = get_template_type(template_name)

        # 静态模板不需要媒体——退回基类行为
        if template_type not in ("image", "video"):
            ctx.image_prompts = [None] * len(ctx.narrations)
            return

        # 1. 资产库：若 UI 未预生成，在此提取 + 生图
        asset_library = await self._ensure_asset_library(ctx)

        # 2. 每场景插图 prompt（复用 image_generation，保证画风一致）
        self._report_progress(ctx.progress_callback, "generating_image_prompts", 0.15)
        prompt_prefix = ctx.params.get("prompt_prefix", "")
        from pixelle_video.prompts.image_generation import get_style_hint_by_prefix
        style_hint = get_style_hint_by_prefix(prompt_prefix)

        base_prompts = await generate_image_prompts(
            self.llm,
            narrations=ctx.narrations,
            min_words=ctx.params.get("min_image_prompt_words", 30),
            max_words=ctx.params.get("max_image_prompt_words", 60),
            style_hint=style_hint,
        )
        ctx.image_prompts = [build_image_prompt(p, prompt_prefix) for p in base_prompts]

        # 3. 每场景选定引用的资产图（composition 提到的角色/场景/道具 → 对应 image_path）
        compositions = ctx.params.pop("_scene_compositions", [""] * len(ctx.narrations))
        ctx.params["_scene_refs"] = [
            self._select_refs(comp, asset_library) for comp in compositions
        ]
        logger.info(f"✅ {len(ctx.image_prompts)} illustration prompts + reference images ready")

    # ==================== Step 5: 把参考图挂到 frame ====================

    async def initialize_storyboard(self, ctx: PipelineContext):
        """复用基类建 storyboard，再把每场景参考图挂到 frame.reference_image_paths。"""
        await super().initialize_storyboard(ctx)
        scene_refs = ctx.params.pop("_scene_refs", [])
        for i, frame in enumerate(ctx.storyboard.frames):
            if i < len(scene_refs):
                frame.reference_image_paths = scene_refs[i]
        # ponytail: 参考图路径已变化，重存一次 checkpoint
        await self._save_checkpoint(ctx, status="running")

    # ==================== 资产库辅助 ====================

    async def _ensure_asset_library(self, ctx: PipelineContext) -> dict:
        """若 UI 已传 asset_library（含 image_path）直接用；否则提取描述 + 生图。"""
        lib = ctx.params.get("asset_library")
        if lib and self._library_has_images(lib):
            logger.info("✅ Using pre-built asset library from UI")
            return lib

        self._report_progress(ctx.progress_callback, "extracting_assets", 0.08)
        story = ctx.input_text
        response = await self.llm(
            build_story_extraction_prompt(story), temperature=0.7, max_tokens=3000
        )
        lib = _parse_json(response)
        # 生图
        await self._generate_asset_images(ctx, lib)
        ctx.params["asset_library"] = lib
        return lib

    def _library_has_images(self, lib: dict) -> bool:
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                if it.get("image_path"):
                    return True
        return False

    async def _generate_asset_images(self, ctx: PipelineContext, lib: dict):
        """对资产库每项生成参考图，写回 image_path。失败不阻塞（标 None，该资产不入参考图）。"""
        provider = ctx.params.get("asset_provider") or ctx.params.get("media_workflow")
        prompt_prefix = ctx.params.get("prompt_prefix", "")
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                if it.get("image_path"):
                    continue
                try:
                    desc = build_image_prompt(it.get("description", ""), prompt_prefix)
                    out = get_task_frame_path(ctx.task_id, 0, "image")  # 临时，资产图复用 frame 0 目录
                    # 用 index 区分资产：kind+name 哈希进文件名
                    out = out.replace(f"_frame_0_", f"_asset_{kind}_{abs(hash(it.get('name','')))%99999}_", 1) \
                        if "_frame_0_" in out else out
                    media_result = await self.core.media(
                        prompt=desc,
                        workflow=provider,
                        media_type="image",
                        width=ctx.params.get("media_width"),
                        height=ctx.params.get("media_height"),
                        output_path=out,
                    )
                    if media_result.is_image:
                        it["image_path"] = await self.core.frame_processor._download_media(
                            media_result.url, 0, ctx.task_id, "image"
                        )
                        logger.info(f"  🖼️ asset {kind}/{it.get('name')} generated")
                except Exception as e:
                    logger.warning(f"  ⚠️ asset {kind}/{it.get('name')} gen failed (skip): {e}")
                    it["image_path"] = None

    def _select_refs(self, composition: str, lib: dict) -> List[str]:
        """根据构图说明里提到的资产名，选出对应的 image_path（去重，过滤未生成的）。

        长名优先匹配，匹配后从文本中剔除，避免短名（如"白白"）误匹配到长名片段。
        """
        if not composition or not lib:
            return []
        comp = composition.lower()
        # 收集所有 (name, path)，按 name 长度降序
        items = []
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                name = it.get("name", "")
                path = it.get("image_path")
                if name and path:
                    items.append((name, path))
        items.sort(key=lambda x: len(x[0]), reverse=True)

        refs = []
        seen = set()
        for name, path in items:
            name_l = name.lower()
            if name_l in comp and path not in seen:
                refs.append(path)
                seen.add(path)
                comp = comp.replace(name_l, "")  # 剔除已匹配，防短名误命中
        return refs
