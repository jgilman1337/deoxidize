#!/bin/sh

uv sync --group dev

uv run ruff check --fix .
uv run ruff format .
uv run pyright .
