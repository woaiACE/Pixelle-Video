# Pixelle-Video 系统架构深度文档

> 本文为工程级架构说明，覆盖：系统架构图谱、核心模块职责、关键接口契约、主要数据流、核心调用链、潜在技术债、后续改造工程约束。
> 适用于需要改动核心流程、新增 provider/流水线、或做性能/稳定性改造的开发者。
> 代码引用格式 `[文件](path#L行)`，行号基于撰写时仓库状态。

---

## 1. 系统架构图谱

### 1.1 分层总览

```
┌─────────────────────────────────────────────────────────────────────┐
│  Web 层 (web/) — Streamlit UI                                        │
│  app.py → pages(Home/History/VoiceDesigner)                          │
│  components/(settings, content_input, style_config, output_preview)  │
│  pipelines/(PipelineUI 注册表: quick_create/custom_media/            │
│             digital_human/image_to_video/action_transfer)            │
│  utils/async_helpers.run_async  ← 同步↔异步唯一桥                      │
└───────────────┬─────────────────────────────────────────────────────┘
                │  get_pixelle_video() 缓存实例 (按 comfyui 配置 hash 重建)
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  核心编排层 (pixelle_video/)                                          │
│  service.py → PixelleVideoCore (单例服务容器)                         │
│   ├─ pipelines/ (BasePipeline → LinearVideoPipeline 模板方法)        │
│   │    ├─ StandardPipeline   (主题/固定文案 → 视频)                   │
│   │    ├─ CustomPipeline     (参考实现, 直挂 __call__)                │
│   │    └─ AssetBasedPipeline (用户素材 → 视频)                        │
│   ├─ models/ (Storyboard / StoryboardFrame / ProgressEvent / MediaResult) │
│   ├─ config/ (schema.py Pydantic + manager.py 单例 + loader.py YAML)  │
│   └─ prompts/ + utils/content_generators.py (LLM 文案/分镜生成)       │
└───────────────┬─────────────────────────────────────────────────────┘
                │  core.* 服务调用
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  服务层 (pixelle_video/services/)                                    │
│  ├─ llm_service.py        LLMService (OpenAI 兼容 SDK)               │
│  ├─ tts_service.py        TTSService (local/qwen_tts/comfyui 三模式) │
│  ├─ media.py              MediaService ──┐                          │
│  │   (workflow.startswith("api/") 路由)   ├─ ComfyUI 工作流路径       │
│  ├─ api_media.py          APIProviderMediaService ──┘ 直连 API 路径   │
│  ├─ frame_processor.py    FrameProcessor (单帧 4 步: TTS→媒体→合成→片段)│
│  ├─ frame_html.py         HTMLFrameGenerator (Playwright HTML→PNG)   │
│  ├─ video.py              VideoService (ffmpeg 合成/拼接/BGM)         │
│  ├─ comfy_base_service.py ComfyBaseService (工作流扫描/解析基类)      │
│  ├─ image_analysis / video_analysis / api_asset_analysis (素材反推)   │
│  ├─ persistence.py + history_manager.py (文件系统持久化)              │
│  └─ api_services/ (provider 客户端层, 见 1.3)                        │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 两条媒体生成路径

```
MediaService.__call__(prompt, workflow, ...)
        │
        ├── workflow 以 "api/" 开头? ──是──► APIProviderMediaService
        │                                          │
        │                                          ├─ ImageClient (dashscope/seedream/gpt)
        │                                          └─ VideoClient (dashscope/kling/seedance)
        │
        └── 否 ──► ComfyUI 路径
                    │  core._get_or_create_comfykit() (懒加载+热重载)
                    └─ kit.execute(workflow_input, workflow_params)
                        (runninghub 用 workflow_id; selfhost 用本地 path)
```

### 1.3 Provider 客户端层 (api_services/)

```
Façade (鸭子类型, 无 ABC)            Concrete (各 provider SDK 封装)
ImageClient ─┬─ .dashscope_client ─► DashScopeClient   (dashscope SDK)
             ├─ .seedream_client  ─► SeedreamClient    (ARK/OpenAI SDK)
             └─ .gpt_client       ─► ImageGPT          (openai SDK)
VideoClient ─┬─ .Dashscope_client ─► DashscopeVideoClient
             ├─ .kling_client     ─► KlingVideoClient  (JWT/REST)
             └─ .seedance_client  ─► SeedanceVideoClient(ARK REST)
VLM         └─ (eager)            ─► QwenVLClient      (dashscope MultiModal)
Config (metaclass) ──► config_manager + 环境变量覆盖 (兼容旧 Config.X 写法)
```

所有 provider 客户端均为**同步**实现，在 service 层通过 `asyncio.to_thread(...)` 桥接到异步。

---

## 2. 核心模块职责

### 2.1 Web 层

| 模块 | 职责 |
|---|---|
| [web/app.py](web/app.py) | Streamlit 入口，`st.navigation` 挂载三个 Page |
| [web/state/session.py](web/state/session.py) | `init_session_state`/`init_i18n`/`get_pixelle_video`；核心实例按 comfyui 配置 hash 缓存与重建 |
| [web/pipelines/base.py](web/pipelines/base.py) | `PipelineUI` 注册表（`register_pipeline_ui`/`get_all_pipeline_uis`），与核心 pipeline 注册表**相互独立** |
| [web/components/output_preview.py](web/components/output_preview.py) | 单次/批量生成入口；构造 `video_params`；`progress_callback` 闭包把 `ProgressEvent` 翻译成 i18n 文本驱动进度条 |
| [web/components/style_config.py](web/components/style_config.py) | 视觉/TTS/模板配置；预览（媒体/TTS/模板）均走 `run_async` |
| [web/components/settings.py](web/components/settings.py) | LLM/ComfyUI/API provider 配置表单 |
| [web/utils/async_helpers.py](web/utils/async_helpers.py) | `run_async(coro)`：win32 用 `ProactorEventLoop`，结束关闭 Playwright 浏览器 |
| [web/utils/batch_manager.py](web/utils/batch_manager.py) | `SimpleBatchManager`：顺序执行多主题，单任务失败继续；固定 `mode="generate"` |

### 2.2 核心编排层

| 模块 | 职责 |
|---|---|
| [pixelle_video/service.py](pixelle_video/service.py) | `PixelleVideoCore` 单例：持有所有服务、pipeline 注册表、`generate_video` 包装器、ComfyKit 懒加载与热重载 |
| [pixelle_video/pipelines/base.py](pixelle_video/pipelines/base.py) | `BasePipeline(ABC)`：定义 `__call__` 抽象契约与 `_report_progress` |
| [pixelle_video/pipelines/linear.py](pixelle_video/pipelines/linear.py) | `PipelineContext`（共享状态）+ `LinearVideoPipeline`（模板方法 8 步生命周期） |
| [pixelle_video/pipelines/standard.py](pixelle_video/pipelines/standard.py) | `StandardPipeline`：默认流水线，`generate`/`fixed` 两模式；并发分镜处理 |
| [pixelle_video/pipelines/asset_based.py](pixelle_video/pipelines/asset_based.py) | `AssetBasedPipeline`：用户素材驱动；重写 `__call__`，串行分镜 |
| [pixelle_video/pipelines/custom.py](pixelle_video/pipelines/custom.py) | `CustomPipeline`：参考实现模板，不使用模板方法 |
| [pixelle_video/models/storyboard.py](pixelle_video/models/storyboard.py) | `StoryboardConfig`/`StoryboardFrame`/`Storyboard`/`VideoGenerationResult` 数据模型 |
| [pixelle_video/models/progress.py](pixelle_video/models/progress.py) | `ProgressEvent`（0–1 进度 + 帧序号 + action） |
| [pixelle_video/models/media.py](pixelle_video/models/media.py) | `MediaResult`（media_type/url/duration） |
| [pixelle_video/config/schema.py](pixelle_video/config/schema.py) | Pydantic 配置 schema，单一真源 |
| [pixelle_video/config/manager.py](pixelle_video/config/manager.py) | `ConfigManager` 单例：加载/校验/更新/保存 |
| [pixelle_video/prompts/](pixelle_video/prompts/) | LLM prompt 模板（`str.format` 占位符）+ `build_*` 构造函数 |
| [pixelle_video/utils/content_generators.py](pixelle_video/utils/content_generators.py) | 无状态 LLM 内容生成：标题/旁白/图像提示词/视频提示词/脚本分割 |

### 2.3 服务层

| 模块 | 职责 |
|---|---|
| [llm_service.py](pixelle_video/services/llm_service.py) | `LLMService`：OpenAI 兼容；动态读配置（热重载）；支持 `response_type` 结构化输出（JSON schema 注入式，非 `beta.parse`） |
| [tts_service.py](pixelle_video/services/tts_service.py) | `TTSService(ComfyBaseService)`：`local`(edge-tts)/`qwen_tts`(线程池)/`comfyui` 三模式路由 |
| [media.py](pixelle_video/services/media.py) | `MediaService(ComfyBaseService)`：媒体生成分发 hinge，`api/` 前缀委托给 `api_media` |
| [api_media.py](pixelle_video/services/api_media.py) | `APIProviderMediaService`：`IMAGE_MODELS`/`VIDEO_MODELS`/`VIDEO_MODEL_CAPABILITIES` 注册表驱动；视频生成含内容审核失败→LLM 中性化重试 |
| [frame_processor.py](pixelle_video/services/frame_processor.py) | `FrameProcessor`：单帧 4 步（TTS→媒体→HTML 合成→视频片段） |
| [frame_html.py](pixelle_video/services/frame_html.py) | `HTMLFrameGenerator`：Playwright 单例浏览器；自定义 `{{param:type=default}}` DSL |
| [video.py](pixelle_video/services/video.py) | `VideoService`：ffmpeg 拼接/音视频合并/图叠视频/BGM；懒检 ffmpeg |
| [comfy_base_service.py](pixelle_video/services/comfy_base_service.py) | `ComfyBaseService`：工作流扫描/解析/默认值解析基类 |
| [persistence.py](pixelle_video/services/persistence.py) | `PersistenceService`：文件系统 JSON 持久化 + `.index.json` 索引 |
| [history_manager.py](pixelle_video/services/history_manager.py) | `HistoryManager`：历史业务层（`regenerate_frame`/`export_task` 为 stub） |
| [api_services/](pixelle_video/services/api_services/) | provider 客户端层（façade + concrete），全部同步 |
| [utils/os_util.py](pixelle_video/utils/os_util.py) | 路径/任务隔离/资源覆盖（`data/{type}/` 覆盖 `{type}/`） |
| [utils/template_util.py](pixelle_video/utils/template_util.py) | 模板发现/尺寸解析/类型检测（`get_template_type` 编码命名约定） |
| [utils/tts_util.py](pixelle_video/utils/tts_util.py) | edge-tts 直连实现（信号量+限速+重试）；local 模式仍在用 |

---

## 3. 关键接口契约

以下签名是系统其他部分所依赖的契约，改动需评估全链路影响。

### 3.1 核心入口

```python
# service.py:260
async def generate_video(text: str, pipeline: str = "standard", **kwargs) -> VideoGenerationResult
```

### 3.2 Pipeline 契约

```python
# base.py:72
async def __call__(self, text: str,
                   progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
                   **kwargs) -> VideoGenerationResult

