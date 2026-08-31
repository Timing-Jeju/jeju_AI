"""TMAP 메모리 전용 인수 지점 선택의 회귀 테스트."""

from scripts import run_staging_tmap_corridor_poc as poc


def test_representative_places_are_selected_from_the_active_snapshot(monkeypatch) -> None:
    """대표 구간은 만료 가능한 장소 ID 대신 활성 장소 중 권역 anchor 최근접값을 써야 한다."""

    captured: dict[str, object] = {}

    class Result:
        def fetchall(self):
            return [
                (1, "place-east-a", "동부 A", 33.5, 126.7),
                (2, "place-east-b", "동부 B", 33.4, 126.9),
                (3, "place-south", "남부", 33.2, 126.4),
                (4, "place-west", "서부", 33.3, 126.2),
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            captured["statement"] = str(statement)
            captured["parameters"] = parameters
            return Result()

    monkeypatch.setattr(poc.psycopg, "connect", lambda dsn: Connection())
    monkeypatch.setattr(poc, "_required", lambda name: "postgresql://runtime")

    places = poc._places()

    assert len(places) == len(poc.REPRESENTATIVE_ANCHORS)
    assert len({item["place_fact_id"] for item in places}) == len(places)
    assert "travel_read.active_place" in str(captured["statement"])
    assert "ST_Distance" in str(captured["statement"])
    assert captured["parameters"] == (
        [anchor[0] for anchor in poc.REPRESENTATIVE_ANCHORS],
        [anchor[1] for anchor in poc.REPRESENTATIVE_ANCHORS],
    )
