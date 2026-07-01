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
from pixelle_video.utils.content_generators import _parse_json
from pixelle_video.utils.prompt_helper import build_image_prompt
from pixelle_video.prompts.story_prompts import (
    build_story_extraction_prompt,
    build_story_character_prompt,
    build_story_location_prompt,
    build_story_prop_prompt,
    build_story_scenecut_prompt,
    build_character_sheet_prompt,
    build_story_panel_image_batch_prompt,
    assets_to_text,
)
from pixelle_video.utils.os_util import get_task_frame_path, create_task_id, get_output_path
from pixelle_video.models.progress import ProgressEvent


class StoryIllustrationPipeline(StandardPipeline):
    """绘本故事插图视频管线。"""

    # ==================== Step 1: 落盘到项目目录（若有 project_id）====================

    async def setup_environment(self, ctx: PipelineContext):
        """有 project_id 时把任务产物落到 projects/{id}/runs/{task_id}/，否则退回基类扁平结构。"""
        project_id = ctx.params.get("project_id")
        if not project_id:
            return await super().setup_environment(ctx)

        # Resume: 项目 run 的 storyboard 在 projects/{pid}/runs/{task_id}/，
        # 基类 _resume_from_checkpoint 用 get_output_path(task_id) 会找扁平目录，找不到。
        # 这里手动从 run_dir 加载 storyboard 并设 ctx，复用基类反序列化 + drift guard 语义。
        resume_task_id = ctx.params.get("resume_task_id")
        if resume_task_id:
            from pixelle_video.services.project_service import ProjectService
            run_dir = ProjectService.run_dir(project_id, resume_task_id)
            sb_path = run_dir / "storyboard.json"
            if not sb_path.exists():
                raise FileNotFoundError(
                    f"Cannot resume project run {resume_task_id}: no storyboard.json in {run_dir}"
                )
            import json as _json
            persistence = self.core.persistence
            storyboard = persistence._dict_to_storyboard(_json.loads(sb_path.read_text(encoding="utf-8")))
            if not storyboard.frames:
                raise FileNotFoundError(f"Cannot resume: storyboard for {resume_task_id} is empty")

            ctx.task_id = resume_task_id
            ctx.task_dir = str(run_dir)
            ctx.final_video_path = str(run_dir / "final.mp4")
            ctx.storyboard = storyboard
            ctx.config = storyboard.config
            ctx.title = storyboard.title
            ctx.narrations = [f.narration for f in storyboard.frames]
            ctx.image_prompts = [f.image_prompt for f in storyboard.frames]
            ctx.params["_project_id"] = project_id
            done = sum(1 for f in storyboard.frames if f.video_segment_path)
            logger.info(f"▶️ Resuming project run {resume_task_id}: {done}/{len(storyboard.frames)} frames done")
            return

        from pixelle_video.services.project_service import ProjectService
        task_id = create_task_id()
        run_dir = ProjectService.run_dir(project_id, task_id)
        (run_dir / "frames").mkdir(parents=True, exist_ok=True)

        ctx.task_id = task_id
        ctx.task_dir = str(run_dir)
        ctx.final_video_path = str(run_dir / "final.mp4")
        ctx.params["_project_id"] = project_id  # 标记，finalize 时回写 project.json
        logger.info(f"📁 Story project run dir: {run_dir}")

    # ==================== Step 8: 回写 project.json ====================

    async def finalize(self, ctx: PipelineContext) -> "VideoGenerationResult":
        result = await super().finalize(ctx)
        project_id = ctx.params.get("_project_id")
        if project_id and ctx.task_id:
            from pixelle_video.services.project_service import ProjectService
            try:
                cover = None
                if ctx.storyboard and ctx.storyboard.frames:
                    # 首帧合成图作封面
                    cover = ctx.storyboard.frames[0].composed_image_path
                ProjectService.record_run(project_id, ctx.task_id, cover_path=cover)
                logger.info(f"📝 Recorded run {ctx.task_id} to project {project_id}")
            except Exception as e:
                logger.warning(f"record_run failed (non-fatal): {e}")
        return result

    # ==================== Step 2: 分镜 ====================

    async def generate_content(self, ctx: PipelineContext):
        """故事 → 分镜（narration + composition + shot_type/camera_move/lighting）。"""
        # UI 预确认的旁白直接用（向导 Step 3 确认后传入）
        predefined = ctx.params.get("narrations")
        if predefined and isinstance(predefined, list) and predefined:
            ctx.narrations = predefined
            n = len(predefined)
            # 同步取 UI 编辑后的构图说明，供 plan_visuals 选资产参考图；
            # 缺失则补空串（保持索引对齐，避免 _select_refs 错位）
            comps = ctx.params.get("scene_compositions") or []
            ctx.params["_scene_compositions"] = (list(comps) + [""] * (n - len(comps)))[:n]
            # 摄影字段同样对齐（UI 可编辑过）
            ctx.params["_scene_shots"] = self._align_scene_field(ctx.params.get("scene_shots"), n)
            ctx.params["_scene_cameras"] = self._align_scene_field(ctx.params.get("scene_cameras"), n)
            ctx.params["_scene_lightings"] = self._align_scene_field(ctx.params.get("scene_lightings"), n)
            # 绑定资产 + 分镜已生图（UI 分镜阶段产出）
            ctx.params["_scene_characters"] = self._align_scene_lists(ctx.params.get("scene_characters"), n)
            ctx.params["_scene_scenes"] = self._align_scene_field(ctx.params.get("scene_scenes"), n)
            ctx.params["_scene_props"] = self._align_scene_lists(ctx.params.get("scene_props"), n)
            ctx.params["_scene_image_paths"] = self._align_scene_field(ctx.params.get("scene_image_paths"), n)
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
            min_words=ctx.params.get("min_narration_words", 10),
            max_words=ctx.params.get("max_narration_words", 200),
        )
        response = await self.llm(prompt, temperature=0.8, max_tokens=None)
        result = _parse_json(response)
        scenes = result.get("scenes", [])
        if not scenes:
            raise ValueError("story scenecut returned no scenes")

        # 旁白 + 构图 + 摄影字段 + 绑定资产都存到 ctx
        ctx.narrations = [s.get("narration", "").strip() for s in scenes]
        ctx.params["_scene_compositions"] = [s.get("composition", "") for s in scenes]
        ctx.params["_scene_shots"] = [s.get("shot_type", "") for s in scenes]
        ctx.params["_scene_cameras"] = [s.get("camera_move", "") for s in scenes]
        ctx.params["_scene_lightings"] = [s.get("lighting", "") for s in scenes]
        # 绑定资产（LLM 推理匹配，参考 waoowaoo agent_storyboard_plan）
        ctx.params["_scene_characters"] = [s.get("characters", []) for s in scenes]
        ctx.params["_scene_scenes"] = [s.get("scene", "") for s in scenes]
        ctx.params["_scene_props"] = [s.get("props", []) for s in scenes]
        # n_scenes 指定时截断；None（AI 自动决定）则照单全收
        if n_scenes and len(ctx.narrations) > n_scenes:
            for k in ("_scene_compositions", "_scene_shots", "_scene_cameras", "_scene_lightings",
                      "_scene_characters", "_scene_scenes", "_scene_props"):
                ctx.params[k] = ctx.params[k][:n_scenes]
            ctx.narrations = ctx.narrations[:n_scenes]
        ctx.params["_scene_image_paths"] = [""] * len(ctx.narrations)  # LLM 路径无预生图
        logger.info(f"✅ Story split into {len(ctx.narrations)} scenes")

    @staticmethod
    def _align_scene_field(values, n: int) -> List[str]:
        """把 UI 传入的字段列表对齐到 n 长，缺失补空串。"""
        if not values:
            return [""] * n
        return (list(values) + [""] * (n - len(values)))[:n]

    @staticmethod
    def _align_scene_lists(values, n: int) -> List[List[str]]:
        """把 UI 传入的列表字段（characters/props）对齐到 n 长，缺失补空列表。"""
        if not values:
            return [[]] * n
        return (list(values) + [[]] * (n - len(values)))[:n]

    # ==================== Step 4: 插图 prompt + 资产参考图 ====================

    def _collect_refs_by_binding(self, characters: list, scene: str, props: list, lib: dict) -> List[str]:
        """按分镜绑定的角色/场景/道具名收集参考图 image_path（去重，过滤未生成）。"""
        idx = {}
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                if it.get("name"):
                    idx[it["name"]] = it.get("image_path")
        names = list(characters or []) + [scene or ""] + list(props or [])
        refs, seen = [], set()
        for name in names:
            path = idx.get(name)
            if path and path not in seen:
                refs.append(path)
                seen.add(path)
        return refs

    async def plan_visuals(self, ctx: PipelineContext):
        """每场景：插图 prompt + 选定引用的资产图路径（img2img 参考）。"""
        frame_template = ctx.params.get("frame_template") or "1080x1920/story_classic.html"
        template_name = Path(frame_template).name
        from pixelle_video.utils.template_util import get_template_type
        template_type = get_template_type(template_name)

        # 静态模板不需要媒体——退回基类行为
        if template_type not in ("image", "video"):
            ctx.image_prompts = [None] * len(ctx.narrations)
            return

        # 1. 资产库：若 UI 未预生成，在此提取 + 生图
        asset_library = await self._ensure_asset_library(ctx)

        # 2. 批量插图 prompt：一次 LLM 调用处理所有分镜（含摄影规则 + 绑定资产描述 + 参考图一致性约束）。
        self._report_progress(ctx.progress_callback, "generating_image_prompts", 0.15)
        prompt_prefix = ctx.params.get("prompt_prefix", "")

        compositions = ctx.params.get("_scene_compositions", [""] * len(ctx.narrations))
        shots = ctx.params.get("_scene_shots", [""] * len(ctx.narrations))
        cameras = ctx.params.get("_scene_cameras", [""] * len(ctx.narrations))
        lightings = ctx.params.get("_scene_lightings", [""] * len(ctx.narrations))
        char_lists = ctx.params.get("_scene_characters", [[]] * len(ctx.narrations))
        scene_names = ctx.params.get("_scene_scenes", [""] * len(ctx.narrations))
        prop_lists = ctx.params.get("_scene_props", [[]] * len(ctx.narrations))

        # 资产名 → 描述索引，供 panel 注入绑定资产视觉描述
        asset_desc_idx = {}
        for kind in ("characters", "scenes", "props"):
            for it in asset_library.get(kind, []):
                if it.get("name"):
                    asset_desc_idx[it["name"]] = it.get("description", "")

        panels = []
        for i in range(len(ctx.narrations)):
            names = list(char_lists[i] if i < len(char_lists) else []) + \
                    [scene_names[i] if i < len(scene_names) else ""] + \
                    list(prop_lists[i] if i < len(prop_lists) else [])
            ad = {n: asset_desc_idx[n] for n in names if n and n in asset_desc_idx}
            panels.append({
                "narration": ctx.narrations[i],
                "composition": compositions[i] if i < len(compositions) else "",
                "shot_type": shots[i] if i < len(shots) else "",
                "camera_move": cameras[i] if i < len(cameras) else "",
                "lighting": lightings[i] if i < len(lightings) else "",
                "characters": char_lists[i] if i < len(char_lists) else [],
                "scene": scene_names[i] if i < len(scene_names) else "",
                "props": prop_lists[i] if i < len(prop_lists) else [],
                "asset_descriptions": ad,
            })
        batch_prompt = build_story_panel_image_batch_prompt(panels)
        response = await self.llm(batch_prompt, temperature=0.7, max_tokens=None)
        try:
            base_prompts = _parse_json(response).get("image_prompts", [])
        except Exception:
            base_prompts = [response.strip()]
        if len(base_prompts) < len(ctx.narrations):
            base_prompts += [""] * (len(ctx.narrations) - len(base_prompts))
        base_prompts = base_prompts[:len(ctx.narrations)]
        ctx.image_prompts = [build_image_prompt(p, prompt_prefix) for p in base_prompts]
        if ctx.progress_callback:
            ctx.progress_callback(ProgressEvent(
                event_type="generating_image_prompts",
                progress=0.30,
                extra_info=f"{len(ctx.narrations)} 个分镜 prompt 已生成",
            ))

        # 3. 每场景参考图：优先用绑定资产名收集，缺失退回 _select_refs 文本匹配
        ctx.params["_scene_refs"] = [
            self._collect_refs_by_binding(char_lists[i] if i < len(char_lists) else [],
                                          scene_names[i] if i < len(scene_names) else "",
                                          prop_lists[i] if i < len(prop_lists) else [],
                                          asset_library)
            or self._select_refs(compositions[i] if i < len(compositions) else "",
                                  ctx.narrations[i] if i < len(ctx.narrations) else "", asset_library)
            for i in range(len(ctx.narrations))
        ]
        logger.info(f"✅ {len(ctx.image_prompts)} illustration prompts + reference images ready")

    # ==================== Step 5: 把参考图挂到 frame ====================

    async def initialize_storyboard(self, ctx: PipelineContext):
        """复用基类建 storyboard，再把每场景参考图 + 摄影字段挂到 frame。"""
        await super().initialize_storyboard(ctx)
        scene_refs = ctx.params.pop("_scene_refs", [])
        shots = ctx.params.pop("_scene_shots", [])
        cameras = ctx.params.pop("_scene_cameras", [])
        lightings = ctx.params.pop("_scene_lightings", [])
        image_paths = ctx.params.get("_scene_image_paths", [])
        for i, frame in enumerate(ctx.storyboard.frames):
            if i < len(scene_refs):
                frame.reference_image_paths = scene_refs[i]
            if i < len(shots):
                frame.shot_type = shots[i] or None
            if i < len(cameras):
                frame.camera_move = cameras[i] or None
            if i < len(lightings):
                frame.lighting = lightings[i] or None
            # 分镜阶段已生图 → 挂到 frame.image_path，frame_processor 检测到则跳过生图
            if i < len(image_paths) and image_paths[i]:
                frame.image_path = image_paths[i]
                frame.media_type = "image"
        # ponytail: 参考图 + 摄影字段已变化，重存一次 checkpoint
        await self._save_checkpoint(ctx, status="running")

    # ==================== 资产库辅助 ====================

    async def _ensure_asset_library(self, ctx: PipelineContext) -> dict:
        """若 UI 已传 asset_library（含 image_path）直接用；否则分类型提取 + 生图。"""
        lib = ctx.params.get("asset_library")
        if lib and self._library_has_images(lib):
            logger.info("✅ Using pre-built asset library from UI")
            return lib

        self._report_progress(ctx.progress_callback, "extracting_assets", 0.08)
        story = ctx.input_text
        # 分类型三次 LLM 调用：角色/场景/道具各专注一类，避免单 prompt 压缩场景/道具
        lib = {"characters": [], "scenes": [], "props": []}
        for kind, builder in (
            ("characters", build_story_character_prompt),
            ("scenes", build_story_location_prompt),
            ("props", build_story_prop_prompt),
        ):
            try:
                resp = await self.llm(builder(story), temperature=0.7, max_tokens=None)
                items = _parse_json(resp).get(kind, [])
                for it in items:
                    it["image_path"] = None
                lib[kind] = items
            except Exception as e:
                logger.warning(f"extract {kind} failed (skip): {e}")
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
        """对资产库每项生成参考图，写回 image_path。失败不阻塞（标 None，该资产不入参考图）。

        角色：多视角参考图（正面大头特写 + 正面全身 + 侧身全身 + 背面全身，单图四宫格），
              便于 img2img 时角色跨分镜外观一致。
        场景/道具：1:1 参考图。
        """
        provider = ctx.params.get("asset_provider") or ctx.params.get("media_workflow")
        prompt_prefix = ctx.params.get("prompt_prefix", "")
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                if it.get("image_path"):
                    continue
                try:
                    base = it.get("description", "")
                    if kind == "characters":
                        # 多视角角色参考图：四视角同框，保证 img2img 一致性
                        base = build_character_sheet_prompt(base)
                    desc = build_image_prompt(base, prompt_prefix)
                    out = get_task_frame_path(ctx.task_id, 0, "image")  # 临时，资产图复用 frame 0 目录
                    # 用 index 区分资产：kind+name 哈希进文件名
                    out = out.replace(f"_frame_0_", f"_asset_{kind}_{abs(hash(it.get('name','')))%99999}_", 1) \
                        if "_frame_0_" in out else out
                    # 资产图尺寸固定，不受帧模板尺寸影响：场景/道具 1:1，角色多视角 1:1 稍大保细节
                    asset_w = asset_h = 1280 if kind == "characters" else 1024
                    media_result = await self.core.media(
                        prompt=desc,
                        workflow=provider,
                        media_type="image",
                        width=asset_w,
                        height=asset_h,
                        output_path=out,
                    )
                    if media_result.is_image:
                        # media() 落盘到 out (api_media os.replace)，url 即本地路径；
                        # 不调 _download_media —— 它按 frame_index=0 生成路径会把所有资产图覆盖到 01_image.png
                        it["image_path"] = media_result.url
                        logger.info(f"  🖼️ asset {kind}/{it.get('name')} generated")
                except Exception as e:
                    logger.warning(f"  ⚠️ asset {kind}/{it.get('name')} gen failed (skip): {e}")
                    it["image_path"] = None

    def _select_refs(self, composition: str, narration: str, lib: dict) -> List[str]:
        """根据构图 + 旁白里提到的资产名，选出对应的 image_path（去重，过滤未生成的）。

        长名优先匹配，匹配后从文本中剔除，避免短名（如"白白"）误匹配到长名片段。
        composition 用代词/别名匹配不上时，narration 提到角色名的概率更高，作补充匹配。
        两者都匹配不上 → 回退全部角色资产图（角色一致性是首要目标，宁可多给参考图）。
        """
        if not lib:
            return []
        # 收集所有 (name, path)，按 name 长度降序
        items = []
        for kind in ("characters", "scenes", "props"):
            for it in lib.get(kind, []):
                name = it.get("name", "")
                path = it.get("image_path")
                if name and path:
                    items.append((name, path))
        items.sort(key=lambda x: len(x[0]), reverse=True)

        text = f"{composition}\n{narration}".lower()
        refs = []
        seen = set()
        for name, path in items:
            name_l = name.lower()
            if name_l in text and path not in seen:
                refs.append(path)
                seen.add(path)
                text = text.replace(name_l, "")  # 剔除已匹配，防短名误命中

        # ponytail: 回退——LLM 用代词/别名时匹配全空，此时给全部角色图保一致性，
        # 不给场景/道具（无关角色入参考图反而干扰）。上限 4 张防 provider 超限。
        if not refs:
            char_paths = [it["image_path"] for it in lib.get("characters", []) if it.get("image_path")]
            refs = char_paths[:4]
        return refs


