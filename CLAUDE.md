# CLAUDE.md

## Code Style

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).

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
