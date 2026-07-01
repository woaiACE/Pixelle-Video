# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""
Qwen 语音设计工作台 - 用自然语言创建自定义音色
"""

import os
import json
import base64
import logging
import requests
from pathlib import Path
import streamlit as st

logger = logging.getLogger(__name__)

# === 配置 ===
VOICE_DESIGN_DIR = Path(__file__).parent.parent / "voice_designs"
VOICE_DESIGN_DIR.mkdir(exist_ok=True)

DEFAULT_MODEL = "qwen3-tts-vd-2026-01-26"
PREVIEW_TEXT = "大家好，欢迎来到我们的直播间！今天给大家推荐的这款产品真的超级好用。"

# 百炼 API 默认配置（华北2 北京）
BAILIAN_WORKSPACE_ID = "ws-isj5kj6v0r14ktey"
BAILIAN_BASE_URL = f"https://{BAILIAN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com"

st.set_page_config(page_title="语音设计工作台", page_icon="🔊", layout="wide")


def load_saved_voices():
    """加载已保存的音色列表"""
    voices = []
    try:
        for f in VOICE_DESIGN_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    data["id"] = f.stem
                    voices.append(data)
            except Exception as e:
                logger.warning(f"加载音色失败 {f}: {e}")
    except Exception:
        pass
    return sorted(voices, key=lambda x: x.get("created_at", ""), reverse=True)


def generate_voice_prompt_with_llm(description: str) -> tuple[str, str]:
    """用LLM生成专业的voice_prompt和音色名称"""
    try:
        from pixelle_video.config import config_manager
        from openai import OpenAI

        cfg = config_manager.config
        if not cfg.llm.api_key or not cfg.llm.base_url:
            raise RuntimeError("请先在「系统配置」中设置 LLM API Key 和 Base URL")
        if not cfg.llm.model:
            raise RuntimeError("请先在「系统配置」中选择 LLM 模型")

        client = OpenAI(api_key=cfg.llm.api_key, base_url=cfg.llm.base_url)

        system_prompt = """你是专业的语音音色设计专家。用户会输入简单的声音描述，请你生成：
1. 专业详细的voice_prompt（200字以内，包含性别、年龄、音调、语速、情感、特点、适用场景）
2. 一个简短有记忆点的音色名称（4-8个字）

