#!/usr/bin/env python3
"""Prepare a review-only figure handoff for MAINLINE-RESEARCH-V2.

The input must be an existing aggregate-only analysis export, an existing
frozen article, and a one-source figure-tool output. This does not run a
statistical model, rewrite a manuscript, refresh QC, or approve a review.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
from typing import Any

from PIL import Image

REVIEW_EXTRAS = {'scientific_review_assessment.json', 'scientific_review_evidence.json',
                 'scientific_review_acceptance.json'}


def sha(path: Path) -> str:
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()


def object_at(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} must be a JSON object')
    return value


def relative_name(value: object) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Invalid package-relative file name')
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ('.', '..') for part in p.parts):
        raise ValueError('Invalid package-relative file name')
    return p.as_posix()


def safe_file(base: Path, name: object) -> Path:
    rel = relative_name(name)
    path = base / rel
    if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(f'Package file unavailable: {rel}')
    return path


def check_inventory(base: Path, manifest: dict[str, Any]) -> None:
    refs = manifest.get('files')
    if not isinstance(refs, list) or any(not isinstance(item, dict) for item in refs):
        raise ValueError('Article file inventory is missing')
    names = [relative_name(item.get('logical_name')) for item in refs]
    if len(names) != len(set(names)):
        raise ValueError('Article file inventory contains duplicates')
    for p in base.rglob('*'):
        if p.is_symlink():
            raise ValueError('Article package contains a symlink')
    present = {p.relative_to(base).as_posix() for p in base.rglob('*')
               if p.is_file() and p.name != 'manifest.json' and p.name not in REVIEW_EXTRAS
               and not p.name.startswith('.')}
    if set(names) != present:
        raise ValueError('Article file inventory does not match its contents')
    for item, name in zip(refs, names):
        if sha(safe_file(base, name)) != item.get('sha256'):
            raise ValueError(f'Article file checksum mismatch: {name}')


def display(rows: object, display_id: str) -> dict[str, Any]:
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        raise ValueError('Display list is missing or malformed')
    found = [item for item in rows if item.get('display_id') == display_id]
    if len(found) != 1:
        raise ValueError('Expected exactly one display with the requested ID')
    return found[0]


def passed_gate(value: object, schema: str, analysis: Path, manifest: dict[str, Any]) -> bool:
    if schema != '3.2.0':
        return value == 'PASS'
    if not isinstance(value, dict) or value.get('status') != 'PASS' or not value.get('evidence_refs'):
        return False
    expected = value.get('evidence_sha256')
    files = manifest.get('files')
    if not isinstance(files, list):
        return False
    matches = [entry for entry in files if isinstance(entry, dict) and entry.get('sha256') == expected]
    return bool(matches) and any(sha(safe_file(analysis, entry.get('logical_name'))) == expected for entry in matches)


def validate_figure(path: Path) -> None:
    with Image.open(path) as im:
        if im.format != 'PNG' or getattr(im, 'n_frames', 1) != 1 or im.width * im.height > 120_000_000:
            raise ValueError('Handoff image must be a single PNG within the pixel limit')
        im.verify()


def output_dir(path: Path, *forbidden: Path) -> Path:
    path = path.expanduser().absolute()
    if path.exists() or not path.parent.is_dir():
        raise ValueError('Output must be a new directory with an existing parent')
    resolved = path.resolve()
    if any(resolved.is_relative_to(p.resolve()) for p in forbidden):
        raise ValueError('Output cannot be inside an input directory')
    return path


def prepare(analysis: Path, article: Path, figure: Path, display_id: str, out: Path) -> dict:
    analysis, article, figure = (p.expanduser().resolve(strict=True) for p in (analysis, article, figure))
    if not all(p.is_dir() for p in (analysis, article, figure)):
        raise ValueError('Analysis, article and figure inputs must be directories')
    out = output_dir(out, analysis, article, figure)
    am = object_at(analysis/'manifest.json')
    ad = object_at(analysis/'display_manifest.json')
    wm = object_at(article/'manifest.json')
    wd = object_at(article/'display_manifest.json')
    receipt = object_at(figure/'receipt.json')
    schema = am.get('schema_version')
    if (schema not in ('3.1.0', '3.2.0')
        or am.get('workflow_id') != 'MAINLINE-RESEARCH-V2' or am.get('package_type') != '02_analysis_result'
        or am.get('aggregate_only') is not True or am.get('no_physical_paths') is not True
        or am.get('formal_status') != 'COMPLETE'
        or not passed_gate(am.get('technical_qc'), schema, analysis, am)
        or not passed_gate(am.get('independent_review'), schema, analysis, am)
        or am.get('open_blocking_findings') != 0):
        raise ValueError('Analysis manifest is not a completed aggregate-only V2 export')
    candidate = am.get('candidate_id')
    if (not isinstance(candidate, str) or not candidate or wm.get('candidate_id') != candidate
        or ad.get('candidate_id') != candidate or wd.get('candidate_id') != candidate
        or wm.get('workflow_id') != 'MAINLINE-RESEARCH-V2' or wm.get('package_type') != '03_final_article'
        or wm.get('analysis_result_manifest_sha256') != sha(analysis/'manifest.json')):
        raise ValueError('Analysis and article identities or manifest bindings differ')
    check_inventory(article, wm)
    a = display(ad.get('displays'), display_id)
    a_manifest = display(am.get('displays'), display_id)
    w = display(wd.get('displays'), display_id)
    keys = ('kind', 'role', 'source_object', 'source_sha256')
    if (a.get('kind') != 'FIGURE' or a.get('semantic_status') != 'PASS'
        or w.get('semantic_status') != 'PASS'
        or any(a.get(k) != w.get(k) or a.get(k) != a_manifest.get(k) for k in keys)
        or a.get('artifact_sha256') != a_manifest.get('artifact_sha256')):
        raise ValueError('Analysis and article display identity or source differs')
    old_name = relative_name(w.get('logical_name'))
    old = safe_file(article, old_name)
    if old.suffix.lower() != '.png' or sha(old) != w.get('artifact_sha256'):
        raise ValueError('Existing article display does not match its manifest')
    caption = w.get('md_caption')
    if not isinstance(caption, str) or not caption or caption != w.get('docx_caption'):
        raise ValueError('Existing article captions are missing or disagree')
    if receipt.get('status') != 'CREATED_REVIEW_REQUIRED':
        raise ValueError('Figure receipt is not a completed review-required output')
    sources = receipt.get('sources')
    if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
        raise ValueError('Automatic handoff requires exactly one analysis source figure')
    source_path = Path(sources[0].get('path', '')).resolve(strict=True)
    if (not source_path.is_relative_to(analysis) or not source_path.is_file()
        or 'sha256:' + str(sources[0].get('sha256')) != a.get('artifact_sha256')
        or sha(source_path) != a.get('artifact_sha256')):
        raise ValueError('Figure input is not the bound analysis display artifact')
    new = figure/'figure.png'
    validate_figure(new)
    entries = receipt.get('outputs')
    if not isinstance(entries, list):
        raise ValueError('Figure receipt output inventory is missing')
    png_entries = [item for item in entries if isinstance(item, dict) and item.get('file') == 'figure.png']
    if len(png_entries) != 1 or 'sha256:' + str(png_entries[0].get('sha256')) != sha(new):
        raise ValueError('New figure does not match its receipt')
    handoff = {
        'schema': 'medical-research-figure-handoff/0.1', 'status': 'REVIEW_REQUIRED_NOT_APPROVED',
        'workflow_id': 'MAINLINE-RESEARCH-V2', 'analysis_schema_version':schema, 'candidate_id': candidate,
        'display_id': display_id, 'display_role': a['role'], 'source_object': a['source_object'],
        'analysis_manifest_sha256': sha(analysis/'manifest.json'),
        'analysis_display_manifest_sha256': sha(analysis/'display_manifest.json'),
        'analysis_source_sha256': a['source_sha256'], 'analysis_figure_sha256': a['artifact_sha256'],
        'analysis_figure_relative_path': source_path.relative_to(analysis).as_posix(),
        'article_manifest_sha256': sha(article/'manifest.json'),
        'article_display_manifest_sha256': sha(article/'display_manifest.json'),
        'article_figure_relative_path': old_name, 'article_original_figure_sha256': sha(old),
        'new_figure_relative_path': 'figure.png', 'new_figure_sha256': sha(new),
        'figure_receipt_sha256': sha(figure/'receipt.json'), 'proposed_caption': caption,
        'scope': 'single existing display; visual and semantic review required',
        'required_next_steps': ['Review final size, values, scientific meaning and caption',
                                'Rebuild manuscript and check all figure references',
                                'Refresh manifest, QC and independent review using the owning workflow'],
        'limitations': ['Receipt is not a signed proof of scientific equivalence',
                        'Old review and QC do not apply to this replacement',
                        'Multiple-display regrouping requires explicit manuscript and blueprint review']}
    out.mkdir()
    try:
        shutil.copyfile(new, out/'figure.png')
        (out/'handoff.json').write_text(json.dumps(handoff, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    except Exception:
        (out/'FAILED.json').write_text('{"status":"FAILED"}\n',encoding='utf-8')
        raise
    return {'status':handoff['status'], 'output_dir':str(out), 'display_id':display_id}


def stage(analysis: Path, article: Path, handoff_dir: Path, out: Path) -> dict:
    analysis, article, handoff_dir = (p.expanduser().resolve(strict=True) for p in (analysis, article, handoff_dir))
    if not analysis.is_dir() or not article.is_dir() or not handoff_dir.is_dir():
        raise ValueError('Analysis, article and handoff inputs must be directories')
    out = output_dir(out, analysis, article, handoff_dir)
    handoff = object_at(handoff_dir/'handoff.json')
    wm = object_at(article/'manifest.json')
    if (handoff.get('schema') != 'medical-research-figure-handoff/0.1'
        or handoff.get('status') != 'REVIEW_REQUIRED_NOT_APPROVED'
        or handoff.get('candidate_id') != wm.get('candidate_id')
        or handoff.get('analysis_manifest_sha256') != sha(analysis/'manifest.json')
        or handoff.get('analysis_display_manifest_sha256') != sha(analysis/'display_manifest.json')
        or handoff.get('article_manifest_sha256') != sha(article/'manifest.json')
        or handoff.get('article_display_manifest_sha256') != sha(article/'display_manifest.json')):
        raise ValueError('Handoff is stale or refers to another article')
    check_inventory(article, wm)
    if sha(safe_file(analysis, handoff.get('analysis_figure_relative_path'))) != handoff.get('analysis_figure_sha256'):
        raise ValueError('Analysis figure changed since handoff')
    old_name = relative_name(handoff.get('article_figure_relative_path'))
    wd = object_at(article/'display_manifest.json')
    current = display(wd.get('displays'), handoff.get('display_id'))
    if (current.get('kind') != 'FIGURE' or current.get('semantic_status') != 'PASS'
        or current.get('logical_name') != old_name or current.get('role') != handoff.get('display_role')
        or current.get('source_object') != handoff.get('source_object')
        or current.get('source_sha256') != handoff.get('analysis_source_sha256')
        or current.get('artifact_sha256') != handoff.get('article_original_figure_sha256')
        or current.get('md_caption') != handoff.get('proposed_caption')
        or current.get('docx_caption') != handoff.get('proposed_caption')):
        raise ValueError('Handoff display identity no longer matches the article')
    if sha(safe_file(article, old_name)) != handoff.get('article_original_figure_sha256'):
        raise ValueError('Original article figure changed since handoff')
    new = safe_file(handoff_dir, handoff.get('new_figure_relative_path'))
    validate_figure(new)
    if sha(new) != handoff.get('new_figure_sha256'):
        raise ValueError('Handoff figure changed after preparation')
    out.mkdir()
    try:
        staged = out/'ARTICLE_COPY_REVIEW_REQUIRED'
        shutil.copytree(article, staged)
        shutil.copyfile(new, staged/old_name)
        if sha(staged/old_name) != handoff['new_figure_sha256']:
            raise ValueError('Staged figure checksum mismatch')
        state = {'status':'REVIEW_REQUIRED_NOT_APPROVED', 'source_article_manifest_sha256':sha(article/'manifest.json'),
                 'handoff_sha256':sha(handoff_dir/'handoff.json'), 'changed_file':old_name,
                 'new_figure_sha256':sha(staged/old_name),
                 'notice':'The copied manifest, DOCX, QC and review are stale; this is not a valid final article.'}
        (out/'STAGE_STATUS.json').write_text(json.dumps(state, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    except Exception:
        (out/'FAILED.json').write_text('{"status":"FAILED"}\n',encoding='utf-8')
        raise
    return {'status':state['status'], 'output_dir':str(out), 'article_copy':str(staged)}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare')
    a.add_argument('--analysis-dir',type=Path,required=True)
    a.add_argument('--article-dir',type=Path,required=True)
    a.add_argument('--figure-dir',type=Path,required=True)
    a.add_argument('--display-id',required=True)
    a.add_argument('--out',type=Path,required=True)
    s=sub.add_parser('stage')
    s.add_argument('--analysis-dir',type=Path,required=True)
    s.add_argument('--article-dir',type=Path,required=True)
    s.add_argument('--handoff-dir',type=Path,required=True)
    s.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        value = prepare(args.analysis_dir,args.article_dir,args.figure_dir,args.display_id,args.out) if args.command=='prepare' else stage(args.analysis_dir,args.article_dir,args.handoff_dir,args.out)
        print(json.dumps(value,ensure_ascii=False,indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'status':'FAILED','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
