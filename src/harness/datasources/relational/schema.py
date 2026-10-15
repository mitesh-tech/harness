"""What data.yaml may say for a relational source: connection, entities, relationships."""

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, StringConstraints, model_validator

from harness.knowledge.schema import EntityName, Model, SnakeName, Text

SqlName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
EnvName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Connection(Model):
    """How to reach the database. Connection details come from the environment."""

    url_env: EnvName  # e.g. BOOKSTORE_DATABASE_URL
    default_url: Text | None = None  # local development only

    @model_validator(mode="after")
    def _local_default(self) -> "Connection":
        if self.default_url is None:
            return self
        parts = urlsplit(self.default_url)
        if parts.password:
            raise ValueError("default_url must not contain a password; use url_env")
        if not parts.scheme.startswith("sqlite") and parts.hostname not in LOCAL_HOSTS:
            raise ValueError(
                "default_url is only for a local database (sqlite or localhost); "
                f"set {self.url_env} for any other"
            )
        return self


class Column(Model):
    """An attribute stored under another name, converted, or not stored at all."""

    column: SqlName | None = None
    convert: SnakeName | None = None
    stored: StrictBool = True

    @model_validator(mode="after")
    def _check(self) -> "Column":
        if self.stored and self.column is None:
            raise ValueError("give the 'column', or 'stored: false' if it is not stored")
        if not self.stored and (self.column or self.convert):
            raise ValueError("an attribute that is not stored has no column or converter")
        return self


class EntityMapping(Model):
    source: SnakeName
    table: SqlName
    columns: dict[SnakeName, SqlName | Column] = Field(min_length=1)  # attribute -> column
    parent_key: SqlName | None = None  # composition child: the column pointing at its parent

    def column_of(self, attribute: str) -> str | None:
        spec = self.columns.get(attribute)
        if isinstance(spec, Column):
            return spec.column if spec.stored else None
        return spec


class RelationshipMapping(Model):
    """A many-to-many link table with a key per side, or a one-to-many reference column."""

    source: SnakeName
    table: SqlName | None = None
    keys: dict[EntityName, SqlName] | None = None  # link table: entity -> its key column
    column: Annotated[str, StringConstraints(strict=True)] | None = None  # "books.publisher_id"

    @model_validator(mode="after")
    def _check(self) -> "RelationshipMapping":
        link = self.table is not None or self.keys is not None
        if link == (self.column is not None):
            raise ValueError(
                "give either a link 'table' with 'keys' (many-to-many) "
                "or a reference 'column' (one-to-many)"
            )
        if link and (self.table is None or not self.keys or len(self.keys) != 2):
            raise ValueError("a link table needs 'table' and a key for each of its two entities")
        if self.column is not None and not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", self.column):
            raise ValueError("'column' is written as table.column, e.g. books.publisher_id")
        return self

    @property
    def stored_as(self) -> str:
        return "link_table" if self.table else "reference"


class SourceForEditors(Model):
    type: Text
    description: Text | None = None
    connection: Connection


class DataFileForEditors(Model):
    """data.yaml with relational sources, for editor autocomplete (data.schema.json)."""

    sources: dict[SnakeName, SourceForEditors]
    entities: dict[EntityName, EntityMapping] = Field(default_factory=dict)
    relationships: dict[SnakeName, RelationshipMapping] = Field(default_factory=dict)
