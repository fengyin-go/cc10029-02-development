"""监控指标数据集核心：按关联任务把采集观测值整理成异常时间线。

设计约束（来自运行监控与告警中心的联动需求）：

1. 采集速率快照与异常时间线必须来自同一个观测窗口，提交时校验所有观测值、
   慢请求指标、事件流都落在窗口内，拒绝跨批次拼装。
2. 同一任务重复上报观测值只允许扩展当前时间线，相同时段不会产生第二条异常记录。
3. 慢请求指标与事件流对同一时段给出不同异常起点时，合并为一个带全部观测来源的
   完整时间线版本，冲突不产生两条记录。
4. 监控指标数据集并发写入时只允许一个提交成功（进程内互斥锁 + 指纹幂等）。
5. 同一份数据集重复提交只生效一次；告警中心消费同一数据集也只生成一份通知清单。
6. 数据集与告警通知在同一把锁内原子写入，通知中的任务、时段、错误次数均直接
   取自同一份时间线，不存在跨服务取数窗口漂移。
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# ---- 阈值与口径（固定规则，避免随机数导致同一份数据两次算出不同结果） ----
SUCCESS_RATE_THRESHOLD = 0.90   # 成功率低于该值视为异常
ERROR_COUNT_THRESHOLD = 3       # 错误次数达到该值视为错误峰值
SLOT_MINUTES = 5                # 观测窗口内按 5 分钟一个观测时段切分
SLOW_LATENCY_MS = 800           # 平均延迟超过该值视为慢请求

# 提交载荷里允许的字段别名：规范名用中文，兼容既有接口的英文键
_ALIASES = {
    "dataset_id": "数据集编号", "datasetId": "数据集编号",
    "task": "关联任务", "task_name": "关联任务",
    "window_start": "观测开始", "windowStart": "观测开始",
    "window_end": "观测结束", "windowEnd": "观测结束",
    "batch_no": "批次号", "batchNo": "批次号",
    "observations": "观测值",
    "slow_metrics": "慢请求指标", "slowMetrics": "慢请求指标",
    "events": "事件流",
}
_OBS_ALIASES = {
    "slot": "时段", "time": "时段", "timestamp": "时段",
    "rate": "采集速率", "crawl_rate": "采集速率",
    "success_rate": "成功率",
    "latency_ms": "平均延迟", "latency": "平均延迟",
    "errors": "错误次数", "error_count": "错误次数",
}
_INTERVAL_ALIASES = {
    "start": "异常开始", "start_slot": "异常开始", "startSlot": "异常开始",
    "end": "异常结束", "end_slot": "异常结束", "endSlot": "异常结束",
    "latency_ms": "延迟毫秒", "latency": "延迟毫秒",
    "source": "观测来源",
}
_EVENT_ALIASES = {
    "slot": "时段", "time": "时段", "timestamp": "时段",
    "errors": "错误次数", "error_count": "错误次数",
    "kind": "事件类型", "event_type": "事件类型",
}


class MetricDatasetError(ValueError):
    """数据集校验失败（窗口不一致、字段缺失等），映射为 HTTP 400。"""


class DatasetConflictError(Exception):
    """并发抢占或重复提交导致本次写入被拒绝，映射为 HTTP 409。"""

    def __init__(self, message: str, *, dataset: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.dataset = dataset


def _canonicalize(payload: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        out[aliases.get(key, key)] = value
    return out


def parse_time(value: Any) -> datetime:
    """接受 'YYYY-MM-DD HH:MM' / ISO8601；失败时给出可读错误。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        raise MetricDatasetError("观测时段不能为空")
    normalized = text.replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized[: len(fmt) + 2].strip() if fmt == "%Y-%m-%d" else normalized, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError as exc:
        raise MetricDatasetError(f"时间格式无法识别：{text}（应为 YYYY-MM-DD HH:MM）") from exc


def fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 时间线引擎（纯函数，便于单测）
# --------------------------------------------------------------------------- #
@dataclass
class Window:
    start: datetime
    end: datetime

    @property
    def slots(self) -> list[datetime]:
        result: list[datetime] = []
        cursor = self.start
        while cursor < self.end:
            result.append(cursor)
            cursor += timedelta(minutes=SLOT_MINUTES)
        return result

    def ensure_inside(self, moment: datetime, label: str) -> None:
        if not (self.start <= moment < self.end):
            raise MetricDatasetError(
                f"{label} {fmt_time(moment)} 落在观测窗口 "
                f"{fmt_time(self.start)} ~ {fmt_time(self.end)} 之外，指标与事件不允许跨批次"
            )


def build_timeline(payload: dict[str, Any]) -> dict[str, Any]:
    """把一份提交载荷整理为「同窗口」的速率快照 + 异常时间线。"""
    data = _canonicalize(payload, _ALIASES)
    task = str(data.get("关联任务") or "").strip()
    if not task:
        raise MetricDatasetError("关联任务为必填项")
    window_start = parse_time(data.get("观测开始"))
    window_end = parse_time(data.get("观测结束"))
    if window_end <= window_start:
        raise MetricDatasetError("观测结束必须晚于观测开始")
    window = Window(start=window_start, end=window_end)

    raw_observations = [_canonicalize(item, _OBS_ALIASES) for item in (data.get("观测值") or [])]
    slow_metrics = [_canonicalize(item, _INTERVAL_ALIASES) for item in (data.get("慢请求指标") or [])]
    events = [_canonicalize(item, _EVENT_ALIASES) for item in (data.get("事件流") or [])]

    # 1) 先做同窗口校验：任何一个输入越界都拒绝整批，杜绝指标/事件来自不同批次
    for item in raw_observations:
        window.ensure_inside(parse_time(item.get("时段")), "观测值时段")
    for item in slow_metrics:
        window.ensure_inside(parse_time(item.get("异常开始")), "慢请求异常开始")
        window.ensure_inside(parse_time(item.get("异常结束")), "慢请求异常结束")
    for item in events:
        window.ensure_inside(parse_time(item.get("时段")), "事件流时段")

    # 2) 按观测时段归并：同一 slot 的重复上报以首次值为准（只扩展不覆盖）
    by_slot: dict[datetime, dict[str, Any]] = {}
    for item in raw_observations:
        slot = parse_time(item.get("时段")).replace(second=0, microsecond=0)
        window.ensure_inside(slot, "观测值时段")
        if slot not in by_slot:
            by_slot[slot] = {
                "时段": fmt_time(slot),
                "采集速率": _to_float(item.get("采集速率")),
                "成功率": _to_float(item.get("成功率"), 1.0),
                "平均延迟": _to_int(item.get("平均延迟")),
                "错误次数": _to_int(item.get("错误次数")),
                "观测来源": ["观测值"],
            }

    # 事件流按 slot 叠加错误次数与来源
    event_slots: set[datetime] = set()
    for item in events:
        slot = parse_time(item.get("时段")).replace(second=0, microsecond=0)
        event_slots.add(slot)
        bucket = by_slot.setdefault(slot, {
            "时段": fmt_time(slot), "采集速率": 0.0, "成功率": 1.0,
            "平均延迟": 0, "错误次数": 0, "观测来源": [],
        })
        bucket["错误次数"] += _to_int(item.get("错误次数"))
        if "事件流" not in bucket["观测来源"]:
            bucket["观测来源"].append("事件流")

    # 慢请求指标：覆盖区间内每个 slot，标记来源与延迟（不改变已统计的错误次数）
    slow_slots: dict[datetime, int] = {}
    for item in slow_metrics:
        start = parse_time(item.get("异常开始")).replace(second=0, microsecond=0)
        end = parse_time(item.get("异常结束")).replace(second=0, microsecond=0)
        latency = _to_int(item.get("延迟毫秒"), SLOW_LATENCY_MS)
        cursor = start
        while cursor <= end and cursor < window.end:
            slow_slots[cursor] = max(slow_slots.get(cursor, 0), latency)
            bucket = by_slot.setdefault(cursor, {
                "时段": fmt_time(cursor), "采集速率": 0.0, "成功率": 1.0,
                "平均延迟": 0, "错误次数": 0, "观测来源": [],
            })
            if bucket["平均延迟"] < latency:
                bucket["平均延迟"] = latency
            if "慢请求指标" not in bucket["观测来源"]:
                bucket["观测来源"].append("慢请求指标")
            cursor += timedelta(minutes=SLOT_MINUTES)

    # 3) 速率快照覆盖窗口内全部 slot（未上报 slot 按健康占位，保证快照与时间线同窗口）
    snapshots: list[dict[str, Any]] = []
    abnormal_flags: dict[datetime, bool] = {}
    for slot in window.slots:
        bucket = by_slot.get(slot)
        if bucket is None:
            snapshot = {
                "时段": fmt_time(slot), "采集速率": 0.0, "成功率": 1.0,
                "平均延迟": 0, "错误次数": 0, "观测来源": ["窗口占位"],
                "是否异常": False,
            }
            abnormal_flags[slot] = False
        else:
            snapshot = bucket
            rate_bad = snapshot["成功率"] < SUCCESS_RATE_THRESHOLD
            error_bad = snapshot["错误次数"] >= ERROR_COUNT_THRESHOLD
            slow_bad = slot in slow_slots
            event_bad = slot in event_slots
            abnormal_flags[slot] = rate_bad or error_bad or slow_bad or event_bad
            snapshot["是否异常"] = abnormal_flags[slot]
        snapshots.append(snapshot)

    # 4) 相邻异常 slot 合并为异常区间；冲突来源在同一区间内归并，只保留一条记录
    intervals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for slot in window.slots:
        if not abnormal_flags[slot]:
            if current is not None:
                intervals.append(current)
                current = None
            continue
        snapshot = next(item for item in snapshots if item["时段"] == fmt_time(slot))
        if current is None:
            current = {
                "异常开始": fmt_time(slot),
                "异常结束": fmt_time(slot + timedelta(minutes=SLOT_MINUTES)),
                "错误次数": snapshot["错误次数"],
                "最低成功率": snapshot["成功率"],
                "最大延迟": snapshot["平均延迟"],
                "观测来源": list(snapshot["观测来源"]),
                "冲突合并": False,
            }
        else:
            current["异常结束"] = fmt_time(slot + timedelta(minutes=SLOT_MINUTES))
            current["错误次数"] += snapshot["错误次数"]
            current["最低成功率"] = min(current["最低成功率"], snapshot["成功率"])
            current["最大延迟"] = max(current["最大延迟"], snapshot["平均延迟"])
            before = set(current["观测来源"])
            for source in snapshot["观测来源"]:
                if source not in current["观测来源"]:
                    current["观测来源"].append(source)
            # 同一 slot 同时被慢请求与事件流判定为异常、起点不一致即记录一次冲突合并
            if {"慢请求指标", "事件流"} <= set(snapshot["观测来源"]) or before != set(current["观测来源"]):
                current["冲突合并"] = True
    if current is not None:
        intervals.append(current)

    total_errors = sum(item["错误次数"] for item in snapshots)
    return {
        "关联任务": task,
        "观测开始": fmt_time(window.start),
        "观测结束": fmt_time(window.end),
        "批次号": str(data.get("批次号") or ""),
        "采集速率快照": snapshots,
        "异常时间线": intervals,
        "窗口错误总数": total_errors,
        "异常区间数": len(intervals),
    }


