# xjfx

Collection of simple utility functions and classes that extend standard library functionality.

## Installation

```bash
$ pip install xjfx
```

## Development

First-time setup:
```bash
$ make setup
```
Enforce code formatting:
```bash
$ make format
```
Check for errors (build, lint, types, format, tests):
```bash
$ make format-check
```

### Create a release

0. `VERSION=<version>`
1. `make format format-check test`
2. `git tag -a $VERSION -m $VERSION && git tag -ln2 $VERSION && git push github master $VERSION`
3. `make build`
4. ```bash
   $ uv venv /tmp/xjfx-test && uv pip install --python /tmp/xjfx-test dist/xjfx-$VERSION-py3-none-any.whl
   $ /tmp/xjfx-test/bin/python -c 'import xjfx ; print(xjfx.__version__)'
   ```
5. `uvx twine check dist/xjfx-$VERSION-py3-none-any.whl`
6. `uvx twine upload --repository testpypi dist/xjfx-$VERSION-py3-none-any.whl`
7. ```bash
   $ uv venv /tmp/xjfx-test && uv pip install --python /tmp/xjfx-test \
       --index-url https://test.pypi.org/simple/ xjfx==$VERSION
   $ /tmp/xjfx-test/bin/python -c 'import xjfx ; print(xjfx.__version__)'
   ```
8. `uvx twine upload --repository pypi dist/xjfx-$VERSION-py3-none-any.whl`
9. ```bash
   $ uv venv /tmp/xjfx-test && uv pip install --python /tmp/xjfx-test \
       --index-url https://test.pypi.org/simple/ xjfx==$VERSION
   $ /tmp/xjfx-test/bin/python -c 'import xjfx ; print(xjfx.__version__)'
   ```
