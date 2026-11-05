# 执行说明：环境、图片盘点、组图与 R 单图调整

本说明面向 Agent；医生使用自然语言和现有材料即可。所有命令在本地运行，只处理已授权、已去标识化的工作文件。源文件与既有分析／文章结果保持只读，输出到新的不存在目录。

## 环境与能力

- 文章线优先使用 CPython 3.13 的可用维护版本；其他已验证兼容的 Python 环境可继续复用。基础依赖为 Pillow、pypdf、reportlab，版本范围见 `requirements-python.txt`。Agent 优先复用宿主提供的 Python 和合适的隔离环境；如缺失，由 Agent 按宿主能力、权限及官方来源准备，医生不必选择版本、搜索安装包或输入命令。生成 PNG/TIFF 时使用已有 `pdftoppm`，没有时可用 `requirements-render.txt` 中的 pypdfium2。不可用时先处理不依赖它的部分，不把安装失败冒称完成。
- SVG 输入按需使用 `requirements-svg.txt` 中的 svglib，本地转 PDF 不需要单独安装 Cairo；普通 PDF／位图任务不依赖它。若 SVG 使用遮罩，当前转换优先尝试宿主已有且可用的 CairoSVG；无合适转换工具时由 Agent 按实际环境处理或先完成其他图。转换器支持范围及字体会影响呈现，应查看转换前后的效果；不把转换后可选择的文字、矢量路径或嵌入位图一概称为全部可编辑。SVG 内嵌位图的实际有效 PPI 暂不自动计算。
- 由分析汇总结果绘图时，按需使用 `requirements-plotting.txt` 中的 Matplotlib，具体接收、出图及修改方法见[结果绘图说明](plotting.md)。只排版已有图片时不需要安装绘图库。
- 医生需要自行移动面板或改动新增字母标签时，按需使用 `requirements-pptx.txt` 生成 PowerPoint。每个面板作为独立图片、每个新增标签作为可编辑文字；PDF／SVG 面板放入 PowerPoint 时会转为图片，图内文字、曲线或细胞不因此成为可编辑对象。另保留 PDF 母版及原始图源。
- R 路径需 R、ggplot2 和 jsonlite；只在需要可信 ggplot RDS 样式调整时使用，图像组装无需 R。
- 可选的最终尺寸 OCR 提示需要本地 `tesseract`；缺少时仍可按预览人工检查，不得声称 OCR 已完成。
- Python 组图输入：单页或明确页码的 PDF、自包含 SVG，以及单帧、8 位 RGB／RGBA／灰度／调色板 PNG、JPEG 或 TIFF。允许 PDF 的空批注列表；实际带批注或批注结构异常的 PDF、多帧／高位深图、旋转 PDF 和复杂专有格式不在当前范围；不要悄悄转成不等价的材料。含外部图片、样式或字体引用的 SVG 应由 Agent 先在授权范围内形成自包含工作副本；无法安全取得所需文件时说明并继续能做的部分，不自动联网拉取图源。
- 当前交付 PDF、PNG、TIFF，可按需附带面板位置可编辑的 PPTX；布局和图注保留为单独可编辑文件。PDF 面板按矢量页内容合入，SVG 先在本机转换为 PDF 再合入；光栅面板仍是位图。SVG 转换的矢量与文字保留取决于原图和转换支持，原图内文字／细胞／线条不因此自动成为独立可编辑对象。SVG 成品输出和真实客户端格式兼容尚未验证。

