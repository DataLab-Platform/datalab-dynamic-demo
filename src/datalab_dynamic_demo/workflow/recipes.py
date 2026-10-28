"""Headless recipe registry."""

from __future__ import annotations

from datalab.plugins.recipes import RecipeDescriptor

RECIPES: tuple[RecipeDescriptor, ...] = ()

__all__ = ["RECIPES"]
