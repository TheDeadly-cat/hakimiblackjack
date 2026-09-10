# 本次增量审查证据包

固定提交：ee485dd22285ec14f83bf8fe81df4fad2f3ea005。

## 实际运行与未运行的区别

- 已运行：与仓库 Git blob 哈希一致的4个数学源码/参考/测试文件，在空包初始化文件的隔离目录复跑5项原数学测试，通过。
- 已运行：48个新增固定场景，对照同一仓库的 Fraction 参考实现，全部通过。不是第三方新引擎，不加算进 upstream 154 项。
- 已核对但非本机重跑：GitHub最终SHA的154项Windows CI及2,500轮辅助模拟。
- 未运行：handoff中4项完整项目/Tk回归提案，仅语法检查。不要把它们写成实测失败或通过。
- 未完成：完整clone、本机完整应用重跑、Windows真机性能/DPI、真实平台。

## 复现实际数学检查

进入 isolated 目录后：

```bash
python -m unittest tests.test_analysis_math -v
python run_supplementary_math.py
```

它是隔离核验，不是可启动的产品源码包。

## 运行交接用例

把 handoff/test_v02a_review_regressions.py 放入完整项目根目录下 review_tests/，使用含Tk的Windows环境或已配置显示的Linux环境：

```bash
python -m unittest discover -s review_tests -p 'test_v02a_review_regressions.py' -v
```

用例指定了建议契约；若实现采用等价“保留但明确标旧”机制，可调整内部对象断言，但必须保留“旧结果不能作为当前结果发布”和原始问题场景，不可删掉测试目的。

详细审查、路径、优先级、验收和下一单见《增量审查与下一单.md》。
