#!/usr/bin/env python3
"""Build S1/S2 comparison documentation artifacts.

Inputs are the completed Compact V9 final destination validation outputs.
The script does not run SUMO; it summarizes existing B04(S1) and B4(S2)
repeat results and renders static route/delay figures for presentation.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import sumolib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_RUN_ID = "final_destination_validation_bo_best_20260608"
REPLACEMENT_RUN_ID = "scenario_replace_er_acc_001_20260608"
REJECTED_REPLACEMENT_RUN_IDS = [
    "scenario_replace_er_acc_019_20260608",
    "scenario_replace_er_acc_004_20260608",
]
METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_final_destination_validation"
SELECTED_ROUTE_IDS = [
    "FINAL_DEST_ER_ACC_006",
    "FINAL_DEST_ER_ACC_016",
    "FINAL_DEST_ER_ACC_001",
]
OUT_DIR = PROJECT_ROOT / "reports/scenario_comparison_validation"

S1_MODE = "B04"
S2_MODE = "B4"
DEMO_ROUTE_ID = "FINAL_DEST_ER_ACC_006"
W_EMERGENCY = 10.0
W_GENERAL = 1.0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def mean(rows: list[dict[str, str]], field: str) -> float:
    vals = [float(row[field]) for row in rows if row.get(field) not in ("", None)]
    if not vals:
        return math.nan
    return statistics.mean(vals)


def mean_any(rows: list[dict[str, str]], fields: list[str]) -> float:
    vals: list[float] = []
    for row in rows:
        for field in fields:
            value = row.get(field)
            if value not in ("", None):
                vals.append(float(value))
                break
    if not vals:
        return math.nan
    return statistics.mean(vals)


def fmt(value: float, digits: int = 2) -> str:
    if value is None or math.isnan(value):
        return ""
    return f"{value:.{digits}f}"


def load_inputs() -> tuple[dict[str, Any], list[dict[str, str]]]:
    selected_payloads = []
    run_rows: list[dict[str, str]] = []
    for run_id in [BASE_RUN_ID, REPLACEMENT_RUN_ID]:
        input_root = METRICS_ROOT / run_id / "final"
        selected_payloads.append(json.loads((input_root / "selected_destinations.json").read_text(encoding="utf-8")))
        run_rows.extend(read_csv(input_root / "selected_route_runs.csv"))

    route_by_id = {
        route["route_id"]: route
        for payload in selected_payloads
        for route in payload["routes"]
    }
    selection_by_id = {
        row["route_id"]: row
        for payload in selected_payloads
        for row in payload["selection"]
    }
    selected = {
        "schema": "scenario_comparison_selected_destinations.v1",
        "run_id": f"{BASE_RUN_ID}+{REPLACEMENT_RUN_ID}",
        "phase": "final",
        "selection": [],
        "routes": [],
    }
    for index, route_id in enumerate(SELECTED_ROUTE_IDS, start=1):
        row = dict(selection_by_id[route_id])
        row["selection_rank"] = index
        selected["selection"].append(row)
        selected["routes"].append(route_by_id[route_id])

    run_rows = [row for row in run_rows if row.get("route_id") in set(SELECTED_ROUTE_IDS)]
    return selected, run_rows


def mode_groups(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("mode") in {S1_MODE, S2_MODE}:
            groups[(row["route_id"], row["mode"])].append(row)
    return groups


def build_summary_rows(selected: dict[str, Any], runs: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups = mode_groups(runs)
    route_meta = {route["route_id"]: route for route in selected["routes"]}
    selection_rank = {row["route_id"]: row for row in selected["selection"]}
    rows: list[dict[str, Any]] = []

    for item in sorted(selected["selection"], key=lambda row: int(row["selection_rank"])):
        route_id = item["route_id"]
        s1 = groups[(route_id, S1_MODE)]
        s2 = groups[(route_id, S2_MODE)]
        meta = route_meta[route_id]
        s1_emergency_delay = mean_any(s1, ["d_EMV_sec", "D_E_sec"])
        s2_emergency_delay = mean_any(s2, ["d_EMV_sec", "D_E_sec"])
        s1_general_delay = mean(s1, "general_mean_delay_sec")
        s2_general_delay = mean(s2, "general_mean_delay_sec")
        s1_general_travel = mean(s1, "general_mean_travel_time_sec")
        s2_general_travel = mean(s2, "general_mean_travel_time_sec")
        s1_score = mean(s1, "objective_score")
        s2_score = mean(s2, "objective_score")
        emergency_saved = s1_emergency_delay - s2_emergency_delay
        general_delay_saved = s1_general_delay - s2_general_delay
        weighted_proxy = (W_EMERGENCY * emergency_saved + W_GENERAL * general_delay_saved) / (W_EMERGENCY + W_GENERAL)

        rows.append({
            "selection_rank": int(item["selection_rank"]),
            "route_id": route_id,
            "source_route_id": item["source_route_id"],
            "target_edge_id": item["target_edge_id"],
            "route_length_m": float(meta["route_length_m"]),
            "route_edge_count": int(meta["route_edge_count"]),
            "mainroad_length_ratio": float(meta["mainroad_length_ratio"]),
            "legacy_spine_length_ratio": float(meta["legacy_spine_length_ratio"]),
            "repeat_count_per_mode": len(s1),
            "s1_emergency_delay_sec": s1_emergency_delay,
            "s2_emergency_delay_sec": s2_emergency_delay,
            "emergency_delay_saved_sec": emergency_saved,
            "s1_general_avg_delay_sec": s1_general_delay,
            "s2_general_avg_delay_sec": s2_general_delay,
            "general_avg_delay_saved_sec": general_delay_saved,
            "s1_general_mean_travel_time_sec": s1_general_travel,
            "s2_general_mean_travel_time_sec": s2_general_travel,
            "general_mean_travel_time_saved_sec": s1_general_travel - s2_general_travel,
            "s1_objective_score": s1_score,
            "s2_objective_score": s2_score,
            "objective_score_improvement": s1_score - s2_score,
            "b4_stage3_preemption_mean": mean(s2, "stage3_preemption_count"),
            "b4_stage2_hold_mean": mean(s2, "stage2_hold_count"),
            "weighted_social_value_proxy_10to1_sec": weighted_proxy,
            "selection_reason": selection_rank[route_id]["selection_reason"],
        })
    return rows


def edge_shape(net: Any, edge_id: str) -> list[tuple[float, float]]:
    edge = net.getEdge(edge_id)
    shape = list(edge.getShape())
    if len(shape) < 2 and edge.getLanes():
        shape = list(edge.getLanes()[0].getShape())
    return [(float(x), float(y)) for x, y in shape]


def route_center(net: Any, edge_ids: list[str]) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for edge_id in edge_ids:
        for x, y in edge_shape(net, edge_id):
            xs.append(x)
            ys.append(y)
    return statistics.mean(xs), statistics.mean(ys)


def plot_network_background(ax: Any, net: Any, bounds: tuple[float, float, float, float]) -> None:
    xmin, xmax, ymin, ymax = bounds
    for edge in net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":"):
            continue
        shape = edge_shape(net, edge_id)
        if len(shape) < 2:
            continue
        xs = [x for x, _ in shape]
        ys = [y for _, y in shape]
        if max(xs) < xmin or min(xs) > xmax or max(ys) < ymin or min(ys) > ymax:
            continue
        ax.plot(xs, ys, color="#d1d5db", linewidth=0.7, alpha=0.75, zorder=1)


def plot_route(ax: Any, net: Any, edge_ids: list[str], color: str, width: float, alpha: float = 1.0) -> None:
    for edge_id in edge_ids:
        shape = edge_shape(net, edge_id)
        if len(shape) >= 2:
            ax.plot(
                [x for x, _ in shape],
                [y for _, y in shape],
                color=color,
                linewidth=width,
                alpha=alpha,
                solid_capstyle="round",
                zorder=4,
            )


def route_bounds(net: Any, routes: list[dict[str, Any]], pad: float = 120.0) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for route in routes:
        for edge_id in route["route_edges"]:
            for x, y in edge_shape(net, edge_id):
                xs.append(x)
                ys.append(y)
    return min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad


def target_point(net: Any, edge_id: str) -> tuple[float, float]:
    shape = edge_shape(net, edge_id)
    return shape[-1]


def start_point(net: Any, edge_id: str) -> tuple[float, float]:
    shape = edge_shape(net, edge_id)
    return shape[0]


def on_route_tls(net: Any, edge_ids: list[str]) -> list[tuple[str, float, float]]:
    nodes = [net.getEdge(edge_ids[0]).getFromNode()] + [net.getEdge(edge).getToNode() for edge in edge_ids]
    seen: set[str] = set()
    out: list[tuple[str, float, float]] = []
    for node in nodes:
        node_id = node.getID()
        if node_id in seen or node.getType() != "traffic_light":
            continue
        seen.add(node_id)
        x, y = node.getCoord()
        out.append((node_id, float(x), float(y)))
    return out


def render_route_figures(selected: dict[str, Any], net_path: Path) -> None:
    net = sumolib.net.readNet(str(net_path))
    routes = selected["routes"]
    colors = {
        "FINAL_DEST_ER_ACC_006": "#0f766e",
        "FINAL_DEST_ER_ACC_016": "#2563eb",
        "FINAL_DEST_ER_ACC_001": "#b45309",
    }
    bounds = route_bounds(net, routes, pad=180.0)

    fig, ax = plt.subplots(figsize=(11, 8), dpi=180)
    plot_network_background(ax, net, bounds)
    for route in routes:
        route_id = route["route_id"]
        color = colors.get(route_id, "#111827")
        plot_route(ax, net, route["route_edges"], color, 3.2)
        tx, ty = target_point(net, route["target_edge_id"])
        ax.scatter([tx], [ty], s=58, color=color, edgecolors="white", linewidths=1.2, zorder=6)
        ax.text(tx + 22, ty + 22, route["source_route_id"], fontsize=9, color=color, weight="bold", zorder=7)
    sx, sy = start_point(net, routes[0]["start_edge_id"])
    ax.scatter([sx], [sy], s=80, marker="s", color="#111827", edgecolors="white", linewidths=1.2, zorder=8)
    ax.text(sx + 22, sy + 22, "START", fontsize=9, color="#111827", weight="bold", zorder=9)
    ax.set_title("S1/S2 Validation Routes: 3 Accident Locations", fontsize=15, weight="bold")
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(bounds[0], bounds[1])
    ax.set_ylim(bounds[2], bounds[3])
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "scenario_routes_overview.png", bbox_inches="tight")
    plt.close(fig)

    demo = next(route for route in routes if route["route_id"] == DEMO_ROUTE_ID)
    demo_bounds = route_bounds(net, [demo], pad=130.0)
    fig, ax = plt.subplots(figsize=(10, 8), dpi=180)
    plot_network_background(ax, net, demo_bounds)
    plot_route(ax, net, demo["route_edges"], "#0f766e", 4.0)
    tls = on_route_tls(net, demo["route_edges"])
    if tls:
        ax.scatter([x for _, x, _ in tls], [y for _, _, y in tls], s=30, color="#dc2626", edgecolors="white", linewidths=0.8, zorder=7)
    sx, sy = start_point(net, demo["start_edge_id"])
    tx, ty = target_point(net, demo["target_edge_id"])
    ax.scatter([sx], [sy], s=85, marker="s", color="#111827", edgecolors="white", linewidths=1.2, zorder=8)
    ax.scatter([tx], [ty], s=95, marker="*", color="#0f766e", edgecolors="white", linewidths=1.2, zorder=8)
    ax.text(sx + 18, sy + 18, "START", fontsize=9, color="#111827", weight="bold")
    ax.text(tx + 18, ty + 18, "ER_ACC_006", fontsize=9, color="#0f766e", weight="bold")
    ax.text(0.015, 0.02, f"route length {demo['route_length_m']:.0f} m | edges {demo['route_edge_count']} | on-route TLS {len(tls)}",
            transform=ax.transAxes, fontsize=9, color="#374151")
    ax.set_title("Demo Route: ER_ACC_006", fontsize=15, weight="bold")
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(demo_bounds[0], demo_bounds[1])
    ax.set_ylim(demo_bounds[2], demo_bounds[3])
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "demo_route_er_acc_006.png", bbox_inches="tight")
    plt.close(fig)


def render_delay_figure(summary_rows: list[dict[str, Any]]) -> None:
    labels = [row["source_route_id"].replace("ER_ACC_", "ACC ") for row in summary_rows]
    x = range(len(summary_rows))
    width = 0.36

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.8), dpi=180)
    ax1.bar([i - width / 2 for i in x], [row["s1_emergency_delay_sec"] for row in summary_rows], width, label="S1 current", color="#9ca3af")
    ax1.bar([i + width / 2 for i in x], [row["s2_emergency_delay_sec"] for row in summary_rows], width, label="S2 priority", color="#0f766e")
    ax1.set_title("Emergency Vehicle Delay")
    ax1.set_ylabel("seconds")
    ax1.set_xticks(list(x), labels)
    ax1.legend(frameon=False)
    ax1.grid(axis="y", color="#e5e7eb")

    ax2.bar([i - width / 2 for i in x], [row["s1_general_avg_delay_sec"] for row in summary_rows], width, label="S1 current", color="#9ca3af")
    ax2.bar([i + width / 2 for i in x], [row["s2_general_avg_delay_sec"] for row in summary_rows], width, label="S2 priority", color="#2563eb")
    ax2.set_title("General Vehicle Average Delay")
    ax2.set_ylabel("seconds / vehicle")
    ax2.set_xticks(list(x), labels)
    ax2.legend(frameon=False)
    ax2.grid(axis="y", color="#e5e7eb")

    fig.suptitle("S1 vs S2 Delay Comparison, 30 Repeats per Location", fontsize=14, weight="bold")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "s1_s2_delay_comparison.png", bbox_inches="tight")
    plt.close(fig)


def markdown_table(rows: list[list[str]], headers: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(out)


def build_report(summary_rows: list[dict[str, Any]], selected: dict[str, Any]) -> str:
    avg_ev_saved = statistics.mean(row["emergency_delay_saved_sec"] for row in summary_rows)
    avg_general_saved = statistics.mean(row["general_avg_delay_saved_sec"] for row in summary_rows)
    avg_score_saved = statistics.mean(row["objective_score_improvement"] for row in summary_rows)
    avg_proxy = statistics.mean(row["weighted_social_value_proxy_10to1_sec"] for row in summary_rows)

    comparison_table = markdown_table(
        [
            [
                str(row["selection_rank"]),
                row["source_route_id"],
                row["target_edge_id"],
                fmt(row["route_length_m"], 0),
                str(row["repeat_count_per_mode"]),
                fmt(row["s1_emergency_delay_sec"]),
                fmt(row["s2_emergency_delay_sec"]),
                fmt(row["emergency_delay_saved_sec"]),
                fmt(row["s1_general_avg_delay_sec"]),
                fmt(row["s2_general_avg_delay_sec"]),
                fmt(row["general_avg_delay_saved_sec"]),
            ]
            for row in summary_rows
        ],
        [
            "순위", "사고위치", "target edge", "경로 m", "반복",
            "S1 긴급차 지연", "S2 긴급차 지연", "긴급차 단축",
            "S1 일반차 평균지연", "S2 일반차 평균지연", "일반차 지연 감소",
        ],
    )

    social_table = markdown_table(
        [
            [
                row["source_route_id"],
                fmt(row["emergency_delay_saved_sec"]),
                fmt(row["general_avg_delay_saved_sec"]),
                fmt(row["objective_score_improvement"]),
                fmt(row["weighted_social_value_proxy_10to1_sec"]),
                fmt(row["b4_stage3_preemption_mean"]),
                fmt(row["b4_stage2_hold_mean"]),
            ]
            for row in summary_rows
        ],
        [
            "사고위치", "Δ긴급차 지연 sec", "Δ일반차 평균지연 sec/veh",
            "목적함수 개선 sec", "10:1 사회가치 proxy sec",
            "S2 Stage3 평균", "S2 Stage2 평균",
        ],
    )

    route_table = markdown_table(
        [
            [
                row["source_route_id"],
                row["route_id"],
                fmt(row["route_length_m"], 0),
                str(row["route_edge_count"]),
                fmt(row["mainroad_length_ratio"], 3),
                fmt(row["legacy_spine_length_ratio"], 3),
                row["selection_reason"],
            ]
            for row in summary_rows
        ],
        ["사고위치", "route id", "경로 m", "edge 수", "대로 비율", "spine 비율", "선정 사유"],
    )
    rejected_rows = []
    for run_id in REJECTED_REPLACEMENT_RUN_IDS:
        path = METRICS_ROOT / run_id / "final" / "candidate_selection.csv"
        if not path.is_file():
            continue
        row = read_csv(path)[0]
        rejected_rows.append([
            row["source_route_id"],
            row["route_id"],
            row.get("B4_vs_B04_D_E_improvement_sec", ""),
            row.get("arrival_rate_min", ""),
            row.get("fail_count", ""),
            row.get("selection_reason", ""),
        ])
    rejected_table = markdown_table(
        rejected_rows,
        ["검토 후보", "route id", "B4 개선 sec", "도착률", "fail", "탈락 사유"],
    )

    report = textwrap.dedent(f"""\
    # S1/S2 시나리오 비교 검증 문서

    작성 기준: `{BASE_RUN_ID}` + `{REPLACEMENT_RUN_ID}` 최종 검증 산출물

    ## 비교 정의

    - S1: 현행 시나리오. 코드상 `B04`, 우선신호 제어를 적용하지 않은 baseline입니다.
    - S2: 우선신호체계 도입 시나리오. 코드상 `B4`, 잠근 theta를 적용한 제어 시나리오입니다.
    - B004: 자유류 기준값입니다. S1/S2 직접 비교 대상은 아니고 긴급차 지연시간을 계산하기 위한 free reference입니다.

    비교 지표는 `긴급차량 지연시간`, `일반차량 평균지연시간`, `목적함수 점수`입니다. 목적함수는 기존 코드 계약과 동일하게 `(10/11) * d_EMV_sec + (1/11) * d_veh_sec`를 사용합니다.

    ## 사고위치 수와 선정 이유

    최종 비교 검증은 사고위치 3개로 수행합니다. 이유는 다음과 같습니다.

    - 18개 후보를 먼저 screening해서 도달 실패, teleport, 개입 없음, 개선 없음인 후보를 제거했습니다.
    - 사용자가 제외 요청한 `ER_ACC_011`은 최종 세트에서 제거했습니다.
    - 대체 후보로 `ER_ACC_019`, `ER_ACC_004`, `ER_ACC_001`을 순차 확인했습니다. `ER_ACC_019`와 `ER_ACC_004`는 final 30회에서 B4 fail이 발생해 탈락했고, `ER_ACC_001`만 30회 모두 도착/teleport 0/fail 0 조건을 통과했습니다.
    - 최종 3개는 `ER_ACC_006`, `ER_ACC_016`, `ER_ACC_001`입니다. `ER_ACC_006`은 시연용 대표 경로, `ER_ACC_016`은 장거리/다신호 검증 경로, `ER_ACC_001`은 11번 탈락 뒤 통과한 보수적 짧은 대체 경로입니다.
    - 최종 반복 검증 비용을 통제하면서도 한 위치만 검증했다는 약점을 피할 수 있습니다.

    {route_table}

    ## 11번 대체 후보 확인

    {rejected_table}

    `ER_ACC_001`은 개선폭이 크지는 않지만 final 30회 안정성 조건을 통과했습니다. 따라서 11번을 빼야 한다면, 현재 결과 기준으로는 `ER_ACC_001`이 가장 방어 가능한 대체 경로입니다.

    ## 반복 횟수

    - Screening: 18개 후보 위치를 1회씩 실행해 검증 가능한 후보를 고릅니다.
    - Final validation: 최종 3개 사고위치에서 S1/S2 각각 30회 반복합니다. B004 자유류 기준은 위치별 1회 생성합니다.
    - 반복 출발시각은 550-650초 구간에서 seed 기반으로 흔들어 단일 출발시각 과적합을 줄입니다.
    - 현재 산출물의 SPC 안정성 표에서는 3개 위치의 주요 지표가 모두 `stable`, `stable_round=5`로 판정됐습니다.

    ## S1/S2 비교값

    단위는 초입니다. `일반차 지연 감소`가 양수면 S2에서 일반차 평균지연도 줄었다는 뜻이고, 음수면 S2에서 일반차 평균지연이 증가한 위치입니다.

    {comparison_table}

    요약하면 S2는 3개 위치 평균으로 긴급차 지연을 `{avg_ev_saved:.2f}`초 줄였습니다. 일반차 평균지연은 위치별로 상충이 있으나 평균 `{avg_general_saved:.2f}`초/veh 감소입니다. 코드 목적함수 기준 평균 개선은 `{avg_score_saved:.2f}`초입니다.

    ## 사회적 가치 계산

    현 단계에서는 금액 단위 계수 없이도 비교 가능한 사회가치 proxy를 계산합니다.

    ```text
    ΔD_E_i = D_E(S1, i) - D_E(S2, i)
    ΔD_G_i = D_G(S1, i) - D_G(S2, i)

    10:1 사회가치 proxy_i = (10 * ΔD_E_i + 1 * ΔD_G_i) / 11
    금액 환산_i = V_E_sec * ΔD_E_i + V_G_sec * N_G_i * ΔD_G_i
    ```

    여기서 `V_E_sec`는 긴급차 도착 1초 단축 가치, `V_G_sec`는 일반차 1대의 1초 시간가치, `N_G_i`는 사고위치 i에서 정책 평가에 포함할 일반차 대수입니다. 제출 자료에서 원화 금액을 넣으려면 이 세 정책계수를 별도 표로 고정하면 됩니다.

    {social_table}

    현재 검증 결과의 10:1 사회가치 proxy 평균은 `{avg_proxy:.2f}`초입니다. `ER_ACC_001`은 일반차 평균지연이 증가하므로, 전체 메시지는 `ER_ACC_006`과 `ER_ACC_016`에서 강한 긴급차 단축 효과를 보이고 `ER_ACC_001`은 11번 대체용 안정성 확인 경로로 설명하는 편이 맞습니다.

    ## 시각화 산출물

    - `scenario_routes_overview.png`: 최종 검증 3개 사고위치와 경로 전체 개요입니다.
    - `demo_route_er_acc_006.png`: 시연 영상에 사용할 단일 사고위치 상세 경로입니다.
    - `s1_s2_delay_comparison.png`: 긴급차 지연과 일반차 평균지연의 S1/S2 비교 막대그래프입니다.

    시연 영상은 `ER_ACC_006` 하나만 쓰는 것이 좋습니다. 개선폭이 가장 크고, 대로 비율이 높으며, 경로가 짧아 발표 시간 안에 우선신호 개입 전후를 설명하기 쉽습니다. 나머지 2개 위치는 영상이 아니라 검증 표와 overview 이미지로 “여러 위치에서 비교했다”는 근거를 제공합니다.

    ## 연결 산출물

    - 요약 CSV: `scenario_comparison_summary.csv`
    - 원본 final report: `results/metrics/compact_v9_final_destination_validation/{BASE_RUN_ID}/final/final_destination_validation_report.md`
    - 11번 대체 final report: `results/metrics/compact_v9_final_destination_validation/{REPLACEMENT_RUN_ID}/final/final_destination_validation_report.md`
    - 원본 selected runs: `results/metrics/compact_v9_final_destination_validation/{BASE_RUN_ID}/final/selected_route_runs.csv`
    - 대체 selected runs: `results/metrics/compact_v9_final_destination_validation/{REPLACEMENT_RUN_ID}/final/selected_route_runs.csv`
    """)
    return "\n".join(line[4:] if line.startswith("    ") else line for line in report.splitlines()) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    selected, runs = load_inputs()
    summary_rows = build_summary_rows(selected, runs)
    net_path = PROJECT_ROOT / runs[0]["net_file"]

    fields = [
        "selection_rank",
        "route_id",
        "source_route_id",
        "target_edge_id",
        "route_length_m",
        "route_edge_count",
        "mainroad_length_ratio",
        "legacy_spine_length_ratio",
        "repeat_count_per_mode",
        "s1_emergency_delay_sec",
        "s2_emergency_delay_sec",
        "emergency_delay_saved_sec",
        "s1_general_avg_delay_sec",
        "s2_general_avg_delay_sec",
        "general_avg_delay_saved_sec",
        "s1_general_mean_travel_time_sec",
        "s2_general_mean_travel_time_sec",
        "general_mean_travel_time_saved_sec",
        "s1_objective_score",
        "s2_objective_score",
        "objective_score_improvement",
        "b4_stage3_preemption_mean",
        "b4_stage2_hold_mean",
        "weighted_social_value_proxy_10to1_sec",
        "selection_reason",
    ]
    write_csv(OUT_DIR / "scenario_comparison_summary.csv", summary_rows, fields)
    render_route_figures(selected, net_path)
    render_delay_figure(summary_rows)
    (OUT_DIR / "SCENARIO_COMPARISON_VALIDATION_KO.md").write_text(build_report(summary_rows, selected), encoding="utf-8")

    print(f"Wrote artifacts to {OUT_DIR}")
    for name in [
        "SCENARIO_COMPARISON_VALIDATION_KO.md",
        "scenario_comparison_summary.csv",
        "scenario_routes_overview.png",
        "demo_route_er_acc_006.png",
        "s1_s2_delay_comparison.png",
    ]:
        print(f"- {OUT_DIR / name}")


if __name__ == "__main__":
    main()
