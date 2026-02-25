# xjfx

Collection of simple utility functions and classes that extend standard library functionality.

## Installation

```bash
$ pip install xjfx
```

## Development

Check for code lint errors:
```bash
$ tox run-parallel -m analyze
```
Enforce code formatting:
```bash
$ tox run-parallel -m edit
```
Both:
```bash
$ tox run-parallel -m edit analyze
```

### Create a release

0. `VERSION=<version>`
1. `tox p -m analyze edit`
2. `python3 -m build --wheel`
3. `twine check dist/xjfx-$VERSION-py3-none-any.whl`
4. Update `version` field in `pyproject.toml` to `$VERSION` and `git tag -a $VERSION`
5. Push `master` and `$VERSION` tag to github
6. `twine upload --repository testpypi dist/xjfx-$VERSION-py3-none-any.whl`
7.
  ```bash
  $ virtualenv venv
  $ venv/bin/pip install --index-url https://test.pypi.org/simple/ xjfx
  $ venv/bin/python -c 'import xjfx'
  ```
8. `twine upload --repository pypi dist/xjfx-$VERSION-py3-none-any.whl`
