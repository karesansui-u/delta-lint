import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from retrieval import _find_project_file


class RetrievalExcludeTest(unittest.TestCase):
    def test_find_project_file_skips_virtualenv_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            venv_file = repo / ".venv" / "lib" / "python3.14" / "site-packages" / "subprocess.py"
            src_file = repo / "src" / "subprocess.py"
            venv_file.parent.mkdir(parents=True)
            src_file.parent.mkdir(parents=True)
            venv_file.write_text("# external", encoding="utf-8")
            src_file.write_text("# project", encoding="utf-8")

            found = _find_project_file(repo, ["subprocess.py"], set())

        self.assertIsNotNone(found)
        self.assertEqual(found[0], "src/subprocess.py")


if __name__ == "__main__":
    unittest.main()
