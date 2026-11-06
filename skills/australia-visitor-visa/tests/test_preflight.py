"""Run with python -m unittest discover -s tests from the skill root.
Fixtures are synthetic and created only in a temporary directory.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'preflight.py'
spec = importlib.util.spec_from_file_location('preflight', SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'private-person-name.png'
        Image.new('RGB', (120, 200), 'white').save(self.image, dpi=(300, 300))

    def pdf(self, encrypted=False):
        path = self.root / ('encrypted.pdf' if encrypted else 'document.pdf')
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=595, height=842)
        if encrypted:
            writer.encrypt('synthetic-password')
        writer.write(path)
        return path

    def test_unknown_limit_is_not_size_approval(self):
        result = preflight.inspect_file(self.image, 1)
        self.assertEqual(result['size_status'], 'limit_unverified')
        self.assertIn('verify_current_portal_limit', result['unresolved'])
        self.assertEqual(result['visual_review'], 'required')
        self.assertFalse(result['ocr_performed'])

    def test_size_boundary_and_read_only(self):
        digest = hashlib.sha256(self.image.read_bytes()).digest()
        size = self.image.stat().st_size
        equal = preflight.inspect_file(self.image, 1, size)
        self.assertNotIn('over_size_limit', equal['blockers'])
        over = preflight.inspect_file(self.image, 1, size - 1)
        self.assertIn('over_size_limit', over['blockers'])
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).digest(), digest)
        self.assertEqual(equal['frames'][0]['width_pixels'], 120)
        self.assertEqual(equal['visual_review'], 'required')  # DPI metadata is not readability.

    def test_pdf_page_count_and_no_text_does_not_invent_ocr(self):
        result = preflight.inspect_file(self.pdf(), 1, 1_000_000)
        self.assertEqual(result['page_count'], 2)
        self.assertTrue(all(p['text_layer'] == 'not_detected' for p in result['pages']))
        self.assertFalse(result['ocr_performed'])

    def test_encrypted_pdf_is_blocked(self):
        result = preflight.inspect_file(self.pdf(encrypted=True), 1, 1_000_000)
        self.assertTrue(result['encrypted'])
        self.assertIn('encrypted_pdf_requires_review', result['blockers'])

    def test_missing_empty_corrupt_and_mismatched(self):
        absent = preflight.inspect_file(self.root / 'missing.pdf', 1, 100)
        self.assertIn('missing_or_not_regular_file', absent['blockers'])
        empty = self.root / 'empty.pdf'
        empty.touch()
        self.assertIn('empty_file', preflight.inspect_file(empty, 1, 100)['blockers'])
        corrupt = self.root / 'broken.pdf'
        corrupt.write_bytes(b'%PDF-1.7\nnot a real PDF')
        self.assertIn('unreadable_or_corrupt_file', preflight.inspect_file(corrupt, 1, 100)['blockers'])
        mismatched = self.root / 'wrong.jpg'
        mismatched.write_bytes(self.image.read_bytes())
        self.assertIn('extension_content_mismatch', preflight.inspect_file(mismatched, 1, 1_000_000)['blockers'])

    def test_cli_exit_codes_and_no_source_details(self):
        for limit, expected in [(None, 2), (1, 1), (1_000_000, 0)]:
            args = [sys.executable, str(SCRIPT), str(self.image)]
            if limit is not None:
                args += ['--max-bytes', str(limit)]
            run = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(run.returncode, expected, run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(report['scope'], 'mechanical_only_not_upload_approval')
            self.assertNotIn(self.image.name, run.stdout + run.stderr)
            self.assertNotIn(str(self.root), run.stdout + run.stderr)
        corrupt = self.root / 'private-source-id.pdf'
        corrupt.write_bytes(b'%PDF-1.7\ninvalid')
        failure = subprocess.run([sys.executable, str(SCRIPT), str(corrupt), '--max-bytes', '1000'],
                                 capture_output=True, text=True)
        self.assertEqual(failure.returncode, 1)
        self.assertEqual(failure.stderr, '')
        self.assertNotIn(str(corrupt), failure.stdout)
        bad = subprocess.run([sys.executable, str(SCRIPT), str(self.image), '--max-bytes', '0'],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)


if __name__ == '__main__':
    unittest.main()
