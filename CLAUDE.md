# CLAUDE.md

## Code Style

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
Full type hints on all methods. Google-style docstrings (Args / Returns).

## OOP Principles

**Single responsibility** — one class, one job. When a class grows, split it by concern rather than adding more methods. Example: `TaskSetGenerator` delegates calculation to `FrameSizeCalculator` and validation to `TaskSetValidator`.

**Stateless utilities** — group pure, stateless operations into a static-method class (e.g. `JsonIO`) instead of free functions or a mixed bag.

**Template method via `_parse`** — `AppBaseModel.load_from_json` defines the loading skeleton; subclasses override `_parse` for custom construction logic.

**Dependency injection** — accept collaborators as constructor parameters with sensible defaults. Keeps classes testable without monkey-patching.

**Private by default** — prefix internal methods and attributes with `_`. Only expose what callers actually need.

## Git Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>
```

**Types:** `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`

**Scope** is optional — use the module or layer name (e.g. `model`, `generator`).

**Subject** is lowercase, imperative mood, no period at the end.

Examples from this repo:
```
feat(model): implement real-time task data structures
chore: update .gitignore to ignore output folder
```