# 注意 AssetBasedPipeline.__call__ 返回 PipelineContext 而非 VideoGenerationResult —— 契约偏离
```

### 3.3 服务契约（core.* 上暴露的 callable）

```python
await core.llm(prompt, api_key=None, base_url=None, model=None,
               temperature=0.7, max_tokens=2000, response_type=None, **kwargs) -> str | T
#   response_type 为 Pydantic 模型时走结构化输出

await core.tts(text, workflow=None, voice=None, speed=None,
               inference_mode=None, output_path=None, **params) -> str   # 音频路径

await core.media(prompt, workflow=None, media_type="image", width=None, height=None,
                 duration=None, output_path=None, image_path=None, **params) -> MediaResult

await core.api_media(prompt, workflow="api/...", media_type="image", ...) -> MediaResult

# FrameProcessor
async def __call__(self, frame: StoryboardFrame, storyboard, config: StoryboardConfig,
                   total_frames=1, progress_callback=None) -> StoryboardFrame   # frame_processor.py:44

# HTMLFrameGenerator
async def generate_frame(self, title, text, image, ext=None, output_path=None) -> str   # frame_html.py:395
```

### 3.4 Provider 客户端契约（鸭子类型）

```python
# 图像 provider
generate_image(prompt, image_paths=None, model=..., save_dir=None, ...) -> List[str]   # 本地路径

# 视频 provider
generate_video(prompt, image_path, save_path, model=..., duration=5, ...) -> str        # 远端 URL, 文件存到 save_path

