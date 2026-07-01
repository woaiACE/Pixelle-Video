# 落地计划 — 故事插图视频：项目级管理 + Prompt 升级

> 依据：`SPEC_story_project.md`（2026-07-01）
> 原则：垂直切片（每任务一条可验证路径）+ 阶段间检查点 + 不破旧数据。

## 依赖图

```
P1 数据层 (ProjectService + Project model)
   │
   ├─→ P2 Prompt 层 (4 个 prompt 升级)        ← 独立，可与 P1 并行
   │        │
   │        └─→ P3 后端 pipeline (产物落 projects/，frame 加字段)
   │                 │
   │                 └─→ P5 前端工作区 (4 阶段编辑)
   │                          │
   └─→ P4 前端列表页 ──────────┴─→ P6 历史页兼容
                                      │
                                      └─→ P7 实机验证
```

- P1、P2 无 UI 依赖、无互相依赖 → 可并行先行。
- P3 依赖 P1（落盘路径）+ P2（新 prompt）。
- P4 依赖 P1（列表数据源）。
- P5 依赖 P1+P2+P3（工作区编辑 → 调 pipeline 生成）。
- P6 依赖 P1（扫描 projects/）。
- P7 依赖全部。

## 垂直切片说明
不做"先改所有 model → 再改所有 service → 再改所有 UI"的横向切。每任务是一条完整可验证路径：
- T1 建项目 → T2 列表能看见它 → T3 编辑故事能存盘 → ... → T8 生成视频落进项目目录。
- 每任务结束都有一个**可手动点到的 UI 行为或可跑的自检**，不留"半截功能"。

---

## 检查点

| 检查点 | 位置 | 通过标准 |
|---|---|---|
| CP1 | P1+P2 后 | `python -m pixelle_video.services.project_service` 自检绿 + `python -m pixelle_video.prompts.story_prompts` 占位替换自检绿 |
| CP2 | P3 后 | `python -c` 调 story_illustration pipeline，产物出现在 `output/projects/{id}/runs/{run_id}/`，storyboard.json 含 shot_type 字段 |
| CP3 | P4+P5 后 | 浏览器进故事插图 tab → 看到列表 → 新建项目 → 4 阶段切换编辑 → 关闭重进恢复 |
| CP4 | P6 后 | 历史页同时显示新项目 + 旧 task，旧 task 只读不报错 |
| CP5 | P7 后 | 实机跑通完整流程（6.3 清单），旧 task 未损坏 |

---

## 任务清单

### T1 — ProjectService + Project model（P1 数据层）
**路径**：建项目 → 算阶段就绪 → 列表扫描。
- 新增 `pixelle_video/models/project.py`：`Project` / `ProjectStage` dataclass（字段见 SPEC 2.2）。
- 新增 `pixelle_video/services/project_service.py`：`ProjectService`
  - `create_project(title) -> Project`：生成 `project_id`，建 `output/projects/{id}/` + 空 `project.json` + `story.txt`。
  - `load_project(project_id) / save_project(project)`：JSON 读写。
  - `update_stage_ready(project)`：据 `story.txt`/`assets.json`/`scenes.json`/`latest_run` 算 `stages_ready`。
  - `list_projects() -> List[Project]`：扫 `output/projects/*/`，按 `updated_at` 降序。
  - `save_story / save_assets / save_scenes`：写对应文件 + 刷新 `stages_ready` + `updated_at`。
- 文件末尾 `if __name__ == "__main__":` 自检：tmp_path 建项目 → 写 story → 断言 `stages_ready.story=True` → 加资产图 → 断言 `assets=True` → list 返回 1。
- **验收**：`python -m pixelle_video.services.project_service` 退出码 0，断言全过。
- **验证步骤**：`cd <repo> && python -m pixelle_video.services.project_service`

