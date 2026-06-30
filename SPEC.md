# SPEC — AI 故事插图视频 (Story Illustration Pipeline)

> 状态：草案，待用户确认后进入实现。
> 日期：2026-06-30
> 参考：复用现有 `LinearVideoPipeline` 框架与 `image_*` 生成栈，新增"资产库"前置阶段。

## 1. 目标 (Objective)

新增一个**绘本故事插图视频**板块，与现有"快速创作"并列。用户输入一段故事文本，系统自动完成：

```
故事文本
  → [步1] AI 提取角色/场景/道具描述
  → [步2] 生成资产库（角色形象图 + 场景图 + 道具图）
  → [步3] LLM 智能分镜（故事 → N 个场景，每场景一段旁白 + 画面构图说明）
  → [步4] 一致性插图生成（每场景插图 prompt → 以资产库作参考图图生图）
  → [步5] 语音合成（复用现有 TTS）
  → [步6] 融合成绘本插图视频（HTML 模板 + ffmpeg 拼接，复用现有管线）
```

**与"快速创作"的核心差异**：快速创作是"旁白 → 每段独立出图"，插图之间无角色连贯性；本管线在分镜前先建立**资产库**，分镜后的每张插图以资产库图片作参考图（img2img），保证同一角色/场景在多个分镜中视觉一致。

**目标用户**：想用一段故事文本快速产出绘本风短视频的创作者（自托管场景）。

**验收标准（Done = 全部满足）**：
- [ ] Web UI 出现新板块"故事插图视频"，分步向导形态。
- [ ] 输入故事文本 → AI 提取并展示角色/场景/道具清单，用户可编辑、可对单项重新生成资产图。
- [ ] 资产库生成完成后，进入分镜预览：展示 N 个场景的旁白，用户可编辑。
- [ ] 确认后生成视频：每场景插图使用资产库参考图，角色在跨场景分镜中保持一致。
- [ ] 最终产出 mp4（HTML 模板渲染 + TTS + ffmpeg 拼接），与快速创作产物格式一致。
- [ ] 进度回调、断点续跑（checkpoint）、任务持久化与现有管线行为一致。

---

## 2. 命令 / 调用契约 (Commands)

### 2.1 后端 pipeline 注册

`pixelle_video/service.py::initialize()` 的 `self.pipelines` 新增：

```python
"story_illustration": StoryIllustrationPipeline(self),
```

并在 `pixelle_video/pipelines/__init__.py` 导出 `StoryIllustrationPipeline`。

### 2.2 pipeline 调用签名

继承 `LinearVideoPipeline.__call__(text, progress_callback=None, **kwargs)`，`kwargs` 增量参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `story_text` | str | 故事原文（也可走 `text` 兼容） |
| `n_scenes` | int | 期望分镜数（默认 6） |
| `art_style` | str | 画风预设 key（复用 `IMAGE_STYLE_PRESETS`），如 `watercolor` |
| `template` | str | 帧模板名，默认 `1080x1920/image_story.html` |
| `asset_provider` | str | 资产图生成 provider，默认 `api/gemini/gemini-3-pro-image` |
| `illustration_provider` | str | 插图生成 provider，默认同 `asset_provider` |
| `tts_*` | - | 复用现有 TTS 参数（`tts_inference_mode`/`voice_id`/`tts_speed`） |
| `bgm_path`/`bgm_volume` | - | 复用现有 BGM 参数 |
| `asset_library` | dict \| None | 预生成的资产库（向导已建好则直接传入，跳过步1-2） |
| `narrations` | list[str] \| None | 预确认的分镜旁白（向导已确认则跳过步3的 LLM 分镜） |

### 2.3 资产库数据结构

```python
@dataclass
class Asset:
    kind: str          # "character" | "scene" | "prop"
    name: str          # 角色名/场景名/道具名
    description: str   # 外貌/场景/道具描述（中文）
    image_path: str    # 生成的资产图本地路径

@dataclass
class AssetLibrary:
    characters: list[Asset]
    scenes: list[Asset]
    props: list[Asset]
```

### 2.4 Web UI 板块

- 新文件 `web/pipelines/story_illustration.py`：`StoryIllustrationPipelineUI(PipelineUI)`，`name="story_illustration"`，`icon="📖"`，`display_name="故事插图视频"`。
- 在 `web/pipelines/__init__.py` 增 `from web.pipelines import story_illustration`。
- 模块底部 `register_pipeline_ui(StoryIllustrationPipelineUI)`。

---

## 3. 项目结构 (Project Structure)

新增文件：

```
pixelle_video/
  pipelines/
    story_illustration.py          # StoryIllustrationPipeline(LinearVideoPipeline)
  prompts/
    story_extraction.py            # 故事→角色/场景/道具提取 prompt（结构化 JSON）
    story_scenecut.py              # 故事→分镜 prompt（结构化 JSON：narration + composition）
    illustration_prompt.py         # 场景+资产→插图 prompt（含参考图引用说明）
  models/
    asset_library.py               # Asset / AssetLibrary dataclass
templates/
  1080x1920/image_story.html       # 绘本风帧模板（大图 + 少字 + 页码占位）
web/
  pipelines/
    story_illustration.py          # 分步向导 UI
  components/
    asset_library_editor.py        # 资产库编辑组件（列表 + 重新生成单项）
```

