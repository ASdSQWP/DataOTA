"""本地可视化控制台：python -m dataota web

只依赖标准库，直接读取 runs/<workdir> 下的产物的只读数据接口 + 静态前端。
"""
from .api import Dashboard, RunManager
from .server import serve

__all__ = ["Dashboard", "RunManager", "serve"]
