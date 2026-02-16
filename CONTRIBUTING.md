# Contributing to promptdiff

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/yourusername/promptdiff.git
cd promptdiff
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
pytest --cov=promptdiff
```

## Code Style

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Pull Request Process

1. Fork the repo and create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass and code is formatted
4. Submit a PR with a clear description of changes

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS
