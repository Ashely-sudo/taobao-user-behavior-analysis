# 淘宝用户价值分层与召回策略工具（Streamlit 看板）

在"淘宝用户行为端到端分析（复现 + 创新升级）"基础上构建的**决策型交互看板**，
围绕一条业务故事线：**识别高价值用户 → 发现流失风险 → 给出召回/运营策略 → 用指标验证**。

## 页面
- 🏠 决策总览：四个关键结论 + "所以运营该做什么"
- 🚨 流失预警召回：可筛选/下载的召回人群 + 召回策略 + 效果验证口径
- 🕐 Cohort 留存：回访热力图 + 复购率 + 口径陷阱说明（周末效应/抽样偏差）
- 🛒 行为×转化：购买 vs 未购买对比、浏览深度四分位、决策时长
- 🧩 捆绑机会：选品类 → 看"买了它还常买什么"（交叉推荐 / Case Pack）

## 本地运行（在仓库根目录）
```bash
pip install -r streamlit_app/requirements.txt
streamlit run streamlit_app/app.py
# 浏览器打开 http://localhost:8501
```

## 部署（Streamlit Community Cloud，免费）
1. 把本仓库推送到 GitHub（已完成）；
2. 打开 https://share.streamlit.io 用 GitHub 登录；
3. New app → 选择 `Ashely-sudo/taobao-user-behavior-analysis` → Main file 填 `streamlit_app/app.py` → Deploy。
