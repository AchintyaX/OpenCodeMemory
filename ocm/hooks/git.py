from __future__ import annotations

from pathlib import Path


def get_head_sha(project_root: Path) -> str | None:
    """Return the current HEAD commit SHA, or None if not a git repo."""
    try:
        import git
        repo = git.Repo(project_root, search_parent_directories=True)
        return repo.head.commit.hexsha
    except Exception:
        return None


def get_diff_stat(sha_start: str, project_root: Path) -> str:
    """Return git diff --stat between sha_start and HEAD."""
    try:
        import git
        repo = git.Repo(project_root, search_parent_directories=True)
        return repo.git.diff("--stat", sha_start)
    except Exception:
        return ""


def get_changed_files(sha_start: str, project_root: Path) -> list[tuple[str, str]]:
    """Return (file_path, change_type) for all files changed since sha_start.
    change_type: 'created' | 'modified' | 'deleted'
    """
    try:
        import git
        repo = git.Repo(project_root, search_parent_directories=True)
        raw = repo.git.diff("--name-status", sha_start)
        result = []
        for line in raw.splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status, path = parts
            if status.startswith("A"):
                change_type = "created"
            elif status.startswith("D"):
                change_type = "deleted"
            else:
                change_type = "modified"
            result.append((path, change_type))
        return result
    except Exception:
        return []


def get_project_name(project_root: Path) -> str:
    """Derive project name from git remote URL or directory name."""
    try:
        import git
        repo = git.Repo(project_root, search_parent_directories=True)
        if repo.remotes:
            remote_url = repo.remotes[0].url
            # Extract repo name from URL (handles https and ssh formats)
            name = remote_url.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name
    except Exception:
        pass
    return project_root.name