准备环境前，先检查当前工作区根目录已有的 `.venv`（包括隐藏目录或指向已知环境的链接）或本次已知的可复用环境：用其 Python 按当前输入和输出检查所需模块能否导入；满足时直接使用，不再新建虚拟环境或重装依赖。若现有隔离环境的 Python 兼容，只缺本次任务的包，就直接使用该环境的 Python 补齐缺失包，而不是另建环境；仅在版本或依赖冲突时另行隔离。只检查已知位置，不扫描整台电脑。环境未准备好时，Agent 利用 WorkBuddy 或其他宿主实际提供的运行环境；没有合适 Python 时按权限使用官方来源准备。**完全没有 Python 时，不以现成 Python 或 pip 解析下载信息为前提**；可使用宿主现有的环境管理工具或 shell／PowerShell 等可用工具查找并准备适配本机的可信 Python 3.13。然后在客户允许且可写的位置创建或复用隔离虚拟环境，按本次任务补齐缺失依赖：排版工具使用 `requirements-python.txt`，需要结果绘图时使用 `requirements-plotting.txt`；仅遇到 SVG 输入时再安装 `requirements-svg.txt`，需要 PNG／TIFF 且没有可用 `pdftoppm` 时再安装 `requirements-render.txt`，需要 PPTX 时安装 `requirements-pptx.txt`。用该环境的 Python 执行 `-m pip install -r 清单路径`；清单路径指向本 Skill 文件夹。Windows 使用虚拟环境的 `Scripts/python.exe`，macOS／Linux 使用 `bin/python`。环境放在 Skill 文件夹和图件交付目录之外，例如授权工作区根目录的 `.venv`，避免把整个环境塞进交付物；不改变系统全局环境。仅在确需其他转换器、缺少联网或无法自行准备时，说明实际缺口并先完成其他能做的事。真正需要系统授权时，简短请医生配合必要操作；不要求医生排查 Python。Skill 包不包含 Python 解释器。

## 1. 查看单个输入

```bash
python scripts/figure_tool.py inspect /path/to/deidentified-panel.png
python scripts/figure_tool.py inspect /path/to/panel.pdf --page 1
python scripts/figure_tool.py inspect /path/to/self-contained-panel.svg
```

输出包括 SHA-256、文件类型、尺寸、图片模式或 PDF／SVG 转换后的页面大小。`page` 从 1 开始；多页 PDF 必须明确指定，防止悄悄只用第一页。SVG 的独立资源若可在本机安全取得，由 Agent 自行生成自包含工作副本；保持原件。EXIF 非默认方向、动画／多帧与高位深输入会拒绝自动处理；先找到合适工作副本或明确说明能力缺口。此检查不完成去标识化，也不判断图片科学内容。

## 2. 组图

由 Agent 根据已确认材料在本地新建任务 JSON；不要要求医生写 JSON。适合直接处理的原始 SVG 直接写入 `panels[].path`；临时预览 PDF 只用于查看，不替代原图进入组图记录，以便回执和布局保留实际来源。以下仅说明结构：

```json
{
  "canvas": {"width_mm": 150, "height_mm": 90, "dpi": 300},
  "grid": {"rows": 1, "cols": 2, "margin_mm": 3, "gap_mm": 3, "label_band_mm": 7},
  "outputs": ["pdf", "png", "tiff"],
  "panels": [
    {"path": "/local/work/panel-a.pdf", "page": 1, "label": "A", "role": "author-provided role"},
    {"path": "/local/work/panel-b.png", "label": "B", "role": "author-provided role"}
  ]
}
```

```bash
python scripts/figure_tool.py compose --job /local/work/layout.json --out /local/work/figure-v1
```

`outputs` 支持 `pdf`、`png`、`tiff`、`pptx`。PDF 是中间母版，输出列表必须包含 PDF；只需 PDF 时不依赖 PDF 渲染工具。通过 `panels[].box_mm` 可对所有面板指定 `[x, y, width, height]`，原点为画布左上方；也可只指定 `grid` 使用规则网格。`label` 始终是唯一面板标识；`label_mode` 可取 `add`（默认，在内容上方新加标签）、`source`（源图已有同一标签，仅保留）或 `none`（不加外层标签，适合源图内部已有多个标签）。`source` 必须由 Agent 视觉确认，脚本不识别原图标签。不同面板可混用模式。带外层标签的 10 pt 标签区至少约 4.24 mm；不加标签的面板不占标签区。不能指定部分自定义位置、重叠或超出画布的框。

程序只按比例放置图件，不自动改亮度、色彩、背景、裁剪或拉伸。输出光栅图由 PDF 渲染，缩放会造成重采样；自动记录原文件哈希、布局和位图面板的有效 PPI，不把输出 DPI 当成原图质量。低于 `minimum_effective_ppi` 的面板在回执中警告；默认 300 仅是可配置的筛查阈值，不能当作所有期刊的要求。`layout.json` 可在未变更源图时重用：

```bash
python scripts/figure_tool.py compose --job /local/work/figure-v1/layout.json --out /local/work/figure-v2
```

