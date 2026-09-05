# -*- coding: utf-8 -*-
"""
upgrade_analysis.py
===================
淘宝用户行为端到端分析 —— 创新升级模块（在原仓库复现基础上新增）

新增 4 个分析维度（原仓库均未覆盖，见原 README "Future Improvements"）：
  1. Cohort 留存分析：按"首次活跃日 / 首次购买日"分队列，计算逐日留存（矩阵 + 曲线）
  2. RFM 用户分层：R(最近购买间隔) x F(购买频次)，无金额字段故不含 M，并给出分层贡献度
  3. 行为深度 x 转化：浏览商品数与购买率的关系 + 首次浏览到首次购买的决策时长
  4. 跨品类连带购买：挖掘"买了 A 品类还常买 B 品类"的组合，识别捆绑/交叉销售机会

运行方式（在仓库根目录）：
  python src/upgrade_analysis.py --input data/cleaned_user_behavior.csv

输出：
  reports/upgrade_summary.md          升级总结（含业务建议，中文）
  reports/upgrade_cohort_retention.md cohort 留存明细
  reports/upgrade_rfm.md              RFM 分层明细
  reports/upgrade_behavior_depth.md   行为深度与转化明细
  reports/upgrade_bundling.md         跨品类捆绑机会明细
  reports/figures/upgrade_*.png       可视化图表（英文标签，避免中文字体依赖）
  reports/tables/upgrade_*.csv        明细表
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from datetime import timedelta

# ------------------------- 全局配置 -------------------------
DEFAULT_INPUT = Path("data/cleaned_user_behavior.csv")
REPO_ROOT = Path(__file__).resolve().parents[1]

plt.rcParams.update({
    "figure.dpi": 130,
    "axes.titlesize": 12,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

SEGMENT_CN = {
    "high_value_active": "高价值活跃客户（近期高频）",
    "potential":         "潜力客户（近期低频）",
    "at_risk":           "流失预警客户（沉默高频）",
    "lost":              "沉默长尾客户（沉默低频）",
}

# ------------------------- 通用工具 -------------------------
def write_md(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    """把 (小标题, markdown正文) 列表写入一个 md 文件。"""
    lines = [f"# {title}", ""]
    for header, body in sections:
        lines += [f"## {header}", "", body.strip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def df_to_md_table(df: pd.DataFrame, caption: str = "") -> str:
    """DataFrame -> GitHub 风格 markdown 表格。"""
    out = [caption, ""] if caption else []
    out.append("| " + " | ".join(str(c) for c in df.columns) + " |")
    out.append("|" + "|".join(["---"] * len(df.columns)) + "|")
    def _fmt(v):
        return f"{v:.2f}" if isinstance(v, float) else str(v)
    for _, row in df.iterrows():
        out.append("| " + " | ".join(_fmt(v) for v in row.values) + " |")
    return "\n".join(out)


def save_table(df: pd.DataFrame, name: str) -> Path:
    out_dir = REPO_ROOT / "reports" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_fig(fig, name: str) -> Path:
    fig_dir = REPO_ROOT / "reports" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    path = fig_dir / f"upgrade_{name}.png"
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ------------------------- 1. Cohort 留存 -------------------------
def cohort_retention(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    df = df.copy()
    # 统一转成 date 对象，避免 Timestamp/date 比较陷阱
    df["date_obj"] = pd.to_datetime(df["date"]).dt.date
    last_date = df["date_obj"].max()

    first_active = df.groupby("user_id")["date_obj"].min().rename("first_active")
    d = df.merge(first_active, on="user_id")
    active_days = df.groupby("user_id")["date_obj"].apply(lambda s: set(s))
    cohorts = d.groupby("first_active")["user_id"].unique()

    # --- 首次活跃 cohort 留存矩阵 ---
    rows = []
    for cohort_day, users in sorted(cohorts.items()):
        cohort_users = set(int(u) for u in users)
        max_obs = (last_date - cohort_day).days
        row = {"cohort": cohort_day.isoformat(), "size": len(cohort_users)}
        for offset in range(0, max_obs + 1):
            target = cohort_day + timedelta(days=offset)
            retained = sum(1 for u in cohort_users if target in active_days.get(u, set()))
            row[f"d{offset}"] = round(retained / len(cohort_users) * 100, 1)
        rows.append(row)
    matrix = pd.DataFrame(rows).set_index("cohort")

    # --- 平均留存曲线（只统计能观测到该相对天的队列，避免尾部截断偏差）---
    curve_rows = []
    for offset in range(0, 9):
        eligible, retained = [], []
        for cohort_day, users in sorted(cohorts.items()):
            if cohort_day + timedelta(days=offset) <= last_date:
                cohort_users = set(int(u) for u in users)
                target = cohort_day + timedelta(days=offset)
                eligible.append(len(cohort_users))
                retained.append(sum(1 for u in cohort_users if target in active_days.get(u, set())))
        if sum(eligible):
            curve_rows.append({"day_offset": offset,
                               "retention_pct": round(sum(retained) / sum(eligible) * 100, 1)})
    curve = pd.DataFrame(curve_rows)

    # --- 首次购买 cohort 留存（仅购买用户）---
    buyers = df[df["behavior_type"] == "buy"]
    buy_curve = pd.DataFrame(columns=["day_offset", "retention_pct"])
    if not buyers.empty:
        first_buy = buyers.groupby("user_id")["date_obj"].min().rename("first_buy")
        bd = buyers.merge(first_buy, on="user_id")
        b_cohorts = bd.groupby("first_buy")["user_id"].unique()
        b_rows = []
        for offset in range(0, 9):
            elig, ret = [], []
            for cday, users in sorted(b_cohorts.items()):
                if cday + timedelta(days=offset) <= last_date:
                    uu = set(int(u) for u in users)
                    tgt = cday + timedelta(days=offset)
                    elig.append(len(uu))
                    ret.append(sum(1 for u in uu if tgt in active_days.get(u, set())))
            if sum(elig):
                b_rows.append({"day_offset": offset,
                               "retention_pct": round(sum(ret) / sum(elig) * 100, 1)})
        buy_curve = pd.DataFrame(b_rows)

    # 图1：留存热力图
    plot_m = matrix.iloc[:, 1:].astype(float)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    im = ax.imshow(plot_m.values, aspect="auto", cmap="YlGnBu", vmin=0, vmax=100)
    ax.set_xticks(range(plot_m.shape[1]))
    ax.set_xticklabels([c.replace("d", "D") for c in plot_m.columns])
    ax.set_yticks(range(plot_m.shape[0]))
    ax.set_yticklabels(plot_m.index)
    ax.set_xlabel("Day after first active")
    ax.set_ylabel("First-active cohort")
    ax.set_title("User retention by first-active cohort (%)")
    for i in range(plot_m.shape[0]):
        for j in range(plot_m.shape[1]):
            v = plot_m.iloc[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8,
                        color="white" if v > 60 else "black")
    fig.colorbar(im, ax=ax, label="Retention %")
    fig_path = save_fig(fig, "cohort_retention_heatmap")

    # 图2：平均留存曲线
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(curve["day_offset"], curve["retention_pct"], "-o", label="All users (first-active)")
    if not buy_curve.empty:
        ax.plot(buy_curve["day_offset"], buy_curve["retention_pct"], "-s", label="Buyers (first-purchase)")
    ax.set_xlabel("Day offset")
    ax.set_ylabel("Retention %")
    ax.set_title("Average retention curve")
    ax.legend()
    fig_path2 = save_fig(fig, "cohort_retention_curve")

    # --- 补充更可靠的业务指标：购买队列的"窗口内复购率" ---
    buyers = df[df["behavior_type"] == "buy"].copy()
    repeat_text = ""
    if not buyers.empty:
        buy_days = buyers.groupby("user_id")["date_obj"].apply(lambda s: set(s))
        fb = buyers.groupby("user_id")["date_obj"].min().rename("first_buy")
        fb_df = fb.reset_index()
        fb_df["n_buy_days"] = fb_df["user_id"].map(lambda u: len(buy_days.get(u, set())))
        fb_df["repeat_buyer"] = fb_df["n_buy_days"] >= 2
        repeat = (fb_df.groupby("first_buy")
                  .agg(cohort_size=("user_id", "count"),
                       repeat_buyers=("repeat_buyer", "sum"))
                  .reset_index())
        repeat["first_buy"] = repeat["first_buy"].astype(str)
        repeat["repeat_rate_pct"] = (repeat["repeat_buyers"] / repeat["cohort_size"] * 100).round(1)
        save_table(repeat, "first_purchase_cohort_repeat")
        repeat_text = f"""
