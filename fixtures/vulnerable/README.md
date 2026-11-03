# Intentionally vulnerable fixtures

**Do not use this code.** These files exist only so SecGenie's self-test
workflow has known Semgrep findings to scan. Every pattern here is insecure on
purpose. They live outside `tests/` because Semgrep's default ignore list skips
`test/` and `tests/` directories.
