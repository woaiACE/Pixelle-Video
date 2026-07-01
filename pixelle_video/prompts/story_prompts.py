# Copyright (C) 2025 AIDC-AI
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""
Story illustration prompts (upgraded — see SPEC_story_project.md §4).

Four prompts:
- build_story_extraction_prompt: 故事 → 角色/场景/道具视觉描述（升级，含摄影规格）
- build_character_sheet_prompt: 角色描述 → 四视角参考图 prompt（新增，替代硬编码）
- build_story_scenecut_prompt: 故事 → 分镜（旁白+构图+景别+运镜+光照）（升级）
- build_story_panel_image_prompt: 分镜 → 单张插图英文 prompt（新增，绘本专用）
"""

# =====================================================================
# 1. 资产描述提取（升级）
# =====================================================================
STORY_EXTRACTION_PROMPT = """# 角色任务
你是一位绘本故事拆解专家。从用户输入的故事文本中提取"角色""场景""道具"三类要素，给出每项的中文视觉描述（可直接作为图片生成 prompt 的基础）。

# 输入故事
{story}

# 提取要求
- 角色（characters）：故事中出场的人物/动物拟人形象。描述须包含：性别、年龄段、发型发色、脸型五官特征、体态身材、服装款式与配色、鞋款、配饰、辨识标志（如疤痕/胎记/独特饰物）。禁止写表情、动作、姿势、背景。非人类角色灵活处理但保留上述可画维度。
- 场景（scenes）：故事发生的地点/环境。描述须包含：空间结构（室内外/地形/建筑布局）、主要材质（木/石/草/水）、光线（时段/光源方向/明暗）、氛围色调、天气。开头点明场景名。
- 道具（props）：对剧情有关键作用的物品。描述须包含：主体结构、材质、颜色、表面处理、装饰细节、数量关系。禁止写用途、剧情、人物、光影氛围。白底居中资产图视角。

# 通用规则
- 描述用中文，具体可画，避免抽象抒情词（如"好看""神秘""高级"）。
- 不出现故事文本之外杜撰的关键设定。
- 若某类没有，返回空数组。每类不超过 5 项，挑最重要的。

# 输出格式
严格输出以下 JSON，不要任何解释文字：

```json
{{
  "characters": [{{"name": "角色名", "description": "中文视觉描述"}}],
  "scenes": [{{"name": "场景名", "description": "中文视觉描述"}}],
  "props": [{{"name": "道具名", "description": "中文视觉描述"}}]
}}
```
"""


# =====================================================================
# 1a. 分类型资产提取（细粒度，参考 waoowaoo select_location/select_prop）
#     三次独立 LLM 调用，每次专注一类，避免单 prompt 注意力分散导致场景/道具被压缩。
# =====================================================================

STORY_CHARACTER_PROMPT = """# 角色任务
你是绘本角色设计师。从故事中提取所有出场的人物/动物拟人形象，给出每项的中文视觉描述 + 初始位置。

# 输入故事
{story}

# 提取要求
- 提取所有有名字或反复出场的角色（人物/动物拟人形象）。
- 每角色描述须包含：性别、年龄段、发型发色、脸型五官特征、体态身材、服装款式与配色、鞋款、配饰、辨识标志（如疤痕/胎记/独特饰物）。
- 非人类角色（动物）灵活处理：保留体型/毛色/品种特征/标志性外观（如八哥犬的褶皱脸/短腿）。
- 描述末尾加"初始位置：xxx"（角色首次出场时所在的场景区域，如"工具箱上""暖气片旁"），供分镜阶段避免空间错位。
- 禁止写表情、动作、姿势、背景环境。中文，具体可画，避免抽象抒情词。

# 输出格式
严格输出 JSON，不要解释：

```json
{{
  "characters": [{{"name": "角色名", "description": "中文视觉描述。初始位置：xxx"}}]
}}
```
"""


STORY_LOCATION_PROMPT = """# 角色任务
你是影视场记 + 绘本场景设计师。从故事中提取所有需要制画的场景，按空间深度/朝向/子区域拆分为独立场景。

# 输入故事
{story}

