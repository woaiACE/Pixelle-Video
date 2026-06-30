"""Qwen TTS 语音合成 — REST API（与语音工作台共用 voice design 模型）"""

import os
import json
import base64
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)

# 百炼默认配置（华北2 北京）
BAILIAN_WORKSPACE_ID = "ws-isj5kj6v0r14ktey"
BAILIAN_BASE_URL = f"https://{BAILIAN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com"

# 与语音工作台使用相同模型（voice design 模型支持 REST API + 自定义音色）
DEFAULT_MODEL = "qwen3-tts-vd-2026-01-26"
DEFAULT_FORMAT = "wav"


def _get_api_key() -> str:
    """从系统配置或环境变量读取 DashScope API Key"""
    try:
        from pixelle_video.config import config_manager
        key = config_manager.get_api_providers_config().get("voice_design", {}).get("api_key", "")
        if key:
            return key
    except Exception:
        pass
    return os.getenv("DASHSCOPE_API_KEY", "")


def synthesize(text: str, voice: str, output_path: str = "",
               model: str = DEFAULT_MODEL,
               response_format: str = DEFAULT_FORMAT) -> str:
    """调用 Qwen TTS REST API 合成语音，返回音频文件路径

    与语音工作台共用 voice design 模型和 Key。
    文档：https://help.aliyun.com/zh/model-studio/voice-design-api-references

    Args:
        text: 待合成文本
        voice: 音色 ID（语音工作台创建的 voice_xxx）
        output_path: 输出音频路径（默认自动生成）
        model: TTS 模型
        response_format: 音频格式

    Returns:
        音频文件路径
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("请先在「系统配置 → API 媒体模型 → 语音设计」中填写 DashScope API Key")

    url = f"{BAILIAN_BASE_URL}/api/v1/services/aigc/multimodal-generation/generation"

    payload = {
        "model": model,
        "input": {
            "text": text,
            "voice": voice
        }
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # 指数退避重试（429 限流/5xx 服务错误）
    import time, random
    max_retries = 3
    for attempt in range(max_retries):
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code == 200:
            break
        if resp.status_code in (429, 500, 502, 503) and attempt < max_retries - 1:
            wait = (2 ** attempt) + random.uniform(0, 1)  # ponytail: jitter 避免同时重试
            logger.warning(f"Qwen TTS {resp.status_code}, retry {attempt + 1}/{max_retries - 1} after {wait:.1f}s")
            time.sleep(wait)
        else:
            raise RuntimeError(f"Qwen TTS 失败 HTTP {resp.status_code}: {resp.text[:300]}")

    result = resp.json()
    logger.info(f"Qwen TTS raw response keys: {list(result.keys())}")
    if "output" in result:
        logger.info(f"Qwen TTS output keys: {list(result['output'].keys())}")

    # 解析响应 — 尝试多种可能的格式
    audio_bytes = None

    # 格式1: output.choices[0].message.content[0].audio (MultiModal 标准格式)
    try:
        content_list = result["output"]["choices"][0]["message"]["content"]
        for item in content_list:
            if "audio" in item:
                audio_b64 = item["audio"]
                audio_bytes = base64.b64decode(audio_b64)
                break
    except (KeyError, IndexError, TypeError):
        pass

    # 格式2: output.audio.data (base64)
    if not audio_bytes:
        try:
            audio_b64 = result["output"]["audio"]["data"]
            audio_bytes = base64.b64decode(audio_b64)
        except (KeyError, IndexError, TypeError):
            pass

    # 格式3: output.audio.url (下载)
    if not audio_bytes:
        try:
            audio_url = result["output"]["audio"]["url"]
            dl = requests.get(audio_url, timeout=60)
            if dl.status_code == 200:
                audio_bytes = dl.content
        except (KeyError, IndexError, TypeError):
            pass

    if not audio_bytes:
        raise RuntimeError(f"无法解析 TTS 响应: {json.dumps(result, ensure_ascii=False)[:800]}")

    request_id = result.get("request_id", "unknown")
    logger.info(
        f"Qwen TTS: voice={voice}, request_id={request_id}, "
        f"text_len={len(text)}, audio_size={len(audio_bytes)}"
    )

    if not output_path:
        import uuid
        output_path = f"output/qwen_tts_{uuid.uuid4().hex[:8]}.{response_format}"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return output_path
