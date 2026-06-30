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
Image prompt generation template

For generating image prompts from narrations.
"""

import json
from typing import List, Optional


# ==================== PRESET IMAGE STYLES ====================
# Predefined visual styles, grouped by 1-level category.
#   `prefix`: rendering keywords, prepended to each LLM-generated prompt (see build_image_prompt).
#   `hint`:   creative guidance injected into LLM reasoning (see build_image_prompt_prompt),
#             shapes SCENE COMPOSITION so the base description fits the style. Distinct from
#             prefix keywords, so no duplication when both are applied.

IMAGE_STYLE_PRESETS = {
    # 极简讲解
    "stick_figure": {
        "category": "极简讲解", "category_en": "Minimal Explainer",
        "name": "火柴人极简", "name_en": "Stick Figure",
        "prefix": "Minimalist black-and-white matchstick figure style illustration, clean lines, simple sketch style",
        "hint": "Compose extremely simple scenes: one or two figures with minimal props. Avoid complex lighting, detailed textures, or crowded compositions. Favor literal, direct imagery over symbolic metaphors.",
    },
    "minimal": {
        "category": "极简讲解", "category_en": "Minimal Explainer",
        "name": "极简抽象", "name_en": "Minimalist Abstract",
        "prefix": "Minimalist abstract illustration, geometric shapes, clean composition, soft pastel colors",
        "hint": "Compose clean scenes with a single focal subject and generous negative space. Avoid clutter and multiple focal points. Favor literal, direct imagery over symbolic metaphors.",
    },
    # 教学育儿
    "flat_cartoon": {
        "category": "教学育儿", "category_en": "Teaching & Kids",
        "name": "扁平卡通", "name_en": "Flat Cartoon",
        "prefix": "Flat vector illustration, bold outlines, vibrant flat colors, modern cartoon style, clean shapes",
        "hint": "Compose bold, readable scenes with clear subjects in everyday situations. Strong silhouettes, simple backgrounds. Favor literal, direct imagery.",
    },
    "watercolor": {
        "category": "教学育儿", "category_en": "Teaching & Kids",
        "name": "水彩绘本", "name_en": "Watercolor Storybook",
        "prefix": "Soft watercolor illustration, children's storybook style, gentle pastel tones, hand-painted texture",
        "hint": "Compose gentle, warm everyday moments with a soft emotional tone. Favor literal, direct imagery.",
    },
    # 故事剧情
    "pixar3d": {
        "category": "故事剧情", "category_en": "Story & Drama",
        "name": "3D 皮克斯", "name_en": "3D Pixar",
        "prefix": "3D Pixar style render, soft cinematic lighting, smooth subsurface scattering, friendly stylized characters",
        "hint": "Compose expressive character-driven scenes with clear emotion and dynamic poses.",
    },
    "anime": {
        "category": "故事剧情", "category_en": "Story & Drama",
        "name": "日系动漫", "name_en": "Anime",
        "prefix": "Anime key visual style, cel shading, clean line art, vibrant colors, Studio Ghibli inspired atmosphere",
        "hint": "Compose dramatic, atmospheric scenes with strong mood and dynamic composition.",
    },
    # 科技产品
    "isometric": {
        "category": "科技产品", "category_en": "Tech & Product",
        "name": "等距 3D", "name_en": "Isometric 3D",
        "prefix": "Isometric 3D illustration, low-poly stylized, soft ambient occlusion, miniature diorama look",
        "hint": "Compose scenes as small setups viewed from above at an angle — objects, rooms, workspaces, multi-object arrangements. Favor literal, direct imagery over single-character portraits.",
    },
    # 潮流营销
    "pop_art": {
        "category": "潮流营销", "category_en": "Trendy Marketing",
        "name": "复古波普", "name_en": "Retro Pop Art",
        "prefix": "Retro pop art style, bold halftone dots, high-contrast saturated colors, 1960s comic aesthetic",
        "hint": "Compose bold high-contrast scenes with strong silhouettes and punchy subjects.",
    },
    # 知识科普
    "ink_line": {
        "category": "知识科普", "category_en": "Knowledge & Science",
        "name": "手绘线稿", "name_en": "Ink Line Art",
        "prefix": "Hand-drawn ink line illustration, monochrome sketch, cross-hatching shading, loose expressive strokes",
        "hint": "Compose scenes that read clearly in monochrome line work — strong outlines, simple shading, do not rely on color. Favor literal, direct imagery.",
    },
    "concept": {
        "category": "知识科普", "category_en": "Knowledge & Science",
        "name": "概念视觉", "name_en": "Conceptual Visual",
        "prefix": "Conceptual visual metaphor, symbolic elements, thought-provoking imagery, artistic interpretation",
        "hint": "Use symbolic metaphors to visualize abstract concepts (paths for choices, chains for constraints, etc.). Thought-provoking, artistic interpretation.",
    },
    # 商业带货
    "corporate": {
        "category": "商业带货", "category_en": "Commerce",
        "name": "商务扁平", "name_en": "Corporate Flat",
        "prefix": "Corporate flat design illustration, geometric shapes, professional muted palette, clean minimalist style",
        "hint": "Compose professional business scenes — people in work settings, productivity concepts. Favor literal, clear representations over symbolic metaphors.",
    },
    # 情感氛围
    "warm_realistic": {
        "category": "情感氛围", "category_en": "Mood & Atmosphere",
        "name": "暖光写实", "name_en": "Warm Realistic",
        "prefix": "Warm cinematic illustration, soft golden-hour lighting, semi-realistic painterly style, depth of field",
        "hint": "Compose warm, cinematic, emotionally resonant scenes with a golden-hour mood.",
    },
}


def get_style_hint_by_prefix(prefix: str) -> Optional[str]:
    """Return the style hint whose prefix matches the given prefix string.

    Used by the pipeline to inject style-aware guidance into LLM prompt generation
    when the user selected a preset (the prefix string is all the pipeline receives).
    Returns None for a hand-edited prefix that matches no preset — falls back to
    the default (metaphor-leaning) generation behavior.
    """
    if not prefix:
        return None
    target = prefix.strip()
    for preset in IMAGE_STYLE_PRESETS.values():
        if preset["prefix"].strip() == target:
            return preset.get("hint")
    return None

# Default preset
DEFAULT_IMAGE_STYLE = "stick_figure"


IMAGE_PROMPT_GENERATION_PROMPT = """# Role Definition
You are a professional visual creative designer, skilled at creating expressive and symbolic image prompts for video scripts, transforming abstract concepts into concrete visual scenes.