# 拆分规则
- 不同空间深度（前景/中景/远景）、不同朝向、室内外、车内车外、门内门外 → 独立场景。
- 同一物理空间的子区域，若角色在不同分镜中分别活动（如车库里的工具箱角落/暖气片旁/脏衣篓区）→ 独立场景。
- 不要把整个大场景归并成一项。角色移动经过的每个有视觉差异的子空间都要拆。
- 上限 8 场景，挑视觉差异最明显的。若故事只有单一空间无子区域，可只返回 1 个。

# 绝对禁止（关键）
- 场景是**空镜环境**，只描述空间本身。禁止写任何角色、动物、人物的动作/姿态/表情（如"玛丽莎坐在上面写作业""八哥犬打鼾""教授飞落"）。
- 禁止写剧情事件/瞬时状态（如"炸丸子膨胀""冲击波爆发""机器喷吐火花"）。同一区域的不同事件状态（如"机器区"与"机器区爆炸瞬间"）不拆成两个场景，只保留一个空镜环境。
- 禁止写视角/尺度变化（如"巨人视角""俯视"）。视角属于分镜，不属于场景。
- 角色位置信息由角色资产的"初始位置"承担，场景不写谁在哪里做什么。

# 描述要求
每场景描述须含：空间结构（室内外/布局/边界）、主要材质（木/石/金属/布料）、光线（时段/光源方向/明暗）、氛围色调、与其它场景的空间关系（如"车库西侧，暖气片所在角落"）。可提及场景内的固定家具/装置（如暖气片/工具箱/脏衣篓作为环境陈设），但不要写角色在其中的活动。

# 输出格式
严格输出 JSON，不要解释：

```json
{{
  "scenes": [{{"name": "场景名（含子区域）", "description": "中文视觉描述。空间关系：xxx"}}]
}}
```
"""


STORY_PROP_PROMPT = """# 角色任务
你是关键剧情道具资产分析师。从故事中识别需要制画的关键道具。

# 输入故事
{story}

# 三测试（满足至少一项即提取）
1. 可移动性：能被角色拿起/移动/操作的物品（如炸丸子/吐司/天线叉子）。固定装置（如墙壁/窗户）不算。
2. 可替换性：换一个会影响剧情的物品（如红色按钮是触发器，不可替换）。
3. 贯穿剧情：在 2 个以上分镜出现的物品（如食物放大器贯穿全程）。

# 绝对排除（关键）
- 角色与活物不算道具（人物/动物/拟人形象归角色类，如"八哥犬土豆"不是道具）。
- 角色穿着/随身物不算道具（衣服/鞋子/发夹/项圈是角色外观的一部分，归角色描述）。
- 场景家具/固定陈设不算道具（桌椅/工具箱/脏衣篓/暖气片归场景描述）。
- 只提取独立的、可单独制画的剧情物品。

# 描述要求
- 描述道具的**默认/原始形态**，禁止写剧情中的瞬时状态（如"此刻膨胀到沙袋大小""因巨人而撑大""被放大后"）。资产图是通用参考，不是某一镜的状态。
- 每道具描述须含：主体结构、材质、颜色、表面处理、装饰细节、数量关系。
- 禁止写用途/剧情/人物/光影氛围/动作。白底居中资产图视角。中文，具体可画。
- 上限 8 道具。若故事确实无关键道具，返回空数组。

# 输出格式
严格输出 JSON，不要解释：

