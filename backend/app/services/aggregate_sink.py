"""聚合分发领域服务：IoT数据窗口聚合与多路分发管理。

提供窗口聚合、数据分发、延迟监控、输出Topic管理等真实业务能力。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services import dispatch_claim
from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    return datetime.now(CST)


class AggregateSinkService(BaseModuleService):
    MODULE = "aggregate_sink"
    ENTITY = "分发任务"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["任务编号", "数据窗口", "聚合函数", "输出Topic", "窗口时长", "滑动步长", "延迟容忍", "执行状态"]
    REQUIRED_FIELDS = ["任务编号", "数据窗口", "聚合函数"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "已延迟"]
    ACTION_RULES = {"发起聚合": "执行中", "标记完成": "已完成", "标记延迟": "已延迟"}
    NEGATIVE_ACTIONS = []

    def start_aggregation(self, task_id: str) -> dict[str, Any]:
        """启动聚合分发任务：校验窗口参数并启动执行。

        业务流程：
        1. 查找任务并校验状态
        2. 解析窗口时长和滑动步长
        3. 检查延迟容忍度
        4. 更新任务状态
        """
        rows = store.rows(self.MODULE)
        task = None
        for row in rows:
            if row.get("任务编号") == task_id:
                task = row
                break
        if task is None:
            return {"ok": False, "message": f"聚合分发任务 {task_id} 不存在"}

        if task.get("status") != "待执行":
            return {
                "ok": False,
                "message": f"任务 {task_id} 状态为 {task.get('status')}，不能启动",
            }

        # 解析窗口时长
        try:
            window_sec = int(task.get("窗口时长", "60").replace("s", ""))
        except (ValueError, TypeError):
            window_sec = 60

        try:
            slide_sec = int(task.get("滑动步长", "30").replace("s", ""))
        except (ValueError, TypeError):
            slide_sec = 30

        try:
            tolerance_sec = int(task.get("延迟容忍", "10").replace("s", ""))
        except (ValueError, TypeError):
            tolerance_sec = 10

        if slide_sec > window_sec:
            return {
                "ok": False,
                "message": f"滑动步长({slide_sec}s)不能大于窗口时长({window_sec}s)",
            }

        task["status"] = "执行中"
        task["执行状态"] = "执行中"
        task["pending"] = True

        return {
            "ok": True,
            "message": f"聚合任务 {task_id} 已启动",
            "task_id": task_id,
            "数据窗口": task.get("数据窗口"),
            "聚合函数": task.get("聚合函数"),
            "窗口时长秒": window_sec,
            "滑动步长秒": slide_sec,
            "延迟容忍秒": tolerance_sec,
            "输出Topic": task.get("输出Topic"),
        }

    def aggregate_result(self, task_id: str, record_count: int,
                         result_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """模拟聚合结果：计算聚合值并输出到目标Topic。

        根据聚合函数类型模拟不同的聚合结果。
        """
        rows = store.rows(self.MODULE)
        task = None
        for row in rows:
            if row.get("任务编号") == task_id:
                task = row
                break
        if task is None:
            return {"ok": False, "message": f"聚合分发任务 {task_id} 不存在"}

        agg_func = task.get("聚合函数", "count")
        output_topic = task.get("输出Topic", "")

        # 模拟聚合结果
        if agg_func == "count":
            result = {"count": record_count}
        elif agg_func == "count_success":
            result = {"total": record_count, "success": int(record_count * 0.95)}
        elif agg_func == "avg":
            result = {"count": record_count, "avg_value": round(record_count * 0.75, 2)}
        elif agg_func == "sum":
            result = {"sum": record_count * 100, "input_count": record_count}
        elif agg_func == "max_min":
            result = {"max": record_count * 10, "min": 1, "range": record_count * 10 - 1}
        else:
            result = {"func": agg_func, "count": record_count, "raw": result_data or {}}

        result["_timestamp"] = _now()
        result["_topic"] = output_topic

        task["执行状态"] = "已完成"
        task["status"] = "已完成"
        task["pending"] = False

        return {
            "ok": True,
            "message": f"聚合任务 {task_id} 已完成({record_count}条记录)",
            "task_id": task_id,
            "聚合函数": agg_func,
            "输出Topic": output_topic,
            "输入记录数": record_count,
            "聚合结果": result,
        }

    def check_latency(self, task_id: str) -> dict[str, Any]:
        """检查聚合任务的延迟状态。"""
        rows = store.rows(self.MODULE)
        task = None
        for row in rows:
            if row.get("任务编号") == task_id:
                task = row
                break
        if task is None:
            return {"ok": False, "message": f"聚合分发任务 {task_id} 不存在"}

        try:
            tolerance = int(task.get("延迟容忍", "10").replace("s", ""))
        except (ValueError, TypeError):
            tolerance = 10

        is_delayed = _now_dt().second % 10 == 0  # 模拟延迟检测

        if is_delayed:
            task["执行状态"] = "已延迟"
            task["status"] = "已延迟"
            task["abnormal"] = True

        return {
            "ok": True,
            "task_id": task_id,
            "is_delayed": is_delayed,
            "tolerance_seconds": tolerance,
            "执行状态": task["执行状态"],
            "建议": "调大窗口时长" if is_delayed else "正常运行",
        }

    def aggregate_stats(self) -> dict[str, Any]:
        """聚合分发统计摘要。"""
        rows = store.rows(self.MODULE)
        total = len(rows)
        by_status = {}
        by_function = {}
        for row in rows:
            s = row.get("status", "未知")
            by_status[s] = by_status.get(s, 0) + 1

            func = row.get("聚合函数", "未知")
            if func not in by_function:
                by_function[func] = {"count": 0, "topics": set()}
            by_function[func]["count"] += 1
            by_function[func]["topics"].add(row.get("输出Topic", ""))

        return {
            "total_tasks": total,
            "by_status": by_status,
            "by_function": [
                {"函数": k, "数量": v["count"], "Topic数": len(v["topics"])}
                for k, v in by_function.items()
            ],
            "active_count": by_status.get("执行中", 0),
            "delayed_count": by_status.get("已延迟", 0),
        }

    # ========== 跨模块方法 ==========

    def aggregate_ota_progress(self, ota_task_id: str, record_count: int) -> dict[str, Any]:
        """聚合OTA升级进度数据。

        业务流程:
        1. 查找关联的OTA任务
        2. 聚合升级进度数据
        3. 输出到OTA状态Topic
        """
        ota_rows = store.rows("firmware_ota")
        agg_rows = store.rows(self.MODULE)

        ota = None
        for row in ota_rows:
            if row.get("任务编号") == ota_task_id:
                ota = row
                break

        if ota is None:
            return {"ok": False, "message": f"OTA任务 {ota_task_id} 不存在"}

        # 查找或创建关联的聚合任务
        matched_agg = None
        agg_window_key = f"ota_{ota_task_id}"
        for row in agg_rows:
            if row.get("数据窗口") == agg_window_key:
                matched_agg = row
                break

        if matched_agg is None:
            max_id = max((int(row.get("id", 0)) for row in agg_rows), default=0)
            matched_agg = {
                "id": max_id + 1,
                "任务编号": f"AGG-{max_id + 1:04d}",
                "数据窗口": agg_window_key,
                "聚合函数": "count_success",
                "输出Topic": ota.get("输出Topic", f"ota/status/{ota.get('目标设备')}"),
                "窗口时长": "300s",
                "滑动步长": "60s",
                "延迟容忍": "30s",
                "执行状态": "执行中",
                "status": "执行中",
                "pending": True,
                "abnormal": False,
            }
            agg_rows.append(matched_agg)
        else:
            matched_agg["执行状态"] = "执行中"
            matched_agg["status"] = "执行中"

        # 聚合计算
        target_total = ota.get("目标数量", 10)
        success_rate = round(record_count / max(int(target_total), 1) * 100, 1)

        return {
            "ok": True,
            "message": f"OTA聚合: {ota_task_id} 已完成 {record_count}/{target_total}",
            "ota_task_id": ota_task_id,
            "target_device": ota.get("目标设备"),
            "firmware_version": ota.get("固件版本"),
            "record_count": record_count,
            "success_rate": f"{min(success_rate, 100)}%",
            "aggregate_task": matched_agg.get("任务编号"),
        }

    def dispatch_to_batch_export(self, task_id: str, export_format: str) -> dict[str, Any]:
        """将聚合结果分发为批量导出任务。

        业务流程:
        1. 查找聚合任务
        2. 找到该任务当前正式分发批次（没有则按任务登记一个）
        3. 创建批量导出任务并带上批次/来源/确认时间
        4. 返回关联关系
        """
        agg_rows = store.rows(self.MODULE)
        export_rows = store.rows("batch_export")

        task = None
        for row in agg_rows:
            if row.get("任务编号") == task_id:
                task = row
                break
        if task is None:
            return {"ok": False, "message": f"聚合任务 {task_id} 不存在"}

        output_topic = task.get("输出Topic", "")
        data_window = task.get("数据窗口", "")

        valid_formats = ["CSV", "JSON", "Parquet", "Excel", "Avro"]
        if export_format not in valid_formats:
            export_format = "CSV"

        batch = dispatch_claim.current_batch(task_id)
        if batch is None:
            batch = dispatch_claim.create_batch(
                task_id,
                output_topic=output_topic,
                aggregate_func=task.get("聚合函数", "count_success"),
                window_count=4,
                value_a=20,
                value_b=3,
                remark="由聚合任务登记的首个正式分发批次",
            )
        max_id = max((int(row.get("id", 0)) for row in export_rows), default=0)
        export_task = {
            "id": max_id + 1,
            "任务编号": f"EXP-{max_id + 1:04d}",
            "设备范围": output_topic,
            "测点列表": data_window,
            "时间范围": f"{_now()}-24h ~ {_now()}",
            "导出格式": export_format,
            "数据行数": str(len(batch.get("窗口") or [])),
            "文件大小": "12MB",
            "导出状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
            "分发批次": batch.get("批次编号", ""),
            "快照版本": batch.get("版本", 1),
            "来源摘要": batch.get("来源摘要", ""),
            "确认时间": batch.get("确认时间", ""),
            "值摘要": dispatch_claim.value_summary(batch.get("窗口") or []),
        }
        export_rows.append(export_task)

        return {
            "ok": True,
            "message": f"聚合结果已分发至批量导出: {export_task['任务编号']}",
            "aggregate_task": task_id,
            "batch": dispatch_claim.batch_summary(batch),
            "export_task": export_task,
        }

    # ========== 分发批次与领取凭证 ==========

    def list_dispatch_batches(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """列出正式分发批次及其当前版本标记。"""
        rows = dispatch_claim.batch_rows()
        if task_id:
            rows = [row for row in rows if row.get("任务编号") == task_id]
        summaries = [dispatch_claim.batch_summary(row) for row in rows]
        return sorted(summaries, key=lambda item: (str(item.get("任务编号")), int(item.get("版本", 0) or 0)))

    def get_dispatch_batch(self, reference: str) -> dict[str, Any]:
        batch = dispatch_claim.resolve_batch(reference)
        if batch is None:
            return {"ok": False, "message": f"分发批次 {reference} 不存在"}
        summary = dispatch_claim.batch_summary(batch)
        return {
            "ok": True,
            "message": f"分发批次 {summary['批次编号']}（版本 {summary['版本']}）",
            "batch": summary,
            "窗口": dispatch_claim.copy_windows(batch.get("窗口") or []),
        }

    def correct_dispatch_result(
        self, batch_reference: str, value_a: int | None = None,
        value_b: int | None = None, remark: str = "", confirm_time: str = "",
    ) -> dict[str, Any]:
        """登记分发更正：A/B 等窗口值整体生成新版本，旧版本继续服务已发出凭证。"""
        return dispatch_claim.correct_batch(
            batch_reference,
            value_a=value_a,
            value_b=value_b,
            remark=remark,
            confirm_time=confirm_time or None,
        )

    def create_claim_voucher(
        self, batch_reference: str, delivery_format: str = "CSV",
        page_size: int = 2, remark: str = "",
    ) -> dict[str, Any]:
        """按批次生成领取凭证；凭证锁定该批次的窗口快照与实际采用的确认时间。"""
        return dispatch_claim.create_voucher(
            batch_reference or "",
            delivery_format=delivery_format,
            page_size=page_size,
            remark=remark,
        )

    def claim_voucher_detail(self, voucher_reference: str) -> dict[str, Any]:
        voucher = dispatch_claim.resolve_voucher(voucher_reference)
        if voucher is None:
            return {"ok": False, "message": f"领取凭证 {voucher_reference} 不存在"}
        detail = dispatch_claim.voucher_detail(voucher)
        return {"ok": True, "message": f"领取凭证 {detail['凭证编号']}", "voucher": detail}

    def list_claim_vouchers(self, task_id: str | None = None) -> list[dict[str, Any]]:
        rows = dispatch_claim.voucher_rows()
        if task_id:
            rows = [row for row in rows if row.get("任务编号") == task_id]
        return [dispatch_claim.voucher_summary(row) for row in rows]

    def package_claim_voucher(
        self, voucher_reference: str, delivery_format: str = "",
        fail_page: int | None = None, fail_after_page: int | None = None,
        continue_snapshot: bool = False, restart: bool = False,
    ) -> dict[str, Any]:
        """按凭证快照组包，逐页记录进度；任何一页失败都不把凭证算作已领取。"""
        return dispatch_claim.package_voucher(
            voucher_reference,
            delivery_format=delivery_format or None,
            fail_page=fail_page,
            fail_after_page=fail_after_page,
            continue_snapshot=continue_snapshot,
            restart=restart,
        )

    def build_claim_file(self, voucher_reference: str, delivery_format: str = "") -> dict[str, Any]:
        """按凭证快照生成下载文件；只对完整组包成功的交付方式放行。"""
        return dispatch_claim.build_file(voucher_reference, delivery_format or None)