# 实验配置优先级

明确优先级：**显式命令行参数 > 配置JSON > 软件默认值**。省略选项不覆盖文件；显式 `--removed ""` 会清空文件中的额外移除。JSON中的规范字段与别名不能同时出现；未知字段、非对象根和非法字段类型在启动计算前报错，退出码2。

JSON支持 `kind`、`n_decks`、`template`、`player_ranks`、`dealer_up`、`extra_removed`、`peek_negative`、`seat`、`db_path`、`session_id`、`through_seq`、`note`。兼容别名 `decks`、`player`、`up`、`removed`。牌面可写逗号分隔字符串或数组；副数可写整数、数组或逗号分隔字符串。文本字段不能用容器或布尔值代替。历史目标的路径和会话可为null；历史回放仍需完整目标才能计算。

无文件时默认合成实验、6/7/8副、single模板、玩家10/6、庄家10、无额外移除、玩家1。文件可设置 `kind: history-prefix-replay`；`--mode` 只在明确传入时覆盖它。历史回放的实际桌规和牌况来自选定数据库事件前缀，不从合成参数替换历史。

```powershell
python scripts/run_experiment.py --config fixtures/experiments/config-das-eight.json --output .local-evidence/config-example
python scripts/run_experiment.py --config fixtures/experiments/config-das-eight.json --player 9,9 --removed "" --output .local-evidence/config-override
```

输出目录必须尚不存在，不覆盖原实验。正常输出包含 `experiment.json`、`experiment.csv` 及可再次作为输入的 `effective_config.json`。终端JSON同时给出生效配置和本次实验ID。完整实验中的 `config` 还保留格式及实验身份；结果逐项保留输入摘要、规则摘要、事件前缀、引擎和策略身份。失败/取消项继续保留，不能因为命令运行完毕就当成有效EV。

示例中的 `das` 是原“两手顺序完成”研究模板；此次配置修复没有改变任何桌规、C#数学、精度或计算预算。它是合成当前手牌实验，不是顶部固定策略开局估算，也不是连续完整牌靴模拟。

验证入口：`python -m unittest tests.test_experiment_cli_config tests.test_experiments -v`。参数合并测试使用Runner替身，明确不计算EV；后者包含真实实验服务、历史前缀及JSON/CSV持久化。审查包原始4项反例与原脚本按原字节保留；修复后的正式回归使用当前生产CLI与真实配置模型。