```json
{{
  "props": [{{"name": "道具名", "description": "中文视觉描述"}}]
}}
```
"""


def build_story_character_prompt(story: str) -> str:
    return STORY_CHARACTER_PROMPT.format(story=story)


def build_story_location_prompt(story: str) -> str:
    return STORY_LOCATION_PROMPT.format(story=story)


def build_story_prop_prompt(story: str) -> str:
    return STORY_PROP_PROMPT.format(story=story)


# =====================================================================
# 2. 角色四视角参考图（新增，替代原硬编码串）
# =====================================================================
CHARACTER_SHEET_PROMPT = """{description}. Character reference sheet, single image with 4 views arranged in a 2x2 grid: top-left front head close-up portrait, top-right front full-body, bottom-left side profile full-body, bottom-right back full-body. Same character, consistent appearance, hairstyle, outfit and identifying marks across all 4 views. Natural neutral facial expression, looking at camera, normal healthy body proportions. Plain neutral light-grey background, soft even studio lighting, no harsh shadows. No text, no labels, no watermarks, no captions. Crisp clean professional quality."""


def build_character_sheet_prompt(description: str) -> str:
    """角色描述 → 四视角参考图 prompt（英文，拼到 prefix 后送图模型）。"""
    return CHARACTER_SHEET_PROMPT.format(description=description.strip())


# =====================================================================
# 3. 分镜生成（升级：输出含景别/运镜/光照 + 衔接铁律 + 资产白名单）
# =====================================================================
STORY_SCENECUT_PROMPT = """# 角色任务
你是一位绘本分镜师。把用户输入的故事文本拆成{count_hint}，每个分镜包含旁白、景别、运镜、光照、绑定资产、原文摘录六个字段。

# 输入故事
{story}

# 已知资产库（角色/场景/道具清单，绑定字段必须从中选择）
{assets}

# 分镜划分核心原则（画面驱动，非字数驱动）
- 每个分镜 = 一个独立可画的画面叙事单元。判断标准：这段原文能否用**一张画面**讲清楚？
- 一个分镜只画一个动作/一个场景/一个情绪点。若原文一段包含多个动作变化、场景切换、或时间跳跃，必须拆成多个分镜。
- 反之，若原文几句都在描述同一画面的同一动作（只是细节补充），合并为一个分镜，旁白逐字截取这些句子。
- 不要为凑数量而拆，也不要为省数量而合并。画面撑不起就拆，画面够撑就合。

# 分镜要求
- 旁白（narration）：必须是原文的**逐字截取**（截取原文一句或连续几句，可去掉引号，但不可改写、概括、翻译、杜撰任何字词）。长度不限，按画面需要的原文片段实际长度为准。
- 景别（shot_type）：从【特写 / 近景 / 中景 / 全景 / 远景】中选一个。
- 运镜（camera_move）：从【固定 / 推 / 拉 / 横移 / 跟随 / 仰俯】中选一个。
- 光照（lighting）：从【顺光 / 逆光 / 侧光 / 顶光 / 暖光 / 冷光】中选一个。
- 绑定资产：
  - characters：本镜出场的角色名数组，必须从资产库角色名中一字不差地复制。
  - scene：本镜发生的场景名，必须从资产库场景名中一字不差地复制（仅 1 个）。**选择最精确的子场景**（如角色在暖气片旁就选"暖气片角落"而非笼统的"车库"）。
  - props：本镜出场的道具名数组，必须从资产库道具名中一字不差地复制。
- source_text：从原文逐字摘录本镜对应的完整片段（不可改写、不可杜撰）。

# 原文忠实铁律（最高优先级）
- narration 必须是原文逐字截取，禁止任何改写/概括/翻译/杜撰。
- source_text 必须是原文逐字摘录，不可篡改一字。
- 所有分镜连起来要覆盖故事完整情节，顺序与原文一致。
- 衔接铁律：相邻分镜的角色/场景保持连续性，景别与运镜方向不跳轴，动作方向连贯。

# 资产白名单法则
- characters / scene / props 的值必须与资产库中的名称完全一致，禁止拼接、自创、修改。
- 资产库为空时，characters/scene/props 返回空数组/空串。

# 输出格式
严格输出以下 JSON，不要任何解释文字：

