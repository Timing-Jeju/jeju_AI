"""공항의 단계적 적재·게시 재시도를 검증한다."""

from datetime import date
from uuid import uuid4

from scripts.import_kac_airport import find_staged_airport_publication


def test_staged_airport_can_be_activated_on_same_file_retry(monkeypatch):
    """동일 원본 재시도에서도 기존 검증된 대기 publication을 찾는다."""
    publication = uuid4()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters=None):
            if parameters:
                assert parameters == ("a" * 64, date(2025, 8, 1))
                assert "STAGED" in statement and "kac.airport" in statement
            return self

        def fetchone(self):
            return (publication,)

    monkeypatch.setattr("scripts.import_kac_airport.psycopg.connect", lambda _: Connection())
    assert find_staged_airport_publication("unused", "a" * 64, date(2025, 8, 1)) == publication
