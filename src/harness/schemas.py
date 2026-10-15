"""Every JSON Schema the harness publishes for editors (`harness schema export`)."""

from harness.datasources.relational.schema import DataFileForEditors
from harness.knowledge.schema import SCHEMAS as KNOWLEDGE_SCHEMAS

# data.yaml's entries depend on each source's type; editors get the relational shape.
SCHEMAS = {**KNOWLEDGE_SCHEMAS, "data": DataFileForEditors}
