# Copyright (C) 2025 AIDC-AI
# Licensed under the Apache License, Version 2.0
"""End-to-end smoke for story_illustration pipeline. Not a unit test — costs real API calls."""
import asyncio
import sys
import time

from loguru import logger

from pixelle_video.service import pixelle_video

STORY = (
    "小兔子白白在森林里捡到一颗发光的种子。她把种子种在家门口，每天浇水。"
    "几天后长出一棵会唱歌的小树，森林里的动物们都来听歌，白白再也不孤单了。"
)


def on_progress(ev):
    # ev is a ProgressEvent-like object
    try:
        logger.info(f"[progress] {ev}")
    except Exception:
        pass


async def main():
    logger.info("=== story_illustration e2e start ===")
    await pixelle_video.initialize()
    t0 = time.time()
    result = await pixelle_video.generate_video(
        text=STORY,
        pipeline="story_illustration",
        mode="generate",
        n_scenes=3,
        prompt_prefix="watercolor",
        media_workflow="api/gemini/gemini-3-pro-image",
        asset_provider="api/gemini/gemini-3-pro-image",
        frame_template="1080x1920/image_story.html",
        progress_callback=on_progress,
    )
    dt = time.time() - t0
    logger.info(f"=== DONE in {dt:.1f}s ===")
    logger.info(f"result type: {type(result).__name__}")
    # Print whatever fields exist
    for attr in ("video_path", "audio_path", "task_id", "title", "url", "success", "error"):
        v = getattr(result, attr, None)
        if v is not None:
            logger.info(f"  {attr}: {v}")
    # Fallback: dump repr
    logger.info(f"  repr: {result!r}")
    return result


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception(e)
        sys.exit(1)
