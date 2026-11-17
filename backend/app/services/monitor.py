"""运行监控 分析看板服务：汇总指标、运行分析，并维护监控指标数据集。

监控指标数据集把采集任务观测值整理成异常时间线；提交、扩展、幂等与并发
控制统一收口在 app.metrics_dataset，本服务只做参数透传与结果包装。
"""
from __future__ import annotations

import random
from typing import Any

from app.metrics_dataset import (
    DatasetConflictError,
    MetricDatasetError,
    registry,
    sample_payloads,
)
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["大盘编号", "关联任务", "采集速率"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


class MonitorService:
    def list_results(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        results: list[dict] = []
        for row in rows:
            d = dict(row)
            d["指标值"] = round(random.uniform(0.65, 0.98), 3)
            d["对比基准"] = round(random.uniform(0.60, 0.92), 3)
            d["差异"] = round(d["指标值"] - d["对比基准"], 3)
            results.append(d)
        return results

    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        count = int(params.get("样本范围", params.get("并行数量", "6")))
        results: list[dict[str, Any]] = []
        for i in range(count):
            results.append({
                "id": i + 1,
                "分组": f"分组{(i%3)+1}",
                "指标值": round(random.uniform(0.62, 1.02), 3),
                "对比基准": round(random.uniform(0.58, 0.95), 3),
                "差异": round(random.uniform(-0.2, 0.3), 3),
            })
        return results

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ----- 监控指标数据集 ----- #
    def dataset_overview(self) -> dict[str, Any]:
        return registry.overview()

    def list_datasets(
        self, *, task: str | None = None, window: str | None = None,
        page: int = 1, size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        return registry.list_datasets(task=task, window=window, page=page, size=size)

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        return registry.get_dataset(dataset_id)

    def commit_dataset(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            return registry.commit(payload)
        except MetricDatasetError:
            raise
        except DatasetConflictError:
            raise

    def sample_payloads(self) -> list[dict[str, Any]]:
        return sample_payloads()

    def task_options(self) -> list[str]:
        return registry.tasks()