# Core Task
Based on the existing video script, create corresponding **English** image prompts for each storyboard's "narration content", ensuring visual scenes perfectly match the narrative content and enhance audience understanding and memory.

**Important: The input contains {narrations_count} narrations. You must generate one corresponding image prompt for each narration, totaling {narrations_count} image prompts.**

{style_section}
# Input Content
{narrations_json}

# Output Requirements

## Image Prompt Specifications
- Language: **Must use English** (for AI image generation models)
- Description structure: scene + character action + emotion + symbolic elements
- Description length: Ensure clear, complete, and creative descriptions (recommended 50-100 English words)

## Visual Creative Requirements
- Each image must accurately reflect the specific content and emotion of the corresponding narration
- Use symbolic techniques to visualize abstract concepts (e.g., use paths to represent life choices, chains to represent constraints, etc.)
- Scenes should express rich emotions and actions to enhance visual impact
- Highlight themes through composition and element arrangement, avoid overly literal representations

## Key English Vocabulary Reference
- Symbolic elements: symbolic elements
- Expression: expression / facial expression
- Action: action / gesture / movement
- Scene: scene / setting
- Atmosphere: atmosphere / mood

## Visual and Copy Coordination Principles
- Images should serve the copy, becoming a visual extension of the copy content
- Avoid visual elements unrelated to or contradicting the copy content
- Choose visual presentation methods that best enhance the persuasiveness of the copy
- Ensure the audience can quickly understand the core viewpoint of the copy through images

## Creative Guidance
1. **Phenomenon Description Copy**: Use intuitive scenes to represent social phenomena
2. **Cause Analysis Copy**: Use visual metaphors of cause-and-effect relationships to represent internal logic
3. **Impact Argumentation Copy**: Use consequence scenes or contrast techniques to represent the degree of impact
4. **In-depth Discussion Copy**: Use concretization of abstract concepts to represent deep thinking
5. **Conclusion Inspiration Copy**: Use open-ended scenes or guiding elements to represent inspiration

# Output Format
Strictly output in the following JSON format, **image prompts must be in English**:

```json
{{
  "image_prompts": [
    "[detailed English image prompt following the style requirements]",
    "[detailed English image prompt following the style requirements]"
  ]
}}
```

# Important Reminders
1. Only output JSON format content, do not add any explanations
2. Ensure JSON format is strictly correct and can be directly parsed by the program
3. Input is {{"narrations": [narration array]}} format, output is {{"image_prompts": [image prompt array]}} format
4. **The output image_prompts array must contain exactly {narrations_count} elements, corresponding one-to-one with the input narrations array**
5. **Image prompts must use English** (for AI image generation models)
6. Image prompts must accurately reflect the specific content and emotion of the corresponding narration
7. Each image must be creative and visually impactful, avoid being monotonous
8. Ensure visual scenes can enhance the persuasiveness of the copy and audience understanding

Now, please create {narrations_count} corresponding **English** image prompts for the above {narrations_count} narrations. Only output JSON, no other content.
"""


def build_image_prompt_prompt(
    narrations: List[str],
    min_words: int,
    max_words: int,
    style_hint: Optional[str] = None
) -> str:
    """
    Build image prompt generation prompt

    Args:
        narrations: List of narrations
        min_words: Minimum word count
        max_words: Maximum word count
        style_hint: Optional creative guidance for the target visual style. When provided,
            injected into the LLM prompt so scene composition fits the style (e.g. simple
            scenes for stick figure, literal imagery for teaching). Rendering keywords are
            still applied later via prompt_prefix — hint only shapes composition, no overlap.

    Returns:
        Formatted prompt for LLM

    Example:
        >>> build_image_prompt_prompt(narrations, 50, 100)
    """
    narrations_json = json.dumps(
        {"narrations": narrations},
        ensure_ascii=False,
        indent=2
    )

    style_section = (
        f"# Target Visual Style\nCompose each scene to fit this style. This OVERRIDES the "
        f"default symbolic-metaphor tendency where it conflicts:\n{style_hint}\n"
        if style_hint else ""
    )

    return IMAGE_PROMPT_GENERATION_PROMPT.format(
        narrations_json=narrations_json,
        narrations_count=len(narrations),
        min_words=min_words,
        max_words=max_words,
        style_section=style_section
    )