if __name__ == "__main__":
    # Self-check: _select_refs 匹配 + 回退逻辑
    p = StoryIllustrationPipeline.__new__(StoryIllustrationPipeline)
    lib = {
        "characters": [
            {"name": "白白", "image_path": "/a.png"},
            {"name": "会唱歌的小树", "image_path": "/b.png"},
        ],
        "scenes": [{"name": "森林", "image_path": "/s.png"}],
        "props": [{"name": "发光种子", "image_path": "/p.png"}],
    }
    # composition 直接提到资产名
    r = p._select_refs("白白在森林里捡到种子", "", lib)
    assert "/a.png" in r and "/s.png" in r, f"direct match failed: {r}"
    # composition 用代词，narration 提到角色名
    r2 = p._select_refs("她看着那棵树", "白白每天都来浇水", lib)
    assert "/a.png" in r2, f"narration fallback failed: {r2}"
    # 全空匹配 → 回退全部角色图
    r3 = p._select_refs("一片祥和", "远处传来歌声", lib)
    assert r3 == ["/a.png", "/b.png"], f"char fallback failed: {r3}"
    # 长名优先：会唱歌的小树 不被 白白 抢占
    r4 = p._select_refs("会唱歌的小树长大了", "", lib)
    assert "/b.png" in r4 and "/a.png" not in r4, f"long-name priority failed: {r4}"
    print("story_illustration _select_refs self-check OK")
