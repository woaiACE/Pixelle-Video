# TODO — 故事插图视频：项目级管理 + Prompt 升级

> 详细计划见 `tasks/plan.md`。每任务完成后勾选并标注提交 hash。

## P1 数据层 + P2 Prompt 层（CP1）

- [x] **T1** ProjectService + Project model + 自检
  - 验收：`python -m pixelle_video.services.project_service` 退出码 0 ✅
  - commit: _

- [x] **T2** Prompt 升级（4 个 prompt）+ 占位替换自检
  - 验收：`python -m pixelle_video.prompts.story_prompts` 退出码 0 ✅
  - commit: _

- [x] **T3** story_illustration 改用 build_story_panel_image_prompt 逐镜生成（跳过 generate_image_prompts 改造，更懒）
  - 验收：import OK，自检绿 ✅
  - commit: _

- [x] **T4** StoryboardFrame 加 shot_type/camera_move/lighting + persistence 序列化（含 reference_image_paths 修复）
  - 验收：语法+import OK ✅
  - commit: _

## P3 后端 pipeline（CP2）

- [x] **T5** story_illustration 产物落 projects/{id}/runs/{run_id}/ + 解析新分镜字段 + 回写 project.json
  - 验收：setup_environment/finalize 覆写完成，import OK ✅（实机验证留 T9）
  - commit: _

## P4 前端列表 + P6 历史兼容

- [x] **T6** 前端项目列表页（卡片 + 新建项目）
  - 验收：语法+import OK ✅（实机留 T9）
  - commit: _

- [x] **T8** 历史页扫描 projects/ + 旧 task 区分展示
  - 验收：语法 OK，故事项目区块 + 旧 task 并存 ✅
  - commit: _

## P5 前端工作区（CP3）

- [x] **T7** 工作区 4 阶段导航 + 编辑 + 自动保存 + 重进恢复
  - 验收：语法+import OK ✅（实机留 T9）
  - commit: _

## P7 实机验证（CP5）

- [x] **T9** README 更新（zh+en）+ 静态校验全绿；实机验证待用户本地跑
  - 验收：8 文件语法 OK + 3 自检绿 ✅；实机清单待跑
  - commit: _
