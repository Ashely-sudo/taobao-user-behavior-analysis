# -*- coding: utf-8 -*-
"""
淘宝用户价值分层与召回策略工具 (Streamlit)
=========================================
在"淘宝用户行为端到端分析（复现+升级）"项目基础上构建的决策型看板，
围绕一条业务故事线：识别高价值用户 → 发现流失风险 → 给出召回/运营策略 → 用指标验证。

本地运行（在仓库根目录）：
    streamlit run streamlit_app/app.py
数据源：data/cleaned_user_behavior.csv + reports/tables/*.csv（均由 src/upgrade_analysis.py 产出）
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).resolve().parents[1]

st.set_page_config(
    page_title="淘宝用户价值分层与召回策略工具",
    page_icon="📊",
    layout="wide",
)

# ------------------------------------------------------------------
# 数据加载（带缓存）
# ------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_behavior() -> pd.DataFrame:
    df = pd.read_csv(BASE / "data" / "cleaned_user_behavior.csv")
    df["behavior_time"] = pd.to_datetime(df["behavior_time"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


@st.cache_data(show_spinner=False)
def load_rfm() -> pd.DataFrame:
    return pd.read_csv(BASE / "reports" / "tables" / "rfm_user_segments.csv")


@st.cache_data(show_spinner=False)
def load_repeat() -> pd.DataFrame:
    return pd.read_csv(BASE / "reports" / "tables" / "first_purchase_cohort_repeat.csv")


# ------------------------------------------------------------------
# 计算函数（带缓存，复用升级模块的方法）
# ------------------------------------------------------------------
SEGMENT_CN = {
    "high_value_active": "高价值活跃（近期高频）",
    "at_risk": "流失预警（沉默高频）",
    "lost": "沉默长尾（沉默低频）",
    "potential": "潜力客户（近期低频）",
}
SEG_STRATEGY = {
    "high_value_active": "会员专属权益 / 新品优先购 / 复购激励，维持频次与忠诚度。",
    "at_risk": "高价值挽回：专属折扣 + 定向召回（短信/App 推送），优先于一般流失用户。",
    "potential": "个性化推荐 + 定向满减，推动二次购买、提升购买频次。",
    "lost": "低成本内容触达为主，不投入重营销资源；聚焦中高价值群体。",
}
SEG_VERIFY = {
    "high_value_active": "复购率、人均购买次数、会员活跃度",
    "at_risk": "触达组 vs 对照组：召回后 7 天复购率、购买次数",
    "potential": "二次购买率、加购-购买转化率",
    "lost": "低成本唤醒的打开/点击率（不作为核心 KPI）",
}


@st.cache_data(show_spinner=False)
def rfm_summary() -> pd.DataFrame:
    rfm = load_rfm()
    total_purchase = int(rfm["f"].sum())
    s = (rfm.groupby("segment")
         .agg(users=("user_id", "nunique"),
              purchases=("f", "sum"),
              avg_r_days=("r_days", "mean"),
              avg_f=("f", "mean"))
         .reset_index())
    s["user_share_pct"] = (s["users"] / s["users"].sum() * 100).round(1)
    s["purchase_share_pct"] = (s["purchases"] / total_purchase * 100).round(1)
    s["segment_cn"] = s["segment"].map(SEGMENT_CN)
    return s.sort_values("purchases", ascending=False).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def cohort_matrix_and_curve():
    df = load_behavior()
    first_active = df.groupby("user_id")["date"].min().rename("first_active")
    d = df.merge(first_active, on="user_id")
    active_days = df.groupby("user_id")["date"].apply(lambda s: set(s))
    cohorts = d.groupby("first_active")["user_id"].unique()
    last_date = df["date"].max()

    rows = []
    for cohort_day, users in sorted(cohorts.items()):
        cu = set(int(u) for u in users)
        max_obs = (last_date - cohort_day).days
        row = {"cohort": cohort_day.isoformat(), "size": len(cu)}
        for offset in range(max_obs + 1):
            target = cohort_day + timedelta(days=offset)
            kept = sum(1 for u in cu if target in active_days.get(u, set()))
            row[f"d{offset}"] = round(kept / len(cu) * 100, 1)
        rows.append(row)
    matrix = pd.DataFrame(rows).set_index("cohort")

    curve_rows = []
    for offset in range(9):
        elig, kept = [], []
        for cohort_day, users in sorted(cohorts.items()):
            if cohort_day + timedelta(days=offset) <= last_date:
                cu = set(int(u) for u in users)
                target = cohort_day + timedelta(days=offset)
                elig.append(len(cu))
                kept.append(sum(1 for u in cu if target in active_days.get(u, set())))
        if sum(elig):
            curve_rows.append({"day_offset": offset,
                               "retention_pct": round(sum(kept) / sum(elig) * 100, 1)})
    return matrix, pd.DataFrame(curve_rows)


@st.cache_data(show_spinner=False)
def behavior_depth():
    df = load_behavior()
    user = (df.groupby("user_id")
            .agg(n_view_items=("item_id", lambda s: s[df.loc[s.index, "behavior_type"] == "pv"].nunique()),
                 n_pv=("behavior_type", lambda s: (s == "pv").sum()),
                 n_cart_items=("item_id", lambda s: s[df.loc[s.index, "behavior_type"] == "cart"].nunique()),
                 first_pv=("behavior_time", lambda s: s[df.loc[s.index, "behavior_type"] == "pv"].min()),
                 first_buy=("behavior_time", lambda s: s[df.loc[s.index, "behavior_type"] == "buy"].min()))
            .reset_index())
    user["bought"] = user["first_buy"].notna()
    user["lead_hours"] = (user["first_buy"] - user["first_pv"]).dt.total_seconds() / 3600

    comp = (user.groupby("bought")
            .agg(users=("user_id", "count"),
                 avg_view_items=("n_view_items", "mean"),
                 med_view_items=("n_view_items", "median"),
                 avg_pv=("n_pv", "mean"),
                 avg_cart_items=("n_cart_items", "mean"))
            .round(1).reset_index())
    comp["bought"] = comp["bought"].map({True: "购买用户", False: "未购买用户"})

    q = user["n_view_items"].quantile([0, .25, .5, .75, 1.0])
    user["view_q"] = pd.cut(user["n_view_items"], bins=q, include_lowest=True,
                            labels=["Q1 最少", "Q2", "Q3", "Q4 最多"])
    quart = (user.groupby("view_q", observed=True)
             .agg(users=("user_id", "count"),
                  buyers=("bought", "sum"))
             .reset_index())
    quart["buy_rate_pct"] = (quart["buyers"] / quart["users"] * 100).round(1)

    lead = user[user["bought"] & (user["lead_hours"] >= 0)].copy()
    return comp, quart, lead["lead_hours"].clip(upper=72)


@st.cache_data(show_spinner=False)
def bundling():
    df = load_behavior()
    buys = df[df["behavior_type"] == "buy"]
    user_cats = buys.groupby("user_id")["category_id"].apply(lambda s: set(s))
    multi = user_cats[user_cats.map(len) >= 2]
    from collections import Counter
    pair_counter = Counter()
    for cats in multi:
        lst = sorted(cats)
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                pair_counter[(lst[i], lst[j])] += 1
    cat_size = buys.groupby("category_id")["user_id"].nunique()
    rel = {}
    for (a, b), n in pair_counter.items():
        rel.setdefault(a, []).append({"cat_b": b, "users": n, "pct": round(n / cat_size.get(a, 1) * 100, 1)})
        rel.setdefault(b, []).append({"cat_b": a, "users": n, "pct": round(n / cat_size.get(b, 1) * 100, 1)})
    for k in rel:
        rel[k] = sorted(rel[k], key=lambda x: -x["users"])[:10]
    return rel, sorted(rel.keys())


# ------------------------------------------------------------------
# 页面
# ------------------------------------------------------------------
def page_overview():
    st.title("📊 淘宝用户价值分层与召回策略工具")
    st.caption("数据：阿里天池 UserBehavior 公开数据集抽样 10 万行（清洗后 99,956 条 / 983 用户 / 9 天）")
    st.markdown(
        "**业务故事线**：识别高价值用户 → 发现流失风险 → 给出召回/运营策略 → 用指标验证。"
        "下面的每个数字都对应一个可执行的运营动作。")

    s = rfm_summary()
    hv = s[s.segment == "high_value_active"].iloc[0]
    ar = s[s.segment == "at_risk"].iloc[0]
    _, quart, lead = behavior_depth()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("高价值活跃用户购买贡献", f"{hv['purchase_share_pct']:.1f}%",
              f"仅占 {hv['user_share_pct']:.1f}% 用户")
    c2.metric("流失预警用户购买贡献", f"{ar['purchase_share_pct']:.1f}%",
              f"占 {ar['user_share_pct']:.1f}% 用户 · 正在流失")
    c3.metric("浏览最多 1/4 用户购买率", f"{quart['buy_rate_pct'].iloc[-1]:.1f}%",
              f"最少 1/4 仅 {quart['buy_rate_pct'].iloc[0]:.1f}%")
    c4.metric("购买发生在 24h 之后", f"{((lead > 24).mean() * 100):.1f}%",
              "比价/决策型为主")

    st.divider()
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("RFM 分层：人数 vs 购买贡献")
        fig = go.Figure()
        fig.add_bar(x=s["segment_cn"], y=s["user_share_pct"], name="人数占比 %",
                    marker_color="#8ecae6", text=s["user_share_pct"], textposition="outside")
        fig.add_bar(x=s["segment_cn"], y=s["purchase_share_pct"], name="购买贡献 %",
                    marker_color="#f4a261", text=s["purchase_share_pct"], textposition="outside")
        fig.update_layout(barmode="group", height=380, yaxis_title="占比 %",
                          margin=dict(t=30, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        st.info("**所以运营该做什么**：把资源优先投向『高价值活跃』（维护）与『流失预警』（挽回），"
                "因为 22.1% 的流失预警用户贡献了 24.6% 的购买。")
    with col_r:
        st.subheader("购买决策时长（首次浏览 → 首购）")
        fig = px.histogram(lead, nbins=24, color_discrete_sequence=["#e76f51"])
        fig.update_layout(height=380, xaxis_title="小时（封顶 72h）", yaxis_title="用户数",
                          margin=dict(t=30, b=10), showlegend=False)
        st.plotly_chart(fig, width="stretch")
        st.info("**所以运营该做什么**：73% 的购买发生在 24h 之后 → 用购物车/收藏提醒 + 降价提醒做**多轮触达**，"
                "而不是只推一次促销。")


def page_at_risk():
    st.title("🚨 流失预警用户：召回策略工作台")
    st.markdown(
        "流失预警 = **曾经高频购买、但近期沉默**的用户。这批人贡献了 24.6% 的购买，"
        "是最值得优先挽回的人群。")

    rfm = load_rfm()
    s = rfm_summary()
    ar_seg = rfm[rfm.segment == "at_risk"].copy()

    min_f = st.slider("筛选：历史购买次数 ≥", 2, 30, 2, key="ar_minf")
    max_r = st.slider("筛选：沉默天数 ≤（想扩大召回面就调大）", 1, 9, 9, key="ar_maxr")
    ar = ar_seg[(ar_seg["f"] >= min_f) & (ar_seg["r_days"] <= max_r)].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("命中流失预警用户数", f"{len(ar):,}")
    c2.metric("人均历史购买次数", f"{ar['f'].mean():.1f}" if len(ar) else "-")
    c3.metric("平均沉默天数", f"{ar['r_days'].mean():.1f}" if len(ar) else "-")

    st.subheader("召回策略建议")
    st.success(SEG_STRATEGY["at_risk"])
    st.markdown(f"**效果怎么验证**：{SEG_VERIFY['at_risk']} —— 把命中用户随机分『触达组 / 对照组』，"
                "对比召回后 7 天复购率与购买次数，验证 ROI 后再全量放量。")

    st.subheader("人群明细（可下载做后续触达）")
    show = ar.sort_values("f", ascending=False).head(200)
    st.dataframe(show.rename(columns={"user_id": "用户ID", "r_days": "沉默天数",
                                      "f": "购买次数", "r_score": "R分", "f_score": "F分"}),
                 width="stretch", hide_index=True)
    st.download_button("⬇️ 下载全部流失预警用户 CSV",
                       ar.to_csv(index=False).encode("utf-8-sig"),
                       file_name="at_risk_users.csv", mime="text/csv")
    st.caption(f"展示前 200 行；下载为全量 {len(ar):,} 人。当前为行为样本口径，落地前建议在真实全量数据上重算。")


def page_cohort():
    st.title("🕐 Cohort 留存：用户什么时候流失")
    matrix, curve = cohort_matrix_and_curve()
    repeat = load_repeat()

    st.subheader("首次活跃队列 · 逐日回访率热力图（%）")
    plot_m = matrix.iloc[:, 1:].astype(float)
    fig = go.Figure(go.Heatmap(
        z=plot_m.values,
        x=[c.replace("d", "D") for c in plot_m.columns],
        y=plot_m.index,
        colorscale="YlGnBu", zmin=0, zmax=100,
        text=np.round(plot_m.values, 0),
        texttemplate="%{text}",
        colorbar=dict(title="回访率 %")))
    fig.update_layout(height=420, margin=dict(t=20, b=10), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("平均留存曲线")
        fig = px.line(curve, x="day_offset", y="retention_pct", markers=True)
        fig.update_layout(height=320, xaxis_title="相对第 N 天", yaxis_title="回访率 %",
                          yaxis=dict(range=[0, 105]), margin=dict(t=20, b=10))
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.subheader("首购队列 · 窗口内复购率")
        fig = px.bar(repeat, x="first_buy", y="repeat_rate_pct",
                     text=repeat["repeat_rate_pct"].round(0).astype(int).astype(str) + "%")
        fig.update_layout(height=320, xaxis_title="首次购买日期", yaxis_title="复购率 %",
                          margin=dict(t=20, b=10), showlegend=False)
        st.plotly_chart(fig, width="stretch")

    with st.expander("⚠️ 口径陷阱：为什么 D7/D8 回访率飙到 95%+？"):
        st.markdown(
            "1. **周末效应**：D7/D8 = 12/2、12/3，正是周六日，行为天然高峰（与原项目『周末行为激增』结论互相印证）；\n"
            "2. **抽样偏差**：10 万行样本按『行为』抽取，重度用户占比偏高，回访率被系统性高估。\n\n"
            "**结论**：这不是真实留存改善。真实留存需基于注册/会话/全量日志、按『首次访问日』对齐计算。"
            "把口径陷阱讲清楚，比报一个漂亮数字更能体现数据 sense。")
    with st.expander("查看留存矩阵明细"):
        st.dataframe(matrix.reset_index(), width="stretch", hide_index=True)


def page_behavior():
    st.title("🛒 行为深度 × 转化：什么样的用户才会买")
    comp, quart, lead = behavior_depth()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("购买 vs 未购买：行为深度对比")
        fig = go.Figure()
        fig.add_bar(x=comp["bought"], y=comp["med_view_items"], name="浏览商品中位数",
                    marker_color="#2a9d8f", text=comp["med_view_items"], textposition="outside")
        fig.add_bar(x=comp["bought"], y=comp["avg_cart_items"], name="加购商品均值",
                    marker_color="#e9c46a", text=comp["avg_cart_items"], textposition="outside")
        fig.update_layout(barmode="group", height=330, yaxis_title="商品数",
                          margin=dict(t=20, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        st.info("购买用户浏览中位数 60 个商品 vs 未购买 40 个；加购 6.2 vs 3.6 → **多看多比是强购买信号**。")
    with c2:
        st.subheader("浏览深度四分位 → 购买率")
        fig = px.bar(quart, x="view_q", y="buy_rate_pct",
                     text=quart["buy_rate_pct"].round(0).astype(int).astype(str) + "%")
        fig.update_layout(height=330, yaxis_title="购买率 %", yaxis=dict(range=[0, 100]),
                          margin=dict(t=20, b=10), showlegend=False)
        st.plotly_chart(fig, width="stretch")
        st.info("购买率随浏览深度从 56.3% 单调升到 77.1% → 浏览 20~50 个商品仍未加购时，"
                "推『相似款对比/猜你喜欢』比推促销更有效。")

    st.subheader("决策时长分布（首次浏览 → 首次购买，小时）")
    fig = px.histogram(lead, nbins=24, color_discrete_sequence=["#457b9d"])
    fig.update_layout(height=340, xaxis_title="小时（封顶 72h）", yaxis_title="用户数",
                      margin=dict(t=20, b=10), showlegend=False)
    st.plotly_chart(fig, width="stretch")
    st.success("**结论**：73% 的购买发生在首次浏览 24 小时之后、仅 9.3% 在 1 小时内 → 比价/决策型为主。"
               "运营节奏应是『短促 + 长跟』：1~6h 内轻量召回（收藏夹/优惠券），24h+ 用购物车/降价提醒持续跟进。")


def page_bundling():
    st.title("🧩 捆绑机会：买了 A 还常买 B")
    st.markdown(
        "用**用户级品类共现**识别『易连带购买』的品类组合，服务**详情页交叉推荐**与**组合装（Case Pack）**。"
        "思路直接来自 Amazon 运营经验：通过整箱/捆绑销售提升客单价与 ROI。")

    rel, cats = bundling()
    chosen = st.selectbox("选择一个品类（脱敏 ID）", cats, index=None,
                          placeholder="点击选择品类…")
    if chosen is None:
        st.info("👈 先在上方选择一个品类，右侧会展示『买了它还常买什么』。")
        return

    top = rel[chosen]
    df_show = pd.DataFrame(top).rename(columns={"cat_b": "连带品类", "users": "共购用户数",
                                                "pct": "P(B|A) %"})
    c1, c2 = st.columns([1, 1.4])
    with c1:
        st.metric("该品类连带品类数（Top10 展示）", len(top))
        st.dataframe(df_show, width="stretch", hide_index=True)
    with c2:
        fig = px.bar(df_show.sort_values("共购用户数"), x="共购用户数", y="连带品类",
                     orientation="h", text="P(B|A) %",
                     color="P(B|A) %", color_continuous_scale="Blues")
        fig.update_layout(height=420, margin=dict(t=20, b=10), coloraxis_showscale=False,
                          xaxis_title="同时购买两品类的用户数")
        st.plotly_chart(fig, width="stretch")

    st.success("**运营动作**：对高 P(B|A) 组合做①详情页『经常一起买』推荐；②跨品类优惠券（买 A 减 B）；"
               "③若拿到订单级数据，可进一步验证同单共现，设计组合装（Case Pack）提升客单价。")
    with st.expander("⚠️ 口径与局限"):
        st.markdown(
            "UserBehavior **没有订单/会话 ID**，无法确认『同一订单内捆绑』。"
            "这里是**用户级**连带购买信号（同一用户 9 天内买过的品类组合），作为**候选信号**；"
            "落地捆绑装前，需要用带订单 ID 与金额的数据验证同单共现与毛利。")


def render_sidebar():
    with st.sidebar:
        st.title("📊 决策工具")
        page = st.radio("选择页面", [
            "🏠 决策总览", "🚨 流失预警召回", "🕐 Cohort 留存", "🛒 行为×转化", "🧩 捆绑机会",
        ], label_visibility="collapsed")
        st.divider()
        with st.expander("项目与口径说明"):
            st.markdown(
                "- **数据**：天池 UserBehavior 抽样 10 万行 → 清洗后 99,956 条 / 983 用户 / 9 天\n"
                "- **行为**：pv 浏览 / fav 收藏 / cart 加购 / buy 购买\n"
                "- **局限**：无金额（RFM 无 M）、无订单 ID（捆绑为用户级信号）、无注册信息（留存=回访）\n"
                "- **样本**：按行为抽样，重度用户占比偏高，绝对指标偏高，看相对结构与趋势\n"
                "- 本项目复现并升级自 [bluesblue320-hue 开源项目](https://github.com/bluesblue320-hue/Taobao-User-Behavior-Conversion-Analysis)")
        st.caption("个人作品集 · 数据仅供学习演示")
    return page


def main():
    page = render_sidebar()
    if page == "🏠 决策总览":
        page_overview()
    elif page == "🚨 流失预警召回":
        page_at_risk()
    elif page == "🕐 Cohort 留存":
        page_cohort()
    elif page == "🛒 行为×转化":
        page_behavior()
    else:
        page_bundling()


if __name__ == "__main__":
    main()