改动文件：

```
pixelle_video/service.py            # 注册 pipeline
pixelle_video/pipelines/__init__.py # 导出
pixelle_video/prompts/__init__.py   # 导出新 prompt
web/pipelines/__init__.py           # 注册 UI
```

**不改动**：`frame_processor.py`、`frame_html.py`、`video.py`、`tts_service.py`、`image_client.py` 及各 provider —— 全部复用。img2img 已在适配层支持（`image_paths` 参数端到端打通）。

---

## 4. 分步向导 UI 流程 (Wizard Steps)

`web/pipelines/story_illustration.py` 用 `st.session_state` 维护当前步骤，分 4 步：

**Step 1 — 故事输入**
- 文本框（故事原文）+ 期望分镜数 + 画风预设下拉（`IMAGE_STYLE_PRESETS`）+ provider 选择。
- 按钮"提取角色与场景" → 调 LLM（`story_extraction` prompt）→ 解析为 `AssetLibrary`（仅描述，未生成图）→ 进入 Step 2。

**Step 2 — 资产库编辑**
- 三栏列表展示角色 / 场景 / 道具，每项：名称、描述（可编辑文本框）、缩略图（未生成时占位）、"重新生成"按钮。
- 按钮"生成全部资产图" → 对每项 `media(prompt=<描述+画风>, workflow=asset_provider, media_type="image")` → 填充 `image_path`。
- 任意单项可单独重新生成。完成后按钮"进入分镜" → Step 3。

**Step 3 — 分镜预览**
- 调 LLM（`story_scenecut` prompt）→ N 个场景，每场景：旁白 + 画面构图说明。
- 列表展示，旁白可编辑。按钮"确认并生成视频" → Step 4。

**Step 4 — 生成**
- 组装 `video_params`（含 `asset_library`、`narrations`、画风、模板、TTS、BGM）→ `run_async(pixelle_video.generate_video(pipeline="story_illustration", **video_params))`。
- 进度条 + 结果预览（`render_output_preview`），与快速创作一致。

---

## 5. 后端 pipeline 生命周期映射

`StoryIllustrationPipeline(LinearVideoPipeline)` 覆写以下 hook：

| Hook | 行为 |
|---|---|
| `setup_environment` | 复用基类：建 task_dir。 |
| `generate_content` | 若 `kwargs["narrations"]` 已给则直接用；否则调 `story_scenecut` prompt 分镜。 |
| `determine_title` | 复用 `generate_title`。 |
| `plan_visuals` | **新增**：若 `kwargs["asset_library"]` 未给，则在此调 `story_extraction` + 生成资产图（向导已建好则跳过）。然后对每场景用 `illustration_prompt` prompt 生成插图 prompt，并记录该场景引用的资产 `image_path` 列表。 |
| `initialize_storyboard` | 复用：建 `StoryboardFrame`，`image_prompt` = 插图 prompt，额外在 frame 上挂 `reference_image_paths`（资产图）。 |
| `produce_assets` | **覆写**：调 `frame_processor` 时，对 image 生成步骤传入 `image_paths=frame.reference_image_paths`（img2img）。其余 TTS/合成/视频段不变。 |
| `post_production` | 复用：`concat_videos` + BGM。 |
| `finalize` | 复用：`VideoGenerationResult` + 持久化。 |

**关键实现点 — img2img 透传**：
`FrameProcessor._step_generate_media` 调 `self.core.media(prompt=..., workflow=..., media_type="image", ...)`。`MediaService` 经 `**params` 把 `image_paths` 透传到 `ImageClient.generate_image(image_paths=...)`，再路由到各 provider（Gemini `image_urls` / Seedream `extra_body.image` / DashScope `edit_image` / GPT `extra_body.image_url`）。**无需改 provider。**

> 待实现时确认：`FrameProcessor` 当前是否从 `StoryboardFrame`/`config` 读取额外参数透传给 `media()`。若否，最小改动是给 `StoryboardFrame` 加一个 `reference_image_paths` 字段，`FrameProcessor._step_generate_media` 读取后并入 `media()` 调用。这是本特性唯一可能触及核心服务的改动点，实现前先读 `frame_processor.py` 确认透传路径。

---

## 6. Prompt 设计 (Prompts)

三个新 prompt 模块，均返回结构化 JSON：

**`story_extraction.py`** — `build_story_extraction_prompt(story_text) -> str`
- 输出：`{"characters":[{"name","description"}], "scenes":[...], "props":[...]}`
- description 为中文外貌/环境/道具描述，供后续直接作为图片 prompt 基础。

**`story_scenecut.py`** — `build_story_scenecut_prompt(story_text, n_scenes, asset_library) -> str`
- 输出：`{"scenes":[{"narration","composition"}]}`
- narration = 该场景旁白（中文）；composition = 画面构图说明（哪几个角色/场景/道具出场，便于插图 prompt 引用正确资产）。