# VLM
query(prompt, image_paths=None, model=..., session_id=None, video_paths=None) -> str
```

### 3.5 数据模型

```python
# ProgressEvent (progress.py:23)
event_type: str; progress: float (0–1, 校验); frame_current: int (1-based); frame_total: int;
step: int (1–4); action: "audio"|"image"|"compose"|"video"; extra_info: str

# StoryboardFrame (storyboard.py:59)
index, narration, image_prompt, audio_path, media_type("image"|"video"),
image_path, video_path, composed_image_path, video_segment_path, duration, created_at

# MediaResult (media.py:21)
media_type: "image"|"video"; url: str; duration: Optional[float]; is_image/is_video 属性
```

### 3.6 进度范围约定（standard pipeline）

| 阶段 | progress |
|---|---|
| 标题 | 0.01 |
| 旁白生成 | 0.05 |
| 图像提示词 | 0.15–0.30 |
| 分镜生产 | 0.20–0.80（0.6 区间均分到 N 帧） |
| 拼接 | 0.85 |
| 完成 | 1.0 |

---

## 4. 主要数据流

### 4.1 标准视频生成主数据流

```
用户输入(主题/固定文案)
  │
  ▼
Web output_preview 构造 video_params ──run_async──► core.generate_video(text, pipeline="standard", **kwargs, progress_callback)
  │
  ▼
StandardPipeline.__call__ (继承自 LinearVideoPipeline 模板方法)
  │  构建 PipelineContext(input_text, params, progress_callback)
  │
  ├─ setup_environment     : create_task_output_dir() → ctx.task_id/task_dir/final_video_path
  ├─ generate_content      : generate | fixed
  │     generate → generate_narrations_from_topic(llm, topic, n_scenes, min/max_words) → ctx.narrations
  │     fixed    → split_narration_script(text, split_mode) → ctx.narrations
  ├─ determine_title       : generate_title(llm, text, strategy)
  ├─ plan_visuals          : get_template_type(template)
  │     需媒体 → generate_image_prompts(llm, narrations, ...) → build_image_prompt(base, prefix)
  │     static → image_prompts = [None]*N
  ├─ initialize_storyboard : StoryboardConfig + Storyboard + N 个 StoryboardFrame
  ├─ produce_assets        : 并发 (Semaphore=5) 对每帧调 frame_processor  (见 4.2)
  ├─ post_production       : VideoService.concat_videos(segments, bgm) → final_video_path
  └─ finalize              : VideoGenerationResult → _persist_task_data (非致命)
```

### 4.2 单帧数据流 (FrameProcessor)

```
StoryboardFrame(narration, image_prompt)
  │
  ├─ _step_generate_audio   : core.tts(text=narration, ...) → frame.audio_path; frame.duration = ffprobe
  ├─ _step_generate_media   : media_type 由 "video_" in workflow 或 template_type=="video" 决定
  │     core.media(prompt, workflow, duration=frame.duration, ...) → MediaResult → 下载 → frame.image_path/video_path
  │     (api/ 视频工作流: 可选 driving_audio=narration 音频; 无图时用 first_frame_workflow 生成首帧)
  ├─ _step_compose_frame    : HTMLFrameGenerator(template).generate_frame(title, text, image/video) → frame.composed_image_path (PNG)
  └─ _step_create_video_segment:
        video 媒体 → overlay_image_on_video + merge_audio_video(replace_audio=True)
        image 媒体 → create_video_from_image(image, audio, fps)
        → frame.video_segment_path
```

### 4.3 配置数据流

```
config.yaml ──loader.load_config_dict──► ConfigManager (PixelleVideoConfig, 单例)
                                              │
                  ┌───────────────────────────┼──────────────────────────┐
                  ▼                           ▼                          ▼
          service 层动态读取            web get_pixelle_video         api_services/Config
          (llm 每次调用读 config_manager)  (按 comfyui hash 重建 core)   (metaclass + env 覆盖)
```

> 注意：`ConfigManager.update()` 只改内存不落盘，调用方需再调 `save()`。

### 4.4 资源覆盖数据流

```
查找 workflows/templates/bgm 时:
  os_util.get_resource_path(type, *paths)
    ├─ 先查 data/{type}/  (用户自定义, 优先)
    └─ 再查 {type}/       (内置默认)
  未找到 → FileNotFoundError
