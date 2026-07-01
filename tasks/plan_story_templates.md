# 实现计划 — 故事插画：专用模板库 + 模板选择 UI

> 依据：调研结论（字幕=模板 {{text}} 烘焙，story_illustration 已有；缺的是模板选择 UI + 故事专用模板）
> 决策：新建 story_* 模板 + 故事阶段 UI 选择；字幕沿用模板烘焙，不另做 ffmpeg 烧录。

## 现状

- story_illustration 写死 `frame_template="1080x1920/image_story.html"`（web/pipelines/story_illustration.py 阶段4 video_params）。
- 故事阶段（_stage_story）无模板选择控件。
- 快速创作有完整模板库（templates/1080x1920/image_*.html）+ UI 选择（style_config.py 的 workflow_select）。
- 字幕 = 模板 `{{text}}` 占位烘焙进帧 PNG，已工作，无需改。

## 目标

1. 新建 2-3 个故事专用模板（story_* 前缀），参考 image_story.html 但画面更贴合绘本叙事。
2. 故事阶段加模板选择下拉，选择写入 project.json，生成时透传。
3. 模板列表动态扫描（复用现有 list_resource_files），不硬编码。

## 任务

### T1 — 新建故事专用模板
- `templates/1080x1920/story_classic.html`：经典绘本风（米黄纸 + 书脊 + 大图 + 旁白下置，基于 image_story.html 微调）。
- `templates/1080x1920/story_comic.html`：漫画风（白底 + 黑边 + 对话框旁白）。
- `templates/1080x1920/story_warm.html`：温暖风（暖色背景 + 圆角图 + 手写体旁白）。
- 每个含 `template:media-width/height` meta + `{{title}}/{{image}}/{{text}}/{{index}}/{{page_count}}` 占位。
- **验收**：`get_template_type` 识别为 image 类型；`HTMLFrameGenerator.get_media_size` 能解析尺寸。
- **验证**：`python -c "from pixelle_video.utils.template_util import get_template_type; print(get_template_type('story_classic.html'))"` → image。

### T2 — 故事阶段加模板选择 UI + 预览
- `_stage_story` 加 `st.selectbox` 选 story_* 模板（扫描 templates/*/story_*.html）。
- 选择下方加"预览模板"按钮，调 `HTMLFrameGenerator.generate_frame` 用示例标题/旁白/占位图生成预览 PNG 并显示。
- 选择写入 `st.session_state["story_frame_template"]` + `project.json` 的 `frame_template` 字段。
- `_restore_project_to_session` 恢复 `story_frame_template`。
- **验收**：故事阶段可选 3 个模板，点预览生成示例帧，切换后生成用所选模板。
- **验证**：浏览器手动。

### T3 — 生成阶段透传所选模板
- video_params 的 `frame_template` 从 `_ss("story_frame_template")` 取（默认 image_story.html）。
- `Project` model 加 `frame_template` 字段（可选）。
- **验收**：选 story_comic 生成 → 视频用 story_comic.html 渲染。
- **验证**：浏览器手动 + 检查 storyboard.json config.frame_template。

## 不做（YAGNI）
- 不做 ffmpeg 时序字幕烧录（模板烘焙已够）。
- 不做模板编辑器/预览（选了就生成，预览成本高）。
- 不做多尺寸故事模板（先 1080x1920 竖屏，够用）。
- 不改快速创作模板库。

## 执行顺序
T1（模板）→ T2（UI 选择）→ T3（透传）。每任务独立可验证。
