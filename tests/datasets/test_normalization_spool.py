"""정규화 spool 보안과 checksum 테스트."""

from __future__ import annotations

import stat

from jeju_trip.infrastructure.normalization_spool import NormalizationSpool, Rejection


def test_normalization_spool_is_owner_only_and_removed() -> None:
    """정규화 spool은 권한 0600으로 만들고 작업 종료 후 삭제해야 한다."""

    with NormalizationSpool() as spool:
        result = spool.write(
            [{"name": "성산일출봉", "id": "1"}],
            [Rejection("2", "latitude", "INVALID_COORDINATE")],
        )
        path = result.spool_path
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert result.accepted_count == 1
        assert result.rejected_count == 1
        assert list(spool.rows())[0]["name"] == "성산일출봉"
    assert not path.exists()
