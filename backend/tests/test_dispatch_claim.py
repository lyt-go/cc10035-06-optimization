"""分发批次领取回归测试：快照一致性、失败续办、换格式交付与确认时间核验。"""
from __future__ import annotations

import pytest

from app.store import store
from app.services import dispatch_claim


@pytest.fixture(autouse=True)
def isolated_tables():
    """每个用例都在干净的分发批次/领取凭证上运行。"""
    store._tables["dispatch_batches"] = [row for row in _SEED_DISPATCH]
    store._tables["claim_vouchers"] = []
    yield
    store._tables["dispatch_batches"] = [row for row in _SEED_DISPATCH]
    store._tables["claim_vouchers"] = []


_SEED_DISPATCH = [
    {
        "id": 1,
        "批次编号": "DSP-0001",
        "原始批次编号": "DSP-0001",
        "任务编号": "AGGR-0001",
        "版本": 1,
        "聚合函数": "count_success",
        "输出Topic": "agg/result/AGGR-0001",
        "分发状态": "已确认",
        "确认时间": "2026-09-01T10:10:00+08:00",
        "更正说明": "原始分发结果 A=20、B=3",
        "当前版本": True,
        "来源摘要": "",
        "窗口": [
            {
                "窗口编号": f"W-{index + 1:03d}",
                "窗口开始": f"2026-09-01T10:{index * 5:02d}:00+08:00",
                "窗口结束": f"2026-09-01T10:{index * 5 + 5:02d}:00+08:00",
                "值A": 20,
                "值B": 3,
            }
            for index in range(5)
        ],
        "status": "已确认",
        "pending": False,
        "abnormal": False,
    },
]


def _reseed_timing() -> None:
    row = store._tables["dispatch_batches"][0]
    row["来源摘要"] = dispatch_claim.snapshot_digest(
        row["批次编号"], row["版本"], row["任务编号"], row["确认时间"], row["窗口"],
    )


def test_seed_has_a20_b3():
    _reseed_timing()
    batch = dispatch_claim.resolve_batch("DSP-0001")
    assert dispatch_claim.value_summary(batch["窗口"]) == "A=20、B=3"
    assert len(batch["窗口"]) == 5


def test_voucher_locks_snapshot_and_survives_correction():
    _reseed_timing()
    voucher = dispatch_claim.create_voucher("DSP-0001", delivery_format="CSV", page_size=2)["voucher"]
    assert voucher["批次编号"] == "DSP-0001"
    assert voucher["采用确认时间"] == "2026-09-01T10:10:00+08:00"
    assert {row["值A"] for row in voucher["窗口"]} == {20}

    corrected = dispatch_claim.correct_batch("AGGR-0001", value_a=22, confirm_time="2026-09-03T11:00:00+08:00")
    assert corrected["ok"] is True
    assert corrected["batch"]["批次编号"] == "DSP-0001-V2"
    assert corrected["batch"]["值摘要"] == "A=22、B=3"
    assert corrected["已锁定凭证数"] == 1

    stored = dispatch_claim.resolve_voucher(voucher["凭证编号"])
    detail = dispatch_claim.voucher_detail(stored)
    assert {row["值A"] for row in detail["窗口"]} == {20}
    assert detail["采用确认时间"] == "2026-09-01T10:10:00+08:00"
    assert detail["来源核对"]["是否同一版本"] is False
    assert detail["来源核对"]["可核验"] is True


def test_failure_after_first_page_is_not_counted_as_claimed():
    _reseed_timing()
    created = dispatch_claim.create_voucher("DSP-0001", delivery_format="CSV", page_size=2)
    code = created["凭证编号"]
    dispatch_claim.correct_batch("AGGR-0001", value_a=22)

    first = dispatch_claim.package_voucher(code, delivery_format="CSV", fail_after_page=1)
    assert first["ok"] is False
    assert first["已领取"] is False
    assert first["组包状态"] == "组包失败"
    assert first["转换进度"]["已成功页数"] == 1
    assert first["转换进度"]["已成功行数"] == 2
    assert first["转换进度"]["总行数"] == 5
    assert first["已选择批次"] == "DSP-0001"
    assert first["交付方式"] == "CSV"

    # 重新进入凭证详情，页面仍能读到已选择批次、交付方式和进度
    detail = dispatch_claim.voucher_detail(dispatch_claim.resolve_voucher(code))
    assert detail["已选择批次"] == "DSP-0001"
    assert detail["交付方式"] == "CSV"
    assert detail["未领取"] is True
    assert detail["显示行数"] == 2

    # 半成品不能下载
    blocked = dispatch_claim.build_file(code, "CSV")
    assert blocked["ok"] is False

    resumed = dispatch_claim.package_voucher(code, delivery_format="CSV")
    assert resumed["ok"] is True
    assert resumed["已领取"] is True
    assert resumed["转换进度"]["已成功行数"] == 5


def test_csv_and_other_format_come_from_same_snapshot():
    _reseed_timing()
    created = dispatch_claim.create_voucher("DSP-0001", delivery_format="CSV", page_size=2)
    code = created["凭证编号"]
    dispatch_claim.correct_batch("AGGR-0001", value_a=22)

    dispatch_claim.package_voucher(code, delivery_format="CSV", fail_after_page=1)
    dispatch_claim.package_voucher(code, delivery_format="CSV")
    json_result = dispatch_claim.package_voucher(code, delivery_format="JSON")
    assert json_result["ok"] is True

    csv_file = dispatch_claim.build_file(code, "CSV")
    json_file = dispatch_claim.build_file(code, "JSON")
    assert csv_file["ok"] is True and json_file["ok"] is True
    assert {row["值A"] for row in csv_file["窗口"]} == {20}
    assert {row["值A"] for row in json_file["窗口"]} == {20}
    assert {row["值B"] for row in csv_file["窗口"]} == {3}
    assert {row["值B"] for row in json_file["窗口"]} == {3}
    assert csv_file["确认时间"] == json_file["确认时间"] == "2026-09-01T10:10:00+08:00"
    assert csv_file["行数"] == json_file["行数"] == 5
    assert json_file["批次编号"] == "DSP-0001"


def test_corrected_batch_voucher_uses_new_version_snapshot():
    _reseed_timing()
    dispatch_claim.correct_batch("AGGR-0001", value_a=22)
    created = dispatch_claim.create_voucher("AGGR-0001", delivery_format="CSV", page_size=2)
    assert created["批次编号"] == "DSP-0001-V2"
    assert created["快照版本"] == 2
    file = dispatch_claim.build_file(created["凭证编号"], "CSV") if dispatch_claim.package_voucher(
        created["凭证编号"], delivery_format="CSV")["ok"] else None
    assert file is not None and file["ok"] is True
    assert {row["值A"] for row in file["窗口"]} == {22}


def test_injected_page_failure_marks_page_state():
    _reseed_timing()
    created = dispatch_claim.create_voucher("DSP-0001", delivery_format="JSON", page_size=2)
    result = dispatch_claim.package_voucher(created["凭证编号"], delivery_format="JSON", fail_page=2)
    pages = result["voucher"]["交付记录"][0]["页"]
    assert pages[0]["状态"] == "已组包"
    assert pages[1]["状态"] == "组包失败"
    assert pages[2]["状态"] == "未开始"
    assert result["voucher"]["已成功页数"] == 1
