"""流程注册入口。

导入本包即完成所有 flow 的注册。`services/workflow.py` 在真正用到流程时
才延迟导入它，避免 services ↔ flows 的循环依赖。

新增流程：在本目录建模块，末尾 `register_flow(...)`，然后在下面加一行导入。
"""
from edu_app.flows import jd_match  # noqa: F401
from edu_app.flows import mock_interview  # noqa: F401

__all__ = ["jd_match", "mock_interview"]