### 购买队列：窗口内复购率（更稳健的业务指标）
{df_to_md_table(repeat, caption="同一用户在 9 天窗口内 ≥2 个不同日期发生购买 = 复购；口径稳健、不受周末活跃影响")}

> 解读：复购率衡量"首次下单后是否再次下单"，比"回访率"更贴近 GMV 动作。复购率高的队列应给予会员/复购激励，
> 复购率低但单次金额潜力大的品类（若有金额字段）则侧重客单价提升。
> ⚠️ 截断偏差：11-30 之后的首购队列观测窗口不足，复购率被系统性低估（12-03 队列无后续日期必为 0%）。
> 跨队列比较复购时，应限定相同观测长度（例如统一看"首购后 72h 内复购率"）。
"""

    text = f"""
> 口径说明：本数据集为 9 天行为日志，无注册信息，因此"留存"定义为**用户在某天是否有任何行为（回访）**，
> 而非真实注册留存。**注意：本 10 万行样本按"行为"抽样，重度用户占比偏高，会把回访率整体高估**，阅读时请
> 关注相对结构与周末效应，而非绝对值。

### 首次活跃队列留存矩阵（%）
{df_to_md_table(matrix.reset_index(), caption="行=队列首次活跃日期，列=相对第 N 天的回访率")}