def fingerprint_of(payload: dict[str, Any]) -> str:
    """规范化指纹：键排序后哈希，保证同一内容重复提交判定一致。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 数据集 / 告警通知注册表（进程内单例，线程安全）
# --------------------------------------------------------------------------- #
@dataclass
class DatasetRecord:
    dataset: dict[str, Any]
    fingerprint: str
    versions: list[dict[str, Any]] = field(default_factory=list)
    event_count_history: list[dict[str, Any]] = field(default_factory=list)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._datasets: list[DatasetRecord] = []
        self._seq = 0
        self._consumed: dict[str, str] = {}  # 数据集编号 -> 指纹，告警消费幂等

    # ----- 监控侧：提交 ----- #
    def commit(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """返回 (数据集, 是否新建)。重复/并发冲突抛 DatasetConflictError。"""
        norm_payload = _canonicalize(payload, _ALIASES)
        fingerprint = fingerprint_of(norm_payload)
        timeline = build_timeline(norm_payload)
        key = (timeline["关联任务"], timeline["观测开始"], timeline["观测结束"])

        with self._lock:
            existing = next((
                record for record in self._datasets
                if (record.dataset["关联任务"], record.dataset["观测开始"], record.dataset["观测结束"]) == key
            ), None)
            if existing is not None:
                # 同任务同时段：同一指纹 = 重复提交，整份只生效一次
                if existing.fingerprint == fingerprint:
                    raise DatasetConflictError(
                        "监控指标数据集重复提交，本次未生成第二条记录",
                        dataset=existing.dataset,
                    )
                # 不同观测值 = 扩展当前时间线（版本号递增，事件数历史保留可对比）
                self._extend(existing, timeline, fingerprint)
                return existing.dataset, False

            self._seq += 1
            dataset_id = f"MMD-{self._seq:04d}"
            dataset = {
                "数据集编号": dataset_id,
                **timeline,
                "版本号": 1,
                "提交指纹": fingerprint,
                "状态": "已生效",
                "创建时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            record = DatasetRecord(dataset=dataset, fingerprint=fingerprint)
            record.versions.append({
                "版本号": 1, "指纹": fingerprint,
                "异常区间数": dataset["异常区间数"],
                "窗口错误总数": dataset["窗口错误总数"],
                "提交时间": dataset["创建时间"],
            })
            record.event_count_history.append({
                "版本号": 1, "事件数量": dataset["异常区间数"],
                "窗口错误总数": dataset["窗口错误总数"], "类型": "首次上报",
            })
            self._datasets.append(record)
            return dataset, True

    def _extend(self, record: DatasetRecord, timeline: dict[str, Any], fingerprint: str) -> None:
        dataset = record.dataset
        was_consumed = dataset["数据集编号"] in self._consumed
        dataset["版本号"] += 1
        dataset["提交指纹"] = fingerprint
        dataset["采集速率快照"] = timeline["采集速率快照"]
        dataset["异常时间线"] = timeline["异常时间线"]
        dataset["窗口错误总数"] = timeline["窗口错误总数"]
        dataset["异常区间数"] = timeline["异常区间数"]
        dataset["批次号"] = timeline["批次号"] or dataset["批次号"]
        dataset["状态"] = "已扩展"
        submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record.fingerprint = fingerprint
        record.versions.append({
            "版本号": dataset["版本号"], "指纹": fingerprint,
            "异常区间数": dataset["异常区间数"],
            "窗口错误总数": dataset["窗口错误总数"],
            "提交时间": submit_time,
        })
        record.event_count_history.append({
            "版本号": dataset["版本号"], "事件数量": dataset["异常区间数"],
            "窗口错误总数": dataset["窗口错误总数"], "类型": "重复上报扩展",
        })
        # 仅当此前已被告警中心消费时才刷新通知；未消费的数据集扩展后仍需显式消费
        if was_consumed:
            self._write_notifications(dataset)

    # ----- 告警侧：消费 / 查询 ----- #
    def consume(self, dataset_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._find_record(dataset_id)
            if record is None:
                raise MetricDatasetError(f"监控指标数据集 {dataset_id} 不存在，告警中心拒绝消费")
            if dataset_id in self._consumed:
                raise DatasetConflictError(
                    f"数据集 {dataset_id} 已消费，告警通知清单只生成一次",
                    dataset=record.dataset,
                )
            self._write_notifications(record.dataset)
            return record.dataset

    def _write_notifications(self, dataset: dict[str, Any]) -> None:
        """调用方必须持锁。通知字段全部取自同一份时间线。"""
        self._consumed[dataset["数据集编号"]] = dataset["提交指纹"]

    def notifications(self, task: str | None = None, window: str | None = None) -> list[dict[str, Any]]:
        """告警通知清单：只包含已被告警中心消费过的数据集。"""
        with self._lock:
            result: list[dict[str, Any]] = []
            for record in self._datasets:
                dataset = record.dataset
                if dataset["数据集编号"] not in self._consumed:
                    continue
                if task and task not in dataset["关联任务"]:
                    continue
                if window and window not in (dataset["观测开始"], dataset["观测结束"]):
                    continue
                for index, interval in enumerate(dataset["异常时间线"], start=1):
                    result.append({
                        "通知编号": f"ALN-{dataset['数据集编号'].split('-')[1]}-{index:02d}",
                        "数据集编号": dataset["数据集编号"],
                        "关联任务": dataset["关联任务"],
                        "观测开始": dataset["观测开始"],
                        "观测结束": dataset["观测结束"],
                        "异常开始": interval["异常开始"],
                        "异常结束": interval["异常结束"],
                        "错误次数": interval["错误次数"],
                        "最低成功率": interval["最低成功率"],
                        "观测来源": "、".join(interval["观测来源"]),
                        "冲突合并": interval["冲突合并"],
                        "版本号": dataset["版本号"],
                        "告警条件": f"成功率<{SUCCESS_RATE_THRESHOLD:.0%} 或 错误次数>={ERROR_COUNT_THRESHOLD}",
                        "通知渠道": "监控平台站内信",
                    })
            return result

    # ----- 查询 ----- #
    def list_datasets(
        self, *, task: str | None = None, window: str | None = None,
        page: int = 1, size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            items = [dict(record.dataset) for record in self._datasets]
        if task:
            items = [item for item in items if task in item["关联任务"]]
        if window:
            items = [item for item in items if window in (item["观测开始"], item["观测结束"])]
        total = len(items)
        start = (page - 1) * size
        return items[start:start + size], total

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._find_record(dataset_id)
            if record is None:
                return None
            dataset = dict(record.dataset)
            dataset["版本记录"] = [dict(item) for item in record.versions]
            dataset["事件数量对比"] = [dict(item) for item in record.event_count_history]
            return dataset

    def overview(self) -> dict[str, Any]:
        with self._lock:
            datasets = [record.dataset for record in self._datasets]
            intervals = sum(item["异常区间数"] for item in datasets)
            errors = sum(item["窗口错误总数"] for item in datasets)
            return {
                "数据集总数": len(datasets),
                "异常区间总数": intervals,
                "窗口错误总数": errors,
                "已生成告警通知": len(self.notifications()),
                "任务数": len({item["关联任务"] for item in datasets}),
            }

    def tasks(self) -> list[str]:
        with self._lock:
            return sorted({record.dataset["关联任务"] for record in self._datasets})

    def _find_record(self, dataset_id: str) -> DatasetRecord | None:
        return next((record for record in self._datasets
                     if record.dataset["数据集编号"] == dataset_id), None)


registry = MetricsRegistry()


# --------------------------------------------------------------------------- #
# 内置样例数据：服务启动即有可查看的当前结果（确定性数据，不含随机数）
# --------------------------------------------------------------------------- #
def seed_datasets() -> None:
    samples = sample_payloads()
    # 第三份样例模拟重复上报：先首报，再用扩展窗口数据二次提交
    dataset_ids: list[str] = []
    for payload in samples[:2]:
        dataset, _ = registry.commit(payload)
        dataset_ids.append(dataset["数据集编号"])
    first, _ = registry.commit(samples[2])
    dataset_ids.append(first["数据集编号"])
    registry.commit(samples[3])
    # 启动即由告警中心消费，首屏就能看到与时间线同源的告警通知清单
    for dataset_id in dict.fromkeys(dataset_ids):
        registry.consume(dataset_id)


def sample_payloads() -> list[dict[str, Any]]:
    """返回演示用提交载荷；前端「填充样例」与后端播种共用同一份。"""
    return [
        {
            "数据集编号": "SAMPLE-A",
            "关联任务": "商品页采集任务-TASK01",
            "观测开始": "2026-11-17 09:00",
            "观测结束": "2026-11-17 09:20",
            "批次号": "BATCH-20261117-0900",
            "观测值": [
                {"时段": "2026-11-17 09:00", "采集速率": 128.0, "成功率": 0.98, "平均延迟": 210, "错误次数": 1},
                {"时段": "2026-11-17 09:05", "采集速率": 120.0, "成功率": 0.84, "平均延迟": 460, "错误次数": 6},
                {"时段": "2026-11-17 09:10", "采集速率": 96.0, "成功率": 0.78, "平均延迟": 520, "错误次数": 9},
                {"时段": "2026-11-17 09:15", "采集速率": 130.0, "成功率": 0.97, "平均延迟": 230, "错误次数": 1},
            ],
            "慢请求指标": [],
            "事件流": [],
        },
        {
            "数据集编号": "SAMPLE-B",
            "关联任务": "接口轮询任务-TASK02",
            "观测开始": "2026-11-17 10:00",
            "观测结束": "2026-11-17 10:20",
            "批次号": "BATCH-20261117-1000",
            "观测值": [
                {"时段": "2026-11-17 10:00", "采集速率": 64.0, "成功率": 0.99, "平均延迟": 180, "错误次数": 0},
                {"时段": "2026-11-17 10:05", "采集速率": 60.0, "成功率": 0.96, "平均延迟": 300, "错误次数": 2},
                {"时段": "2026-11-17 10:10", "采集速率": 62.0, "成功率": 0.97, "平均延迟": 260, "错误次数": 1},
                {"时段": "2026-11-17 10:15", "采集速率": 65.0, "成功率": 0.99, "平均延迟": 170, "错误次数": 0},
            ],
            "慢请求指标": [],
            "事件流": [],
        },
        {
            # 首报：事件流先报 10:05 异常
            "数据集编号": "SAMPLE-C-1",
            "关联任务": "增量抓取任务-TASK03",
            "观测开始": "2026-11-17 11:00",
            "观测结束": "2026-11-17 11:20",
            "批次号": "BATCH-20261117-1100",
            "观测值": [
                {"时段": "2026-11-17 11:00", "采集速率": 80.0, "成功率": 0.98, "平均延迟": 240, "错误次数": 1},
                {"时段": "2026-11-17 11:05", "采集速率": 70.0, "成功率": 0.92, "平均延迟": 500, "错误次数": 2},
                {"时段": "2026-11-17 11:10", "采集速率": 82.0, "成功率": 0.98, "平均延迟": 220, "错误次数": 1},
                {"时段": "2026-11-17 11:15", "采集速率": 84.0, "成功率": 0.99, "平均延迟": 200, "错误次数": 0},
            ],
            "慢请求指标": [
                {"异常开始": "2026-11-17 11:10", "异常结束": "2026-11-17 11:15", "延迟毫秒": 960, "观测来源": "慢请求指标"},
            ],
            "事件流": [
                {"时段": "2026-11-17 11:05", "错误次数": 4, "事件类型": "错误峰值"},
            ],
        },
        {
            # 重复上报（同任务同窗口）：慢请求与事件流在 11:10 起点冲突，合并为一个时间线版本
            "数据集编号": "SAMPLE-C-2",
            "关联任务": "增量抓取任务-TASK03",
            "观测开始": "2026-11-17 11:00",
            "观测结束": "2026-11-17 11:20",
            "批次号": "BATCH-20261117-1100-R",
            "观测值": [
                {"时段": "2026-11-17 11:00", "采集速率": 80.0, "成功率": 0.98, "平均延迟": 240, "错误次数": 1},
                {"时段": "2026-11-17 11:05", "采集速率": 70.0, "成功率": 0.92, "平均延迟": 500, "错误次数": 2},
                {"时段": "2026-11-17 11:10", "采集速率": 66.0, "成功率": 0.82, "平均延迟": 980, "错误次数": 7},
                {"时段": "2026-11-17 11:15", "采集速率": 84.0, "成功率": 0.99, "平均延迟": 200, "错误次数": 0},
            ],
            "慢请求指标": [
                {"异常开始": "2026-11-17 11:05", "异常结束": "2026-11-17 11:10", "延迟毫秒": 1200, "观测来源": "慢请求指标"},
            ],
            "事件流": [
                {"时段": "2026-11-17 11:05", "错误次数": 4, "事件类型": "错误峰值"},
                {"时段": "2026-11-17 11:10", "错误次数": 5, "事件类型": "错误峰值"},
            ],
        },
    ]
