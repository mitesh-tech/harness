"""Importing every model here registers its table with `Base.metadata`."""

from app.models.book import Book

__all__ = ["Book"]