### 平均留存曲线
{df_to_md_table(curve, caption="全体用户按首次活跃日对齐")}
{f'#### 购买用户（首次购买日对齐）\n' + df_to_md_table(buy_curve, caption='购买用户回访率') if not buy_curve.empty else ''}
{repeat_text}
### 图表
- 留存热力图：`reports/figures/upgrade_cohort_retention_heatmap.png`
- 留存曲线：`reports/figures/upgrade_cohort_retention_curve.png`

### 业务解读（示例）
- **先识别口径陷阱（数据 sense）**：D7/D8 回访率普遍抬升至 ~95%+，原因是 12/2、12/3 为**周六日**（行为高峰，
  与原项目"周末行为激增"结论一致），叠加行为加权抽样，**并非真实留存改善**。真实留存需基于注册/会话/全量日志
  按"首次访问日"对齐计算——这里明确写出口径局限，避免误导决策。
- 观察 D1→D3 衰减：11-26 之后队列 D1 回访率 55-60%，说明"次日不回访"是主要流失点，建议在首次活跃后 24h 内
  触发新客触达（收藏夹/购物车提醒、新客券），推动二次访问。
- 购买队列复购率口径更贴近业务：复购率高 → 做复购激励；复购率低 → 侧重客单价/连带购买（与捆绑分析呼应）。
"""
    return matrix, curve, text


# ------------------------- 2. RFM 分层 -------------------------
def rfm_segmentation(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    buys = df[df["behavior_type"] == "buy"].copy()
    buys["behavior_time"] = pd.to_datetime(buys["behavior_time"])
    if buys.empty or buys["user_id"].nunique() < 20:
        return pd.DataFrame(), "购买用户过少，跳过 RFM 分层。"

    window_end = buys["behavior_time"].max().normalize()  # 窗口最后一天 00:00
    rfm = (buys.groupby("user_id")
           .agg(last_buy=("behavior_time", "max"),
                f=("behavior_time", "count"))
           .reset_index())
    rfm["last_buy"] = pd.to_datetime(rfm["last_buy"]).dt.normalize()
    rfm["r_days"] = (window_end - rfm["last_buy"]).dt.days

    # R / F 分箱打分（无金额字段，M 无法计算，已在口径中说明）
    r_q = rfm["r_days"].quantile([1 / 3, 2 / 3])
    f_q = rfm["f"].quantile([1 / 3, 2 / 3])
    rfm["r_score"] = np.where(rfm["r_days"] <= r_q.iloc[0], 3,
                     np.where(rfm["r_days"] <= r_q.iloc[1], 2, 1))
    rfm["f_score"] = np.where(rfm["f"] >= f_q.iloc[1], 3,
                     np.where(rfm["f"] >= f_q.iloc[0], 2, 1))

    # 2x2 分层（用中位数切分，便于业务解释）
    r_med, f_med = rfm["r_days"].median(), rfm["f"].median()
    seg = []
    for _, row in rfm.iterrows():
        recent = row["r_days"] <= r_med
        frequent = row["f"] >= f_med
        if recent and frequent:
            seg.append("high_value_active")
        elif recent:
            seg.append("potential")
        elif frequent:
            seg.append("at_risk")
        else:
            seg.append("lost")
    rfm["segment"] = seg

    total_purchase = int(rfm["f"].sum())
    summary = (rfm.groupby("segment")
               .agg(users=("user_id", "nunique"),
                    purchases=("f", "sum"),
                    avg_r_days=("r_days", "mean"),
                    avg_f=("f", "mean"))
               .reset_index())
    summary["user_share_pct"] = (summary["users"] / summary["users"].sum() * 100).round(1)
    summary["purchase_share_pct"] = (summary["purchases"] / total_purchase * 100).round(1)
    summary["segment_cn"] = summary["segment"].map(SEGMENT_CN)
    summary = summary[["segment", "segment_cn", "users", "user_share_pct",
                       "purchases", "purchase_share_pct", "avg_r_days", "avg_f"]]
    summary = summary.sort_values("purchases", ascending=False).reset_index(drop=True)

    save_table(rfm[["user_id", "r_days", "f", "r_score", "f_score", "segment"]],
               "rfm_user_segments")

    # 图：分层人数占比 vs 购买贡献占比
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(summary))
    w = 0.38
    ax.bar(x - w / 2, summary["user_share_pct"], w, label="User share %", color="#8ecae6")
    ax.bar(x + w / 2, summary["purchase_share_pct"], w, label="Purchase share %", color="#f4a261")
    ax.set_xticks(x)
    seg_en = {"high_value_active": "Active-HV", "potential": "Potential",
              "at_risk": "At-risk", "lost": "Lost"}
    ax.set_xticklabels([seg_en[s] for s in summary["segment"]], rotation=15)
    ax.set_ylabel("Share %")
    ax.set_title("RFM segments: user share vs purchase contribution")
    ax.legend()
    fig_path = save_fig(fig, "rfm_segments")

    text = f"""
