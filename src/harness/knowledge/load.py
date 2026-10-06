"""Reading YAML files and turning schema errors into clear problems with line numbers."""

import difflib
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from harness.knowledge.schema import NAME_HINTS

PathKey = tuple[str | int, ...]


@dataclass(frozen=True)
class Problem:
    file: Path
    path: PathKey
    message: str
    line: int | None = None
    severity: Literal["error", "warning"] = "error"


@dataclass
class YamlFile:
    path: Path
    data: object
    lines: dict[PathKey, int]

    def line_of(self, path: PathKey) -> int | None:
        """Line of the deepest part of `path` that exists in the file."""
        for end in range(len(path), -1, -1):
            if path[:end] in self.lines:
                return self.lines[path[:end]]
        return None

    def problem(self, path: PathKey, message: str, severity="error") -> Problem:
        return Problem(self.path, path, message, self.line_of(path), severity)


def read_yaml(path: Path) -> tuple[YamlFile | None, list[Problem]]:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None, [Problem(path, (), "file not found")]
    try:
        node = yaml.compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else None
        reason = getattr(exc, "problem", None) or str(exc)
        return None, [Problem(path, (), f"not valid YAML: {reason}", line)]
    lines = _line_map(node) if node is not None else {}
    return YamlFile(path, data if data is not None else {}, lines), []


def _line_map(node: yaml.Node, path: PathKey = ()) -> dict[PathKey, int]:
    lines = {path: node.start_mark.line + 1}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            child = path + (key.value,)
            lines.update(_line_map(value, child))
            lines[child] = key.start_mark.line + 1
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            lines.update(_line_map(item, path + (index,)))
    return lines


def parse[M: BaseModel](model: type[M], file: YamlFile) -> tuple[M | None, list[Problem]]:
    try:
        return model.model_validate(file.data), []
    except ValidationError as exc:
        return None, [_to_problem(model, file, error) for error in exc.errors()]


def _to_problem(model: type[BaseModel], file: YamlFile, error: dict) -> Problem:
    loc = tuple(error["loc"])
    path = _real_path(loc, file)
    kind = error["type"]
    value = error.get("input")

    if kind == "missing":
        message = f"'{loc[-1]}' is required"
        path = path + (loc[-1],)
    elif kind == "extra_forbidden":
        message = f"unknown field '{loc[-1]}'"
        allowed = _allowed_fields(model, path[:-1])
        guess = difflib.get_close_matches(str(loc[-1]), allowed, n=1, cutoff=0.6)
        if guess:
            message += f" (did you mean '{guess[0]}'?)"
        elif allowed:
            message += f" (allowed: {', '.join(allowed)})"
    elif kind in ("string_type", "literal_error") and isinstance(value, bool):
        message = (
            f"expected text but YAML read this as {str(value).lower()}; "
            "unquoted yes/no/on/off/true/false become true/false, so put it in quotes"
        )
    elif kind == "literal_error":
        message = f"{value!r} is not allowed (expected {error['ctx']['expected']})"
    elif kind == "string_pattern_mismatch":
        hint = NAME_HINTS.get(error["ctx"]["pattern"], "a valid name")
        message = f"{value!r} is not a valid name (expected {hint})"
    elif kind == "too_long":
        ctx = error["ctx"]
        message = f"too many entries: {ctx['actual_length']} (at most {ctx['max_length']})"
    elif kind == "value_error":
        message = error["msg"].removeprefix("Value error, ")
    else:
        message = error["msg"]
    return file.problem(path, message)


def _real_path(loc: PathKey, file: YamlFile) -> PathKey:
    """Keep only the parts of a pydantic location that exist in the YAML file."""
    path: PathKey = ()
    for part in loc:
        if path + (part,) in file.lines:
            path = path + (part,)
    return path


def _allowed_fields(model: type[BaseModel], path: PathKey) -> list[str]:
    target = _model_at(model, path)
    if target is None:
        return []
    return [field.alias or name for name, field in target.model_fields.items()]


def _model_at(model: object, path: PathKey) -> type[BaseModel] | None:
    current = model
    for part in path:
        current = _unwrap(current)
        if isinstance(current, type) and issubclass(current, BaseModel):
            field = next(
                (f for n, f in current.model_fields.items() if (f.alias or n) == part), None
            )
            if field is None:
                return None
            current = field.annotation
        elif get_origin(current) is dict:
            current = get_args(current)[1]
        elif get_origin(current) is list:
            current = get_args(current)[0]
        else:
            return None
    current = _unwrap(current)
    return current if isinstance(current, type) and issubclass(current, BaseModel) else None


def _unwrap(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _unwrap(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        for arg in get_args(annotation):
            inner = _unwrap(arg)
            if isinstance(inner, type) and issubclass(inner, BaseModel):
                return inner
    return annotation
