#!/usr/bin/env python3
"""Inspect and arrange existing figures without modifying their sources.

Local deterministic helper for the medical-research-figure Skill. It does not
infer scientific meaning, journal compliance or the safety of a source image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageOps
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdf_canvas
except ImportError as exc:
    raise SystemExit(f"Missing Python dependency: {exc}. See requirements-python.txt.") from exc

MM_TO_PT = 72.0 / 25.4
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_INPUT_PIXELS = 100_000_000
MAX_RENDER_PIXELS = 120_000_000
RASTER = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".tif": "TIFF", ".tiff": "TIFF"}
ALLOWED_MODES = {"1", "L", "LA", "P", "RGB", "RGBA"}


class FigureError(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def number(value: Any, name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise FigureError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FigureError(f"{name} must be a number") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise FigureError(f"{name} must be between {minimum} and {maximum}")
    return result


def whole(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    n = number(value, name, minimum=minimum, maximum=maximum)
    if not n.is_integer():
        raise FigureError(f"{name} must be an integer")
    return int(n)


def pdf_page(path: Path, page_number: int | None) -> tuple[PdfReader, Any, int]:
    try:
        reader = PdfReader(io.BytesIO(path.read_bytes()), strict=False)
    except Exception as exc:
        raise FigureError(f"Unable to read PDF {path.name}: {exc}") from exc
    if reader.is_encrypted:
        raise FigureError(f"Encrypted PDF is unsupported: {path.name}")
    count = len(reader.pages)
    if count == 0:
        raise FigureError(f"PDF contains no pages: {path.name}")
    if page_number is None:
        if count != 1:
            raise FigureError(f"Specify the page of a multi-page PDF: {path.name}")
        page_number = 1
    if page_number < 1 or page_number > count:
        raise FigureError(f"PDF page {page_number} is outside 1..{count}: {path.name}")
    page = reader.pages[page_number - 1]
    if (int(page.get("/Rotate", 0) or 0) % 360) != 0:
        raise FigureError(f"Rotated PDF pages are not supported by this prototype: {path.name}")
    if float(page.get("/UserUnit", 1) or 1) != 1:
        raise FigureError(f"PDF /UserUnit other than 1 is unsupported: {path.name}")
    media = tuple(float(x) for x in page.mediabox)
    crop = tuple(float(x) for x in page.cropbox)
    if any(abs(a - b) > 0.001 for a, b in zip(media, crop)) or abs(media[0]) > 0.001 or abs(media[1]) > 0.001:
        raise FigureError(f"PDF with shifted or cropped page boxes requires another workflow: {path.name}")
    annotations = page.get("/Annots")
    if annotations is not None:
        # Matplotlib often writes an indirect reference to an empty annotation
        # array. The reference itself is truthy even when its array is empty.
        try:
            annotations = annotations.get_object()
        except Exception as exc:
            raise FigureError(f"Unable to inspect PDF annotations: {path.name}") from exc
        if not isinstance(annotations, ArrayObject):
            raise FigureError(f"Malformed PDF annotations: {path.name}")
        if len(annotations):
            raise FigureError(f"PDF annotations are not supported by this prototype: {path.name}")
    if page.get_contents() is None:
        raise FigureError(f"PDF page has no drawing content: {path.name}")
    return reader, page, page_number


def svg_to_pdf(path: Path) -> tuple[bytes, str]:
    """Convert a self-contained SVG locally, preserving vector content when possible."""
    try:
        from defusedxml import ElementTree
    except (ImportError, OSError) as exc:
        raise FigureError("SVG input needs the optional SVG environment; see requirements-svg.txt") from exc
    try:
        data = path.read_bytes()
        root = ElementTree.fromstring(data)
        if root.tag.rsplit("}", 1)[-1] != "svg":
            raise FigureError(f"Not an SVG document: {path.name}")
        uses_mask = False
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""
            if tag == "foreignObject":
                raise FigureError(f"SVG contains unsupported embedded HTML: {path.name}; use a compatible local export")
            uses_mask = uses_mask or tag == "mask"
            for key, value in element.attrib.items():
                if key.rsplit("}", 1)[-1] == "href" and tag != "a":
                    if value and not value.strip().startswith(("#", "data:")):
                        raise FigureError(f"SVG references a separate resource: {path.name}; make a self-contained local copy")
                for match in re.finditer(r"url\(\s*['\"]?([^\s)\"']+)", value, flags=re.I):
                    if not match.group(1).startswith(("#", "data:")):
                        raise FigureError(f"SVG references a separate resource: {path.name}; make a self-contained local copy")
            if tag == "style" and element.text:
                if re.search(r"@import\b", element.text, flags=re.I):
                    raise FigureError(f"SVG imports a separate stylesheet: {path.name}; make a self-contained local copy")
                for match in re.finditer(r"url\(\s*['\"]?([^\s)\"']+)", element.text, flags=re.I):
                    if not match.group(1).startswith(("#", "data:")):
                        raise FigureError(f"SVG references a separate resource: {path.name}; make a self-contained local copy")
        svg_error = None
        if not uses_mask:
            try:
                from svglib.svglib import svg2rlg
                from reportlab.graphics import renderPDF
                drawing = svg2rlg(io.BytesIO(data))
                if drawing is not None and drawing.width > 0 and drawing.height > 0:
                    return renderPDF.drawToString(drawing), "svglib"
                svg_error = "empty or invalid drawing"
            except Exception as exc:
                svg_error = str(exc)
        try:
            import cairosvg
            # Safe mode does not fetch external images; references were checked above.
            return cairosvg.svg2pdf(bytestring=data, unsafe=False), "CairoSVG"
        except (ImportError, OSError) as exc:
            if uses_mask:
                raise FigureError(f"SVG mask needs a compatible local converter: {path.name}") from exc
            raise FigureError(f"SVG conversion unavailable for {path.name}: {svg_error or 'install requirements-svg.txt'}") from exc
    except FigureError:
        raise
    except Exception as exc:
        raise FigureError(f"Unable to convert SVG {path.name}: {exc}") from exc


def inspect(path: Path, page_number: int | None = None, *, include_svg_pdf: bool = False) -> dict[str, Any]:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise FigureError(f"Input is not a regular file within the size limit: {path.name}")
    ext = path.suffix.lower()
    details: dict[str, Any] = {
        "path": str(path), "file_name": path.name, "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }
    if ext == ".pdf":
        if not path.read_bytes()[:5] == b"%PDF-":
            raise FigureError(f"File suffix is PDF but content is not a PDF: {path.name}")
        reader, page, chosen = pdf_page(path, page_number)
        details.update({"kind": "pdf", "page": chosen, "page_count": len(reader.pages),
                        "width_pt": float(page.mediabox.width), "height_pt": float(page.mediabox.height),
                        "embedded_raster_effective_ppi": "not assessed"})
        if details["width_pt"] <= 0 or details["height_pt"] <= 0:
            raise FigureError(f"Invalid PDF page size: {path.name}")
    elif ext == ".svg":
        if page_number is not None:
            raise FigureError(f"Page selection applies to PDF, not SVG: {path.name}")
        converted, engine = svg_to_pdf(path)
        try:
            reader = PdfReader(io.BytesIO(converted), strict=False)
            if len(reader.pages) != 1:
                raise FigureError(f"SVG conversion did not produce one page: {path.name}")
            page = reader.pages[0]
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if width <= 0 or height <= 0:
                raise FigureError(f"SVG has an invalid size: {path.name}")
            details.update({"kind": "svg", "width_pt": width, "height_pt": height,
                            "conversion": f"{engine} to PDF; visually verify fidelity",
                            "embedded_raster_effective_ppi": "not assessed"})
            if include_svg_pdf:
                details["_svg_pdf_bytes"] = converted
        except FigureError:
            raise
        except Exception as exc:
            raise FigureError(f"Unable to inspect converted SVG {path.name}: {exc}") from exc
    elif ext in RASTER:
        try:
            with Image.open(path) as img:
                if img.format != RASTER[ext]:
                    raise FigureError(f"File extension and detected format disagree: {path.name}")
                if getattr(img, "n_frames", 1) != 1:
                    raise FigureError(f"Multi-frame images are unsupported: {path.name}")
                if img.mode not in ALLOWED_MODES:
                    raise FigureError(f"Image mode {img.mode} is unsupported: {path.name}")
                if img.width * img.height > MAX_INPUT_PIXELS:
                    raise FigureError(f"Image exceeds pixel limit: {path.name}")
                orientation = img.getexif().get(274, 1)
                if orientation not in (None, 1):
                    raise FigureError(f"Image requires EXIF orientation handling: {path.name}")
                declared_dpi = img.info.get("dpi")
                if declared_dpi is not None:
                    if isinstance(declared_dpi, (list, tuple)):
                        declared_dpi = [float(v) for v in declared_dpi[:2]]
                    else:
                        declared_dpi = [float(declared_dpi), float(declared_dpi)]
                details.update({"kind": "raster", "format": img.format, "width_px": img.width,
                                "height_px": img.height, "mode": img.mode,
                                "icc_profile_present": bool(img.info.get("icc_profile")),
                                "exif_present": bool(img.info.get("exif")),
                                "declared_dpi": declared_dpi})
            with Image.open(path) as verifier:
                verifier.verify()
        except FigureError:
            raise
        except Exception as exc:
            raise FigureError(f"Unable to read image {path.name}: {exc}") from exc
    else:
        raise FigureError(f"Unsupported input format: {path.name}")
    return details


def load_job(job_path: Path) -> dict[str, Any]:
    with job_path.open(encoding="utf-8") as f:
        job = json.load(f)
    if not isinstance(job, dict):
        raise FigureError("Job must be a JSON object")
    return job


def boxes_for(job: dict[str, Any], panels: list[dict[str, Any]], w: float, h: float) -> tuple[list[list[float]], float]:
    grid = job.get("grid", {})
    if not isinstance(grid, dict):
        raise FigureError("grid must be an object")
    # The 10 pt label and 2 pt inset need at least 12 pt; reject smaller
    # bands before creating the output directory.
    band = number(grid.get("label_band_mm", 7), "label_band_mm", minimum=12 / MM_TO_PT, maximum=30)
    modes = [p.get("label_mode", "add") for p in panels]
    if any(mode not in ("add", "source", "none") for mode in modes):
        raise FigureError("label_mode must be add, source, or none")
    custom = ["box_mm" in p for p in panels]
    if any(custom) and not all(custom):
        raise FigureError("Supply box_mm for every panel, or none")
    if all(custom):
        boxes = []
        for i, p in enumerate(panels):
            box = p["box_mm"]
            if not isinstance(box, list) or len(box) != 4:
                raise FigureError(f"Panel {i+1} box_mm must have four numbers")
            x = number(box[0], "box x", minimum=0, maximum=w)
            y = number(box[1], "box y", minimum=0, maximum=h)
            bw = number(box[2], "box width", minimum=1, maximum=w)
            required_band = band if modes[i] == "add" else 0
            bh = number(box[3], "box height", minimum=required_band + 1, maximum=h)
            if x + bw > w + 1e-7 or y + bh > h + 1e-7:
                raise FigureError(f"Panel {i+1} exceeds canvas")
            boxes.append([x, y, bw, bh])
    else:
        n = len(panels)
        cols = whole(grid.get("cols", math.ceil(math.sqrt(n))), "grid cols", minimum=1, maximum=20)
        rows = whole(grid.get("rows", math.ceil(n / cols)), "grid rows", minimum=1, maximum=20)
        if rows * cols < n:
            raise FigureError("Grid has fewer cells than panels")
        margin = number(grid.get("margin_mm", 3), "margin_mm", minimum=0, maximum=50)
        gap = number(grid.get("gap_mm", 3), "gap_mm", minimum=0, maximum=50)
        bw = (w - 2 * margin - gap * (cols - 1)) / cols
        bh = (h - 2 * margin - gap * (rows - 1)) / rows
        required_band = band if "add" in modes else 0
        if bw < 1 or bh <= required_band + 1:
            raise FigureError("Grid cells are too small for content and labels")
        boxes = [[margin + (i % cols) * (bw + gap), margin + (i // cols) * (bh + gap), bw, bh] for i in range(n)]
    for i, (x, y, bw, bh) in enumerate(boxes):
        for x2, y2, bw2, bh2 in boxes[i + 1:]:
            if x < x2 + bw2 - 1e-7 and x + bw > x2 + 1e-7 and y < y2 + bh2 - 1e-7 and y + bh > y2 + 1e-7:
                raise FigureError("Panel boxes overlap")
    return boxes, band


def prepare(job: dict[str, Any], job_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_panels = job.get("panels")
    if not isinstance(raw_panels, list) or not (1 <= len(raw_panels) <= 30) or any(not isinstance(p, dict) for p in raw_panels):
        raise FigureError("panels must contain 1 to 30 objects")
    canvas = job.get("canvas", {})
    if not isinstance(canvas, dict):
        raise FigureError("canvas must be an object")
    w = number(canvas.get("width_mm", 150), "width_mm", minimum=20, maximum=500)
    h = number(canvas.get("height_mm", 100), "height_mm", minimum=20, maximum=500)
    dpi = whole(canvas.get("dpi", 300), "dpi", minimum=50, maximum=1200)
    if math.ceil(w / 25.4 * dpi) * math.ceil(h / 25.4 * dpi) > MAX_RENDER_PIXELS:
        raise FigureError("Requested canvas and DPI exceed the render pixel limit")
    requested = job.get("outputs", ["pdf", "png"])
    if not isinstance(requested, list) or not requested or any(x not in ("pdf", "png", "tiff", "pptx") for x in requested) or "pdf" not in requested:
        raise FigureError("outputs must include pdf and may also contain png, tiff and pptx")
    outputs = list(dict.fromkeys(requested))
    ppi_target = number(job.get("minimum_effective_ppi", 300), "minimum_effective_ppi", minimum=50, maximum=1200)
    if any(x in outputs for x in ("png", "tiff")) and shutil.which("pdftoppm") is None:
        try:
            import pypdfium2  # noqa: F401 - used if Poppler is unavailable
        except (ImportError, OSError) as exc:
            raise FigureError("PNG/TIFF rendering needs pypdfium2 or pdftoppm; see references/execution.md") from exc
    if "pptx" in outputs:
        try:
            import pptx  # noqa: F401 - used for editable panel layout
        except (ImportError, OSError) as exc:
            raise FigureError("PPTX output needs python-pptx; see requirements-pptx.txt") from exc
    boxes, band = boxes_for(job, raw_panels, w, h)
    sources, normalized = [], []
    labels = []
    for i, (panel, box) in enumerate(zip(raw_panels, boxes)):
        raw_path = panel.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise FigureError(f"Panel {i+1} path is required")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = job_dir / path
        page = panel.get("page")
        page = None if page is None else whole(page, "page", minimum=1, maximum=100000)
        source = inspect(path, page, include_svg_pdf=True)
        if "pptx" in outputs and source["kind"] in ("pdf", "svg"):
            try:
                import pypdfium2  # noqa: F401 - used to insert PDF/SVG as images
            except (ImportError, OSError) as exc:
                raise FigureError("PPTX output with PDF/SVG panels needs pypdfium2; see requirements-pptx.txt") from exc
        expected = panel.get("sha256")
        if expected is not None and expected != source["sha256"]:
            raise FigureError(f"Input changed since layout was saved: {source['file_name']}")
        allow_icc = panel.get("allow_icc_conversion", False)
        if not isinstance(allow_icc, bool):
            raise FigureError("allow_icc_conversion must be a boolean")
        if source.get("icc_profile_present") and not allow_icc:
            raise FigureError(f"ICC profile preservation is not verified for {source['file_name']}; choose a safe workflow or explicitly record accepted conversion")
        label = panel.get("label", chr(65 + i) if i < 26 else f"P{i + 1}")
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9]{1,6}", label):
            raise FigureError("Panel labels must be 1–6 ASCII letters or digits")
        if label in labels:
            raise FigureError(f"Duplicate panel label: {label}")
        labels.append(label)
        label_mode = panel.get("label_mode", "add")
        role = panel.get("role", "")
        if not isinstance(role, str):
            raise FigureError("Panel role must be text")
        normalized.append({"path": source["path"], "sha256": source["sha256"], "page": source.get("page"),
                           "label": label, "label_mode": label_mode, "role": role, "box_mm": box,
                           "allow_icc_conversion": allow_icc})
        sources.append(source)
    result = {"schema": "medical-research-figure-layout/0.2", "canvas": {"width_mm": w, "height_mm": h, "dpi": dpi},
              "grid": {"label_band_mm": band}, "minimum_effective_ppi": ppi_target, "outputs": outputs, "panels": normalized}
    return result, sources


def placed_rect(box: list[float], band: float, source_w: float, source_h: float, page_h_mm: float) -> tuple[float, float, float, float]:
    x, y, w, h = box
    available_h = h - band
    scale = min(w / source_w, available_h / source_h)
    width, height = source_w * scale, source_h * scale
    x_mm = x + (w - width) / 2
    y_top_mm = y + band + (available_h - height) / 2
    y_bottom_mm = page_h_mm - y_top_mm - height
    return x_mm * MM_TO_PT, y_bottom_mm * MM_TO_PT, width * MM_TO_PT, height * MM_TO_PT


def compose(layout: dict[str, Any], sources: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    page_w = layout["canvas"]["width_mm"] * MM_TO_PT
    page_h = layout["canvas"]["height_mm"] * MM_TO_PT
    band = layout["grid"]["label_band_mm"]
    buffer = io.BytesIO()
    base = pdf_canvas.Canvas(buffer, pagesize=(page_w, page_h), pageCompression=1)
    base.setFillColorRGB(1, 1, 1)
    base.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    base.setFillColorRGB(0, 0, 0)
    base.setFont("Helvetica-Bold", 10)
    records = []
    vectors = []
    for panel, source in zip(layout["panels"], sources):
        box = panel["box_mm"]
        x, y, bw, bh = box
        panel_band = band if panel["label_mode"] == "add" else 0
        if panel["label_mode"] == "add":
            label_y = page_h - (y + band) * MM_TO_PT + 2
            if 10 > band * MM_TO_PT - 2:
                raise FigureError("Label band is too small for the panel label")
            base.drawString(x * MM_TO_PT, label_y, panel["label"])
        source_w = source.get("width_px", source.get("width_pt"))
        source_h = source.get("height_px", source.get("height_pt"))
        px, py, pw, ph = placed_rect(box, panel_band, source_w, source_h, layout["canvas"]["height_mm"])
        if source["kind"] == "raster":
            with Image.open(source["path"]) as img:
                # Palette/alpha are interpreted as image display content; no contrast/background edit.
                mode = "RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB"
                image = img.convert(mode)
                base.drawImage(ImageReader(image), px, py, pw, ph, mask="auto", preserveAspectRatio=True, anchor="c")
            source["embedded_pixel_mode"] = mode
            effective = min(source_w / (pw / 72), source_h / (ph / 72))
            source["effective_ppi_at_placement"] = round(effective, 2)
        else:
            vectors.append((source, (px, py, pw, ph)))
        records.append({"label": panel["label"], "label_mode": panel["label_mode"], "source_sha256": source["sha256"], "source_path": source["path"],
                        "role": panel["role"], "box_mm": box,
                        "placed_rect_pt_from_bottom_left": [round(v, 4) for v in (px, py, pw, ph)],
                        "processing": "proportional placement; no crop or image background adjustment"})
    base.showPage()
    base.save()
    page = PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
    for source, (px, py, pw, ph) in vectors:
        if source["kind"] == "svg":
            svg_reader = PdfReader(io.BytesIO(source["_svg_pdf_bytes"]), strict=False)
            source_page = svg_reader.pages[0]
        else:
            _, source_page, _ = pdf_page(Path(source["path"]), source["page"])
        scale = min(pw / source["width_pt"], ph / source["height_pt"])
        page.merge_transformed_page(source_page, (scale, 0, 0, scale, px, py), over=True, expand=False)
    writer = PdfWriter()
    writer.add_page(page)
    out.mkdir(parents=True, exist_ok=False)
    try:
        return write_outputs(layout, sources, records, writer, out)
    except Exception as exc:
        try:
            (out / "FAILED.json").write_text(json.dumps({"status": "FAILED", "error": str(exc), "notice": "Do not use partial files as completed output."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        raise


def render_pdf(pdf: Path, dpi: int) -> tuple[Any, str]:
    """Render locally; PDFium avoids requiring a separate Poppler installation."""
    errors = []
    poppler = shutil.which("pdftoppm")
    if poppler:
        with tempfile.TemporaryDirectory(prefix="figure-render-") as tmp:
            prefix = Path(tmp) / "preview"
            cmd = [poppler, "-f", "1", "-l", "1", "-singlefile", "-r", str(dpi), "-png", str(pdf), str(prefix)]
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
                preview = prefix.with_suffix(".png")
                if proc.returncode == 0 and preview.is_file():
                    with Image.open(preview) as img:
                        img.load()
                        return img.copy(), "pdftoppm"
                errors.append(f"pdftoppm: {proc.stderr[:300] or 'no preview produced'}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"pdftoppm: {exc}")
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(pdf))
        try:
            page = document[0]
            try:
                bitmap = page.render(scale=dpi / 72)
                try:
                    return bitmap.to_pil().copy(), "pypdfium2"
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
    except (ImportError, OSError) as exc:
        errors.append(f"pypdfium2 unavailable: {exc}")
    except Exception as exc:
        errors.append(f"pypdfium2: {exc}")
    raise FigureError("PDF rendering failed: " + "; ".join(errors))


def write_editable_pptx(layout: dict[str, Any], sources: list[dict[str, Any]], records: list[dict[str, Any]], out: Path) -> None:
    """Place each panel and its label as separate slide objects, without editing source content."""
    from pptx import Presentation
    from pptx.util import Pt

    presentation = Presentation()
    page_w = layout["canvas"]["width_mm"] * MM_TO_PT
    page_h = layout["canvas"]["height_mm"] * MM_TO_PT
    presentation.slide_width, presentation.slide_height = Pt(page_w), Pt(page_h)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    labels = []
    for panel, source, record in zip(layout["panels"], sources, records):
        px, py, pw, ph = record["placed_rect_pt_from_bottom_left"]
        if source["kind"] == "raster":
            with Image.open(source["path"]) as original:
                mode = "RGBA" if "A" in original.getbands() or "transparency" in original.info else "RGB"
                image = original.convert(mode)
        else:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(source["_svg_pdf_bytes"] if source["kind"] == "svg" else source["path"])
            try:
                page = document[0 if source["kind"] == "svg" else source["page"] - 1]
                try:
                    scale = layout["canvas"]["dpi"] / 72 * pw / source["width_pt"]
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
        buffer = io.BytesIO()
        try:
            image.save(buffer, format="PNG")
        finally:
            image.close()
        buffer.seek(0)
        shape = slide.shapes.add_picture(buffer, Pt(px), Pt(page_h - py - ph), width=Pt(pw), height=Pt(ph))
        shape.name = f"Panel {panel['label']}"
        if panel["label_mode"] == "add":
            labels.append(panel)
    for panel in labels:
        x, y, _, _ = panel["box_mm"]
        band = layout["grid"]["label_band_mm"]
        text_box = slide.shapes.add_textbox(Pt(x * MM_TO_PT), Pt((y + band) * MM_TO_PT - 12), Pt(60), Pt(14))
        text_box.name = f"Panel label {panel['label']}"
        frame = text_box.text_frame
        frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
        frame.text = panel["label"]
        run = frame.paragraphs[0].runs[0]
        run.font.name, run.font.bold, run.font.size = "Arial", True, Pt(10)
    presentation.save(str(out / "figure.pptx"))


def write_outputs(layout: dict[str, Any], sources: list[dict[str, Any]], records: list[dict[str, Any]], writer: PdfWriter, out: Path) -> dict[str, Any]:
    pdf = out / "figure.pdf"
    with pdf.open("wb") as f:
        writer.write(f)
    reread = PdfReader(str(pdf))
    if len(reread.pages) != 1 or reread.pages[0].get_contents() is None:
        raise FigureError("Written PDF failed basic structural verification")
    outputs = layout["outputs"]
    renderer = None
    if "png" in outputs or "tiff" in outputs:
        image, renderer = render_pdf(pdf, layout["canvas"]["dpi"])
        with image as img:
            expected_w = math.ceil(layout["canvas"]["width_mm"] / 25.4 * layout["canvas"]["dpi"])
            expected_h = math.ceil(layout["canvas"]["height_mm"] / 25.4 * layout["canvas"]["dpi"])
            if abs(img.width - expected_w) > 2 or abs(img.height - expected_h) > 2:
                raise FigureError("Rendered size does not match requested physical dimensions")
            if "png" in outputs:
                img.save(out / "figure.png", dpi=(layout["canvas"]["dpi"],) * 2)
            if "tiff" in outputs:
                img.save(out / "figure.tiff", compression="tiff_lzw", dpi=(layout["canvas"]["dpi"],) * 2)
    if "pptx" in outputs:
        write_editable_pptx(layout, sources, records, out)
    for source in sources:
        if sha256(Path(source["path"])) != source["sha256"]:
            raise FigureError(f"Input changed during composition: {source['file_name']}")
    (out / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_names = ["figure.pdf"] + (["figure.png"] if "png" in outputs else []) + (["figure.tiff"] if "tiff" in outputs else []) + (["figure.pptx"] if "pptx" in outputs else []) + ["layout.json"]
    ppi_warnings = [{"label": panel["label"], "effective_ppi": source["effective_ppi_at_placement"],
                     "threshold_ppi": layout["minimum_effective_ppi"]}
                    for panel, source in zip(layout["panels"], sources)
                    if source.get("effective_ppi_at_placement", float("inf")) < layout["minimum_effective_ppi"]]
    public_sources = [{key: value for key, value in source.items() if key != "_svg_pdf_bytes"} for source in sources]
    receipt = {"schema": "medical-research-figure-receipt/0.2", "created_utc": datetime.now(timezone.utc).isoformat(),
               "status": "CREATED_REVIEW_REQUIRED", "sources": public_sources, "panels": records,
               "effective_ppi_warnings": ppi_warnings, "raster_renderer": renderer,
               "outputs": [{"file": name, "sha256": sha256(out / name)} for name in output_names],
               "limitations": ["Scientific meaning and captions not validated", "Visual review at final size required", "Journal policy not verified", "Raster previews are resampled", "PDF/SVG embedded raster resolution not assessed", "PPTX panel positions and added labels are editable; panel content is an image"]}
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": receipt["status"], "output_dir": str(out.resolve()), "files": output_names + ["receipt.json"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("inspect", help="Inspect one permitted input file")
    single.add_argument("input", type=Path)
    single.add_argument("--page", type=int)
    make = sub.add_parser("compose", help="Compose a new version from a local job")
    make.add_argument("--job", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = inspect(args.input, args.page)
        else:
            job = args.job.expanduser().resolve(strict=True)
            layout, sources = prepare(load_job(job), job.parent)
            result = compose(layout, sources, args.out.expanduser().absolute())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (FigureError, OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
