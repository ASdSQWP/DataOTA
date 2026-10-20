"""模型库：检测模型、场景质量 / 漂移检测、世界模型、几何门禁度量。

导入本包即完成 Registry 注册；各实现的可选依赖在调用时检查，缺依赖会按配置回退并写审计。
目录（configs 中的 [models.*] 段）由 detectors.describe / world.describe 汇总给控制台。
"""
from . import detectors, gate, imaging, quality, world  # noqa: F401
