# 第三方代码与许可说明

- V0.1 全部代码为本项目原创，**未直接整包并入任何第三方项目**；
- Python 主程序依赖标准库。V0.2b1 数值加速器为本项目 C# 源码，调用现有 Windows .NET Framework 4 编译器/运行时和 System.Web.Extensions；不重新分发 Microsoft 运行时或编译器，不引入 NuGet/pip 传递依赖；
- `possibly-wrong/blackjack`（GPL-3.0-or-later）仍是外部交叉验证引擎，**不并入产品**。
  本地可选步骤：`scripts/fetch_possibly_wrong.py` 按钉住的 v7.6/`a1f7dbb` 下载官方 `strategy.exe`，
  `scripts/compare_possibly_wrong.py` 在对齐牌靴与规则后做庄家分布和未分牌动作差分。
  二进制与源码只放在 gitignore 的 `.local-evidence/`，不进 `blackjack_lab/`、安装包或便携副本。
  对照通过也不等于采用了该实现、不等于分牌/DAS 已对齐，也不等于获得任何平台许可；
  详见 [V0.2b2 外部对照](docs/V0.2b2-外部对照.md)；
- 本工具不接真实资金账户、不自动下注、不自动点击、不读取隐藏接口、
  不绕过屏幕捕获限制、不提供反检测或账号规避功能；
- Stake 等真实平台的连接适配须单独核对平台条款、供应商与适用权限，
  V0.1 默认不启用任何实盘适配。
- V0.3a 识牌为**可选依赖**。未安装时手动录牌与数学分析仍可用。若安装 `requirements-vision.txt`，将引入 `opencv-python-headless`（PyPI 发行许可见该包；OpenCV 库为 Apache-2.0）。模板匹配分数不是校准概率。不把 OpenCV 示例工程或第三方牌面美术整包复制进本仓库；首版素材为自建合成像素。
