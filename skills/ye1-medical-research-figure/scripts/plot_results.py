#!/usr/bin/env python3
"""Make editable, styled figures from already-computed aggregate results.

The helper does not fit models or calculate statistical estimates. A doctor
does not need to write its input: the Agent maps the available results and
request to an aggregate plot-data JSON when the source is not a known report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

SCHEMA = "ye1-figure-data/1.0"
SETTINGS_SCHEMA = "ye1-figure-settings/1.0"


class PlotError(ValueError):
    pass


def number(value, label):
    if isinstance(value, bool):
        raise PlotError(f"{label} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PlotError(f"{label} must be a number") from exc
    if not math.isfinite(value):
        raise PlotError(f"{label} must be finite")
    return value


def interval(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PlotError(f"{label} needs lower and upper values")
    lo, hi = (number(v, label) for v in value)
    if lo > hi:
        raise PlotError(f"{label} lower value exceeds upper value")
    return [lo, hi]


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlotError(f"Cannot read aggregate JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise PlotError("Aggregate result must be a JSON object")
    return value


def make_forest(terms, estimate_key, interval_key, xlabel, ratio):
    rows = []
    for term in terms:
        name = str(term.get("term", ""))
        if name.lower() in {"intercept", "const", "(intercept)"}:
            continue
        if not name or term.get(estimate_key) is None or term.get(interval_key) is None:
            raise PlotError("A forest-plot term needs a label, estimate and interval")
        lo, hi = interval(term[interval_key], name)
        point = number(term[estimate_key], name)
        rows.append({"label": name, "estimate": point, "lower": lo, "upper": hi})
    if not rows:
        raise PlotError("No plottable terms in the supplied results")
    return {"schema": SCHEMA, "kind": "forest", "rows": rows,
            "x_label": xlabel, "reference": 1.0 if ratio else 0.0,
            "x_scale": "log" if ratio else "linear"}


def clinical_report(report, chart, variable):
    status = str(report.get("status", ""))
    if status.startswith(("BLOCKED", "FAILED")):
        raise PlotError("The supplied analysis did not produce usable results")
    method = str(report.get("method", ""))
    if report.get("family") == "association" and report.get("coefficients"):
        if chart not in {"auto", "forest"}:
            raise PlotError("This association report has forest-plot results")
        if report.get("model") == "logistic":
            data = make_forest(report["coefficients"], "odds_ratio", "or_ci95", "Odds ratio (95% CI)", True)
        else:
            data = make_forest(report["coefficients"], "estimate", "ci95", "Coefficient (95% CI)", False)
    elif method in {"cox_ph", "cause_specific_cox"} and chart in {"auto", "forest"}:
        cox = report.get("cox") or {}
        data = make_forest(cox.get("terms", []), "hazard_ratio", "hr_ci95", "Hazard ratio (95% CI)", True)
    elif method == "diagnostic_roc" or (report.get("schema_version") == "clinical-prediction/1.0.0" and (report.get("metrics") or {}).get("aggregate_roc_pr")):
        if chart not in {"auto", "roc"}:
            raise PlotError("This report has ROC coordinates")
        points = report.get("roc") or report["metrics"]["aggregate_roc_pr"]
        xy = [(number(p.get("fpr"), "false positive rate"), number(p.get("tpr"), "true positive rate")) for p in points]
        if len(xy) < 2:
            raise PlotError("ROC requires at least two reported coordinates")
        if xy[0][0] > xy[-1][0]:
            xy.reverse()
        data = {"schema": SCHEMA, "kind": "curve", "x_label": "False positive rate",
                "y_label": "True positive rate", "diagonal": True, "x_limits": [0, 1], "y_limits": [0, 1],
                "series": [{"label": "ROC", "x": [p[0] for p in xy], "y": [p[1] for p in xy], "mode": "line"}]}
    elif method in {"kaplan_meier", "kaplan_meier_by_group", "aalen_johansen", "aalen_johansen_by_group", "cox_ph", "cause_specific_cox"}:
        if chart not in {"auto", "survival"}:
            raise PlotError("This report has survival time-point results")
        is_cif = method in {"aalen_johansen", "aalen_johansen_by_group"}
        groups = report.get("groups") or [{"group": "", "result": report}]
        series, units = [], set()
        for group in groups:
            result = group.get("result") or {}
            points = result.get("curves") or []
            if not points:
                continue
            if result.get("time_unit"):
                units.add(str(result["time_unit"]))
            x = [number(p.get("time"), "time") for p in points]
            if is_cif:
                y = [number((p.get("cumulative_incidence") or [None])[0], "target-event cumulative incidence") for p in points]
                # The clinical report stores bootstrap bounds as [bound][time][cause].
                reported = (result.get("cif_uncertainty") or {}).get("intervals")
                if reported is not None:
                    try:
                        cis = [[reported[0][i][0], reported[1][i][0]] for i in range(len(points))]
                    except (IndexError, TypeError) as exc:
                        raise PlotError("Cumulative incidence intervals do not match reported time points") from exc
                else:
                    cis = [None] * len(points)
            else:
                y = [number(p.get("event_free_probability"), "event-free probability") for p in points]
                cis = [p.get("event_free_ci95") for p in points]
            item = {"label": str(group.get("group", "")), "x": x, "y": y, "mode": "points"}
            if all(ci is not None for ci in cis):
                spans = [interval(ci, "pointwise interval") for ci in cis]
                item["y_lower"] = [z[0] for z in spans]
                item["y_upper"] = [z[1] for z in spans]
            series.append(item)
        if not series:
            raise PlotError("No reported survival time points")
        unit = f" ({next(iter(units))})" if len(units) == 1 else ""
        data = {"schema": SCHEMA, "kind": "curve", "x_label": "Time" + unit,
                "y_label": "Target-event cumulative incidence" if is_cif else "Event-free probability",
                "y_limits": [-0.03, 1.03], "series": series,
                "note": "Reported evaluation points, not a reconstructed event-time step curve."}
    elif method == "baseline_summary":
        if chart not in {"auto", "summary"}:
            raise PlotError("This report has baseline summary results")
        choices = [v for v in report.get("variables", []) if v.get("type") == "numeric"]
        if variable:
            choices = [v for v in choices if str(v.get("variable")) == variable]
        if len(choices) != 1:
            raise PlotError("Choose the intended numeric variable from the available results")
        item = choices[0]
        groups = item.get("groups") or [{"group": "Overall", "summary": item.get("overall") or {}}]
        rows = []
        for group in groups:
            summary = group.get("summary") or {}
            if summary.get("mean") is None or summary.get("sd") is None:
                raise PlotError("Mean and SD must both be present for this summary plot")
            rows.append({"label": str(group.get("group", "")), "value": number(summary["mean"], "mean"),
                         "error": number(summary["sd"], "SD")})
        data = {"schema": SCHEMA, "kind": "summary", "rows": rows, "x_label": "Group",
                "y_label": str(item.get("variable", "Value")), "note": "Mean ± SD"}
    else:
        raise PlotError("Unsupported report format; map available aggregate results to the documented plot-data format")
    data["source_status"] = status or "unknown"
    return data


def normalize(source, chart, variable):
    if source.get("schema") == SCHEMA:
        if source.get("kind") not in {"forest", "curve", "summary"}:
            raise PlotError("Plot-data kind must be forest, curve or summary")
        return source
    return clinical_report(source, chart, variable)


def styles_dir():
    here = Path(__file__).resolve().parent
    bundled = here / "styles"
    return bundled if bundled.is_dir() else here.parent / "assets" / "styles"


def text_values(data, settings):
    texts = []
    def visit(value):
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
    visit(data)
    visit(settings)
    return " ".join(texts)


def configure_font(data, settings, plt, font_manager):
    names = {font.name for font in font_manager.fontManager.ttflist}
    candidates = ["PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK JP",
                  "Source Han Sans SC", "WenQuanYi Zen Hei", "SimHei", "Heiti SC",
                  "Lantinghei SC", "STHeiti", "Arial Unicode MS", "PingFang HK", "Songti SC"]
    chars = {c for c in text_values(data, settings) if "\u3400" <= c <= "\u9fff"}
    cjk = None
    if chars:
        for name in candidates:
            if name not in names:
                continue
            try:
                font = font_manager.get_font(font_manager.findfont(name, fallback_to_default=False))
                if all(font.get_char_index(ord(c)) for c in chars):
                    cjk = name
                    break
            except (OSError, ValueError, RuntimeError):
                continue
        if not cjk:
            raise PlotError("Chinese text needs a locally available CJK font; prepare one in the task environment")
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ([cjk] if cjk else []) + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    size = number(settings["font_size"], "font size")
    plt.rcParams.update({"font.size": size, "axes.labelsize": size, "axes.titlesize": size + 1,
                         "xtick.labelsize": size, "ytick.labelsize": size, "legend.fontsize": size})


def draw_forest(ax, data, settings):
    rows = data.get("rows") or []
    if not rows:
        raise PlotError("Forest plot needs reported estimates")
    scale = data.get("x_scale", "linear")
    if scale not in {"linear", "log"}:
        raise PlotError("Forest plot scale must be linear or log")
    if scale == "log":
        from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
    color = settings.get("color") or "#285e80"
    if settings["style"] == "mono" and not settings.get("color"):
        color = "#222222"
    bounds = []
    for i, row in enumerate(rows):
        label = str(row.get("label", ""))
        estimate = number(row.get("estimate"), label or "estimate")
        lower, upper = interval([row.get("lower"), row.get("upper")], label or "interval")
        if scale == "log" and min(estimate, lower, upper) <= 0:
            raise PlotError("Ratio estimates and intervals must be positive on a log axis")
        bounds.extend((estimate, lower, upper))
        ax.hlines(i, lower, upper, color=color, linewidth=settings["line_width"])
        ax.scatter([estimate], [i], color=color, marker=settings["marker"], s=24, zorder=3)
        if settings["show_values"]:
            ax.text(1.025, i, f"{estimate:#.3g} ({lower:#.3g}–{upper:#.3g})", transform=ax.get_yaxis_transform(),
                    va="center", ha="left", fontsize=settings["font_size"] * 0.9)
    ax.set_yticks(range(len(rows)), [str(row.get("label", "")) for row in rows])
    ax.set_ylim(len(rows) - 0.5, -0.5)
    if data.get("reference") is not None:
        reference = number(data["reference"], "reference")
        if scale == "log" and reference <= 0:
            raise PlotError("Reference must be positive on a log axis")
        bounds.append(reference)
        ax.axvline(reference, color="#888888", linestyle="--", linewidth=0.8, zorder=0)
    # Determine the range from all reported intervals: Matplotlib's mixed
    # collection/scatter autoscaling can otherwise crop a confidence interval.
    if scale == "log":
        lo, hi = min(math.log10(v) for v in bounds), max(math.log10(v) for v in bounds)
        padding = max((hi - lo) * 0.08, 0.04)
        ax.set_xlim(10 ** (lo - padding), 10 ** (hi + padding))
    else:
        lo, hi = min(bounds), max(bounds)
        padding = max((hi - lo) * 0.08, 0.05)
        ax.set_xlim(lo - padding, hi + padding)
    ax.set_xlabel(settings.get("xlabel") or data.get("x_label", "Estimate"))


def draw_curve(ax, data, settings):
    series = data.get("series") or []
    if not series:
        raise PlotError("Curve plot needs reported coordinates")
    markers = ["o", "s", "^", "D", "v", "P"]
    lines = ["-", "--", "-.", ":", "-", "--"]
    for i, item in enumerate(series):
        x = [number(v, "x coordinate") for v in item.get("x", [])]
        y = [number(v, "y coordinate") for v in item.get("y", [])]
        if not x or len(x) != len(y):
            raise PlotError("Each series needs equally sized x and y coordinates")
        mode = item.get("mode", "line")
        if mode not in {"line", "points", "step-post"}:
            raise PlotError("Curve mode must be line, points or step-post")
        label = str(item.get("label", "")) or None
        kw = {"label": label, "linewidth": settings["line_width"]}
        if settings.get("color"):
            kw["color"] = settings["color"]
        if settings["style"] == "mono":
            kw["linestyle"] = lines[i % len(lines)]
            kw["marker"] = markers[i % len(markers)]
            kw["markevery"] = max(1, len(x) // 8)
        if mode == "points":
            kw["linestyle"] = "none"
            kw["marker"] = markers[i % len(markers)]
        if mode == "step-post":
            kw["drawstyle"] = "steps-post"
        line = ax.plot(x, y, **kw)[0]
        if item.get("y_lower") is not None and item.get("y_upper") is not None:
            lows = [number(v, "lower interval") for v in item["y_lower"]]
            highs = [number(v, "upper interval") for v in item["y_upper"]]
            if len(lows) != len(y) or len(highs) != len(y) or any(lo > hi for lo, hi in zip(lows, highs)):
                raise PlotError("Curve interval coordinates do not match the reported points")
            if mode == "points":
                # Draw the reported intervals without inventing values between time points.
                for xv, lo, hi in zip(x, lows, highs):
                    ax.vlines(xv, lo, hi, color=line.get_color(), alpha=0.6, linewidth=0.7)
            else:
                ax.fill_between(x, lows, highs, color=line.get_color(), alpha=0.13,
                                step="post" if mode == "step-post" else None)
    if data.get("diagonal"):
        ax.plot([0, 1], [0, 1], color="#999999", linestyle="--", linewidth=0.8, zorder=0)
    for key, setter in (("x_limits", ax.set_xlim), ("y_limits", ax.set_ylim)):
        if data.get(key) is not None:
            setter(*interval(data[key], key))
    ax.set_xlabel(settings.get("xlabel") or data.get("x_label", "X"))
    ax.set_ylabel(settings.get("ylabel") or data.get("y_label", "Y"))
    if settings["legend"] != "none" and any(item.get("label") for item in series):
        if settings["legend"] == "right":
            ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))
        else:
            ax.legend(loc="best")


def draw_summary(ax, data, settings):
    rows = data.get("rows") or []
    if not rows:
        raise PlotError("Summary plot needs reported group values")
    vals = [number(row.get("value"), "group value") for row in rows]
    color = settings.get("color") or ("#222222" if settings["style"] == "mono" else "#285e80")
    for i, (row, value) in enumerate(zip(rows, vals)):
        if row.get("error") is not None:
            error = number(row["error"], "reported uncertainty")
            if error < 0:
                raise PlotError("Reported uncertainty cannot be negative")
            ax.errorbar([i], [value], yerr=[error], fmt=settings["marker"], color=color,
                        linewidth=settings["line_width"], capsize=3, markersize=5)
        else:
            ax.plot([i], [value], marker=settings["marker"], color=color, markersize=5)
    ax.set_xticks(range(len(rows)), [str(row.get("label", "")) for row in rows])
    ax.set_xlim(-0.5, len(rows) - 0.5)
    ax.set_xlabel(settings.get("xlabel") or data.get("x_label", "Group"))
    ax.set_ylabel(settings.get("ylabel") or data.get("y_label", "Value"))
    if data.get("note"):
        ax.text(0.01, 0.99, str(data["note"]), transform=ax.transAxes, ha="left", va="top", fontsize=settings["font_size"] * 0.9)


def render(data, settings, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except ImportError as exc:
        raise PlotError("Plotting needs matplotlib; see requirements-plotting.txt") from exc
    style_path = styles_dir()
    style = settings["style"]
    layers = [style_path / "science.mplstyle", style_path / "no-latex.mplstyle", style_path / f"ye1-{style}.mplstyle"]
    if any(not layer.is_file() for layer in layers):
        raise PlotError("The bundled figure styles are missing")
    with plt.style.context([str(p) for p in layers]):
        configure_font(data, settings, plt, font_manager)
        plt.rcParams["savefig.bbox"] = None
        width = number(settings["width_mm"], "width") / 25.4
        height = number(settings["height_mm"], "height") / 25.4
        fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
        if data["kind"] == "forest":
            draw_forest(ax, data, settings)
        elif data["kind"] == "curve":
            draw_curve(ax, data, settings)
        elif data["kind"] == "summary":
            draw_summary(ax, data, settings)
        else:
            raise PlotError("Unsupported plot-data kind")
        title = settings.get("title") or data.get("title")
        if title:
            ax.set_title(str(title), loc="left", pad=8)
        if settings.get("ylabel") and data["kind"] == "forest":
            ax.set_ylabel(settings["ylabel"])
        try:
            for fmt in settings["formats"]:
                fig.savefig(out / f"figure.{fmt}", dpi=int(settings["dpi"]), facecolor="white")
        finally:
            plt.close(fig)


def settings_from(args, base=None):
    settings = dict(base or {"schema": SETTINGS_SCHEMA, "style": "clinical", "width_mm": 130,
                             "height_mm": 90, "dpi": 300, "font_size": 9, "line_width": 1.5,
                             "marker": "o", "legend": "best", "show_values": True,
                             "formats": ["pdf", "png", "svg"]})
    for key in ("style", "width_mm", "height_mm", "dpi", "font_size", "line_width", "marker", "legend", "title", "xlabel", "ylabel", "color"):
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
    if args.formats:
        settings["formats"] = list(dict.fromkeys(args.formats.split(",")))
    if not settings.get("formats") or any(fmt not in {"pdf", "png", "svg", "tiff"} for fmt in settings["formats"]):
        raise PlotError("Formats must be a comma-separated selection of pdf,png,svg,tiff")
    for key in ("width_mm", "height_mm", "dpi", "font_size", "line_width"):
        settings[key] = number(settings[key], key)
        if settings[key] <= 0:
            raise PlotError(f"{key} must be positive")
    if settings["style"] not in {"clinical", "mono"}:
        raise PlotError("Style must be clinical or mono")
    return settings


def write_bundle(data, settings, out, source):
    out = out.expanduser().resolve()
    if out.exists():
        raise PlotError("Choose a new output folder; existing files were not replaced")
    out.mkdir(parents=True)
    try:
        (out / "plot_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (out / "plot_settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        render(data, settings, out)
        shutil.copy2(Path(__file__).resolve(), out / "draw.py")
        shutil.copytree(styles_dir(), out / "styles")
        result = {"status": "CREATED_REVIEW_REQUIRED", "source_file": source.name,
                  "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "source_status": data.get("source_status", "not supplied"),
                  "figure_files": [f"figure.{fmt}" for fmt in settings["formats"]],
                  "reusable_files": ["draw.py", "plot_data.json", "plot_settings.json", "styles/"],
                  "note": "Uses supplied aggregate results; does not perform statistical analysis or establish scientific validity."}
        (out / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"status": result["status"], "output_dir": str(out), "files": result["figure_files"] + result["reusable_files"]}
    except Exception:
        (out / "FAILED.json").write_text('{"status":"FAILED"}\n', encoding="utf-8")
        raise


def add_style_args(parser):
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    parser.add_argument("--style", choices=["clinical", "mono"])
    parser.add_argument("--width-mm", type=float)
    parser.add_argument("--height-mm", type=float)
    parser.add_argument("--dpi", type=float)
    parser.add_argument("--font-size", type=float)
    parser.add_argument("--line-width", type=float)
    parser.add_argument("--marker", choices=["o", "s", "D", "^", "v", "P", "X"])
    parser.add_argument("--legend", choices=["best", "right", "none"])
    parser.add_argument("--title")
    parser.add_argument("--xlabel")
    parser.add_argument("--ylabel")
    parser.add_argument("--color", help="Matplotlib color, e.g. #285e80")
    parser.add_argument("--formats", help="comma-separated pdf,png,svg,tiff")


def prepare_matplotlib_cache():
    # Reuse a user-specific temporary font cache instead of rebuilding it for
    # every figure; do not change the system or another project's matplotlibrc.
    if not os.environ.get("MPLCONFIGDIR"):
        uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
        cache = Path(tempfile.gettempdir()) / f"ye1-medical-figure-mpl-{uid}"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(cache)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="draw from a clinical aggregate report or documented aggregate plot-data JSON")
    create.add_argument("--input", type=Path, required=True, help="report.json, report folder, or aggregate plot-data JSON")
    create.add_argument("--chart", choices=["auto", "forest", "roc", "survival", "summary"], default="auto")
    create.add_argument("--variable", help="numeric variable name in a baseline report")
    add_style_args(create)
    redraw = sub.add_parser("redraw", help="redraw a saved figure bundle into a new folder")
    redraw.add_argument("--data", type=Path, default=Path(__file__).resolve().parent / "plot_data.json")
    redraw.add_argument("--settings", type=Path, default=Path(__file__).resolve().parent / "plot_settings.json")
    add_style_args(redraw)
    args = parser.parse_args()
    try:
        if args.command == "create":
            source = args.input.expanduser().resolve()
            if source.is_dir():
                source = source / "report.json"
            data = normalize(read_json(source), args.chart, args.variable)
            settings = settings_from(args)
            if data["kind"] == "forest" and args.height_mm is None:
                settings["height_mm"] = max(45, min(180, 30 + 9 * len(data.get("rows") or [])))
        else:
            source = args.data.expanduser().resolve()
            data = normalize(read_json(source), "auto", None)
            settings = settings_from(args, read_json(args.settings.expanduser().resolve()))
        prepare_matplotlib_cache()
        result = write_bundle(data, settings, args.out, source)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (PlotError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