```

---

## 5. 核心调用链

### 5.1 Web → Core → Pipeline → FrameProcessor → Services

```
web/pages/1_🎬_Home.py:58  get_pixelle_video()
  └─ web/state/session.py:42  (hash 重建)
web/components/output_preview.py:242  run_async(pixelle_video.generate_video(**video_params))
  └─ service.py:260  generate_video_wrapper
       └─ pipelines/standard.py  StandardPipeline.__call__ (linear.py:84 模板方法)
            └─ produce_assets (standard.py:305)
                 └─ asyncio.gather( per_frame: semaphore )
                      └─ frame_processor.py:44  FrameProcessor.__call__
                           ├─ tts_service.py:65   core.tts → edge_tts / qwen_tts(to_thread) / comfyui(kit)
                           ├─ media.py:118        core.media
                           │    ├─ api/ → api_media.py:426 → ImageClient/VideoClient (to_thread)
                           │    └─ else → core._get_or_create_comfykit().execute
                           ├─ frame_html.py:395   HTMLFrameGenerator.generate_frame (Playwright)
                           └─ video.py            VideoService (ffmpeg)
```

### 5.2 直连扩展 pipeline 调用链（不走 generate_video）

```
digital_human / image_to_video / action_transfer UI
  └─ 各自定义内部 async generate_*_video()
       └─ run_async(...)
            └─ 直接调 core.media / core.tts / core.llm + core._get_or_create_comfykit()
       (不经过 StandardPipeline，也不产出 Storyboard 标准结构)
```

### 5.3 ComfyKit 热重载链

```
任何 comfyui 工作流调用
  └─ service.py:147  _get_or_create_comfykit()
       ├─ 首次: 创建 ComfyKit, 记录 config_hash
       └─ hash 变化: close 旧实例(try/except) → 创建新实例