```json
{{
  "scenes": [
    {{"narration": "原文逐字截取", "shot_type": "景别", "camera_move": "运镜", "lighting": "光照", "characters": ["角色名"], "scene": "场景名", "props": ["道具名"], "source_text": "原文逐字摘录"}}
  ]
}}
```
"""


def build_story_extraction_prompt(story: str) -> str:
    """故事 → 资产库提取 prompt（结构化 JSON）。"""
    return STORY_EXTRACTION_PROMPT.format(story=story)


def build_story_scenecut_prompt(
    story: str,
    n_scenes: int | None,
    assets: str,
    min_words: int = 10,
    max_words: int = 200,
) -> str:
    """故事 → 分镜 prompt。分镜数与字数不限，按画面叙事单元分。
    min_words/max_words 保留供后端默认值兼容，prompt 内不再硬约束字数。"""
    if n_scenes:
        count_hint = f" {n_scenes} 个分镜"
    else:
        count_hint = " 若干分镜（数量不限，按画面叙事单元划分，见下方规则）"
    return STORY_SCENECUT_PROMPT.format(
        story=story,
        count_hint=count_hint,
        assets=assets or "（无）",
    )


# =====================================================================
# 4. 分镜图片生成（新增，绘本专用，替代通用 IMAGE_PROMPT_GENERATION_PROMPT）
# =====================================================================
STORY_PANEL_IMAGE_PROMPT = """You are a professional children's picture-book illustrator. Generate ONE detailed English image prompt for ONE storyboard panel.

# Panel data (JSON)
{panel_json}

The panel JSON contains: narration (story beat), composition (who/what/where), shot_type, camera_move, lighting, and bound_assets with visual descriptions of the characters/scene/props appearing in this shot.

# Reference image usage rules
- Character references (provided separately as img2img inputs): use to keep identity, hairstyle, outfit, identifying marks consistent. Do not copy the 2x2 grid layout — extract the single character view that fits this shot.
- Scene references: use only for atmosphere/layout/color mood. Repaint the background to match this shot's angle and shot type. Do not paste the reference background directly.
- For close-up/detail shots, use blurred or partial backgrounds.
- Background must be repainted according to shot type and angle, not copied from reference.

# Photography rules (must follow the panel's shot_type/camera_move/lighting)

# Absolute constraints
- Output ONE image prompt for ONE single shot. No collage, no multi-frame.
- NO text in the image: no labels, no numbers, no captions, no watermarks, no subtitles, no in-image writing of any kind.
- Aspect ratio must match the target frame; do not let reference aspect ratios leak.
- Style must stay consistent with the provided character/scene reference art.

# Output
A single concise English image prompt (60-120 words): scene + character action + emotion + atmosphere, incorporating the bound_assets visual descriptions, photography rules, and reference-consistency constraints above. No JSON, no explanation, just the prompt text."""


def build_story_panel_image_prompt(panel: dict) -> str:
    """单分镜 → 英文插图 prompt（绘本专用，注入分镜 JSON + 绑定资产描述 + 参考图规则）。

    panel: {"narration","composition","shot_type","camera_move","lighting",
            "characters":[],"scene":"","props":[], "asset_descriptions":{...}}
    """
    import json
    return STORY_PANEL_IMAGE_PROMPT.format(
        panel_json=json.dumps(panel, ensure_ascii=False, indent=2)
    )


# 批量版：一次传所有分镜，LLM 一次返回所有英文 prompt（替代逐镜 N 次调用）
STORY_PANEL_IMAGE_BATCH_PROMPT = """You are a professional children's picture-book illustrator. Generate ONE detailed English image prompt for EACH storyboard panel below. Output {count} prompts in order.

# Panels (JSON array, one object per panel)
{panels_json}

Each panel has: narration (story beat), composition (who/what/where), shot_type, camera_move, lighting.

# Reference images (provided separately as img2img inputs per panel)
- Character references: keep identity, hairstyle, outfit, identifying marks consistent with reference sheets. Do not copy the 2x2 grid layout — extract the single character view that fits this shot.
- Scene references: use only for atmosphere/layout/color mood. Repaint the background to match this shot's angle and shot type. Do not paste the reference background directly.
- For close-up/detail shots, use blurred or partial backgrounds.

# Absolute constraints (apply to every panel)
- ONE image prompt per panel. No collage, no multi-frame.
- NO text in the image: no labels, no numbers, no captions, no watermarks, no subtitles.
- Style must stay consistent with the provided character/scene reference art.
- Follow each panel's photography rules (shot_type/camera_move/lighting).