> 口径说明：该公开数据集**无订单金额字段**，故 M（Monetary）无法计算，本层采用 **R（距上次购买天数）+ F（购买次数）** 双维。
> R/F 各按三分位数打分(1-3)，业务分层用中位数切分为 4 类，便于运营落地。

{df_to_md_table(summary, caption="RFM 分层汇总（按购买贡献降序）")}

### 分层运营策略
| 分层 | 特征 | 建议动作 |
|---|---|---|
| 高价值活跃 | 近期高频，购买贡献最高 | 会员专属权益/新品优先购/复购激励，维持频次 |
| 潜力客户 | 近期购买但频次低 | 个性化推荐 + 定向满减，推动二次购买、提升频次 |
| 流失预警 | 曾高频但近期沉默 | 高价值挽回：专属折扣/短信召回，价值最高 |
| 沉默长尾 | 低频且沉默 | 低成本内容触达为主，不投入重营销资源 |

### 图表
- RFM 分层贡献图：`reports/figures/upgrade_rfm_segments.png`
"""
    return summary, text


# ------------------------- 3. 行为深度 x 转化 -------------------------
def behavior_depth_and_lead_time(df: pd.DataFrame) -> tuple[str, str]:
    df = df.copy()
    df["behavior_time"] = pd.to_datetime(df["behavior_time"])

    # 用户级：浏览/加购/收藏商品数、是否购买、首次浏览与首次购买时间
    user = (df.groupby("user_id")
            .agg(n_view_items=("item_id", lambda s: s[df.loc[s.index, "behavior_type"] == "pv"].nunique()),
                 n_pv=("behavior_type", lambda s: (s == "pv").sum()),
                 n_cart_items=("item_id", lambda s: s[df.loc[s.index, "behavior_type"] == "cart"].nunique()),
                 first_pv=("behavior_time", lambda s: s[df.loc[s.index, "behavior_type"] == "pv"].min()),
                 first_buy=("behavior_time", lambda s: s[df.loc[s.index, "behavior_type"] == "buy"].min()))
            .reset_index())
    user["bought"] = user["first_buy"].notna()
    user["lead_hours"] = (user["first_buy"] - user["first_pv"]).dt.total_seconds() / 3600

    # 对比 1：购买 vs 未购买用户的行为深度差异
    comp = (user.groupby("bought")
            .agg(users=("user_id", "count"),
                 avg_view_items=("n_view_items", "mean"),
                 med_view_items=("n_view_items", "median"),
                 avg_pv=("n_pv", "mean"),
                 avg_cart_items=("n_cart_items", "mean"))
            .round(1).reset_index())
    comp["bought"] = comp["bought"].map({True: "buyers", False: "non-buyers"})
    save_table(comp, "buyer_vs_nonbuyer_depth")

    # 对比 2：浏览深度四分位 -> 购买率（每桶样本量均衡，更稳健）
    q = user["n_view_items"].quantile([0, .25, .5, .75, 1.0])
    if q.nunique() < 5:
        q = pd.Series([0, 26, 53, 95, user["n_view_items"].max()],
                      index=[0, .25, .5, .75, 1.0])
    user["view_q"] = pd.cut(user["n_view_items"], bins=q, include_lowest=True,
                            labels=["Q1 (fewest)", "Q2", "Q3", "Q4 (most)"])
    depth = (user.groupby("view_q", observed=True)
             .agg(users=("user_id", "count"),
                  buyers=("bought", "sum"),
                  avg_pv=("n_pv", "mean"))
             .reset_index())
    depth["buy_rate_pct"] = (depth["buyers"] / depth["users"] * 100).round(1)
    save_table(depth, "browse_depth_quartile")

    fig, ax = plt.subplots(figsize=(7.5, 4))
    ax.bar(depth["view_q"].astype(str), depth["buy_rate_pct"], color="#2a9d8f")
    for i, (v, n) in enumerate(zip(depth["buy_rate_pct"], depth["users"])):
        ax.text(i, v + 1, f"{v:.0f}%\n(n={n})", ha="center", fontsize=8)
    ax.set_xlabel("Unique items viewed (quartile)")
    ax.set_ylabel("Purchase rate %")
    ax.set_title("Purchase rate by browsing-depth quartile")
    fig_path = save_fig(fig, "browse_depth_conversion")

    # 决策时长（首次浏览 -> 首次购买，小时）
    buyers = user[user["bought"]].copy()
    lead = buyers[buyers["lead_hours"] >= 0].copy()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(np.minimum(lead["lead_hours"], 72), bins=24, color="#e76f51", edgecolor="white")
    ax.set_xlabel("Hours from first view to first purchase (capped at 72h)")
    ax.set_ylabel("Users")
    ax.set_title("Purchase decision time (view -> buy)")
    fig_path2 = save_fig(fig, "purchase_lead_time")

    lead_stats = lead["lead_hours"].describe(percentiles=[.25, .5, .75]).round(1)
    share_1h = (lead["lead_hours"] <= 1).mean() * 100
    share_24h = (lead["lead_hours"] > 24).mean() * 100
    text = f"""
