#!/usr/bin/env python3
"""Flag OCR-recognized small word boxes at a specified physical output size.

This is a heuristic review aid, not a measurement of nominal font size, OCR
coverage, scientific correctness, or journal compliance. Requires Tesseract.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded(value: float, lower: float, upper: float, label: str) -> float:
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"Invalid {label}")
    return value


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--width-mm', type=float, required=True)
    p.add_argument('--height-mm', type=float, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--min-text-height-pt', type=float, default=4.0)
    p.add_argument('--min-confidence', type=float, default=60.0)
    args = p.parse_args()
    try:
        width = bounded(args.width_mm, 20, 500, 'width-mm')
        height = bounded(args.height_mm, 20, 500, 'height-mm')
        minimum = bounded(args.min_text_height_pt, 1, 20, 'min-text-height-pt')
        confidence = bounded(args.min_confidence, 0, 100, 'min-confidence')
        image_path = args.image.expanduser().resolve(strict=True)
        out = args.out.expanduser().absolute()
        if out.exists() or not out.parent.is_dir():
            raise ValueError('Output must be a new directory with an existing parent')
        executable = shutil.which('tesseract')
        if executable is None:
            raise ValueError('Tesseract is required for OCR review; visual review remains necessary')
        if image_path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
            raise ValueError('Readability review requires a raster preview')
        with Image.open(image_path) as source:
            if getattr(source, 'n_frames', 1) != 1 or source.width * source.height > 100_000_000:
                raise ValueError('Unsupported image frames or size')
            source.load()
            original_size = source.size
            preview = source.convert('RGB')
        dpi_x = original_size[0] * 25.4 / width
        dpi_y = original_size[1] * 25.4 / height
        if abs(dpi_x / dpi_y - 1) > .01:
            raise ValueError('Image proportions differ from the stated physical size')
        source_hash = digest(image_path)
        result = subprocess.run([executable, str(image_path), 'stdout', '--psm', '11', 'tsv'],
                                capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ValueError('Tesseract could not inspect this preview')
        words = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter='\t'):
            text = (row.get('text') or '').strip()
            try:
                conf = float(row['conf'])
                x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            except (ValueError, TypeError, KeyError):
                continue
            if conf < confidence or sum(c.isalnum() for c in text) < 2 or w <= 0 or h <= 0:
                continue
            if x < 0 or y < 0 or x + w > original_size[0] or y + h > original_size[1]:
                continue
            # Word bounding-box ink height, not a typography font-size measurement.
            words.append({'box_px': [x, y, w, h], 'ink_height_pt': round(h * 72 / dpi_y, 2),
                          'confidence': round(conf, 1)})
        small = [word for word in words if word['ink_height_pt'] < minimum]
        drawing = ImageDraw.Draw(preview)
        for word in small:
            x, y, w, h = word['box_px']
            drawing.rectangle((x, y, x+w, y+h), outline=(230, 30, 30), width=max(1, round(dpi_x / 150)))
        if digest(image_path) != source_hash:
            raise ValueError('Image changed during readability review')
        out.mkdir()
        preview.save(out/'small_text_overlay.png')
        audit = {'schema': 'medical-research-figure-readability/0.1',
                 'status': 'REVIEW_REQUIRED' if words else 'UNASSESSED_NO_RECOGNIZED_TEXT',
                 'source_sha256': source_hash, 'image_px': list(original_size),
                 'assumed_size_mm': [width, height], 'render_dpi': [round(dpi_x, 2), round(dpi_y, 2)],
                 'minimum_ink_height_pt': minimum, 'min_ocr_confidence': confidence,
                 'recognized_word_count': len(words), 'flagged_small_word_count': len(small),
                 'flagged_word_boxes': small,
                 'limitations': ['Threshold is a review heuristic, not a journal rule or nominal font size',
                                 'OCR can miss or misread text, especially small labels and scale bars',
                                 'Unflagged text is not proof of legibility; inspect at the final physical size',
                                 'Output DPI is not the effective resolution of embedded source images',
                                 'Changing the figure size invalidates these measurements']}
        (out/'audit.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        print(json.dumps({'status': audit['status'], 'recognized_word_count': len(words),
                          'flagged_small_word_count': len(small), 'output_dir': str(out)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status':'FAILED','error':str(exc)}, ensure_ascii=False),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
