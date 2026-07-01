# Copyright (C) 2025 AIDC-AI
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""ProjectService: file-based CRUD for story-illustration projects.

Layout (see SPEC_story_project.md §2.1):
    output/projects/{project_id}/
        project.json   # facts of record
        story.txt
        assets.json
        scenes.json
        runs/{run_id}/...
        thumbnails/cover.png

No DB. pathlib + json only. Stage readiness is computed from file contents
on every save, mirroring waoowaoo's stage-readiness approach.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from loguru import logger

from pixelle_video.models.project import Project
from pixelle_video.utils.os_util import get_output_path, create_task_id


def _projects_root() -> Path:
    p = Path(get_output_path("projects"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _project_dir(project_id: str) -> Path:
    return _projects_root() / project_id


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ProjectService:
    """File-based project store. Stateless methods, instance for API symmetry."""

    # ---------- CRUD ----------

    @staticmethod
    def create_project(title: str) -> Project:
        project_id = create_task_id()  # ponytail: reuse existing id generator (timestamp + hex)
        d = _project_dir(project_id)
        (d / "runs").mkdir(parents=True, exist_ok=True)
        (d / "thumbnails").mkdir(exist_ok=True)
        now = _now_iso()
        project = Project(
            project_id=project_id,
            title=title or "未命名故事",
            created_at=now,
            updated_at=now,
        )
        ProjectService._write(project)
        (d / "story.txt").write_text("", encoding="utf-8")
        logger.info(f"Created project {project_id}: {project.title}")
        return project

    @staticmethod
    def load_project(project_id: str) -> Optional[Project]:
        f = _project_dir(project_id) / "project.json"
        if not f.exists():
            return None
        try:
            return Project.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning(f"Failed to load project {project_id}: {e}")
            return None

    @staticmethod
    def list_projects() -> List[Project]:
        out = []
        for d in _projects_root().iterdir():
            if not d.is_dir():
                continue
            p = ProjectService.load_project(d.name)
            if p:
                out.append(p)
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out

    @staticmethod
    def delete_project(project_id: str) -> bool:
        import shutil
        d = _project_dir(project_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            return True
        return False

    # ---------- stage content ----------

    @staticmethod
    def save_story(project_id: str, story: str, **fields) -> Project:
        """Persist story text + update project fields (title/style/etc)."""
        project = ProjectService._require(project_id)
        (d := _project_dir(project_id) / "story.txt").write_text(story or "", encoding="utf-8")
        for k, v in fields.items():
            if v is not None and k in Project.__dataclass_fields__:
                setattr(project, k, v)
        return ProjectService._finalize(project)

    @staticmethod
    def load_story(project_id: str) -> str:
        f = _project_dir(project_id) / "story.txt"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    @staticmethod
    def save_assets(project_id: str, assets: Dict[str, Any]) -> Project:
        project = ProjectService._require(project_id)
        (_project_dir(project_id) / "assets.json").write_text(
            json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ProjectService._finalize(project)

    @staticmethod
    def load_assets(project_id: str) -> Dict[str, Any]:
        f = _project_dir(project_id) / "assets.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"characters": [], "scenes": [], "props": []}

    @staticmethod
    def save_scenes(project_id: str, scenes: List[Dict[str, Any]]) -> Project:
        project = ProjectService._require(project_id)
        (_project_dir(project_id) / "scenes.json").write_text(
            json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ProjectService._finalize(project)

    @staticmethod
    def load_scenes(project_id: str) -> List[Dict[str, Any]]:
        f = _project_dir(project_id) / "scenes.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    @staticmethod
    def record_run(project_id: str, run_id: str, cover_path: Optional[str] = None) -> Project:
        """Called by pipeline after a successful video generation."""
        project = ProjectService._require(project_id)
        project.latest_run_id = run_id
        if cover_path:
            project.cover_path = cover_path
        return ProjectService._finalize(project)

    @staticmethod
    def run_dir(project_id: str, run_id: str) -> Path:
        d = _project_dir(project_id) / "runs" / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def list_runs(project_id: str) -> List[Dict[str, Any]]:
        """List run dirs that contain a final.mp4, newest first."""
        runs_root = _project_dir(project_id) / "runs"
        if not runs_root.exists():
            return []
        out = []
        for d in runs_root.iterdir():
            if d.is_dir() and (d / "final.mp4").exists():
                out.append({"run_id": d.name, "video_path": str(d / "final.mp4")})
        out.sort(key=lambda r: r["run_id"], reverse=True)  # task_id is timestamped
        return out

    # ---------- internals ----------

    @staticmethod
    def _require(project_id: str) -> Project:
        p = ProjectService.load_project(project_id)
        if p is None:
            raise FileNotFoundError(f"Project not found: {project_id}")
        return p

    @staticmethod
    def _write(project: Project) -> None:
        (_project_dir(project.project_id) / "project.json").write_text(
            json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _compute_stages_ready(project: Project) -> Dict[str, bool]:
        d = _project_dir(project.project_id)
        story = (d / "story.txt").exists() and bool((d / "story.txt").read_text(encoding="utf-8").strip())
        assets = False
        af = d / "assets.json"
        if af.exists():
            try:
                lib = json.loads(af.read_text(encoding="utf-8"))
                assets = any(
                    item.get("image_path") for kind in ("characters", "scenes", "props") for item in lib.get(kind, [])
                )
            except Exception:
                assets = False
        scenes = False
        sf = d / "scenes.json"
        if sf.exists():
            try:
                arr = json.loads(sf.read_text(encoding="utf-8"))
                scenes = any(s.get("narration") for s in arr)
            except Exception:
                scenes = False
        video = bool(project.latest_run_id and (d / "runs" / project.latest_run_id / "final.mp4").exists())
        return {"story": story, "assets": assets, "storyboard": scenes, "video": video}

    @staticmethod
    def _finalize(project: Project) -> Project:
        """Recompute readiness, bump updated_at, persist. Single chokepoint."""
        project.stages_ready = ProjectService._compute_stages_ready(project)
        project.updated_at = _now_iso()
        ProjectService._write(project)
        return project


# ---------------- self-check ----------------
if __name__ == "__main__":
    import tempfile, os
    # ponytail: one runnable check — create/save/load/list cycle on a temp output dir
    # python -m 下 __main__ 与 sys.modules 的模块对象不同，需覆盖当前全局 get_output_path
    tmp = tempfile.mkdtemp()
    import sys
    _cur = sys.modules[__name__]
    _cur.get_output_path = lambda *a, **k: os.path.join(tmp, "output", *a)

    p = ProjectService.create_project("测试故事")
    assert p.stages_ready["story"] is False, "empty story should not be ready"

    ProjectService.save_story(p.project_id, "小兔子捡到一颗发光的种子")
    p = ProjectService.load_project(p.project_id)
    assert p.stages_ready["story"] is True, "story should be ready after save"
    assert p.stages_ready["assets"] is False

    ProjectService.save_assets(p.project_id, {"characters": [{"name": "白白", "image_path": "x.png"}], "scenes": [], "props": []})
    p = ProjectService.load_project(p.project_id)
    assert p.stages_ready["assets"] is True, "assets ready when any image_path set"

    ProjectService.save_scenes(p.project_id, [{"narration": "白白走进森林", "composition": ""}])
    p = ProjectService.load_project(p.project_id)
    assert p.stages_ready["storyboard"] is True

    listed = ProjectService.list_projects()
    assert len(listed) == 1 and listed[0].project_id == p.project_id

    ProjectService.delete_project(p.project_id)
    assert ProjectService.load_project(p.project_id) is None
    print("OK: ProjectService self-check passed")