输出格式：
名称: [音色名称]
描述: [专业的voice_prompt]"""

        resp = client.chat.completions.create(
            model=cfg.llm.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请为这个描述设计音色：{description}"}
            ],
            temperature=0.7
        )

        # 健壮的空值检查
        if not resp.choices or not resp.choices[0].message or not resp.choices[0].message.content:
            return "自定义音色", description

        text = resp.choices[0].message.content.strip()
        if not text:
            return "自定义音色", description

        name = "自定义音色"
        prompt = description

        for line in text.split("\n"):
            if line.startswith("名称:") or line.startswith("名称："):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    name = parts[1].split("：", 1)[-1].strip()
            if line.startswith("描述:") or line.startswith("描述："):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    prompt = parts[1].split("：", 1)[-1].strip()

        return name, prompt
    except Exception as e:
        logger.error(f"LLM生成失败: {e}")
        return "自定义音色", description


def _get_dashscope_api_key() -> str:
    """从系统配置读取语音设计 API Key"""
    try:
        from pixelle_video.config import config_manager
        key = config_manager.get_api_providers_config().get("voice_design", {}).get("api_key", "")
        if key:
            return key
    except Exception:
        pass
    return os.getenv("DASHSCOPE_API_KEY", "")


def create_voice_design(voice_prompt: str, preferred_name: str, preview_text: str = PREVIEW_TEXT):
    """调用百炼API创建音色，返回(voice_id, preview_audio_data)"""
    api_key = _get_dashscope_api_key()
    if not api_key:
        raise RuntimeError("请在「系统配置 → API 媒体模型 → 语音设计」中填写 DashScope API Key")

    # ponytail: API only accepts [a-zA-Z0-9_] for preferred_name
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', preferred_name)
    if not safe_name or safe_name == '_':
        safe_name = "custom_voice"

    url = f"{BAILIAN_BASE_URL}/api/v1/services/audio/tts/customization"

    payload = {
        "model": "qwen-voice-design",
        "input": {
            "action": "create",
            "target_model": DEFAULT_MODEL,
            "preferred_name": safe_name,
            "voice_prompt": voice_prompt,
            "preview_text": preview_text
        },
        "parameters": {
            "sample_rate": 24000,
            "response_format": "wav"
        }
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"API错误 {resp.status_code}: {resp.text[:200]}")

    result = resp.json()
    voice_id = result["output"]["voice"]
    preview_b64 = result["output"]["preview_audio"]["data"]
    preview_audio = base64.b64decode(preview_b64)

    return voice_id, preview_audio


def save_voice_design(name: str, voice_prompt: str, voice_id: str, preview_audio: bytes):
    """保存音色设计到本地"""
    import time
    import re
    timestamp = int(time.time())
    file_id = f"voice_{timestamp}"

    # 用音色名称命名音频文件（清理非法字符）
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', name) or "custom_voice"
    audio_filename = f"{safe_name}.wav"
    audio_path = VOICE_DESIGN_DIR / audio_filename
    with open(audio_path, "wb") as f:
        f.write(preview_audio)

    # 保存元数据
    meta = {
        "name": name,
        "voice_prompt": voice_prompt,
        "voice_id": voice_id,
        "model": DEFAULT_MODEL,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "audio_file": audio_filename
    }
    meta_path = VOICE_DESIGN_DIR / f"{file_id}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return file_id


# === UI ===
st.title("🔊 Qwen 语音设计工作台")
st.caption("用自然语言描述，AI帮你生成自定义音色，支持在线试听和本地管理")

# 侧边栏：描述参考
with st.sidebar:
    st.header("💡 描述参考")
    st.markdown("- 年轻活泼的女性，语速快，上扬语调，适合直播带货")
    st.markdown("- 沉稳中年男性，低沉有磁性，适合新闻播报")
    st.markdown("- 温柔知性女声，30岁左右，适合有声书朗读")
    st.markdown("- 可爱8岁女童，略带稚气，适合动画配音")

# === 主界面：新建音色 ===
with st.container(border=True):
    st.subheader("✨ 设计新音色")

    col1, col2 = st.columns([3, 1])
    with col1:
        user_input = st.text_area(
            "用自然语言描述你想要的声音",
            placeholder="例如：年轻活泼的女性声音，语速较快，带有明显的上扬语调，适合介绍时尚产品",
            height=80
        )
    with col2:
        preview_text = st.text_input("预览文本", value=PREVIEW_TEXT, help="用于生成试听音频的文本")

    col_a, col_b = st.columns([1, 1])
    with col_a:
        ai_generate = st.button("🤖 AI 自动优化描述", width="stretch", type="secondary")
    with col_b:
        create_btn = st.button("🎨 生成音色并试听", width="stretch", type="primary")

    # AI 优化
    if ai_generate and user_input:
        with st.spinner("AI 正在优化声音描述..."):
            name, optimized_prompt = generate_voice_prompt_with_llm(user_input)
            st.session_state.ai_name = name
            st.session_state.ai_prompt = optimized_prompt
            st.rerun()

    # 显示AI优化结果
    if "ai_name" in st.session_state:
        st.success(f"✅ AI 推荐名称：**{st.session_state.ai_name}**")
        with st.expander("查看优化后的完整描述", expanded=True):
            st.code(st.session_state.ai_prompt, language=None)

    # 手动编辑区
    final_name = st.text_input("音色名称", value=st.session_state.get("ai_name", "自定义音色"))
    final_prompt = st.text_area(
        "最终 voice_prompt（可手动修改）",
        value=st.session_state.get("ai_prompt", user_input),
        height=100
    )

    # 生成音色
    if create_btn and final_prompt:
        with st.spinner("正在生成音色，约需5-10秒..."):
            try:
                voice_id, preview_audio = create_voice_design(final_prompt, final_name, preview_text)
                st.session_state.current_voice_id = voice_id
                st.session_state.current_preview = preview_audio
                st.session_state.current_name = final_name
                st.session_state.current_prompt = final_prompt
                st.rerun()
            except Exception as e:
                st.error(f"生成失败：{str(e)}")

    # 显示结果 + 保存
    if "current_preview" in st.session_state:
        st.markdown("---")
        st.subheader("🎵 试听结果")
        st.audio(st.session_state.current_preview, format="audio/wav")
        st.info(f"音色 ID: `{st.session_state.current_voice_id}`")

        download_name = f"{st.session_state.current_name}.wav"
        st.download_button(
            "⬇️ 下载音频",
            data=st.session_state.current_preview,
            file_name=download_name,
            mime="audio/wav",
            width="stretch"
        )

        if st.button("💾 保存到本地库", width="stretch"):
            file_id = save_voice_design(
                st.session_state.current_name,
                st.session_state.current_prompt,
                st.session_state.current_voice_id,
                st.session_state.current_preview
            )
            st.success(f"已保存！ID: {file_id}")
            st.balloons()

# === 音色库 ===
st.markdown("---")
st.subheader("📚 我的音色库")

saved_voices = load_saved_voices()
if not saved_voices:
    st.info("暂无保存的音色，快去创建第一个吧！")
else:
    cols = st.columns(3)
    for idx, voice in enumerate(saved_voices):
        with cols[idx % 3]:
            with st.container(border=True):
                st.markdown(f"**🎙️ {voice.get('name', '未命名')}**")
                st.caption(f"创建时间: {voice.get('created_at', 'N/A')}")
                st.caption(f"Voice ID: `{voice.get('voice_id', 'N/A')}`")
                with st.expander("查看描述", expanded=False):
                    st.write(voice.get("voice_prompt", ""))

                # 播放预览
                audio_file = VOICE_DESIGN_DIR / voice.get("audio_file", "")
                if audio_file.exists():
                    with open(audio_file, "rb") as f:
                        audio_bytes = f.read()
                        st.audio(audio_bytes, format="audio/wav")
                        st.download_button(
                            "⬇️ 下载",
                            data=audio_bytes,
                            file_name=f"{voice.get('name', 'voive')}.wav",
                            mime="audio/wav",
                            key=f"dl_{voice['id']}",
                            width="stretch"
                        )

                # 复制ID按钮
                if st.button("📋 复制 Voice ID", key=f"copy_{voice['id']}", width="stretch"):
                    st.code(voice.get("voice_id", ""))
                    st.toast("已复制到剪贴板")

st.markdown("---")
st.caption(f"音色库目录: {VOICE_DESIGN_DIR.absolute()}")