# Output
Strictly output JSON, no explanation:
```json
{{
  "image_prompts": ["english prompt for panel 1", "english prompt for panel 2", ...]
}}
```
The array must contain exactly {count} prompts, one per panel, in order."""


def build_story_panel_image_batch_prompt(panels: list) -> str:
    """批量分镜 → 一次 LLM 调用返回所有英文插图 prompt。

    panels: [{"narration","composition","shot_type","camera_move","lighting"}, ...]
    """
    import json
    panels_json = json.dumps(
        [{"narration": p.get("narration", ""), "composition": p.get("composition", ""),
          "shot_type": p.get("shot_type", ""), "camera_move": p.get("camera_move", ""),
          "lighting": p.get("lighting", "")} for p in panels],
        ensure_ascii=False, indent=2,
    )
    return STORY_PANEL_IMAGE_BATCH_PROMPT.format(panels_json=panels_json, count=len(panels))


def assets_to_text(asset_library: dict) -> str:
    """把资产库 dict 渲染成给 LLM 看的文本清单。"""
    if not asset_library:
        return ""
    lines = []
    for kind in ("characters", "scenes", "props"):
        items = asset_library.get(kind, [])
        if not items:
            continue
        label = {"characters": "角色", "scenes": "场景", "props": "道具"}[kind]
        lines.append(f"【{label}】")
        for it in items:
            lines.append(f"- {it.get('name', '')}：{it.get('description', '')}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Self-check: every builder fully fills its named placeholders.
    # ponytail: {var} named placeholders must all be substituted; bare {} in JSON
    # examples are fine. Check for leftover {identifier} patterns only.
    import re
    _PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")

    story = "小兔子白白在森林里捡到一颗发光的种子，把它种在院子里。"

    p1 = build_story_extraction_prompt(story)
    assert not _PLACEHOLDER.search(p1), "extraction prompt has unfilled placeholders"
    assert "小兔子" in p1

    # 分类型提取 prompt
    pc = build_story_character_prompt(story)
    assert not _PLACEHOLDER.search(pc), "character prompt has unfilled placeholders"
    pl = build_story_location_prompt(story)
    assert not _PLACEHOLDER.search(pl), "location prompt has unfilled placeholders"
    pp = build_story_prop_prompt(story)
    assert not _PLACEHOLDER.search(pp), "prop prompt has unfilled placeholders"

    p2 = build_character_sheet_prompt("白色小兔子，长耳朵，红眼睛")
    assert not _PLACEHOLDER.search(p2), "character sheet prompt has unfilled placeholders"
    assert "2x2 grid" in p2

    lib = {
        "characters": [{"name": "白白", "description": "白色小兔子"}],
        "scenes": [{"name": "森林", "description": "阳光森林"}],
        "props": [{"name": "发光种子", "description": "发金光的种子"}],
    }
    p3 = build_story_scenecut_prompt(story, n_scenes=4, assets=assets_to_text(lib))
    assert not _PLACEHOLDER.search(p3), "scenecut prompt has unfilled placeholders"
    assert "shot_type" in p3 and "白白" in p3
    p3b = build_story_scenecut_prompt(story, n_scenes=None, assets=assets_to_text(lib))
    assert not _PLACEHOLDER.search(p3b), "auto-scenecut has unfilled placeholders"

    p4 = build_story_panel_image_prompt({
        "narration": "白白走进森林", "composition": "白白在森林里",
        "shot_type": "中景", "camera_move": "跟随", "lighting": "暖光",
        "characters": ["白白"], "scene": "森林", "props": [],
        "asset_descriptions": {"白白": "白色小兔子", "森林": "阳光森林"},
    })
    assert not _PLACEHOLDER.search(p4), "panel image prompt has unfilled placeholders"
    assert "白白" in p4 or "森林" in p4

    p5 = build_story_panel_image_batch_prompt([
        {"narration": "白白走进森林", "composition": "白白在森林", "shot_type": "中景", "camera_move": "跟随", "lighting": "暖光"},
        {"narration": "种子发芽", "composition": "种子在土里", "shot_type": "特写", "camera_move": "固定", "lighting": "顺光"},
    ])
    assert not _PLACEHOLDER.search(p5), "batch panel prompt has unfilled placeholders"
    assert "2" in p5 and "image_prompts" in p5

    print("story_prompts self-check OK")
