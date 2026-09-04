import datetime
from pathlib import Path
import uuid
from typing import Any
import yaml
import logging
from msianalyzer.core.utils import MSIAnalyzerError
from msianalyzer.core.utils.logging_utils import log_call
import re

logger = logging.getLogger(__name__)

_VALID_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_\- ]+$")


class Project:
    """An msianalyzer project and its processing-run history.

    Serialised to `.msianalyzer.yml` at the project-folder root.

    Attributes:
        version: Project file schema version.
        name: Human-readable project name.
        uuid: Randomly generated unique identifier.
        date: Creation timestamp.
        runs: Mapping of run id to serialised run metadata.
    """

    version: int = 1

    def __init__(self, name: str) -> None:
        self.name = self.validate_name(name)
        self.uuid: str = str(uuid.uuid4())
        self.date: datetime.datetime = datetime.datetime.today()
        self.runs: dict[str, dict] = {}

    @staticmethod
    def validate_name(name) -> str:
        """Validate a project name.

        Args:
            name: Candidate project name.

        Returns:
            The name unchanged if valid.

        Raises:
            ValueError: If `name` is empty or contains characters other than
                letters, numbers, spaces, underscores and hyphens.
        """
        if not name or not _VALID_NAME_PATTERN.match(name):
            raise ValueError(
                "only letters, numbers, spaces, underscores and - (minus) are allowed."
            )
        return name

    def to_dict(self) -> dict[str, Any]:
        """Serialise the project to a plain, YAML-friendly dict.

        `datetime` values are converted to ISO strings and nested objects
        exposing `to_dict` are expanded recursively.

        Returns:
            A dict representation of the project.
        """
        d = {}

        for key, value in vars(self).items():
            if isinstance(value, datetime.datetime):
                d[key] = value.isoformat()

            elif isinstance(value, dict):
                d[key] = {
                    k: v.to_dict() if hasattr(v, "to_dict") else v
                    for k, v in value.items()
                }

            elif hasattr(value, "to_dict"):
                d[key] = value.to_dict()

            else:
                d[key] = value

        return d

    @log_call(source="path")
    def export(self, path: str | Path) -> None:
        """Export to a .yml/.yaml or .toml file, format inferred from suffix."""
        data = self.to_dict()

        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)

    @classmethod
    @log_call
    def load(cls, start_path: str | Path | None = None) -> "Project":
        """Locate the enclosing project and load its ``.msianalyzer.yml``.

        Walks up from ``start_path`` (or the current working directory) to
        find the project folder, then delegates to :meth:`load_from_yaml`.

        Raises:
            NotInProjectFolderError: If no project file is found.
        """
        folder = get_project_folder(start_path)
        return cls.load_from_yaml(folder / ".msianalyzer.yml")

    @classmethod
    @log_call(source="yaml_path")
    def load_from_yaml(cls, yaml_path: str | Path) -> "Project":
        """Load a project from a YAML file."""
        path = Path(yaml_path)

        if path.name == "" or yaml_path is None:
            raise ValueError("no file provided.")

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Project file {path} is not a valid project yaml file.")

        missing = ("name", "uuid", "date") - (data.keys())

        if missing:
            raise ValueError(f"Invalid project file, missing {', '.join(missing)}")

        project = cls(name=data["name"])

        # Restore identity from the file. `__init__` mints a fresh uuid/date;
        # keeping those would give every load a new project id, which breaks
        # the raw-DB command cache (run_id == project.uuid) and makes
        # re-runs re-parse / re-map pixels.
        project.uuid = str(data["uuid"])
        date = data["date"]
        project.date = (
            datetime.datetime.fromisoformat(date) if isinstance(date, str) else date
        )
        project.runs = dict(data.get("runs") or {})

        return project


@log_call(source="path")
def create_project_folder(path: str | Path, name: str) -> None:
    """Create a new project folder on disk.

    Creates `path`, writes a `.msianalyzer.yml` project file into it, and
    adds the standard `output`, `data` and `configs` subdirectories.

    Args:
        path: Directory to create for the new project.
        name: Project name, passed to `Project`.

    Raises:
        FileExistsError: If `path` already exists.
        ValueError: If `name` is invalid.
    """
    logger.debug("Creating project %s folder at %s", name, path)
    path = Path(path)
    path.mkdir()

    p = Project(name)
    p.export(path / ".msianalyzer.yml")

    for subdir in ["output", "data", "configs"]:
        (path / subdir).mkdir(exist_ok=True)

    logger.debug("Succesfully created project folder %s", path)


class NotInProjectFolderError(MSIAnalyzerError):
    """Raised when no valid project directory/file is found."""


def get_project_folder(start_path: Path | str | None = None) -> Path:
    """
    Walks up from start_path (or current working directory) to locate
    a project file, similar to how Git finds .git.

    Returns
    -------
    Path
        The path to the located project file.

    Raises
    ------
    ProjectNotFoundError
        If no project file is found in start_path or any parent directories.
    """
    current = Path(start_path or Path.cwd()).resolve()

    for directory in [current] + list(current.parents):
        project_candidate = directory / f".msianalyzer.yml"
        if project_candidate.is_file():
            return directory

    # If loop finishes at filesystem root without finding anything:
    raise NotInProjectFolderError(
        f"Not an msianalyzer project (or any of the parent directories): "
        f"could not find a project file starting from '{current}'"
    )
