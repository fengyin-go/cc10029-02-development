"""告警中心业务规则：状态流转、字段校验、筛选口径与监控指标数据集消费。

告警通知清单只来自运行监控回写的监控指标数据集；通知中的任务、时段、错误次数
均取自同一份异常时间线，同一数据集重复消费只生成一次通知。
"""
from __future__ import annotations

from typing import Any

from app.metrics_dataset import DatasetConflictError, MetricDatasetError, registry
from app.store import store

MODULE = "alert"
LIST_FIELDS = ["规则编号", "监控对象", "告警条件", "通知渠道", "通知模板", "重复间隔", "生效时段", "值班人员"]
REQUIRED_FIELDS = ["规则编号", "监控对象", "告警条件"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


class AlertService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"告警规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于告警中心可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"告警规则已{action}"

    # ----- 监控指标数据集消费：告警通知清单 ----- #
    def list_notifications(
        self, *, task: str | None = None, window: str | None = None,
        page: int = 1, size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        items = registry.notifications(task=task, window=window)
        total = len(items)
        start = (page - 1) * size
        return items[start:start + size], total

    def consume_dataset(self, dataset_id: str) -> dict[str, Any]:
        try:
            return registry.consume(dataset_id)
        except MetricDatasetError:
            raise
        except DatasetConflictError:
            raise