**`illustration_prompt.py`** — `build_illustration_prompt(scene, asset_library, art_style) -> str`
- 输出：单条英文插图 prompt（与现有 `image_generation` 风格一致，供 img2img）。
- 内嵌画风预设描述 + 该场景出场的角色/场景/道具名称（与参考图对应）。
- 同时返回该场景引用的资产 `image_path` 列表（在 pipeline 层拼装，prompt 函数只产文本）。

画风复用 `pixelle_video/prompts/image_generation.py::IMAGE_STYLE_PRESETS` 与 `get_style_hint_by_prefix`。

---

## 7. 帧模板 (Template)

`templates/1080x1920/image_story.html`：
- 绘本风版式：上方大图区（`{{image}}`）、下方少字旁白区（`{{text}}`）、可选页码（`{{index}}`）、标题（`{{title}}`）。
- 占位符约定与现有一致：`{{title}}` `{{text}}` `{{image}}` `{{index}}`，支持 `{{param=default}}` 自定义字段。
- 文件名前缀 `image_` → 触发图片生成行为（`get_template_type` 返回 `"image"`）。
- 可加 `<meta name="template:media-width" content="1024">` 声明插图区尺寸。

---

## 8. 代码风格 (Code Style)

- 严格遵循现有风格：`loguru` 日志、`async/await`、`dataclass` 模型、`pydantic` schema、ruff（line-length 100，E/F/I）。
- 中文注释/docstring 与现有文件一致（项目大量使用中文注释）。
- pipeline 覆写 hook 时保持与 `standard.py` / `asset_based.py` 相同的方法签名与进度回调用法（`_report_progress`）。
- 不引入新依赖。

---

## 9. 测试策略 (Testing)

遵循 ponytail 自检原则——非平凡逻辑留一个可运行检查，不铺测试框架：

- **`story_extraction` / `story_scenecut` / `illustration_prompt` 三个 prompt 模块**：各留一个 `if __name__ == "__main__"` 自检（用一段固定故事文本，打印结构化输出，断言关键字段存在）。注意：Phase 1 刚删过 provider 的 `__main__` 块，但那是包内 provider 死代码；prompt 模块的自检是开发期验证 JSON 结构的合理保留——若与项目惯例冲突则改为 `tests/test_story_prompts.py` 单文件。
- **img2img 透传**：实现后用 `python -c` 跑一次 `media(prompt=..., workflow="api/gemini/...", media_type="image", image_paths=[<一张资产图>])`，确认返回新图且参考图被使用（人工对比角色一致性）。
- **端到端**：在 web UI 走完 4 步向导，产出 mp4，人工确认跨场景角色一致。
- **回归**：`import pixelle_video, api, web` + FastAPI app 启动 + 快速创作仍可用（未触及其核心路径）。

---

## 10. 边界 (Boundaries)

**始终做 (Always)**：
- 复用 `LinearVideoPipeline` 框架、`FrameProcessor`、`VideoService`、`TTSService`、`ImageClient` 及各 provider，不复制粘贴已有逻辑。
- 进度回调、checkpoint、任务持久化与现有管线一致。
- 画风/模板/provider 在 UI 可选，给合理默认值。

**先问 (Ask first)**：
- 若 `FrameProcessor` 无法干净地透传 `image_paths`（需深改核心服务），停下与用户讨论方案，不擅自重构 `frame_processor.py`。
- 是否需要"角色一致性不强时回退到纯 prompt 描述"的降级路径（取决于 img2img 实测效果）。
- 资产库是否需要跨任务复用/持久化（当前 spec：单任务内，不持久化）。

**绝不做 (Never)**：
- 不新增图片生成 provider 或新依赖（img2img 适配层已就绪）。
- 不改 `image_client.py` / 各 `image_*.py` provider 文件。
- 不为单一实现造抽象工厂（ponytail：无未请求的抽象）。
- 不在未确认透传路径前改动 `frame_processor.py` 核心逻辑。

---

## 11. 实现顺序（建议）

1. `models/asset_library.py` + 三个 prompt 模块（带自检）。
2. `templates/1080x1920/image_story.html`。
3. 后端 `pipelines/story_illustration.py`（先跑通：跳过资产库，纯分镜+插图，验证 img2img 透传）。
4. 确认 `FrameProcessor` 透传路径，接入资产库参考图。
5. `web/components/asset_library_editor.py` + `web/pipelines/story_illustration.py` 向导。
6. 注册 + 端到端验证。

每步可独立验证，第 3 步是技术风险点（img2img 透传），先跑通再做 UI。

---

## 待用户确认

1. 上述分步向导 4 步流程是否符合预期？
2. 资产库范围 = 角色 + 场景 + 道具，是否确认（道具会增加提取/生成成本）？
3. 默认 provider 用 Gemini（img2img 契约最干净），还是沿用用户现有配置？
4. 资产库单任务内、不持久化，确认？
