import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))


import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    """Run coroutine tests with asyncio.run; no pytest-asyncio needed."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {n: pyfuncitem.funcargs[n] for n in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
    return None
