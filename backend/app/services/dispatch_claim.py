"""分发批次与领取凭证的共享领域逻辑。

聚合分发把一次正式确认的窗口结果登记为「分发批次」；操作人员按批次发起领取时
生成一张凭证，凭证在创建瞬间深拷贝该批次的窗口快照和确认时间。之后无论批次被
更正、无论换成哪种交付方式组包，凭证都只使用自己锁定的那一版快照，保证一份凭证
最终交付的窗口值不会混入前后两版数据。

组包按页推进：成功页会保留并计入已成功行数，失败页之后的页不再假装成功；
只有所有页都组包成功，凭证才算「已领取」，半成品始终是未领取状态。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.store import store

CST = timezone(timedelta(hours=8))

VALID_FORMATS = ["CSV", "JSON", "Parquet", "Excel", "Avro"]
# 领取凭证需要实际组出可直接交付的文件，目前只对这两种格式生成真实内容。
CLAIM_FORMATS = ["CSV", "JSON"]
FORMAT_ALIASES = {
    "csv": "CSV",
    "json": "JSON",
    "parquet": "Parquet",
    "excel": "Excel",
    "xlsx": "Excel",
    "avro": "Avro",
}
FILE_EXTENSIONS = {
    "CSV": "csv",
    "JSON": "json",
    "Parquet": "parquet",
    "Excel": "xlsx",
    "Avro": "avro",
}
MEDIA_TYPES = {
    "CSV": "text/csv; charset=utf-8",
    "JSON": "application/json; charset=utf-8",
    "Parquet": "application/octet-stream",
    "Excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "Avro": "application/octet-stream",
}
WINDOW_KEYS = ("窗口编号", "窗口开始", "窗口结束", "值A", "值B")


def now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def normalize_format(value: Any, default: str = "CSV") -> str:
    if value is None or str(value).strip() == "":
        return default
    raw = str(value).strip()
    if raw.upper() in VALID_FORMATS:
        return raw.upper() if raw.upper() != "PARQUET" else "Parquet"
    canonical = FORMAT_ALIASES.get(raw.casefold())
    return canonical or default


def as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def copy_windows(windows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [{key: row.get(key) for key in WINDOW_KEYS} for row in (windows or [])]


def unique_values(windows: list[dict[str, Any]], key: str) -> list[Any]:
    values: list[Any] = []
    for row in windows:
        value = row.get(key)
        if value not in values:
            values.append(value)
    return values


def value_summary(windows: list[dict[str, Any]]) -> str:
    a_values = unique_values(windows, "值A")
    b_values = unique_values(windows, "值B")
    label = f"A={','.join(str(v) for v in a_values)}、B={','.join(str(v) for v in b_values)}"
    return label


def digest(parts: dict[str, Any]) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_digest(
    batch_code: str,
    version: int,
    task_id: str,
    confirm_time: str,
    windows: list[dict[str, Any]],
) -> str:
    return digest({
        "批次编号": batch_code,
        "快照版本": version,
        "任务编号": task_id,
        "确认时间": confirm_time,
        "窗口": copy_windows(windows),
    })


def batch_rows() -> list[dict[str, Any]]:
    return store.rows("dispatch_batches")


def voucher_rows() -> list[dict[str, Any]]:
    return store.rows("claim_vouchers")


def current_batch(task_id: str) -> dict[str, Any] | None:
    rows = [row for row in batch_rows() if row.get("任务编号") == task_id]
    current = [row for row in rows if row.get("当前版本")]
    if current:
        return max(current, key=lambda row: int(row.get("版本", 0)) or 0)
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get("版本", 0)) or 0)


def resolve_batch(reference: Any) -> dict[str, Any] | None:
    """按批次编号、原始批次编号、任务编号或数字 id 找到分发批次。

    明确给出某个历史批次编号时返回该版本；只给任务编号时返回当前正式版本。
    """
    if reference is None:
        return None
    text = str(reference).strip()
    if not text:
        return None

    exact = [row for row in batch_rows() if str(row.get("批次编号")) == text]
    if exact:
        return exact[0]

    if text.isdigit():
        entry_id = int(text)
        for row in batch_rows():
            if int(row.get("id", 0)) == entry_id:
                return row

    original = [
        row for row in batch_rows()
        if str(row.get("原始批次编号")) == text and row.get("当前版本")
    ]
    if original:
        return original[0]

    by_task = [row for row in batch_rows() if str(row.get("任务编号")) == text]
    if by_task:
        return max(by_task, key=lambda row: int(row.get("版本", 0)) or 0)

    return None


def resolve_voucher(reference: Any) -> dict[str, Any] | None:
    if reference is None:
        return None
    text = str(reference).strip()
    if not text:
        return None
    for row in voucher_rows():
        if str(row.get("凭证编号")) == text:
            return row
    if text.isdigit():
        for row in voucher_rows():
            if int(row.get("id", 0)) == int(text):
                return row
    return None


def batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
    windows = batch.get("窗口") or []
    return {
        "id": batch.get("id"),
        "批次编号": batch.get("批次编号"),
        "原始批次编号": batch.get("原始批次编号"),
        "任务编号": batch.get("任务编号"),
        "版本": batch.get("版本"),
        "聚合函数": batch.get("聚合函数"),
        "输出Topic": batch.get("输出Topic"),
        "分发状态": batch.get("分发状态"),
        "确认时间": batch.get("确认时间"),
        "当前版本": bool(batch.get("当前版本")),
        "更正说明": batch.get("更正说明", ""),
        "窗口数": len(windows),
        "值摘要": value_summary(windows),
        "值A": unique_values(windows, "值A"),
        "值B": unique_values(windows, "值B"),
        "来源摘要": batch.get("来源摘要") or snapshot_digest(
            batch.get("批次编号", ""), int(batch.get("版本", 1) or 1),
            batch.get("任务编号", ""), batch.get("确认时间", ""), windows,
        ),
        "status": batch.get("status"),
        "pending": batch.get("pending"),
        "abnormal": batch.get("abnormal"),
    }


def _ensure_record_role(voucher: dict[str, Any], delivery_format: str) -> dict[str, Any]:
    records = voucher.setdefault("交付记录", [])
    for record in records:
        if record.get("交付方式") == delivery_format:
            return record
    record = {
        "交付方式": delivery_format,
        "组包状态": "待组包",
        "已领取": False,
        "已成功页数": 0,
        "已成功行数": 0,
        "总页数": 0,
        "总行数": voucher.get("行数总计", 0),
        "窗口数": voucher.get("窗口数", 0),
        "确认时间": voucher.get("确认时间", ""),
        "来源": voucher.get("来源", ""),
        "快照摘要": voucher.get("快照摘要", ""),
        "失败原因": "",
        "需要快照确认": False,
        "页": [],
        "文件摘要": "",
        "更新时间": now(),
    }
    records.append(record)
    return record


def _page_chunks(windows: list[dict[str, Any]], page_size: int) -> list[list[dict[str, Any]]]:
    size = max(page_size, 1)
    return [windows[index:index + size] for index in range(0, len(windows), size)]


def _refresh_record_counts(
    voucher: dict[str, Any], record: dict[str, Any], total_pages: int | None = None,
) -> None:
    pages = record.get("页", [])
    succeeded = [page for page in pages if page.get("状态") == "已组包"]
    record["总页数"] = len(pages) if total_pages is None else total_pages
    record["总行数"] = voucher.get("行数总计", sum(page.get("行数", 0) for page in pages))
    record["已成功页数"] = len(succeeded)
    record["已成功行数"] = sum(int(page.get("行数", 0)) for page in succeeded)
    record["已领取"] = (
        bool(pages)
        and len(succeeded) == record["总页数"]
        and record["已成功行数"] == record["总行数"]
    )
    record["组包状态"] = "已领取" if record["已领取"] else (
        "组包失败" if any(page.get("状态") == "组包失败" for page in pages) else "待组包"
    )
    record["更新时间"] = now()


def _unfinished_pages(
    chunks: list[list[dict[str, Any]]],
    pages: dict[int, dict[str, Any]],
) -> None:
    """把尚未开始的页也登记出来，让页进度在失败时仍然可读。"""
    for index, page_windows in enumerate(chunks):
        page_no = index + 1
        if page_no in pages:
            continue
        pages[page_no] = {
            "页号": page_no,
            "状态": "未开始",
            "行数": len(page_windows),
            "窗口编号": [row.get("窗口编号") for row in page_windows],
            "窗口开始": page_windows[0].get("窗口开始") if page_windows else "",
            "窗口结束": page_windows[-1].get("窗口结束") if page_windows else "",
            "失败原因": "",
            "组包时间": "",
        }


def conversion_progress(voucher: dict[str, Any], record: dict[str, Any] | None = None) -> dict[str, Any]:
    if record is None:
        records = voucher.get("交付记录") or []
        record = records[-1] if records else None
    if record is None:
        total_pages = 0
        succeeded_pages = 0
        total_rows = int(voucher.get("行数总计", 0) or 0)
        succeeded_rows = 0
        status = voucher.get("组包状态", "待组包")
    else:
        total_pages = int(record.get("总页数", 0) or 0)
        succeeded_pages = int(record.get("已成功页数", 0) or 0)
        total_rows = int(record.get("总行数", voucher.get("行数总计", 0)) or 0)
        succeeded_rows = int(record.get("已成功行数", 0) or 0)
        status = record.get("组包状态", "待组包")
    percent = round((succeeded_pages / total_pages) * 100, 1) if total_pages else 0.0
    return {
        "组包状态": status,
        "已领取": bool(record.get("已领取")) if record else False,
        "总页数": total_pages,
        "已成功页数": succeeded_pages,
        "剩余页数": max(total_pages - succeeded_pages, 0),
        "总行数": total_rows,
        "已成功行数": succeeded_rows,
        "剩余行数": max(total_rows - succeeded_rows, 0),
        "百分比": percent,
        "文本": f"{succeeded_pages}/{total_pages} 页 · {succeeded_rows}/{total_rows} 行",
        "交付方式": record.get("交付方式") if record else voucher.get("交付方式", ""),
    }


def voucher_detail(voucher: dict[str, Any]) -> dict[str, Any]:
    latest_record = (voucher.get("交付记录") or [None])[-1]
    progress = conversion_progress(voucher, latest_record)
    current = current_batch(voucher.get("任务编号", ""))
    recomputed = snapshot_digest(
        voucher.get("批次编号", ""),
        int(voucher.get("快照版本", 1) or 1),
        voucher.get("任务编号", ""),
        voucher.get("确认时间", ""),
        voucher.get("窗口") or [],
    )
    source_check = {
        "凭证锁定批次": voucher.get("批次编号"),
        "快照版本": voucher.get("快照版本"),
        "采用确认时间": voucher.get("确认时间"),
        "当前正式批次": current.get("批次编号") if current else "无",
        "当前正式版本": current.get("版本") if current else None,
        "当前确认时间": current.get("确认时间") if current else "",
        "是否同一版本": (
            bool(current)
            and int(current.get("版本", 0) or 0) == int(voucher.get("快照版本", 0) or 0)
        ),
        "快照摘要": voucher.get("快照摘要"),
        "重新计算摘要": recomputed,
        "摘要一致": recomputed == voucher.get("快照摘要"),
        "可核验": recomputed == voucher.get("快照摘要"),
        "说明": (
            "批次已更正；本凭证按锁定快照继续交付，不混入更正后的数据。"
            if current and int(current.get("版本", 0) or 0) != int(voucher.get("快照版本", 0) or 0)
            else "凭证锁定版本与当前正式分发一致。"
        ),
    }
    records = []
    for record in voucher.get("交付记录") or []:
        records.append({
            **{key: record.get(key) for key in (
                "交付方式", "组包状态", "已领取", "已成功页数", "已成功行数",
                "总页数", "总行数", "失败原因", "需要快照确认", "文件摘要", "更新时间",
            )},
            "转换进度": conversion_progress(voucher, record),
            "页": record.get("页") or [],
        })
    return {
        "id": voucher.get("id"),
        "凭证编号": voucher.get("凭证编号"),
        "任务编号": voucher.get("任务编号"),
        "批次编号": voucher.get("批次编号"),
        "原始批次编号": voucher.get("原始批次编号"),
        "快照版本": voucher.get("快照版本"),
        "聚合函数": voucher.get("聚合函数"),
        "输出Topic": voucher.get("输出Topic"),
        "来源": voucher.get("来源"),
        "确认时间": voucher.get("确认时间"),
        "采用确认时间": voucher.get("确认时间"),
        "快照摘要": voucher.get("快照摘要"),
        "窗口数": voucher.get("窗口数"),
        "行数总计": voucher.get("行数总计"),
        "页大小": voucher.get("页大小"),
        "已选择批次": voucher.get("已选择批次") or voucher.get("批次编号"),
        "交付方式": voucher.get("交付方式"),
        "组包状态": voucher.get("组包状态"),
        "已领取": bool(voucher.get("已领取")),
        "未领取": not bool(voucher.get("已领取")),
        "部分交付": bool(voucher.get("部分交付")),
        "已成功页数": progress["已成功页数"],
        "已成功行数": progress["已成功行数"],
        "显示行数": progress["已成功行数"],
        "剩余页数": progress["剩余页数"],
        "百分比": progress["百分比"],
        "转换进度": progress,
        "失败原因": voucher.get("失败原因", ""),
        "需要快照确认": bool(voucher.get("需要快照确认")),
        "来源核对": source_check,
        "交付记录": records,
        "窗口": copy_windows(voucher.get("窗口") or []),
        "创建时间": voucher.get("创建时间"),
        "更新时间": voucher.get("更新时间"),
    }


def voucher_summary(voucher: dict[str, Any]) -> dict[str, Any]:
    detail = voucher_detail(voucher)
    detail.pop("窗口", None)
    detail.pop("交付记录", None)
    return detail


def _pack_page(
    page: dict[str, Any],
    page_no: int,
    windows: list[dict[str, Any]],
    page_size: int,
    status: str,
    reason: str = "",
) -> None:
    page.update({
        "页号": page_no,
        "窗口编号": [row.get("窗口编号") for row in windows],
        "窗口开始": windows[0].get("窗口开始") if windows else "",
        "窗口结束": windows[-1].get("窗口结束") if windows else "",
        "行数": len(windows),
        "页大小": page_size,
        "状态": status,
        "失败原因": reason,
        "组包时间": now() if status == "已组包" else "",
    })


def _mark_failure(
    voucher: dict[str, Any],
    record: dict[str, Any],
    reason: str,
    needs_snapshot: bool = False,
) -> dict[str, Any]:
    _refresh_record_counts(voucher, record)
    record["组包状态"] = "组包失败"
    record["已领取"] = False
    record["失败原因"] = reason
    if needs_snapshot:
        record["需要快照确认"] = True
        voucher["需要快照确认"] = True
    voucher["组包状态"] = "组包失败"
    voucher["失败原因"] = reason
    voucher["已领取"] = any(
        bool(item.get("已领取")) for item in voucher.get("交付记录") or []
        if item is not record
    )
    voucher["部分交付"] = any(
        0 < int(item.get("已成功行数", 0) or 0) < int(item.get("总行数", 0) or 0)
        for item in voucher.get("交付记录") or []
    )
    voucher["交付方式"] = record.get("交付方式")
    voucher["已选择批次"] = voucher.get("批次编号")
    voucher["已成功页数"] = record.get("已成功页数", 0)
    voucher["已成功行数"] = record.get("已成功行数", 0)
    voucher["更新时间"] = now()
    return {
        "ok": False,
        "message": reason,
        "voucher": voucher_detail(voucher),
        "已领取": False,
        "组包状态": "组包失败",
        "转换进度": conversion_progress(voucher, record),
        "已选择批次": voucher.get("已选择批次"),
        "交付方式": voucher.get("交付方式"),
    }


def create_batch(
    task_id: str,
    *,
    value_a: Any = 0,
    value_b: Any = 0,
    window_count: int = 4,
    windows: list[dict[str, Any]] | None = None,
    output_topic: str = "",
    aggregate_func: str = "count_success",
    confirm_time: str | None = None,
    remark: str = "",
    status: str = "已确认",
) -> dict[str, Any]:
    """登记一个正式分发批次（第一批版本）。"""
    task_id = str(task_id).strip()
    if not task_id:
        raise ValueError("任务编号不能为空")
    if windows is None:
        count = max(int(window_count), 1)
        base = datetime.fromisoformat(confirm_time) if confirm_time else datetime.now(CST)
        windows = []
        for index in range(count):
            start = base - timedelta(minutes=5 * (count - index))
            end = start + timedelta(minutes=5)
            windows.append({
                "窗口编号": f"W-{index + 1:03d}",
                "窗口开始": start.isoformat(timespec="seconds"),
                "窗口结束": end.isoformat(timespec="seconds"),
                "值A": as_int(value_a, 0),
                "值B": as_int(value_b, 0),
            })
    rows = batch_rows()
    max_id = max((int(row.get("id", 0)) for row in rows), default=0)
    batch_code = f"DSP-{max_id + 1:04d}"
    confirmed = confirm_time or now()
    entry = {
        "id": max_id + 1,
        "批次编号": batch_code,
        "原始批次编号": batch_code,
        "任务编号": task_id,
        "版本": 1,
        "聚合函数": aggregate_func,
        "输出Topic": output_topic or f"agg/result/{task_id}",
        "分发状态": status,
        "确认时间": confirmed,
        "更正说明": remark or "首个正式分发批次",
        "当前版本": True,
        "来源摘要": snapshot_digest(batch_code, 1, task_id, confirmed, windows),
        "窗口": copy_windows(windows),
        "status": status,
        "pending": False,
        "abnormal": False,
    }
    rows.append(entry)
    return entry


def correct_batch(
    reference: Any,
    *,
    value_a: Any = None,
    value_b: Any = None,
    remark: str = "",
    confirm_time: str | None = None,
) -> dict[str, Any]:
    """登记分发更正：追加新的正式版本，旧版本保留给已锁定的领取凭证。"""
    batch = resolve_batch(reference)
    if batch is None:
        return {"ok": False, "message": f"分发批次 {reference} 不存在"}
    task_id = batch.get("任务编号", "")
    current = current_batch(task_id) or batch
    original_code = current.get("原始批次编号") or current.get("批次编号")
    windows = copy_windows(current.get("窗口") or [])
    if value_a is not None:
        for row in windows:
            row["值A"] = as_int(value_a, row.get("值A"))
    if value_b is not None:
        for row in windows:
            row["值B"] = as_int(value_b, row.get("值B"))
    version = (int(current.get("版本", 1) or 1)) + 1
    rows = batch_rows()
    max_id = max((int(row.get("id", 0)) for row in rows), default=0)
    batch_code = f"{original_code}-V{version}"
    confirmed = confirm_time or now()
    summary_bits = []
    if value_a is not None:
        summary_bits.append(f"A={value_a}")
    if value_b is not None:
        summary_bits.append(f"B={value_b}")
    correction_note = remark or ("分发更正 " + " ".join(summary_bits)).strip()

    for row in rows:
        if row.get("任务编号") == task_id:
            row["当前版本"] = False

    entry = {
        "id": max_id + 1,
        "批次编号": batch_code,
        "原始批次编号": original_code,
        "任务编号": task_id,
        "版本": version,
        "聚合函数": current.get("聚合函数", ""),
        "输出Topic": current.get("输出Topic", ""),
        "分发状态": "已确认",
        "确认时间": confirmed,
        "更正说明": correction_note,
        "当前版本": True,
        "来源摘要": snapshot_digest(batch_code, version, task_id, confirmed, windows),
        "窗口": windows,
        "status": "已确认",
        "pending": False,
        "abnormal": False,
    }
    rows.append(entry)

    pinned = [
        voucher for voucher in voucher_rows()
        if voucher.get("任务编号") == task_id
        and int(voucher.get("快照版本", 0) or 0) < version
    ]
    return {
        "ok": True,
        "message": f"分发已更正为 {entry['批次编号']}（版本 {version}），已锁定凭证仍使用原快照",
        "batch": batch_summary(entry),
        "previous_batch": batch_summary(current),
        "值摘要": value_summary(windows),
        "已锁定凭证数": len(pinned),
        "锁定凭证": [voucher.get("凭证编号") for voucher in pinned],
    }


def create_voucher(
    batch_reference: Any,
    *,
    delivery_format: Any = "CSV",
    page_size: Any = 2,
    remark: str = "",
) -> dict[str, Any]:
    """按分发批次生成领取凭证，创建时锁定窗口快照与确认时间。"""
    batch = resolve_batch(batch_reference)
    if batch is None:
        return {"ok": False, "message": f"分发批次 {batch_reference} 不存在"}
    if batch.get("分发状态") != "已确认":
        return {
            "ok": False,
            "message": f"分发批次 {batch.get('批次编号')} 未正式确认，不能领取",
        }
    windows = copy_windows(batch.get("窗口") or [])
    if not windows:
        return {"ok": False, "message": "分发批次没有可用窗口，不能生成领取凭证"}
    fmt = normalize_format(delivery_format)
    if fmt not in CLAIM_FORMATS:
        return {
            "ok": False,
            "message": f"领取凭证暂只支持 {' / '.join(CLAIM_FORMATS)} 交付，当前为 {fmt}",
        }
    size = as_int(page_size, 2) or 2
    size = min(max(int(size), 1), 100)
    rows = voucher_rows()
    max_id = max((int(row.get("id", 0)) for row in rows), default=0)
    voucher_code = f"CLM-{max_id + 1:04d}"
    confirmed = batch.get("确认时间", "")
    version = int(batch.get("版本", 1) or 1)
    stored_digest = snapshot_digest(
        batch.get("批次编号", ""), version, batch.get("任务编号", ""), confirmed, windows,
    )
    record = {
        "交付方式": fmt,
        "组包状态": "待组包",
        "已领取": False,
        "已成功页数": 0,
        "已成功行数": 0,
        "总页数": (len(windows) + size - 1) // size,
        "总行数": len(windows),
        "窗口数": len(windows),
        "确认时间": confirmed,
        "来源": f"正式分发 {batch.get('批次编号')} v{version}",
        "快照摘要": stored_digest,
        "失败原因": "",
        "需要快照确认": False,
        "页": [],
        "文件摘要": "",
        "更新时间": now(),
    }
    voucher = {
        "id": max_id + 1,
        "凭证编号": voucher_code,
        "任务编号": batch.get("任务编号", ""),
        "批次编号": batch.get("批次编号", ""),
        "原始批次编号": batch.get("原始批次编号", ""),
        "快照版本": version,
        "聚合函数": batch.get("聚合函数", ""),
        "输出Topic": batch.get("输出Topic", ""),
        "来源": f"正式分发 {batch.get('批次编号')} v{version}",
        "确认时间": confirmed,
        "快照摘要": stored_digest,
        "窗口": windows,
        "窗口数": len(windows),
        "行数总计": len(windows),
        "页大小": size,
        "已选择批次": batch.get("批次编号", ""),
        "创建时当前版本": bool(batch.get("当前版本")),
        "交付方式": fmt,
        "组包状态": "待组包",
        "已领取": False,
        "已成功页数": 0,
        "已成功行数": 0,
        "部分交付": False,
        "需要快照确认": False,
        "失败原因": "",
        "创建时间": now(),
        "更新时间": now(),
        "备注": remark,
        "交付记录": [record],
        "status": "待组包",
        "pending": True,
        "abnormal": False,
    }
    rows.append(voucher)
    return {
        "ok": True,
        "message": f"已生成领取凭证 {voucher_code}，锁定 {voucher['来源']}（确认时间 {confirmed}）",
        "voucher": voucher_detail(voucher),
        "凭证编号": voucher_code,
        "批次编号": voucher["批次编号"],
        "快照版本": version,
        "采用确认时间": confirmed,
        "快照摘要": stored_digest,
    }


def package_voucher(
    voucher_reference: Any,
    *,
    delivery_format: Any = None,
    fail_page: Any = None,
    fail_after_page: Any = None,
    continue_snapshot: bool = False,
    restart: bool = False,
) -> dict[str, Any]:
    """按凭证快照组包，并按页记录成功/失败进度。

    - 分发批次在本凭证创建之后被更正时，首次组包会先成功组出第一页，
      随后停在「需按凭证快照继续」的失败状态；再次领取时按锁定快照继续。
    - fail_page / fail_after_page 是显式的故障注入，用于验证失败续办，不影响快照来源。
    """
    voucher = resolve_voucher(voucher_reference)
    if voucher is None:
        return {"ok": False, "message": f"领取凭证 {voucher_reference} 不存在"}
    fmt = normalize_format(delivery_format, voucher.get("交付方式", "CSV"))
    if fmt not in CLAIM_FORMATS:
        return {
            "ok": False,
            "message": f"领取凭证暂只支持 {' / '.join(CLAIM_FORMATS)} 组包，当前为 {fmt}",
            "已领取": False,
            "组包状态": "不支持的交付方式",
        }
    voucher["交付方式"] = fmt
    voucher["已选择批次"] = voucher.get("批次编号")
    record = _ensure_record_role(voucher, fmt)
    if restart:
        record["页"] = []
        record["已领取"] = False
        record["组包状态"] = "待组包"
        record["失败原因"] = ""
        record["需要快照确认"] = False

    windows = copy_windows(voucher.get("窗口") or [])
    if not windows:
        return _mark_failure(voucher, record, "凭证没有可用窗口，无法组包")
    size = max(int(voucher.get("页大小", 2) or 2), 1)
    chunks = _page_chunks(windows, size)
    record["总页数"] = len(chunks)
    record["总行数"] = len(windows)

    current = current_batch(voucher.get("任务编号", ""))
    # 只有「创建凭证时该批次仍是当前正式版本、之后被更正」的情况才需要确认快照；
    # 操作人员主动选择历史批次登记凭证时，直接按该历史快照组包。
    drift = (
        bool(current)
        and bool(voucher.get("创建时当前版本", True))
        and int(current.get("版本", 0) or 0) != int(voucher.get("快照版本", 0) or 0)
    )
    has_progress = any(page.get("状态") == "已组包" for page in record.get("页", []))
    snapshot_acknowledged = (
        bool(voucher.get("快照已确认"))
        or bool(continue_snapshot)
        or has_progress
    )

    existing_pages = {int(page.get("页号", 0) or 0): page for page in record.get("页", [])}
    if restart:
        existing_pages = {}

    def ensure_page(page_no: int, page_windows: list[dict[str, Any]]) -> dict[str, Any]:
        page = existing_pages.get(page_no)
        if page is None or restart:
            page = {}
        existing_pages[page_no] = page
        page.setdefault("状态", "未开始")
        page.setdefault("行数", len(page_windows))
        page.setdefault("窗口编号", [row.get("窗口编号") for row in page_windows])
        page.setdefault("窗口开始", page_windows[0].get("窗口开始") if page_windows else "")
        page.setdefault("窗口结束", page_windows[-1].get("窗口结束") if page_windows else "")
        page.setdefault("失败原因", "")
        page.setdefault("组包时间", "")
        page.setdefault("页号", page_no)
        return page

    def succeed_page(page_no: int, page_windows: list[dict[str, Any]]) -> None:
        page = ensure_page(page_no, page_windows)
        _pack_page(page, page_no, page_windows, size, "已组包")

    # 批次已更正但凭证尚未确认快照：先组出第一页，再停在失败态等待续办。
    if drift and not snapshot_acknowledged:
        if len(chunks) >= 1 and existing_pages.get(1, {}).get("状态") != "已组包":
            succeed_page(1, chunks[0])
        _unfinished_pages(chunks, existing_pages)
        record["页"] = [existing_pages[number] for number in sorted(existing_pages)]
        _refresh_record_counts(voucher, record, total_pages=len(chunks))
        reason = (
            f"分发批次已更正：凭证锁定 {voucher.get('批次编号')} v{voucher.get('快照版本')}，"
            f"当前正式版本为 v{current.get('版本')}；第一页已按凭证快照组出，"
            "请按原凭证继续领取，剩余页仍使用锁定快照。"
        )
        result = _mark_failure(voucher, record, reason, needs_snapshot=True)
        # 失败结果已经把锁定快照和实际采用的确认时间交代清楚，
        # 之后再领取（无论换不换交付方式）都按原快照续办，避免同一凭证混版。
        voucher["快照已确认"] = True
        return result

    if drift and snapshot_acknowledged:
        voucher["快照已确认"] = True
        record["需要快照确认"] = False

    injected_fail = as_int(fail_page)
    injected_after = as_int(fail_after_page)
    failure_reason = ""

    for index, page_windows in enumerate(chunks):
        page_no = index + 1
        page = ensure_page(page_no, page_windows)
        if page.get("状态") == "已组包" and not restart:
            continue
        if injected_fail is not None and injected_fail == page_no:
            page.update({
                "状态": "组包失败",
                "失败原因": f"故障注入：第 {page_no} 页组包失败",
                "行数": len(page_windows),
                "组包时间": "",
            })
            failure_reason = f"第 {page_no} 页组包失败（故障注入），已成功内容保留，未算作已领取"
            break
        succeed_page(page_no, page_windows)
        if injected_after is not None and injected_after == page_no:
            failure_reason = (
                f"第 {page_no} 页组包后故障注入：后续页停止组包，"
                "已成功内容保留，未算作已领取"
            )
            break

    record["页"] = [existing_pages[number] for number in sorted(existing_pages)]
    _refresh_record_counts(voucher, record, total_pages=len(chunks))

    if failure_reason or not record["已领取"]:
        if not failure_reason:
            failure_reason = record.get("失败原因") or "组包未完成"
        _unfinished_pages(chunks, existing_pages)
        record["页"] = [existing_pages[number] for number in sorted(existing_pages)]
        _refresh_record_counts(voucher, record, total_pages=len(chunks))
        return _mark_failure(voucher, record, failure_reason)

    record["组包状态"] = "已领取"
    record["已领取"] = True
    record["失败原因"] = ""
    record["需要快照确认"] = False
    record["文件摘要"] = ""
    voucher["组包状态"] = "已领取"
    voucher["已领取"] = True
    voucher["失败原因"] = ""
    voucher["部分交付"] = any(
        0 < int(item.get("已成功行数", 0) or 0) < int(item.get("总行数", 0) or 0)
        for item in voucher.get("交付记录") or []
    )
    voucher["已成功页数"] = record["已成功页数"]
    voucher["已成功行数"] = record["已成功行数"]
    voucher["交付方式"] = fmt
    voucher["已选择批次"] = voucher.get("批次编号")
    voucher["更新时间"] = now()
    voucher["status"] = "已领取"
    voucher["pending"] = False
    return {
        "ok": True,
        "message": (
            f"凭证 {voucher.get('凭证编号')} 已按 {fmt} 组包完成："
            f"{record['已成功页数']}/{record['总页数']} 页，{record['已成功行数']}/{record['总行数']} 行"
        ),
        "voucher": voucher_detail(voucher),
        "已领取": True,
        "组包状态": "已领取",
        "转换进度": conversion_progress(voucher, record),
        "已选择批次": voucher.get("已选择批次"),
        "交付方式": fmt,
        "行数": record["已成功行数"],
        "采用确认时间": voucher.get("确认时间"),
    }


def _complete_record(voucher: dict[str, Any], delivery_format: str | None) -> tuple[dict[str, Any] | None, str]:
    fmt = normalize_format(delivery_format, voucher.get("交付方式", "CSV"))
    for record in voucher.get("交付记录") or []:
        if record.get("交付方式") == fmt:
            return record, fmt
    return None, fmt


def _build_content(voucher: dict[str, Any], record: dict[str, Any], fmt: str) -> tuple[str, str, str]:
    windows = copy_windows(voucher.get("窗口") or [])
    confirmed = voucher.get("确认时间", "")
    source = voucher.get("来源", "")
    common = {
        "凭证编号": voucher.get("凭证编号"),
        "批次编号": voucher.get("批次编号"),
        "快照版本": voucher.get("快照版本"),
        "来源": source,
        "确认时间": confirmed,
        "快照摘要": voucher.get("快照摘要"),
        "行数": len(windows),
    }
    if fmt == "CSV":
        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([
            "凭证编号", "批次编号", "快照版本", "来源", "确认时间",
            "窗口编号", "窗口开始", "窗口结束", "值A", "值B",
        ])
        for row in windows:
            writer.writerow([
                common["凭证编号"], common["批次编号"], common["快照版本"],
                common["来源"], common["确认时间"],
                row.get("窗口编号"), row.get("窗口开始"), row.get("窗口结束"),
                row.get("值A"), row.get("值B"),
            ])
        return output.getvalue(), MEDIA_TYPES["CSV"], FILE_EXTENSIONS["CSV"]
    payload = {**common, "窗口": windows}
    return (
        json.dumps(payload, ensure_ascii=False, indent=2),
        MEDIA_TYPES["JSON"],
        FILE_EXTENSIONS[fmt],
    )


def build_file(voucher_reference: Any, delivery_format: Any = None) -> dict[str, Any]:
    """按凭证快照生成交付文件；只有完整组包成功的格式才能下载。"""
    voucher = resolve_voucher(voucher_reference)
    if voucher is None:
        return {"ok": False, "message": f"领取凭证 {voucher_reference} 不存在"}
    record, fmt = _complete_record(voucher, delivery_format)
    if record is None:
        return {
            "ok": False,
            "message": f"凭证 {voucher.get('凭证编号')} 尚未以 {fmt} 方式发起组包",
            "已领取": False,
            "组包状态": voucher.get("组包状态"),
        }
    total_rows = int(voucher.get("行数总计", 0) or 0)
    if not record.get("已领取") or int(record.get("已成功行数", 0) or 0) != total_rows:
        return {
            "ok": False,
            "message": (
                f"凭证 {voucher.get('凭证编号')} 的 {fmt} 组包尚未完成"
                f"（{record.get('已成功页数', 0)}/{record.get('总页数', 0)} 页，"
                f"{record.get('已成功行数', 0)}/{total_rows} 行），半成品不计为已领取"
            ),
            "已领取": False,
            "组包状态": record.get("组包状态"),
            "已成功行数": record.get("已成功行数", 0),
            "总行数": total_rows,
            "转换进度": conversion_progress(voucher, record),
        }

    recomputed = snapshot_digest(
        voucher.get("批次编号", ""),
        int(voucher.get("快照版本", 0) or 0),
        voucher.get("任务编号", ""),
        voucher.get("确认时间", ""),
        voucher.get("窗口") or [],
    )
    if recomputed != voucher.get("快照摘要"):
        return {
            "ok": False,
            "message": f"凭证 {voucher.get('凭证编号')} 快照摘要校验不通过，拒绝组包",
            "已领取": False,
            "组包状态": "校验失败",
        }
    pages = record.get("页") or []
    packed_ids = {
        window_id
        for page in pages if page.get("状态") == "已组包"
        for window_id in page.get("窗口编号", [])
    }
    snapshot_ids = [row.get("窗口编号") for row in copy_windows(voucher.get("窗口") or [])]
    if sorted(map(str, packed_ids)) != sorted(map(str, snapshot_ids)):
        return {
            "ok": False,
            "message": f"凭证 {voucher.get('凭证编号')} 成功页未覆盖全部窗口，拒绝交付",
            "已领取": False,
            "组包状态": record.get("组包状态"),
        }
    content, media_type, extension = _build_content(voucher, record, fmt)
    file_digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    record["文件摘要"] = file_digest
    record["行数"] = int(record.get("已成功行数", 0) or 0)
    return {
        "ok": True,
        "message": f"凭证 {voucher.get('凭证编号')} 的 {fmt} 文件已生成",
        "凭证编号": voucher.get("凭证编号"),
        "批次编号": voucher.get("批次编号"),
        "快照版本": voucher.get("快照版本"),
        "来源": voucher.get("来源"),
        "确认时间": voucher.get("确认时间"),
        "采用确认时间": voucher.get("确认时间"),
        "快照摘要": voucher.get("快照摘要"),
        "交付方式": fmt,
        "行数": record.get("已成功行数", len(snapshot_ids)),
        "窗口数": len(snapshot_ids),
        "已领取": True,
        "文件摘要": file_digest,
        "media_type": media_type,
        "filename": f"{voucher.get('凭证编号')}-{voucher.get('批次编号')}-{fmt}.{extension}",
        "content": content,
        "窗口": copy_windows(voucher.get("窗口") or []),
    }
