"""运行监控 分析看板接口：汇总指标、参数运行、监控指标数据集与结果导出。

兼容约定：既有 /summary、/run、/export、/{entry_id} 的字段名与返回结构保持不变；
监控指标数据集使用独立的 /datasets 前缀，不占用既有路径。
"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.metrics_dataset import DatasetConflictError, MetricDatasetError
from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.monitor import MonitorService

router = APIRouter(prefix="/api/monitor", tags=["运行监控"])
service = MonitorService()
LIST_FIELDS = ["大盘编号", "关联任务", "采集速率", "成功率", "平均延迟", "内存占用", "错误次数", "观测时段"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": service.list_results()}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=monitor.csv"})


# --------------------------------------------------------------------------- #
# 监控指标数据集：同窗口速率快照 + 异常时间线，看板排列、幂等提交、并发互斥
# --------------------------------------------------------------------------- #
@router.get("/datasets/overview")
def get_datasets_overview() -> dict[str, Any]:
    """概览卡片：展示监控指标数据集当前结果。"""
    return {"metrics": service.dataset_overview(), "tasks": service.task_options()}


@router.get("/datasets/sample")
def get_dataset_sample() -> dict[str, Any]:
    """返回确定性演示载荷，供页面「填充样例」与重复上报对比使用。"""
    return {"items": service.sample_payloads()}


@router.get("/datasets")
def list_datasets(
    task: str | None = Query(default=None, description="按关联任务筛选"),
    window: str | None = Query(default=None, description="按观测时段筛选（匹配观测开始/结束）"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """监控指标数据集看板列表，沿用 {items,total,page,size} 稳定结构。"""
    items, total = service.list_datasets(task=task, window=window, page=page, size=size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/datasets")
def commit_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """提交监控指标数据集。

    - 同窗口校验失败返回 400；
    - 并发抢占/重复提交返回 409，并回传当前已生效数据集，运行监控不会产生第二条记录；
    - 同任务同时段的新观测值只扩展当前时间线，版本号递增。
    """
    try:
        dataset, created = service.commit_dataset(payload)
    except MetricDatasetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DatasetConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "dataset": exc.dataset},
        ) from exc
    return {
        "ok": True,
        "message": "监控指标数据集已提交" if created else "观测值已扩展到当前时间线，版本号已递增",
        "created": created,
        "dataset": dataset,
    }


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict[str, Any]:
    dataset = service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"监控指标数据集 {dataset_id} 不存在")
    return dataset


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控大盘 {entry_id} 不存在")
    return entry