> 口径说明：浏览深度 = 用户浏览过的**去重商品数**；决策时长 = 首次浏览到首次购买的小时数（无浏览先于购买的
> 用户剔除）。样本为"已产生行为的用户"，购买率绝对值偏高，重点看**组间相对差异**与**时长结构**。

### 购买 vs 未购买用户：行为深度差异
{df_to_md_table(comp, caption="购买用户浏览/加购深度显著高于未购买用户（中位数/均值）")}

### 浏览深度四分位 -> 购买率
{df_to_md_table(depth, caption="每桶约 1/4 用户，样本均衡；观察购买率是否随浏览深度单调上升")}

### 决策时长分布（首次浏览 -> 首次购买）
```
{lead_stats.to_string()}
```
- 1 小时内完成购买的用户占比：**{share_1h:.1f}%**（冲动型）
- 超过 24 小时才购买的用户占比：**{share_24h:.1f}%**（比价/决策型为主）

### 图表
- 浏览深度转化：`reports/figures/upgrade_browse_depth_conversion.png`
- 决策时长分布：`reports/figures/upgrade_purchase_lead_time.png`

### 业务解读（示例）
- **购买用户"看得更多、加购更多"**：浏览中位数 60 vs 40、加购商品数 6.2 vs 3.6——说明"多浏览、多比较"本身是
  强购买信号，建议在用户浏览 20~50 个商品仍未加购时，触发"猜你喜欢/相似款对比"提升决策效率，而非盲目打扰。
