#!/usr/bin/env python3
"""Read-only attachment checks. No OCR, network access, conversion or file writes.

Usage: python preflight.py FILE [FILE ...] [--max-bytes N]
Requires pypdf for PDF files and Pillow for image files.
Exit 0: no detected mechanical blocker (visual review still required).
Exit 1: blocker found. Exit 2: a required check is unresolved (e.g. unknown limit).
Reports use neutral IDs, never source names, paths or extracted text.
"""
import argparse
import json
import warnings
from pathlib import Path


IMAGE_TYPES = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG',
               '.bmp': 'BMP', '.gif': 'GIF', '.tif': 'TIFF', '.tiff': 'TIFF'}


def inspect_file(path, index, max_bytes=None):
    result = {'document_id': f'D{index:03d}', 'blockers': [], 'unresolved': [],
              'visual_review': 'required', 'ocr_performed': False}
    if not path.is_file():
        result['blockers'].append('missing_or_not_regular_file')
        return result
    try:
        size = path.stat().st_size
        result['bytes'] = size
        result['max_bytes'] = max_bytes
        if not size:
            result['blockers'].append('empty_file')
        if max_bytes is None:
            result['size_status'] = 'limit_unverified'
            result['unresolved'].append('verify_current_portal_limit')
        elif size > max_bytes:
            result['size_status'] = 'over_limit'
            result['blockers'].append('over_size_limit')
        else:
            result['size_status'] = 'within_supplied_inclusive_limit'
        suffix = path.suffix.lower()
        if suffix == '.pdf':
            inspect_pdf(path, result)
        elif suffix in IMAGE_TYPES:
            inspect_image(path, result, IMAGE_TYPES[suffix])
        else:
            result['unresolved'].append('unsupported_type_or_missing_decoder')
        result['unresolved'].append('verify_portal_file_type_and_attachment_count')
    except Exception:
        # Parser exception text may contain source paths or applicant contents.
        result['blockers'].append('unreadable_or_corrupt_file')
    return result


def inspect_pdf(path, result):
    try:
        from pypdf import PdfReader
    except ImportError:
        result['unresolved'].append('missing_dependency_pypdf')
        return
    with path.open('rb') as source:
        header = source.read(1024)
    if not header.lstrip().startswith(b'%PDF-'):
        result['blockers'].append('extension_content_mismatch')
        return
    # pypdf can warn about malformed source contents; do not emit private diagnostics.
    import logging
    logger = logging.getLogger('pypdf')
    previous = (logger.disabled, logger.handlers[:], logger.propagate)
    logger.disabled = True
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    try:
        reader = PdfReader(path, strict=False)
        result['detected_format'] = 'PDF'
        result['encrypted'] = reader.is_encrypted
        if reader.is_encrypted:
            result['blockers'].append('encrypted_pdf_requires_review')
            return
        result['page_count'] = len(reader.pages)
        if not reader.pages:
            result['blockers'].append('no_pages')
        pages = []
        for number, page in enumerate(reader.pages, 1):
            record = {'page': number, 'rotation': page.rotation,
                      'width_points': round(float(page.mediabox.width), 2),
                      'height_points': round(float(page.mediabox.height), 2)}
            try:
                record['text_character_count'] = len((page.extract_text() or '').strip())
                record['text_layer'] = 'present' if record['text_character_count'] else 'not_detected'
            except Exception:
                record['text_layer'] = 'extraction_failed'
                result['unresolved'].append(f'page_{number}_text_extraction_failed')
            # Text presence does not show whether the scanned image is sharp or correct.
            pages.append(record)
        result['pages'] = pages
    finally:
        logger.disabled, logger.handlers, logger.propagate = previous


def inspect_image(path, result, expected):
    try:
        from PIL import Image
    except ImportError:
        result['unresolved'].append('missing_dependency_pillow')
        return
    with Image.open(path) as image:
        result['detected_format'] = image.format
        if image.format != expected:
            result['blockers'].append('extension_content_mismatch')
        count = getattr(image, 'n_frames', 1)
        result['frame_count'] = count
        frames = []
        for frame in range(count):
            image.seek(frame)
            image.load()  # Check pixel decoding, not merely a readable header.
            frames.append({'frame': frame + 1, 'width_pixels': image.width,
                           'height_pixels': image.height,
                           'exif_orientation': image.getexif().get(274),
                           'dpi_metadata': list(image.info.get('dpi', ()))})
        result['frames'] = frames
    with Image.open(path) as image:
        image.verify()


def positive_int(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('must be a positive integer') from exc
    if number <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--max-bytes', type=positive_int,
                        help='verified inclusive maximum bytes; for a strict limit L use L-1')
    args = parser.parse_args()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        results = [inspect_file(path, i, args.max_bytes) for i, path in enumerate(args.files, 1)]
    print(json.dumps({'scope': 'mechanical_only_not_upload_approval', 'documents': results},
                     ensure_ascii=False, indent=2))
    if any(item['blockers'] for item in results):
        return 1
    # File format/count and all visual checks remain manual, even after a known size limit.
    required = [issue for item in results for issue in item['unresolved']
                if issue != 'verify_portal_file_type_and_attachment_count']
    return 2 if required else 0


if __name__ == '__main__':
    raise SystemExit(main())
