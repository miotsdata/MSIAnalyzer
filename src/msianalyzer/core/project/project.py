import datetime
from pathlib import Path
import uuid
from typing import Any
import yaml
import logging

logger = logging.getLogger(__name__)


class Project:
    version: int = 1

    def __init__(self, name: str) -> None:
        self.name = name
        self.uuid: str = str(uuid.uuid4())
        self.date: datetime.datetime = datetime.datetime.today()

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for key, value in vars(self).items():
            if isinstance(value, datetime.datetime):
                d[key] = str(value)
            else:
                d[key] = value
        return d

    def export(self, path: str | Path) -> None:
        """Export to a .yml/.yaml or .toml file, format inferred from suffix."""
        data = self.to_dict()

        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        """Load a project from a YAML file."""
        path = Path(path)

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Project file {path} did not parse to a mapping")

        missing = ("name", "uuid", "date") - (data.keys())

        if missing:
            raise ValueError(f"Invalid project file, missing {', '.join(missing)}")

        project = cls(name=data["name"])

        return project


def create_project_folder(path: str | Path, name: str) -> None:
    logger.debug("Creating project %s folder at %s", name, path)
    path = Path(path)
    path.mkdir()

    p = Project(name)
    p.export(path / f"{name}.yml")

    for subdir in ["output", "data", "configs"]:
        (path / subdir).mkdir(exist_ok=True)

    logger.debug("Succesfully created project folder %s", path)
