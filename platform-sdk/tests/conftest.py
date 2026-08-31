"""让这些测试在仓库根目录下也能跑。

**为什么需要**:2026-08-31 发现 `platform-sdk/tests/` 下的 53 条测试
**CI 里从来没跑过** —— 工作流里只有 `pip install ./platform-sdk`(给
check-doc-examples.py 用),没有任何一步 pytest 它们。补进 CI 时才发现:
从仓库根跑 `pytest platform-sdk/tests/` 会 collect 失败,因为
`import platform_sdk` 找不到包(它靠 `cd platform-sdk` 时的当前目录)。

一个"只在某个目录下才跑得起来"的测试套件,很容易变成"没人跑"——
这次就是。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
