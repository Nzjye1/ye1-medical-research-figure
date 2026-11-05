# 根据已有结果绘图

本说明供 Agent 使用。医生只需提供现有分析结果、汇总表或可用图源，说出想解决的问题；不要求其填写 JSON、写代码或安装另一 Skill。先查看授权且已去标识化的汇总结果，必要时结合论文上下文判断图型；临床分析已给出 `report.json` 时可直接接收，也能独立处理其他来源的结果。原始逐行患者数据保持本机边界，不写入交付脚本或一般工具输出。缺少与科学含义有关的关键材料时，先完成有依据的部分，再简短说明具体还需要什么。

交付时先给图和脚本，简短说明“需要调整可以直接告诉我”，只有影响使用且无法自行解决的问题才补充说明。医生未要求解读时，不逐项描述结果、区间与显著性，不展开内部绘制过程；没有核实目标期刊的具体要求，不写“可直接投稿”等合规结论。医生没有要求技术操作时，不指导其修改脚本、CSV 或运行命令，也不列环境信息；这些调整由 Agent 完成。文件链接与可修改范围说清即可，不把验证过程变成医生的任务。

## 默认出图与再修改

先检查工作区根目录隐藏的 `.venv`：若其中 Python 兼容且能运行 pip，但缺少 Matplotlib，就用该 `.venv` 的 Python 执行 `-m pip install -r <本 Skill>/requirements-plotting.txt`，不另建环境；依赖冲突或现有环境不兼容时再隔离处理。其他环境情况按[环境说明](execution.md)由 Agent 准备，医生无需处理环境。脚本内置 SciencePlots `science` 加 `no-latex` 及 Ye1 彩色科研风格，默认不要求 LaTeX；`--style mono` 使用黑白风格。样式来源及许可随包附带。默认图不是任何期刊的格式认证；期刊要求或医生偏好已知时优先按其要求调整。

对已支持的临床分析汇总，Agent 可直接调用以下命令，按输入内容自动选择森林图、ROC、生存评估点或分组均值图。路径由 Agent 根据实际位置替换，输出目录须是新目录：

```bash
python scripts/plot_results.py create --input /local/analysis/report.json --out /local/figure-v1
```

如分组基线结果含多个数值变量，Agent 根据医生目标或上下文用 `--variable` 选出变量；实在不清楚才问。结果内是预设时间点的生存概率时，默认画报告中的点和已提供的逐点区间，不假装这些稀疏点是完整事件时间阶梯曲线。需要画阶梯曲线时应使用有依据的完整时间点及对应结果；不估读缺失值或补造风险表、显著性和区间。

默认输出 PDF、PNG、SVG，及本次使用的 `draw.py`、`plot_data.json`、`plot_settings.json` 和包含许可的 `styles/`，可用于后续复现和修改；按需指定 TIFF。结果文件与既有图、稿件都保留原件，修改到新目录。医生命令如“改黑白、用菱形点、改标题”由 Agent 翻译并执行，例如：

```bash
python /local/figure-v1/draw.py redraw --out /local/figure-v2 --style mono --marker D --title 'Adjusted association'
```

可选项包括 `--width-mm`、`--height-mm`、`--dpi`、`--font-size`、`--line-width`、`--color`、`--xlabel`、`--ylabel`、`--legend best|right|none`、`--formats pdf,png,svg,tiff`。并非所有选项都适用于所有图型。Agent 应查看成图并解决裁切、文字过密或缺字；确需中文字体而本机没有时，在授权及可用条件下自行准备可信字体。最终只把必要文件和影响使用的问题告诉医生。

## 其他结果和自定义图型

没有上述报告格式时，Agent 可从授权的结果表、Excel、CSV 或其他分析软件的汇总输出中提取 **已报告的** 数值，生成本地汇总 JSON 后调用同一工具；患者逐行数据不放进这个 JSON。以下是 Agent 内部格式示意，医生无需填写：

```json
{"schema":"ye1-figure-data/1.0","kind":"forest","x_label":"Odds ratio (95% CI)","reference":1,"x_scale":"log","rows":[{"label":"Marker","estimate":1.42,"lower":1.10,"upper":1.84}]}
```

```json
{"schema":"ye1-figure-data/1.0","kind":"curve","x_label":"Time","y_label":"Value","series":[{"label":"Group A","x":[0,1,2],"y":[1,0.8,0.6],"mode":"line"}]}
```

```json
{"schema":"ye1-figure-data/1.0","kind":"summary","x_label":"Group","y_label":"Value","note":"Mean ± SD","rows":[{"label":"A","value":42,"error":2.5}]}
```

汇总示例中的 `error` 与 `note` 仅在来源确实提供相应含义时填写；没有误差值时省略，不臆造。曲线支持 `line`、`points`、`step-post`；`step-post` 仅用于来源支持完整阶梯含义的结果。更特殊的图型、排版或图层，以及超出预设选项的医生要求，由 Agent 根据已有结果自行编写／修改并运行 Python 脚本，必要时复用 `assets/styles/`：依次加载 `science.mplstyle`、`no-latex.mplstyle` 与 `ye1-clinical.mplstyle` 或 `ye1-mono.mplstyle`。在交付目录内保存脚本、实际使用的汇总输入副本、设置、样式和许可；脚本以相对路径读取这些文件，使交付目录移动后仍可在兼容环境中运行，不依赖原 Skill 或工作区的绝对位置。仅复制本次已授权的汇总结果，不复制患者逐行资料。修改时保存新版本，不覆盖原图。不要因为不属于预设类型就让医生写代码或重新整理材料。

结果绘图只改变表达方式，不在这里重新拟合模型或创造统计结论。成图与来源的数值、单位、组别、区间含义及正文应相符；遇到无法从材料确定的科学含义，先自行寻找依据，仍不确定时只询问关键问题。新单图可按[执行说明](execution.md)继续组图；在现有写作材料中替换图时，保留来源、版本与必要的人工核对，不能声称已自动接收或通过论文审核。