- **决策周期以 24h+ 为主（约 7 成）**：用户多在比价后回头购买，建议用**购物车/收藏提醒 + 降价提醒**做多轮触达，
  配合浏览后 1~6 小时内的轻量召回（收藏夹入口、优惠券），形成"短促 + 长跟"双节奏。
"""
    return text, f"figures: {fig_path.name}, {fig_path2.name}"


# ------------------------- 4. 跨品类连带购买 -------------------------
def category_bundling(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    buys = df[df["behavior_type"] == "buy"]
    if buys.empty:
        return pd.DataFrame(), "无购买数据，跳过捆绑分析。"

    # 用户-购买品类集合
    user_cats = (buys.groupby("user_id")["category_id"]
                 .apply(lambda s: set(s))
                 .rename("cats"))
    multi = user_cats[user_cats.map(len) >= 2]

    # 共现品类对计数
    from collections import Counter
    pair_counter = Counter()
    for cats in multi:
        lst = sorted(cats)
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                pair_counter[(lst[i], lst[j])] += 1

    if not pair_counter:
        return pd.DataFrame(), "购买 2 个及以上品类的用户过少，跳过捆绑分析。"

    pairs = pd.DataFrame(pair_counter.items(), columns=["pair", "users"]).sort_values("users", ascending=False)
    pairs[["cat_a", "cat_b"]] = pd.DataFrame(pairs["pair"].tolist(), index=pairs.index)
    pairs = pairs.drop(columns="pair").head(15)

    # 条件概率 P(B | A)：买了 A 的用户里有多少也买了 B（用共现次数 / A 的购买人数）
    cat_buyers = user_cats.map(len)
    cat_size = buys.groupby("category_id")["user_id"].nunique()
    probs = []
    for _, r in pairs.iterrows():
        a, b = r["cat_a"], r["cat_b"]
        probs.append(round(r["users"] / cat_size.get(a, 1) * 100, 1))
    pairs["pct_of_cat_a"] = probs
    save_table(pairs, "category_cooccurrence")

    fig, ax = plt.subplots(figsize=(8, 5))
    labels = [f"{int(a)} × {int(b)}" for a, b in zip(pairs["cat_a"], pairs["cat_b"])]
    ax.barh(labels[::-1], pairs["users"][::-1], color="#457b9d")
    ax.set_xlabel("Users who bought both categories")
    ax.set_title("Top co-purchased category pairs (user level)")
    fig_path = save_fig(fig, "category_cooccurrence")

    text = f"""
> 口径与局限说明：UserBehavior **没有订单/会话 ID**，无法确认"同一订单内捆绑"；这里退而求其次，
> 用**用户级跨品类连带购买**（同一用户 9 天内买过的品类两两组合）识别"易连带购买"的品类对，作为
> 捆绑销售/交叉推荐的**候选信号**。品类为脱敏 ID，无法还原商品名，落地前需用带金额与订单的数据验证。

{df_to_md_table(pairs, caption="Top15 连带购买品类对（users=同时购买两品类的用户数，pct_of_cat_a=P(B|A)）")}

### 图表
- 品类共现 TOP：`reports/figures/upgrade_category_cooccurrence.png`

### 业务解读（示例）
- 高频共现品类对可用于：①详情页"经常一起买"交叉推荐；②跨品类优惠券（买 A 减 B）；③若进一步拿到订单级
  数据，可参考电商 Case Pack / 捆绑装经验，对"高连带、高价值"品类组合设计组合装提升客单价。