### T2 — Prompt 升级（P2 Prompt 层）
**路径**：4 个 prompt 构建 → 占位全替换。
- 改 `pixelle_video/prompts/story_prompts.py`：
  - 升级 `STORY_EXTRACTION_PROMPT`（角色/场景/道具描述加视觉规格，见 SPEC 4.1）。
  - 升级 `STORY_SCENECUT_PROMPT`：输出增 `shot_type/camera_move/lighting`，加分镜衔接铁律 + 资产白名单铁律（SPEC 4.3）。
  - 新增 `CHARACTER_SHEET_PROMPT` 常量 + `build_character_sheet_prompt(description)`（SPEC 4.2）。
  - 新增 `STORY_PANEL_IMAGE_PROMPT` + `build_story_panel_image_prompt(...)`（SPEC 4.4，输出英文 prompt 串）。
- `build_story_scenecut_prompt` 解析端（pipeline 与 UI）兼容新字段（缺省补空串）。
- 文件末尾自检：每个 `build_xxx` 用样例调用，`assert '{' not in result and '}' not in result`（占位全替换）。
- **验收**：`python -m pixelle_video.prompts.story_prompts` 退出码 0。
- **验证步骤**：`python -m pixelle_video.prompts.story_prompts`

### T3 — generate_image_prompts 支持 prompt_template（P2 子任务）
**路径**：story_illustration 传新 prompt → 图片 prompt 贴合绘本。
- 改 `pixelle_video/utils/content_generators.py:269` `generate_image_prompts`：加 `prompt_template: Optional[callable] = None` 参数；默认仍调 `build_image_prompt_prompt`，传了则用 `prompt_template(...)`。
- story_illustration 的 `plan_visuals` 调用时传 `prompt_template=build_story_panel_image_prompt`，并拼 `photography_rules`（shot_type/camera_move/lighting）。
- standard pipeline 不传 → 行为不变。
- **验收**：standard 路径回归不报错（旧调用签名兼容）；story_illustration 路径产出英文绘本风 prompt。
- **验证步骤**：`python -c "from pixelle_video.utils.content_generators import generate_image_prompts; import inspect; assert 'prompt_template' in inspect.signature(generate_image_prompts).parameters"`

### T4 — StoryboardFrame 加摄影字段 + persistence 序列化（P3 后端基础）
**路径**：分镜新字段能进 storyboard.json 且 resume 不丢。
- 改 `pixelle_video/models/storyboard.py:58` `StoryboardFrame`：加 `shot_type: Optional[str]=None` / `camera_move: Optional[str]=None` / `lighting: Optional[str]=None`（全可选，旧数据兼容）。
- 改 `pixelle_video/services/persistence.py` `_frame_to_dict`：补 `shot_type/camera_move/lighting` + `reference_image_paths`（修复 SPEC 提到的 resume 丢参考图 bug，顺带）。
- `_frame_from_dict` 对称补回。
- **验收**：旧 storyboard.json（无新字段）load 不报错；新字段 round-trip 一致。
- **验证步骤**：`python -c "import json; from pixelle_video.models.storyboard import StoryboardFrame; f=StoryboardFrame(index=0,narration='x',image_prompt='y',shot_type='特写'); print(f.shot_type)"`

### T5 — story_illustration pipeline 产物落 projects/（P3 后端）
**路径**：生成视频 → 产物进 `projects/{id}/runs/{run_id}/`。
- 改 `pixelle_video/pipelines/story_illustration.py`：
  - `generate_content`：解析新分镜字段 → 存 `ctx` → `initialize_storyboard` 时挂到 frame。
  - `plan_visuals`：资产图用 `build_character_sheet_prompt`（替代硬编码）；图片 prompt 用 `build_story_panel_image_prompt` + `photography_rules`。
  - 接受 `project_id` 参数：有则 run 产物写到 `output/projects/{project_id}/runs/{task_id}/`，无则退回旧 `output/{task_id}/`（兼容 API 直调）。
  - 生成完成后回写 `project.json` 的 `latest_run_id` / `cover_path` / `stages_ready.video`。
