# 源图首次可见、首次可读与参考勘误

31 个新可读事件已补充牌体／牌背首次可见区间，5 个开场目标单列，另记录观察截止时仍未读出牌级的一张牌背。这些是源图等待，不能加到或混作软件识别延迟。原模型、拒识阈值和实际运行没有改变。

## 源图复核范围

复用原 0–92 秒录像的四分之一秒原图，逐段查看首次发牌、7 次补牌、第二轮发牌及 88.75–92 秒的牌背尾段；开场另查看原始帧 0、1、2、3、6、9、12、18。计算入口核对 139 个源图文件摘要。首次可见定义为同一张已从牌靴中单独取出的牌体／牌背在本地原生 ROI 中可区分；不把仍混在牌靴里的牌算作已独立观察，也不宣称知道源牌桌的物理发牌时刻。

初次发牌、移动中的牌和手部遮挡均使用区间，通常宽 0.25–0.5 秒，个别保守区间达 0.75 秒。它们不是逐帧精确的身份真值。这轮复核发生在模型结果已经产生之后，明确标记 `made_after_model_outcomes_known=true`，不能包装成新的盲测。标注由助手依据原图完成，仍为 `human_confirmed=false`，没有改写用户原来的 559 条及补充框确认。

| 源图状态 | 数量 | 观察结果 |
| --- | ---: | --- |
| 原分母中的新可读目标 | 31 | 全部有首次可见及首次可读区间；等待中位数的区间为 0.25–0.75 秒 |
| 其中普通发牌／补牌 | 30 | 各自等待区间的端点落在 0–1.75 秒内；不是每张都等待 1.75 秒 |
| 其中稍后揭示的庄家暗牌 | 1 | 牌背在 5–5.5 秒区间已经单独可见，原可读区间为 61–61.25 秒；等待约 55.5–56.25 秒 |
| 开场已有的目标 | 5 | 原帧 0 视频层黑屏，原帧 1（约 8.33 ms）牌体已出现；实际发牌起点未知，另列 |
| 观察尾段仍为牌背 | 1 | 首次可见区间为 88–88.5 秒，约 92 秒的尾帧仍显示牌背；不填未知牌级，不并入可读牌级分母 |

暗牌的长等待不说明软件识别用了 56 秒。可读起点仍来自揭示后的正向上角，不能提前使用未来牌级。末尾未读出牌级的牌在当前抽查范围内只给等待下界约 3.50 秒，上界为未知；不填成零，也不借合计点数倒推出具体牌级。

## 一处牌级勘误与复算

逐张复看原参考上角时发现 `r1-p5-c4` 原写为 7，实际字形为 **J**。核对使用原上角和已知 J／7 的字形，局部旋转仅用于人工查看，未修改识别输入，也未翻转下角补数。首次可读区间、位置框、轨迹位置参考和源视频均未改变。

原 `event-reference-frozen-1.json` 及其 SHA256 `c3a98f188f04ed3eca46c2438a52567cbd29298efb3b7ebe27a45eb4330071a6` 保留。勘误参考另存为 `event-reference-rank-erratum-1.json`，SHA256 `3076e44e0f32491a7908a3d154e1c2e2e80cef6a81d154934b636f837d78cfa1`，注明在冻结后的源图复核中纠正。

已用勘误参考复算原八轮直接录像及六轮有效 WGC 运行，共 14 份原运行。所有分组指标、正确稳定数量、错误数量和时限计数均不变；差异仅是该参考事件的牌级从 7 改成 J，因为它在这些运行的可判锚点中均未匹配到候选。原评分文件不覆盖，新文件使用 `events-rank-erratum-1.json` 或 `wgc-events-rank-erratum-1.json`。这次勘误没有改善或恶化任何模型成绩，旧／RGB 未达标的结论不变。

## 可复核入口

`scripts/summarize_source_visibility.py` 核对参考摘要、源图摘要、每个原事件恰有一条可见记录、牌级和原可读区间一致，并检查时间顺序。它保留所有可读目标；观察截止时仍未可读的牌另列为上界未知，不能替换或重复已有事件。

```powershell
& '.\.local-evidence\rgb-corner-prototype\venv\Scripts\python.exe' scripts/summarize_source_visibility.py `
  --reference '.local-evidence/realtime-r3-new-source/event-reference-rank-erratum-1.json' `
  --appearance '.local-evidence/realtime-r3-new-source/first-appearance-source-review-1/appearance-reference-3.json' `
  --evidence-root '.local-evidence/realtime-r3-new-source' `
  --output '.local-evidence/realtime-r3-new-source/first-appearance-source-review-1/new-wait-report.json'
```

输出不会覆盖已有文件。三个手算检查覆盖完整事件集合、时间区间以及未知牌背不混入可读分母，另已完成真实源图摘要校验与 14 份运行复算。私有完整表、源图序列、字形复核图和旧草稿在 `.local-evidence/realtime-r3-new-source/first-appearance-source-review-1/`，用户入口为 `USER_VISIBILITY_REVIEW.md`。

本轮补齐了首次可见等待记录及一处参考勘误；完整额外输出真伪、物理身份／归属仍须继续，不把源图标注通过当成实时识牌通过。后续已接入 [原生区域观察与相同输入调度](REGION_OBSERVATION_20260914.md)，实测开销及未实现的局部检测、可读性判断另列，原事件结果不变。
