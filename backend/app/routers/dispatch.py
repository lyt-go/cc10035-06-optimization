"""聚合分发 → 批量导出的批次领取接口：批次、更正、凭证、组包与下载。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.services import service_for
from app.services.dispatch_claim import build_file, create_voucher

router = APIRouter(prefix="/api/dispatch", tags=["分发批次领取"])


def _aggregate_service():
    service = service_for("aggregate_sink")
    if service is None:
        raise HTTPException(status_code=404, detail="未知业务模块：aggregate_sink")
    return service


def _as_int(value, default=None):
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@router.get("/batches")
def list_batches(task_id: str | None = None) -> dict:
    """列出正式分发批次；已更正的任务会同时保留旧版本和当前版本。"""
    return {"data": _aggregate_service().list_dispatch_batches(task_id)}


@router.get("/batches/{reference}")
def get_batch(reference: str) -> dict:
    return _aggregate_service().get_dispatch_batch(reference)


@router.post("/batches/correct")
def correct_dispatch(payload: dict) -> dict:
    """登记分发更正（如 A 从 20 改为 22），生成新的正式版本。"""
    reference = str(
        payload.get("batch")
        or payload.get("batch_reference")
        or payload.get("批次编号")
        or payload.get("task")
        or ""
    ).strip()
    if not reference:
        return {"ok": False, "message": "需要提供批次编号或任务编号"}
    return _aggregate_service().correct_dispatch_result(
        reference,
        value_a=_as_int(payload.get("value_a", payload.get("A"))),
        value_b=_as_int(payload.get("value_b", payload.get("B"))),
        remark=str(payload.get("remark") or ""),
        confirm_time=str(payload.get("confirm_time") or ""),
    )


@router.post("/vouchers")
def open_voucher(payload: dict) -> dict:
    """按分发批次生成领取凭证，凭证锁定批次快照和采用确认时间。"""
    reference = str(
        payload.get("batch")
        or payload.get("batch_reference")
        or payload.get("批次编号")
        or ""
    ).strip()
    if not reference:
        return {"ok": False, "message": "需要选择分发批次"}
    return create_voucher(
        reference,
        delivery_format=payload.get("delivery_format") or payload.get("format") or "CSV",
        page_size=_as_int(payload.get("page_size"), 2),
        remark=str(payload.get("remark") or ""),
    )


@router.get("/vouchers")
def list_vouchers(task_id: str | None = None) -> dict:
    return {"data": _aggregate_service().list_claim_vouchers(task_id)}


@router.get("/vouchers/{reference}")
def voucher_detail(reference: str) -> dict:
    couple = _aggregate_service().claim_voucher_detail(reference)
    if not couple.get("ok"):
        raise HTTPException(status_code=404, detail=couple.get("message"))
    return couple


@router.post("/vouchers/{reference}/package")
def package(reference: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    return _aggregate_service().package_claim_voucher(
        reference,
        delivery_format=str(payload.get("delivery_format") or payload.get("format") or ""),
        fail_page=_as_int(payload.get("fail_page")),
        fail_after_page=_as_int(payload.get("fail_after_page")),
        continue_snapshot=bool(payload.get("continue_snapshot")),
        restart=bool(payload.get("restart")),
    )


@router.get("/vouchers/{reference}/file")
def download(reference: str, delivery_format: str | None = None):
    """按凭证快照给出交付文件；组包未完成时返回可核验的失败说明。"""
    result = build_file(reference, delivery_format)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    headers = {"Content-Disposition": f'attachment; filename="{result["filename"]}"'}
    # 摘要与采用确认时间一并放进响应头，方便工作台核验这份文件来自哪一版。
    headers["X-Claim-Voucher"] = str(result.get("凭证编号", ""))
    headers["X-Claim-Batch"] = str(result.get("批次编号", ""))
    headers["X-Claim-Confirmed-At"] = str(result.get("确认时间", ""))
    headers["X-Claim-Rows"] = str(result.get("行数", 0))
    headers["X-Claim-Digest"] = str(result.get("文件摘要", ""))
    return Response(
        content=result["content"].encode("utf-8"),
        media_type=result["media_type"],
        headers=headers,
    )


@router.get("/vouchers/{reference}/artifact")
def voucher_artifact(reference: str, delivery_format: str | None = None) -> dict:
    """JSON 形态的交付成果，便于前端在页面上直接核对窗口、来源和值。"""
    result = build_file(reference, delivery_format)
    if not result.get("ok"):
        return result
    return {
        key: value for key, value in result.items() if key != "content"
    } | {"content_text": result["content"]}
