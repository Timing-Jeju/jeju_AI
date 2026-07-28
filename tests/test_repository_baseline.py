from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryBaselineTest(unittest.TestCase):
    def test_structure_neutral_python_baseline_is_preserved(self):
        for path in (".python-version", "pyproject.toml", "uv.lock"):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

        self.assertFalse((ROOT / "app").exists())
        self.assertFalse((ROOT / "src").exists())

    def test_independent_ci_and_contract_are_present(self):
        self.assertTrue((ROOT / ".github" / "workflows" / "ci.yml").is_file())
        self.assertTrue((ROOT / "docs" / "FASTAPI_MCP_CONTRACT.md").is_file())

    def test_docs_link_to_the_spring_backend_repository(self):
        for path in ("README.md", "AGENTS.md", "docs/FASTAPI_MCP_CONTRACT.md"):
            with self.subTest(path=path):
                document = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("https://github.com/Timing-Jeju/jeju_BE", document)


if __name__ == "__main__":
    unittest.main()
