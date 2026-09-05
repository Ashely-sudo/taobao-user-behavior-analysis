# 淘宝用户行为分析 · 创新升级总结

## 升级概览

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
