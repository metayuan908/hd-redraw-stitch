# HD Redraw & Stitch · 高清重绘与拼接

GPT / Codex 高清放大、4K 高清重绘与分块拼接插件。通过语义蒙版、全局构图锚点和原生细节分块重绘，将已有图片重建为 HD、2K、4K、8K 或自定义尺寸。

搜索关键词：GPT、Codex、高清放大、4K、高清重绘、分块拼接；image upscaling、HD image redraw、region redraw、tile stitching。

作者：Yuanwei  
版本：0.5.1+codex.20261104

## 内容

- `skills/hd-redraw-stitch/`：风险预检、蒙版局部重绘、构图锚点、分块重绘、色彩协调、拼接及交付脚本。
- `skills/codex-gpt-image/`：通过当前机器的 Codex 登录状态调用 GPT Image 的配套 CLI。
- `.codex-plugin/plugin.json`：Codex 插件清单。
- `.agents/plugins/marketplace.json`：可通过本地路径或本仓库 Git URL 添加的插件目录。

## 环境与安装

需要 Python 3.10+，以及当前机器可用的 Codex 登录状态。

```bash
python3 -m pip install -r requirements.txt
```

作为 Codex 插件安装：

```bash
codex plugin marketplace add https://github.com/metayuan908/hd-redraw-stitch.git
codex plugin add hd-redraw-stitch@hd-redraw-stitch
```

本地开发时，第一条命令的来源也可以换成此仓库的绝对路径。更新安装后，重启桌面应用或在新聊天中检查技能选择器。

作为独立技能使用时，将 `skills/` 下的两个目录一起复制到 `~/.agents/skills/`，保持两个技能为相邻目录。插件包结构保留在本仓库中。

凭据仅从使用者本机读取；仓库不包含登录文件或 API 密钥。缺少登录状态时请运行 `codex login`。实际模型可用性取决于账号权限及服务端支持。

其他智能体需要能够加载 `SKILL.md`、执行本地 Python，并访问使用者本机的 Codex 登录状态。技能和脚本可以复用，插件安装清单需按宿主适配；尚未验证所有第三方智能体。

## 使用

在 Codex 中调用 `$hd-redraw-stitch`，提供原图及目标尺寸。详细工作流见 [技能说明](skills/hd-redraw-stitch/SKILL.md)。

自动选择本插件的图像处理请求需要包含以下任一关键词：`4k高清重绘`、`4k`、`分块`、`分区高清重绘`、`插件高清重绘`。`4K` 大小写不敏感，单独“高清重绘”不触发。关键词应是实际处理指令；讨论、引用关键词或其他无关任务不触发生图。

显式选择插件或使用 `$hd-redraw-stitch` 也可调用。`codex-gpt-image` 仅作为已选中重绘流程的后端，普通生成与编辑使用 Codex 内置 `imagegen`。这些是宿主智能体的技能选择规则，不是客户端的硬编码关键词开关。

命令行先检查重绘路线：

```bash
python3 skills/hd-redraw-stitch/scripts/plan_redraw_route.py --help
python3 skills/hd-redraw-stitch/scripts/gpt_coherent_redraw.py --help
python3 skills/hd-redraw-stitch/scripts/gpt_segmented_redraw.py --help
```

产品、人物、建筑等有连续结构的画面应使用带全局锚点或语义蒙版的路线。包含文字、Logo、二维码的图片需要按技能说明保护或确定性合成这些元素。

## 分辨率说明

交付图是 GPT 重绘并拼接的目标尺寸画布，不应称为单张模型原生 4K/8K。请保留每次原生生成尺寸、分块及拼接记录。脚本运行会发起图像生成请求，请先查看 dry-run 和风险预检结果。

## v0.5.1：分块规划与进度

- 按整张画布的网格数量选择分块尺寸，避免尾行高度重叠。本次 3840×1984 建筑图的默认方案由 12 块降为 8 块；实际画质仍须通过首块和成品检查。
- 高风险建筑图缺少可用语义蒙版时，路线预检推荐全局构图锚点，保留高风险标记。已有语义蒙版可传 `--semantic-masks`。
- 终端显示分块完成进度，任务目录的 `progress.json` 提供可读取的进度数据。单次模型请求没有后端百分比，运行期间只显示“生成中”和已完成块数。
- `layout-plan.json` 记录网格、重叠和重复像素比例；`run.json` 记录请求、失败与续跑统计；`review-checklist.json` 提示需要人工核对的内容细节。

本仓库从本地安装插件导出，移除了 Python 缓存，保留原始技能与脚本。未附加开源许可证。

## v0.5.0：名称与触发范围

- 插件与主技能由 `restore-image-hd` 更名为 `hd-redraw-stitch`，显示为“高清重绘与拼接 / HD Redraw & Stitch”。
- 自动触发范围收窄到上述关键词，单独“高清重绘”交给宿主默认能力。
- 配套后端仅服务本插件流程；新增可安装的 marketplace 清单。

## v0.4.2：稳定性与耗时优化

- 根据实际返回尺寸记录容量；默认交付裁块预算为 120 万像素，包含上下文，不假定请求尺寸一定实现。
- 生成输入固定，默认最多 2 路并发；遇到限流降为串行，合成始终按区域优先级执行。
- 原生文件保留不变；逐块记录输入哈希、状态与耗时，续跑只复用匹配结果。
- 明确请求不透明细节图，拒绝异常透明结果，原始透明通道单独恢复。
- 在颜色匹配前进行受限几何检查；大幅错位阻止导出，并支持仅重绘失败区域。
- 支持矩形分块的低差异接缝选择，包括 L 形交叠的角部连接；过宽交叠仅在靠近新增内容的 256 像素内选线。其他复杂遮罩回退会明确记录，仍需视觉检查。
- 对大面积白底图增加轮廓越界检查：小面积亮边或彩色突起即使未触发整体边缘偏移阈值，也会阻止导出并定位失败分块。非白底仍需人工复核轮廓。

分区流程可用 `--stage prepare|generate|compose|all` 分开执行。试绘后检查 `pilot-composite.png`，再使用 `--resume --approve-pilot`。失败区域使用 `--resume --retry-region NAME`。可选的 `--alignment affine` 需要另行安装 `opencv-python-headless`；默认平移校正不需要 OpenCV。

离线验证（不读取登录凭据、不发送生图请求）：

```bash
python3 -m unittest discover -s tests -v
```

23 项离线测试通过；真实服务端并发加速尚未实测，不据模拟测试承诺任务耗时。
