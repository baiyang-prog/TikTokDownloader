"""
带 my_extend 补丁的启动入口，行为与 main.py 完全一致，只是多安装了一层可选补丁。

启动方式（任选其一）：
    .venv\\Scripts\\python.exe my_extend\\start.py
    python my_extend\\start.py

不想要补丁时，继续用原来的 main.py 启动即可，两者互不影响。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from my_extend.search_autodownload import install as _install_patch  # noqa: E402

_install_patch()

from asyncio import run  # noqa: E402

import main as _entry  # noqa: E402

if __name__ == "__main__":
    run(_entry.main())