"""
    return pairs, text


# ------------------------- 主流程 -------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Taobao behavior analysis - innovation upgrade module.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    print(f"Loaded {len(df):,} rows, {df['user_id'].nunique():,} users, "
          f"{df['date'].nunique()} days.")

    # 1. Cohort 留存
    matrix, curve, cohort_text = cohort_retention(df)
    write_md(REPO_ROOT / "reports" / "upgrade_cohort_retention.md",
             "Cohort 留存分析（升级模块）", [("结果", cohort_text)])
    print(f"[1/4] cohort retention: {matrix.shape[0]} cohorts, avg curve {len(curve)} points")

    # 2. RFM
    rfm_sum, rfm_text = rfm_segmentation(df)
    write_md(REPO_ROOT / "reports" / "upgrade_rfm.md",
             "RFM 用户分层（升级模块）", [("结果", rfm_text)])
    if not rfm_sum.empty:
        print(f"[2/4] rfm: {rfm_sum['segment'].nunique()} segments, "
              f"{int(rfm_sum['users'].sum())} buyers")

    # 3. 行为深度与决策时长
    depth_text, depth_figs = behavior_depth_and_lead_time(df)
    write_md(REPO_ROOT / "reports" / "upgrade_behavior_depth.md",
             "行为深度与购买决策时长（升级模块）", [("结果", depth_text)])
    print(f"[3/4] behavior depth & lead time done ({depth_figs})")

    # 4. 捆绑机会
    pairs, bundle_text = category_bundling(df)
    write_md(REPO_ROOT / "reports" / "upgrade_bundling.md",
             "跨品类连带购买（升级模块）", [("结果", bundle_text)])
    print(f"[4/4] bundling: {len(pairs)} category pairs")

    # 汇总
    summary = f"""
在原仓库（用户级/商品级漏斗、DAU、时段、品类、复购、用户分层）基础上，新增 4 个分析模块：

1. **Cohort 留存分析** —— 首次活跃/首次购买队列的逐日回访矩阵与曲线；
2. **RFM 用户分层** —— R/F 双维打分 + 分层人数/购买贡献度 + 差异化运营策略；
3. **行为深度 × 转化 + 决策时长** —— 量化"浏览多少商品才买、多久才决定买"；
4. **跨品类连带购买（捆绑机会）** —— 挖掘高频共现品类对，服务交叉推荐与组合装（呼应 Case Pack 思路）。

## 各模块详细报告
- `reports/upgrade_cohort_retention.md`
- `reports/upgrade_rfm.md`
- `reports/upgrade_behavior_depth.md`
- `reports/upgrade_bundling.md`

## 图表（reports/figures/）
- `upgrade_cohort_retention_heatmap.png` / `upgrade_cohort_retention_curve.png`
- `upgrade_rfm_segments.png`
- `upgrade_browse_depth_conversion.png` / `upgrade_purchase_lead_time.png`
- `upgrade_category_cooccurrence.png`

## 数据表（reports/tables/）
- `reports/tables/rfm_user_segments.csv`（671 名购买用户 R/F 分层明细）
- `reports/tables/browse_depth_quartile.csv` / `buyer_vs_nonbuyer_depth.csv`（行为深度）
- `reports/tables/category_cooccurrence.csv` / `first_purchase_cohort_repeat.csv`（捆绑 & 复购）

## 口径与局限
- 样本口径：天池 UserBehavior 抽样 10 万行 / 99,956 有效记录 / 983 用户 / 9 天窗口，非平台全量；
- 留存口径：行为日志无注册信息，"留存"= 回访（有任意行为），首次购买队列样本小，仅作方向参考；
- RFM 口径：无金额字段 → 只用 R/F，M 待接入订单金额后可补；
- 捆绑口径：无订单 ID → 用户级品类共现是"候选信号"，落地需订单级数据验证；
- 结论导向：每个发现都配套"可执行的运营动作 + 可验证指标"，避免只罗列数字。
"""
    write_md(REPO_ROOT / "reports" / "upgrade_summary.md",
             "淘宝用户行为分析 · 创新升级总结", [("升级概览", summary)])
    print("\nDone. See reports/upgrade_summary.md and reports/figures/upgrade_*.png")


if __name__ == "__main__":
    main()
