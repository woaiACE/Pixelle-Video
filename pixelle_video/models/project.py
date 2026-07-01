# Copyright (C) 2025 AIDC-AI
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Project model for story-illustration project-level management.

A project is a persistent, editable story workspace. Each project owns its
story text, asset library, scenes, and one or more video-generation runs.
See SPEC_story_project.md §2.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class Project:
    """A story-illustration project (facts-of-record lives in project.json)."""
    project_id: str
    title: str
    created_at: str
    updated_at: str
    current_stage: str = "story"  # story | assets | storyboard | video
    story_provider: Optional[str] = None
    art_style_key: Optional[str] = None
    prompt_prefix: Optional[str] = None
    style_params: Dict[str, Any] = field(default_factory=dict)
    auto_scenes: bool = True
    n_scenes: Optional[int] = None
    frame_template: Optional[str] = None
    brand: Optional[str] = None
    stages_ready: Dict[str, bool] = field(
        default_factory=lambda: {"story": False, "assets": False, "storyboard": False, "video": False}
    )
    latest_run_id: Optional[str] = None
    cover_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Project":
        # ponytail: tolerate older project.json missing newer fields
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})
