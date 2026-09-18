"""Exercise the Windows non-admin HuggingFace cache fallback under concurrency."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from model_downloads import prepare_model_downloads


@unittest.skipUnless(os.name == "nt", "Windows model cache compatibility")
class ModelDownloadsTest(unittest.TestCase):
    def test_parallel_downloads_copy_when_symlink_privilege_is_absent(self):
        from huggingface_hub import file_download

        prepare_model_downloads()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blobs = root / "blobs"
            snapshot = root / "snapshots" / "test"
            blobs.mkdir()
            snapshot.mkdir(parents=True)
            for index in range(8):
                (blobs / str(index)).write_text(str(index), encoding="utf-8")

            def denied(*_args, **_kwargs):
                time.sleep(0.05)  # Keep the optimistic probe open to other threads.
                error = OSError("symlink privilege is absent")
                error.winerror = 1314
                raise error

            def place(index):
                file_download._create_symlink(str(blobs / str(index)), str(snapshot / str(index)), new_blob=True)

            with mock.patch.object(file_download.os, "symlink", side_effect=denied), \
                 mock.patch.object(file_download.constants, "HF_HUB_DISABLE_SYMLINKS_WARNING", True):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(place, range(8)))
            self.assertEqual([file.read_text() for file in sorted(snapshot.iterdir())], [str(i) for i in range(8)])


if __name__ == "__main__":
    unittest.main()