- 复用基类 `setup_environment` 的 `create_task_output_dir`，通过 `output_path` 参数导向项目子目录（最小改动）。
- **验收（CP2）**：`python -c` 构造 video_params（带 `project_id`）调 `generate_video`，产物在 projects 子目录，storyboard.json 含 shot_type。
- **验证步骤**：实机 1 次小规模生成（2 镜）+ 检查目录。

### T6 — 前端项目列表页（P4）
**路径**：进 tab → 看列表 → 新建项目。
- 新增 `web/components/story_project_list.py`：`render_story_project_list(pixelle_video)`
  - 卡片网格：缩略图（cover_path 或占位）/ 标题 / 进度阶段点 / updated_at。
  - "+ 新建项目" 按钮 → 弹标题输入 → `ProjectService.create_project` → `st.rerun`。
  - 点卡片 → `st.session_state["current_project_id"] = id` → 切到工作区。
- 重写 `web/pipelines/story_illustration.py` 的 `render()`：无 `current_project_id` 显示列表，有则显示工作区（T7）。
- **验收**：进 tab 看到列表；新建后列表多一张卡。
- **验证步骤**：浏览器手动。

### T7 — 前端工作区 4 阶段（P5）
**路径**：进入项目 → 4 阶段切换编辑 → 自动保存 → 重进恢复。
- 新增 `web/components/story_workspace.py`：`render_story_workspace(pixelle_video, project_id)`
  - 顶部阶段导航（1️⃣故事/2️⃣资产库/3️⃣分镜/4️⃣生成），未就绪灰显可预览，`st.session_state["story_stage"]` 驱动。
  - 阶段 1：故事文本/标题/画风/provider/配音 → 保存 `story.txt` + `project.json`。
  - 阶段 2：资产库（复用现有资产提取+生图逻辑，但用 `build_character_sheet_prompt`）→ 保存 `assets.json`。
  - 阶段 3：分镜（调升级后 `build_story_scenecut_prompt`，可编辑 narration/composition/shot_type/camera_move/lighting）→ 保存 `scenes.json`。
  - 阶段 4：生成（带 `project_id` 调 `generate_video`）+ 历次 run 列表。
  - 每阶段编辑后 `ProjectService.save_*` 自动落盘。
  - 重进：`load_project` → 读 `current_stage` → 定位阶段 → 从 json 恢复表单。
- 把现有 wizard 的资产/分镜/生图子逻辑搬到工作区对应阶段（复用，不重写）。
- **验收（CP3）**：4 阶段切换编辑 → 关闭重进恢复 → 生成视频。
- **验证步骤**：浏览器手动全流程。

### T8 — 历史页兼容（P6）
**路径**：历史页同时显示新项目 + 旧 task。
- 改 `web/pages/2_📚_History.py` 扫描逻辑：
  - 先扫 `output/projects/*/runs/*/`（项目 run，标记📁，可跳工作区）。
  - 再扫 `output/*/`（旧扁平 task，标记📜，只读）。
  - 列表用图标/标签区分两类。
- 不改 `history_manager` 业务层接口，只在 UI 层分流。
- **验收（CP4）**：历史页两类都显示，旧 task 点开不报错。
- **验证步骤**：浏览器手动 + 有旧 task 时。

### T9 — 实机验证 + 收尾（P7）
**路径**：跑通 SPEC 6.3 清单。
- 手动清单：新建项目 → 故事 → 资产 → 分镜 → 生成 → 关闭重进恢复 → 历史页可见 → 旧 task 完好。
- 修实机暴露的问题。
- 更新 README（zh+en）项目级管理 + prompt 升级 changelog。
- **验收（CP5）**：清单全过。
- **验证步骤**：浏览器全流程。

---

## 执行顺序建议
1. T1 + T2 并行（无依赖，纯后端，CP1）。
2. T3 + T4（依赖 T2，CP1 延伸）。
3. T5（CP2）。
4. T6 + T8 可并行（CP3 前半 + CP4）。
5. T7（CP3，主工作量）。
6. T9（CP5）。

每任务独立提交。T1-T5 无 UI 风险可快速连推；T6-T8 是 UI 主战场，逐个验证。