```

---

## 6. 潜在技术债

| # | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| T1 | [video_dashscope.py:27-33](pixelle_video/services/api_services/video_dashscope.py#L27) | `_proxy_env_lock = threading.Lock()` 误写在类 docstring 内部，未成为类属性 | DashScope 视频开代理时 `with self._proxy_env_lock:` 抛 `AttributeError`，代理路径直接不可用 | 缩进到 docstring 外，对齐 image_dashscope.py:22-23 |
| T2 | [standard.py:312](pixelle_video/pipelines/standard.py#L312) | `_FRAME_CONCURRENT = 5` 硬编码（已有 ponytail 注释） | 无法按 provider QPS 调整，高并发可能触发限流 | 挪进 config schema，仿 `runninghub_concurrent_limit` |
| T3 | [asset_based.py](pixelle_video/pipelines/asset_based.py) | `__call__` 返回 `PipelineContext` 而非 `VideoGenerationResult`，且动态给 dataclass 挂 `ctx.request/asset_index/script/matched_scenes/_last_api_video_tail_frame` | 契约偏离基类；类型检查失效；动态属性难追踪 | 统一返回类型；动态属性声明到 dataclass 或独立 context 子类 |
| T4 | [custom.py](pixelle_video/pipelines/custom.py) | `CustomPipeline` 不用模板方法，整段内联，与 standard 大量重复逻辑 | 维护双份逻辑，漂移风险 | 重构为复用 LinearVideoPipeline 钩子，或明确标注为"参考模板勿扩展" |
| T5 | [history_manager.py:178,206](pixelle_video/services/history_manager.py#L178) | `regenerate_frame` / `export_task` 为 stub（Phase 3） | UI 若引用会静默返回 None | 要么实现，要么显式抛 NotImplementedError 并从 UI 隐藏入口 |
| T6 | [video.py:728](pixelle_video/services/video.py#L728) | `add_bgm` 的 `fade_out` 参数文档化但未实现 | 调用方传值无效 | 实现或移除参数 |
| T7 | [tts_util.py](pixelle_video/utils/tts_util.py) | 整模块标注"Temporarily not used"，但 local TTS 模式实际仍调用其 `edge_tts` | 信号量/限速/重试逻辑仍生效但易被误删 | 明确注释存活路径，或合并入 tts_service |
| T8 | [config/schema.py](pixelle_video/config/schema.py) | web 暴露 `qwen_tts` 模式，但 schema 只有 `local`/`comfyui`，`qwen_tts` 不落盘 | 重启后 qwen_tts 选择丢失 | schema 增 `qwen_tts` 枚举或文档说明仅 UI 临时态 |
| T9 | [digital_human/i2v/action_transfer](web/pipelines/digital_human.py) | 三个 UI 用硬编码百分比手动驱动进度条，不走 ProgressEvent | 进度不可观测、风格不统一 | 接入 ProgressEvent 回调 |
| T10 | Provider 客户端 | 全部同步实现，靠 `asyncio.to_thread` 桥接；无 ABC，纯鸭子类型 | 新增 provider 易漏方法签名；线程池压力大时阻塞 | 抽象基类 + 原生 async 客户端 |
| T11 | [frame_html.py:58-60](pixelle_video/services/frame_html.py#L58) | Playwright 浏览器为类级单例 + 跨事件循环重建 | run_async 每次新 loop 都重建浏览器，首帧延迟高 | 进程级单浏览器 + 持久 loop |
| T12 | [config.yaml](config.yaml) | 仓库根 config.yaml 含真实密钥（llm.api_key 等） | 泄露风险 | .gitignore 排除，仅保留 config.example.yaml |
| T13 | [batch_manager.py](web/utils/batch_manager.py) | 批量固定 `mode="generate"`，顺序执行无并发 | 无法批量固定文案/自定义配置 | 如有需求再扩展，当前 YAGNI 可接受 |
| T14 | AssetBasedPipeline.produce_assets | 串行无并发 | 素材型视频生成慢 | 评估素材间独立性后选择性并发 |
| T15 | prompts `__init__.py` | `build_video_prompt_prompt` / `build_asset_script_prompt` 未 re-export，调用方全路径导入 | 风格不一致 | 统一 re-export 或全部全路径 |

---

## 7. 后续改造工程约束

改动本项目时**必须**遵守以下约束。

### 7.1 同步↔异步边界

- **唯一桥是 `run_async`**（[web/utils/async_helpers.py](web/utils/async_helpers.py)）。Web 层不得自建 event loop。
- win32 必须用 `ProactorEventLoop`（Playwright/子进程依赖），不要切回 `WindowsSelectorEventLoopPolicy`。
- `run_async` 结束会关闭共享 Playwright 浏览器，单次调用内完成所有渲染，不要跨 `run_async` 持有浏览器句柄。
- 同步 SDK 调用（dashscope/openai/kling 等）在 service 层**必须**包 `asyncio.to_thread`，不得直接阻塞事件循环。

### 7.2 配置

- 配置单一真源是 [schema.py](pixelle_video/config/schema.py) 的 Pydantic 模型。新增配置项必须加 schema 字段 + 默认值 + 校验。
- `ConfigManager.update()` 只改内存**不落盘**，改完必须调 `save()`。
- LLM 配置热重载依赖 `llm_service` 每次调用动态读 `config_manager`，不要在 service `__init__` 缓存配置快照。
- ComfyUI 配置变更后 `get_pixelle_video()` 会按 hash 重建 core —— 不要在模块级缓存 core 实例。
- API provider 客户端构造必须走懒加载 `@property`（仿 [image_client.py:56](pixelle_video/services/api_services/image_client.py#L56)），缺 key 时抛清晰错误，不要在 `__init__` 强建所有 client。

### 7.3 Provider 扩展

新增图像/视频/VLM provider 时：
1. 在 `api_media.py` 的 `IMAGE_MODELS`/`VIDEO_MODELS`/`VIDEO_MODEL_CAPABILITIES` 注册表登记（视频必须填 `api_contract_verified` 与能力表）。
2. concrete client 实现契约签名（§3.4），同步实现 + 重试 + 代理处理。
3. façade（`ImageClient`/`VideoClient`）加懒加载 property + model 名子串路由。
4. 同步调用一律 `asyncio.to_thread`。
5. 代理仅在该 provider `use_proxy=True` 时传入 `local_proxy`。

### 7.4 Pipeline 扩展

- 优先复用 `LinearVideoPipeline` 的 8 步模板方法钩子，不要重写 `__call__`（`AssetBasedPipeline`/`CustomPipeline` 是反面教材）。
- 新 pipeline 要在 [service.py:214](pixelle_video/service.py#L214) 的 `self.pipelines` 注册表登记。
- `__call__` 必须返回 `VideoGenerationResult`，不要返回 `PipelineContext`。
- 进度必须通过 `_report_progress` 发 `ProgressEvent`，progress 值落在约定区间（§3.6），不要硬编码 UI 百分比。
- 持久化失败**不得**中断视频生成（包 try/except + log）。

### 7.5 媒体生成路由

- `workflow` 以 `api/` 开头 → 直连 API 路径；否则 → ComfyUI 路径。此路由判断在 [media.py:208](pixelle_video/services/media.py#L208)，不要在其他地方重复实现。
- 视频工作流要把 `duration=frame.duration` 传下去，保证视频时长对齐 TTS 音频。
- `api/` 视频工作流的输入校验依赖 `adapter_ability_types`，不要绕过。

### 7.6 资源与命名约定

- 工作流命名：`{source}/{prefix}_{name}.json`，source ∈ `runninghub`/`selfhost`，prefix 由 `WORKFLOW_PREFIX` 决定（`tts_`/`image_`/`video_`/`analyse_`）。
- 模板命名：`{WxH}/{type}_{name}.html`，type ∈ `static`/`image`/`video`，由 [get_template_type](pixelle_video/utils/template_util.py#L389) 解码。
- 用户自定义资源放 `data/{type}/`，会覆盖内置 `{type}/` —— 所有资源查找必须走 `os_util.get_resource_path`，不要硬编码路径。
- HTML 模板参数用 `{{param:type=default}}` DSL（[frame_html.py](pixelle_video/services/frame_html.py)），预设占位符仅 `{title,text,image,index}`。

### 7.7 并发

- 分镜并发上限走 `asyncio.Semaphore`（standard pipeline），不要裸 `gather`。
- 跨 pipeline 不要在 web 层做并发（Streamlit 同步模型）；批量任务顺序执行。
- RunningHub 并发受 `runninghub_concurrent_limit`（1–10）约束。

### 7.8 进度与错误

- `ProgressEvent.progress` 校验 0–1，超界抛 `ValueError`。
- 顶层 pipeline 异常会 `raise` 到 web 层，web 层 try/except 后 `st.error` + `st.stop()`。
- 网络/审核类失败走各 provider 的重试与中性化重试链，不要在 pipeline 层吞掉。
- 持久化、ComfyKit 清理、素材分析失败均为非致命，log 后继续。

### 7.9 依赖与工具链

- Python ≥3.11，ruff line-length 100，pytest-asyncio `auto` 模式。
- `moviepy==1.0.3`、`edge-tts==7.2.7` 锁版本，不要随意升级。
- ffmpeg 必须在 PATH；`VideoService` 懒检。
- 不要新增 Jinja2 —— 两套模板系统（prompt 的 `str.format` + HTML 的自定义 DSL）已定型，ComfyKit 工作流用 `$var.value` 约定。

### 7.10 安全

- `config.yaml` 含真实密钥，**不得提交**；示例用 `config.example.yaml`。
- `Config` facade 的 env 覆盖仅用于本地调试，不要在生产依赖。
- 上传/下载路径必须走 `_get_unique_temp_path` 避免并发冲突。

---

## 8. 附：关键文件索引

| 关注点 | 文件 |
|---|---|
| 服务容器/单例 | [service.py](pixelle_video/service.py) |
| 模板方法/上下文 | [linear.py](pixelle_video/pipelines/linear.py) |
| 默认流水线 | [standard.py](pixelle_video/pipelines/standard.py) |
| 单帧处理 | [frame_processor.py](pixelle_video/services/frame_processor.py) |
| 媒体路由 hinge | [media.py](pixelle_video/services/media.py) + [api_media.py](pixelle_video/services/api_media.py) |
| HTML 渲染 | [frame_html.py](pixelle_video/services/frame_html.py) |
| 视频合成 | [video.py](pixelle_video/services/video.py) |
| 配置真源 | [schema.py](pixelle_video/config/schema.py) + [manager.py](pixelle_video/config/manager.py) |
| Web↔异步桥 | [async_helpers.py](web/utils/async_helpers.py) + [session.py](web/state/session.py) |
| Provider 注册表 | [api_media.py](pixelle_video/services/api_media.py) (`IMAGE_MODELS`/`VIDEO_MODELS`/`VIDEO_MODEL_CAPABILITIES`) |
| 资源覆盖 | [os_util.py](pixelle_video/utils/os_util.py) |
| 命名约定解码 | [template_util.py](pixelle_video/utils/template_util.py#L389) |