`layout.json` 包含本机输入路径，仅用于私有本地回溯；跨模块或外发前检查路径及其他隐私内容。若源哈希发生变化，程序拒绝把它当同一版本。输出包含 `figure.pdf`、按请求生成的预览或 `figure.pptx`、布局和 `receipt.json`。中途失败时保留 `FAILED.json`，此目录中的局部文件不能当作完成品。有效 PPI 数值只是提示，不代表最终期刊或图片内容通过；最终回复仅在确有必要时从最终回执核对数值，不猜测、不混用其他版本的数据。PDF 内含位图面板时，不称整个文件为矢量 PDF。

## 3. R 图对象的有限样式调整

```bash
Rscript --vanilla scripts/restyle_ggplot.R --input /local/work/plot.rds --out /local/work/plot-v1 --width-mm 140 --height-mm 90 --font-size 10 --dpi 300
```

可选 `--text-scale 1.3` 按比例调整固定主题文字和固定 `geom_text`/`geom_label` 字号；`--right-margin-pt 96` 可在右边文字被截时扩大右边距。默认比例 1 保持旧行为。若字号映射到数据，比例调整会拒绝处理。除允许的文字尺寸外，脚本比较前后构建图层；文字放大仍可能挤出画布或覆盖其他元素，必须查看输出。只接收可信、可在本机实际加载的 ggplot RDS，限定普通 `StatIdentity` 图层且排除随机位置。R 对象可能包含原数据和可执行表达式，只应在可信、经授权的本地环境使用；构建比较及样式处理不等于独立统计审核。其他图源让 Agent 判断能否利用真实源代码和已确认结果处理，不能将其说成此工具已支持。

## 4. 按最终物理尺寸检查小字

有 Tesseract 时可对最终 PNG 预览运行：

```bash
python scripts/readability_audit.py --image /local/work/figure-v1/figure.png \
  --width-mm 150 --height-mm 90 --out /local/work/figure-v1-text-review
```

`audit.json` 记录 OCR 识别字词的像素框和实际放置尺寸下的墨迹高度；默认低于 4 pt 墨迹高度的词框会在单独的 `small_text_overlay.png` 标红。该数值不是字体标称字号，也不是期刊通用标准。脚本不在回执中记录识别出的文字；OCR 可能漏掉比例尺、单字及小字，无红框也必须人工检查。无指定尺寸和期刊要求的论文组图，先按约 170–180 mm 页面宽度做工作稿，避免靠超宽画布通过小字检查；超过页面宽度后续缩回论文仍会让字变小。多处关键文字由于缩排而偏小时，优先自行尝试在页面宽度内拆成两行、增大面板占幅，再按实际尺寸查看；用户明确指定布局或期刊尺寸时尊重其限制。只需一次针对性调整，不为消除提示反复重排；不能判断或仍有实际问题时简短告知。高分辨率有助于位图细节，但不会增大原图中文字在成品上的物理尺寸；若放大位图导致有效 PPI 不足，保留警告，不用插值假装恢复原始细节。

## 5. 与统计和写作材料一起使用

从用户授权的统计输出中寻找图件、对应聚合结果与来源记录；从稿件中寻找相关正文及已有图注。用户只提供其中一部分时先做能可靠完成的工作。对于 MAINLINE-RESEARCH-V2 的聚合结果和原文章包，如果新图仅由同一个已登记统计图等比例组版而来，可用：

```bash
python scripts/handoff_tool.py prepare --analysis-dir /local/02_analysis_result \
  --article-dir /local/03_final_article --figure-dir /local/figure-v1 \
  --display-id FIGURE.EXAMPLE.PROJECT --out /local/figure-handoff
python scripts/handoff_tool.py stage --analysis-dir /local/02_analysis_result --article-dir /local/03_final_article \
  --handoff-dir /local/figure-handoff --out /local/figure-stage
```

`prepare` 核对聚合状态、项目、分析 manifest 绑定、图件与结果身份、原文章文件清单、新图及其回执哈希，输出待审核的新图、原图注和变更清单；`stage` 检查原稿及交接未变化，在新目录创建替换图件的稿件副本。当前只支持一个登记统计图对应一个已有文章图件；跨图合并、重新绘图及不符合该契约的其他模块需人工交接。脚本不证明科学等价、不自动生成或审核新稿、不刷新旧 QC、审核或 manifest。副本中旧 DOCX 与审核信息立即过期；需要时在副本上调用现有写作构建和校验命令，按该项目规则重新核对图注、正文、蓝图、哈希和审核。原模块和原稿保持只读；这不是两模块的自动调用。
