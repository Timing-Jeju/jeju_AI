import pytest

from jeju_trip.infrastructure.airport_source import normalize_airport_csv
from jeju_trip.infrastructure.source_catalog import load_default_source_catalog


def test_kac_airport_source_is_approved_and_separate_from_tourapi():
    """공식 공항 원본은 관광공사 장소와 별도 승인 출처로 관리한다."""
    source = load_default_source_catalog().require("kac.airport")
    assert source.license.status == "APPROVED"
    assert source.acquisition.format == "CSV"
    assert source.acquisition.secret_names == ()
    source.assert_network_request_allowed("https://www.data.go.kr/cmm/cmm/fileDownload.do")


def test_airport_csv_preserves_official_identity_without_tourapi_id():
    """공항 대표좌표는 독립 출처로 정규화하고 관광공사 ID를 만들지 않는다."""
    raw = (
        "공항명,행정구역,위도(WGS84좌표),경도(WGS84좌표)\n"
        "제주,제주 제주시 공항로 2,33.511111,126.492778\n"
    ).encode("cp949")
    result = normalize_airport_csv(raw)
    assert len(result.records) == 1
    airport = result.records[0]
    assert airport.fact_id == "kac.airport:CJU"
    assert airport.source_record_id == "제주"
    assert airport.name == "제주국제공항"
    assert airport.content_type_id == "airport"
    assert airport.position.latitude == 33.511111


def test_missing_or_duplicate_jeju_airport_is_rejected():
    """원본에서 제주공항을 유일하게 확정하지 못하면 등록을 중단한다."""
    with pytest.raises(ValueError, match="AIRPORT_IDENTITY_NOT_UNIQUE"):
        normalize_airport_csv("공항명,행정구역,위도(WGS84좌표),경도(WGS84좌표)\n".encode("cp949"))
