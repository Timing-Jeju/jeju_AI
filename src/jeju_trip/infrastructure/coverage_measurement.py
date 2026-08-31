"""active projection 행으로 핵심 capability coverage를 재현 가능하게 측정한다."""

from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg
from psycopg import sql

from jeju_trip.infrastructure.projection_publisher import SourceCoverageRecord


def coverage_ratio(numerator: int, denominator: int) -> tuple[float, str | None]:
    """분모가 없거나 일부만 충족하면 readiness를 켜지 않는 측정값을 만든다."""

    if denominator <= 0:
        return 0.0, "COVERAGE_DENOMINATOR_EMPTY"
    if numerator < 0 or numerator > denominator:
        raise ValueError("COVERAGE_MEASUREMENT_INVALID")
    ratio = numerator / denominator
    return ratio, None if ratio == 1 else "COVERAGE_INCOMPLETE"


class PostgresCoverageMeasurer:
    """현재 active source와 projection을 직접 세어 수동 비율 입력을 대체한다."""

    def __init__(self, importer_dsn: str, assume_role: str | None = None) -> None:
        self._importer_dsn = importer_dsn
        self._assume_role = assume_role

    def measure(
        self,
        publication_id: UUID,
        capability: str,
        service_date_from: date | None = None,
        service_date_to: date | None = None,
        region_code: str = "JEJU_EAST",
        grid_id: str = "POC_V1",
        *,
        active: bool = False,
    ) -> SourceCoverageRecord:
        with psycopg.connect(self._importer_dsn) as connection:
            if self._assume_role:
                connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
                )
            status = "PUBLISHED" if active else "STAGED"
            source_row = connection.execute(
                """SELECT publication.source_id FROM source_admin.publication publication
                   JOIN source_admin.acquisition acquisition
                     ON acquisition.acquisition_id = publication.acquisition_id
                   LEFT JOIN source_admin.active_snapshot active
                     ON active.publication_id = publication.publication_id
                   WHERE publication.publication_id = %s AND acquisition.status = %s
                     AND (%s = false OR active.publication_id IS NOT NULL)""",
                (publication_id, status, active),
            ).fetchone()
            if source_row is None:
                reason = (
                    "COVERAGE_PUBLICATION_NOT_ACTIVE"
                    if active
                    else "COVERAGE_PUBLICATION_NOT_STAGED"
                )
                raise ValueError(reason)
            source_id = str(source_row[0])
            numerator, denominator = self._counts(
                connection,
                publication_id,
                source_id,
                capability,
                region_code,
                grid_id,
                service_date_from,
                service_date_to,
            )
        ratio, blocking_reason = coverage_ratio(numerator, denominator)
        return SourceCoverageRecord(
            capability=capability,
            coverage_ratio=ratio,
            region_code=(
                "JEJU_ALL"
                if capability in {"service_area_ready", "opening_hours_snapshot_ready"}
                else region_code
            ),
            grid_id=(
                "ALL"
                if capability in {"service_area_ready", "opening_hours_snapshot_ready"}
                else grid_id
            ),
            service_date_from=service_date_from,
            service_date_to=service_date_to,
            blocking_reason=blocking_reason,
        )

    @staticmethod
    def _counts(
        connection,
        publication_id: UUID,
        source_id: str,
        capability: str,
        region_code: str,
        grid_id: str,
        service_date_from: date | None = None,
        service_date_to: date | None = None,
    ) -> tuple[int, int]:
        if capability == "service_area_ready" and source_id == "spatial.jeju-boundary":
            count = connection.execute(
                "SELECT count(*) FROM travel_projection.service_area_boundary "
                "WHERE publication_id = %s AND ST_SRID(geometry) = 4326 "
                "AND ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry)",
                (publication_id,),
            ).fetchone()[0]
            return int(count), 1
        if capability == "place_search_ready" and source_id == "tourapi.place":
            count = connection.execute(
                "SELECT count(*) FROM travel_projection.place_fact WHERE publication_id = %s",
                (publication_id,),
            ).fetchone()[0]
            return (1 if int(count) > 0 else 0), 1
        if (
            capability == "opening_hours_snapshot_ready"
            and source_id == "tourapi.place-intro"
        ):
            if region_code != "JEJU_ALL" or grid_id != "ALL":
                raise ValueError("OPENING_HOURS_SNAPSHOT_SCOPE_INVALID")
            if service_date_from is not None or service_date_to is not None:
                raise ValueError("OPENING_HOURS_SNAPSHOT_DATE_NOT_ALLOWED")
            row = connection.execute(
                """WITH required AS (
                     SELECT place.fact_id AS place_fact_id
                     FROM travel_projection.place_fact place
                     JOIN source_admin.active_snapshot active
                       ON active.source_id = place.source_id
                      AND active.publication_id = place.publication_id
                     WHERE place.source_id = 'tourapi.place'
                   ), observed AS (
                     SELECT observation.place_fact_id
                     FROM travel_projection.place_opening_observation observation
                     WHERE observation.publication_id = %s
                   )
                   SELECT CASE
                            WHEN EXISTS (
                              SELECT 1 FROM observed
                              LEFT JOIN required USING (place_fact_id)
                              WHERE required.place_fact_id IS NULL
                            ) THEN 0
                            ELSE count(observed.place_fact_id)
                          END,
                          count(required.place_fact_id)
                   FROM required LEFT JOIN observed USING (place_fact_id)""",
                (publication_id,),
            ).fetchone()
            return int(row[0]), int(row[1])
        if capability == "bus_route_catalog_ready" and source_id == "tago.bus-route":
            row = connection.execute(
                """SELECT CASE
                            WHEN count(*) > 0 AND count(*) = count(DISTINCT fact_id) THEN 1
                            ELSE 0
                          END,
                          1
                     FROM travel_projection.bus_route_fact
                    WHERE publication_id = %s""",
                (publication_id,),
            ).fetchone()
            return int(row[0]), int(row[1])
        if capability == "opening_hours_ready" and source_id in {
            "tourapi.place-intro",
            "travel.place-hours-map",
        }:
            if region_code == "JEJU_ALL" and grid_id == "ALL":
                row = connection.execute(
                    """WITH required AS (
                         SELECT place.fact_id AS member_id
                         FROM travel_projection.place_fact place
                         JOIN source_admin.active_snapshot active
                           ON active.source_id = place.source_id
                          AND active.publication_id = place.publication_id
                         WHERE place.source_id = 'tourapi.place'
                           AND place.attributes->>'content_type_id' <> '32'
                       ), covered_rule AS (
                         SELECT DISTINCT rule.place_fact_id AS member_id
                         FROM travel_projection.place_opening_rule rule
                         WHERE (
                             rule.publication_id = %s
                             OR EXISTS (
                               SELECT 1
                               FROM source_admin.active_snapshot active
                               JOIN source_admin.publication active_publication
                                 ON active_publication.publication_id = active.publication_id
                               WHERE active.publication_id = rule.publication_id
                                 AND active_publication.source_id <> %s
                             )
                           )
                           AND rule.normalization_status = 'VERIFIED'
                           AND %s::date IS NOT NULL AND %s::date = %s::date
                           AND (rule.valid_from IS NULL OR rule.valid_from <= %s::date)
                           AND (rule.valid_to IS NULL OR rule.valid_to >= %s::date)
                           AND (
                             rule.service_day IS NULL
                             OR rule.service_day = EXTRACT(ISODOW FROM %s::date)
                           )
                       ), covered_weekly_closure AS (
                         SELECT DISTINCT closure.place_fact_id AS member_id
                         FROM travel_projection.place_weekly_closure closure
                         WHERE (
                             closure.publication_id = %s
                             OR EXISTS (
                               SELECT 1
                               FROM source_admin.active_snapshot active
                               JOIN source_admin.publication active_publication
                                 ON active_publication.publication_id = active.publication_id
                               WHERE active.publication_id = closure.publication_id
                                 AND active_publication.source_id <> %s
                             )
                           )
                           AND closure.service_day = EXTRACT(ISODOW FROM %s::date)
                           AND (closure.valid_from IS NULL OR closure.valid_from <= %s::date)
                           AND (closure.valid_to IS NULL OR closure.valid_to >= %s::date)
                       ), covered_exception AS (
                         SELECT DISTINCT exception.place_fact_id AS member_id
                         FROM travel_projection.place_schedule_exception exception
                         WHERE (
                             exception.publication_id = %s
                             OR EXISTS (
                               SELECT 1
                               FROM source_admin.active_snapshot active
                               JOIN source_admin.publication active_publication
                                 ON active_publication.publication_id = active.publication_id
                               WHERE active.publication_id = exception.publication_id
                                 AND active_publication.source_id <> %s
                             )
                           )
                           AND exception.exception_date = %s::date
                           AND exception.exception_type IN ('CLOSED', 'SPECIAL_HOURS')
                       ), covered AS (
                         SELECT member_id FROM covered_rule
                         UNION
                         SELECT member_id FROM covered_weekly_closure
                         UNION
                         SELECT member_id FROM covered_exception
                       )
                       SELECT count(covered.member_id), count(required.member_id)
                       FROM required LEFT JOIN covered USING (member_id)""",
                    (
                        publication_id,
                        source_id,
                        service_date_from,
                        service_date_from,
                        service_date_to,
                        service_date_from,
                        service_date_to,
                        service_date_from,
                        publication_id,
                        source_id,
                        service_date_from,
                        service_date_from,
                        service_date_to,
                        publication_id,
                        source_id,
                        service_date_from,
                    ),
                ).fetchone()
                return int(row[0]), int(row[1])
            row = connection.execute(
                """WITH required AS (
                         SELECT member_id
                         FROM travel_read.active_service_scope_member
                         WHERE region_code = %s AND grid_id = %s
                           AND member_type = 'PLACE' AND required
                           AND role <> 'accommodation'
                       ), covered_rule AS (
                         SELECT DISTINCT place_fact_id
                         FROM travel_projection.place_opening_rule
                         WHERE publication_id = %s AND normalization_status = 'VERIFIED'
                           AND %s::date IS NOT NULL AND %s::date = %s::date
                           AND (valid_from IS NULL OR valid_from <= %s::date)
                           AND (valid_to IS NULL OR valid_to >= %s::date)
                           AND (service_day IS NULL OR service_day = EXTRACT(ISODOW FROM %s::date))
                       ), covered_weekly_closure AS (
                         SELECT DISTINCT place_fact_id
                         FROM travel_projection.place_weekly_closure
                         WHERE publication_id = %s
                           AND service_day = EXTRACT(ISODOW FROM %s::date)
                           AND (valid_from IS NULL OR valid_from <= %s::date)
                           AND (valid_to IS NULL OR valid_to >= %s::date)
                       ), covered_exception AS (
                         SELECT DISTINCT place_fact_id
                         FROM travel_projection.place_schedule_exception exception
                         WHERE publication_id = %s
                           AND exception_date = %s::date
                           AND exception.exception_type IN ('CLOSED', 'SPECIAL_HOURS')
                       ), covered AS (
                         SELECT place_fact_id FROM covered_rule
                         UNION
                         SELECT place_fact_id FROM covered_weekly_closure
                         UNION
                         SELECT place_fact_id FROM covered_exception
                       )
                   SELECT count(covered.place_fact_id), count(required.member_id)
                   FROM required LEFT JOIN covered ON covered.place_fact_id = required.member_id""",
                (
                    region_code,
                    grid_id,
                    publication_id,
                    service_date_from,
                    service_date_from,
                    service_date_to,
                    service_date_from,
                    service_date_to,
                    service_date_from,
                    publication_id,
                    service_date_from,
                    service_date_from,
                    service_date_to,
                    publication_id,
                    service_date_from,
                ),
            ).fetchone()
            return int(row[0]), int(row[1])
        if (
            capability == "restaurant_recommendation_ready"
            and source_id == "travel.restaurant-dietary-map"
        ):
            if (
                service_date_from is None
                or service_date_to is None
                or service_date_from != service_date_to
            ):
                return 0, 3
            row = connection.execute(
                """SELECT LEAST(count(DISTINCT dietary.place_fact_id), 3), 3
                   FROM travel_projection.restaurant_dietary_fact dietary
                   JOIN travel_projection.place_fact place
                     ON place.fact_id = dietary.place_fact_id
                   JOIN source_admin.active_snapshot active
                     ON active.source_id = place.source_id
                    AND active.publication_id = place.publication_id
                   WHERE dietary.publication_id = %s
                     AND place.source_id = 'tourapi.place'
                     AND place.category = '39'
                     AND (
                       place.attributes->>'category_level_3' IS NULL
                       OR place.attributes->>'category_level_3' <> 'A05020900'
                     )
                     AND %s::date = %s::date
                     AND dietary.valid_from <= %s::date
                     AND dietary.valid_to >= %s::date
                     AND dietary.verification_expires_at >= (%s::date + INTERVAL '1 day')
                     AND (
                       cardinality(dietary.verified_free_from_allergens)
                       + cardinality(dietary.verified_excludes_foods) > 0
                     )""",
                (
                    publication_id,
                    service_date_from,
                    service_date_to,
                    service_date_from,
                    service_date_to,
                    service_date_to,
                ),
            ).fetchone()
            return int(row[0]), int(row[1])
        if capability in {"verified_entrances_ready", "accessibility_ready"} and source_id == (
            "travel.place-entrance-map"
        ):
            accessibility_clause = (
                "AND entrance_type = 'accessible' AND 'walk' = ANY(supported_modes)"
                if capability == "accessibility_ready"
                else ""
            )
            covered_sql = f"""covered AS (
                         SELECT DISTINCT place_fact_id AS member_id
                         FROM travel_projection.place_entrance
                         WHERE publication_id = %s
                           AND verification_status = 'VERIFIED'
                           {accessibility_clause}
                           AND %s::date IS NOT NULL AND %s::date = %s::date
                           AND (valid_from IS NULL OR valid_from <= %s::date)
                           AND (valid_to IS NULL OR valid_to >= %s::date)
                           AND (
                             verification_expires_at IS NULL
                             OR verification_expires_at >= (%s::date + INTERVAL '1 day')
                           )
                       )"""
            if region_code == "JEJU_ALL" and grid_id == "ALL":
                required_sql = """required AS (
                         SELECT place.fact_id AS member_id
                         FROM travel_projection.place_fact place
                         JOIN source_admin.active_snapshot active
                           ON active.source_id = place.source_id
                          AND active.publication_id = place.publication_id
                         WHERE place.source_id = 'tourapi.place'
                       )"""
                parameters = (
                    publication_id,
                    service_date_from,
                    service_date_from,
                    service_date_to,
                    service_date_from,
                    service_date_to,
                    service_date_to,
                )
            else:
                required_sql = """required AS (
                         SELECT member_id FROM travel_read.active_service_scope_member
                         WHERE region_code = %s AND grid_id = %s
                           AND member_type = 'PLACE' AND required
                       )"""
                parameters = (
                    region_code,
                    grid_id,
                    publication_id,
                    service_date_from,
                    service_date_from,
                    service_date_to,
                    service_date_from,
                    service_date_to,
                    service_date_to,
                )
            row = connection.execute(
                f"""WITH {required_sql}, {covered_sql}
                    SELECT count(covered.member_id), count(required.member_id)
                    FROM required LEFT JOIN covered USING (member_id)""",
                parameters,
            ).fetchone()
            return int(row[0]), int(row[1])
        if (
            capability == "confirmed_stop_mapping_ready"
            and source_id == "transport.stop-identity-map"
        ):
            if region_code == "JEJU_ALL" and grid_id == "ALL":
                row = connection.execute(
                    """WITH required AS (
                             SELECT stop.fact_id AS member_id
                             FROM travel_projection.bus_stop_fact stop
                             JOIN source_admin.active_snapshot active
                               ON active.source_id = stop.source_id
                              AND active.publication_id = stop.publication_id
                             WHERE stop.source_id = 'tago.bus-stop'
                           ), covered AS (
                             SELECT DISTINCT source_fact_id AS member_id
                             FROM travel_projection.stop_identity_fact
                             WHERE publication_id = %s
                               AND mapping_status = 'CONFIRMED'
                           )
                       SELECT count(covered.member_id), count(required.member_id)
                       FROM required LEFT JOIN covered USING (member_id)""",
                    (publication_id,),
                ).fetchone()
                return int(row[0]), int(row[1])
            row = connection.execute(
                """WITH required AS (
                         SELECT member_id
                         FROM travel_read.active_service_scope_member
                         WHERE region_code = %s AND grid_id = %s
                           AND member_type = 'STOP' AND required
                       ), covered AS (
                         SELECT DISTINCT canonical_stop_id AS member_id
                         FROM travel_projection.stop_identity_fact
                         WHERE publication_id = %s
                           AND mapping_status = 'CONFIRMED'
                       )
                   SELECT count(covered.member_id), count(required.member_id)
                   FROM required LEFT JOIN covered USING (member_id)""",
                (region_code, grid_id, publication_id),
            ).fetchone()
            return int(row[0]), int(row[1])
        if capability == "bus_route_stop_catalog_ready" and source_id == "tago.bus-route-stops":
            row = connection.execute(
                """WITH required AS (
                         SELECT route.fact_id
                           FROM travel_projection.bus_route_fact route
                           JOIN source_admin.active_snapshot active
                             ON active.publication_id = route.publication_id
                          WHERE route.source_id = 'tago.bus-route'
                       ), covered AS (
                         SELECT route_fact_id
                           FROM travel_projection.bus_route_stop
                          WHERE publication_id = %s
                          GROUP BY route_fact_id
                         HAVING count(*) >= 2
                       )
                       SELECT count(covered.route_fact_id), count(required.fact_id)
                         FROM required
                         LEFT JOIN covered ON covered.route_fact_id = required.fact_id""",
                (publication_id,),
            ).fetchone()
            return int(row[0]), int(row[1])
        if capability == "future_bus_planning_ready" and source_id == "jeju.bus-timetable":
            trip_date = (
                service_date_from
                if service_date_from is not None and service_date_from == service_date_to
                else None
            )
            if region_code == "JEJU_ALL" and grid_id == "ALL":
                row = connection.execute(
                    """WITH requested AS (
                         SELECT %s::date AS trip_date
                       ), service_day AS (
                         SELECT trip_date,
                                CASE
                                  WHEN EXISTS (
                                    SELECT 1
                                    FROM travel_projection.holiday_fact holiday
                                    JOIN source_admin.active_snapshot active
                                      ON active.publication_id = holiday.publication_id
                                    WHERE holiday.holiday_date = trip_date
                                      AND holiday.is_public_institution_holiday
                                  ) THEN 'HOLIDAY'
                                  WHEN EXTRACT(ISODOW FROM trip_date) = 6 THEN 'SATURDAY'
                                  WHEN EXTRACT(ISODOW FROM trip_date) = 7 THEN 'SUNDAY'
                                  ELSE 'WEEKDAY'
                                END AS day_type
                         FROM requested
                         WHERE trip_date IS NOT NULL
                       ), required AS (
                         SELECT route.fact_id AS route_fact_id
                         FROM travel_projection.bus_route_fact route
                         JOIN source_admin.active_snapshot active
                           ON active.source_id = route.source_id
                          AND active.publication_id = route.publication_id
                         WHERE route.source_id = 'tago.bus-route'
                       ), eligible_trip AS (
                         SELECT trip.route_fact_id, trip.trip_id
                         FROM travel_projection.scheduled_trip trip
                         JOIN service_day requested ON true
                         JOIN travel_projection.service_calendar calendar
                           ON calendar.publication_id = trip.publication_id
                          AND calendar.service_id = trip.service_id
                          AND calendar.day_type = requested.day_type
                          AND calendar.starts_on <= requested.trip_date
                          AND calendar.ends_on >= requested.trip_date
                         JOIN travel_projection.scheduled_stop_time stop
                           ON stop.publication_id = trip.publication_id
                          AND stop.trip_id = trip.trip_id
                         WHERE trip.publication_id = %s
                           AND (trip.timetable_effective_from IS NULL
                                OR trip.timetable_effective_from <= requested.trip_date)
                           AND (trip.timetable_effective_to IS NULL
                                OR trip.timetable_effective_to >= requested.trip_date)
                           AND NOT EXISTS (
                             SELECT 1
                             FROM travel_projection.service_calendar_exception exception
                             WHERE exception.publication_id = trip.publication_id
                               AND exception.service_id = trip.service_id
                               AND exception.exception_date = requested.trip_date
                               AND exception.exception_type = 'REMOVED'
                           )
                         GROUP BY trip.route_fact_id, trip.trip_id
                         HAVING count(stop.fact_id) >= 2
                            AND count(stop.fact_id) = count(stop.fact_id) FILTER (
                              WHERE EXISTS (
                                SELECT 1
                                FROM travel_projection.bus_route_stop route_stop
                                WHERE route_stop.publication_id = trip.publication_id
                                  AND route_stop.route_fact_id = trip.route_fact_id
                                  AND route_stop.stop_fact_id = stop.stop_fact_id
                              )
                            )
                       ), covered AS (
                         SELECT DISTINCT route_fact_id FROM eligible_trip
                       )
                       SELECT count(covered.route_fact_id), count(required.route_fact_id)
                       FROM required LEFT JOIN covered USING (route_fact_id)""",
                    (trip_date, publication_id),
                ).fetchone()
                return int(row[0]), int(row[1])
            row = connection.execute(
                """WITH requested AS (
                     SELECT %s::date AS trip_date
                   ), service_day AS (
                     SELECT trip_date,
                            CASE
                              WHEN EXISTS (
                                SELECT 1
                                FROM travel_projection.holiday_fact holiday
                                JOIN source_admin.active_snapshot active
                                  ON active.publication_id = holiday.publication_id
                                WHERE holiday.holiday_date = trip_date
                                  AND holiday.is_public_institution_holiday
                              ) THEN 'HOLIDAY'
                              WHEN EXTRACT(ISODOW FROM trip_date) = 6 THEN 'SATURDAY'
                              WHEN EXTRACT(ISODOW FROM trip_date) = 7 THEN 'SUNDAY'
                              ELSE 'WEEKDAY'
                            END AS day_type
                     FROM requested
                     WHERE trip_date IS NOT NULL
                   )
                   SELECT count(*) FILTER (
                            WHERE stop_count >= 2
                              AND matched_stop_count = stop_count
                          ), count(*)
                   FROM (
                     SELECT trip.trip_id,
                            count(DISTINCT stop.fact_id) AS stop_count,
                            count(DISTINCT stop.fact_id) FILTER (
                              WHERE EXISTS (
                                SELECT 1
                                FROM travel_projection.bus_route_stop route_stop
                                WHERE route_stop.publication_id = trip.publication_id
                                  AND route_stop.route_fact_id = trip.route_fact_id
                                  AND route_stop.stop_fact_id = stop.stop_fact_id
                              )
                            ) AS matched_stop_count
                     FROM travel_projection.scheduled_trip trip
                     JOIN service_day requested ON true
                     LEFT JOIN travel_projection.scheduled_stop_time stop
                       ON stop.publication_id = trip.publication_id
                      AND stop.trip_id = trip.trip_id
                     JOIN travel_projection.service_calendar calendar
                       ON calendar.publication_id = trip.publication_id
                      AND calendar.service_id = trip.service_id
                      AND calendar.day_type = requested.day_type
                      AND calendar.starts_on <= requested.trip_date
                      AND calendar.ends_on >= requested.trip_date
                     WHERE trip.publication_id = %s
                       AND (trip.timetable_effective_from IS NULL
                            OR trip.timetable_effective_from <= requested.trip_date)
                       AND (trip.timetable_effective_to IS NULL
                            OR trip.timetable_effective_to >= requested.trip_date)
                       AND NOT EXISTS (
                         SELECT 1
                         FROM travel_projection.service_calendar_exception exception
                         WHERE exception.publication_id = trip.publication_id
                           AND exception.service_id = trip.service_id
                           AND exception.exception_date = requested.trip_date
                           AND exception.exception_type = 'REMOVED'
                       )
                     GROUP BY trip.trip_id
                   ) measured""",
                (trip_date, publication_id),
            ).fetchone()
            return int(row[0]), int(row[1])
        if capability in {
            "fare_policy_ready",
            "bus_fare_policy_ready",
        } and source_id == "jeju.bus-fare-policy":
            if service_date_from is None or service_date_to is None:
                return 0, 2
            count = connection.execute(
                """SELECT count(DISTINCT fare_class)
                   FROM travel_projection.bus_fare_policy_fact
                   WHERE publication_id = %s
                     AND fare_class IN ('STANDARD', 'EXPRESS')
                     AND effective_from <= %s
                     AND (effective_to IS NULL OR effective_to >= %s)""",
                (publication_id, service_date_to, service_date_from),
            ).fetchone()[0]
            return int(count), 2
        if capability in {
            "fare_policy_ready",
            "taxi_fare_policy_ready",
        } and source_id == "jeju.taxi-fare-policy":
            if service_date_from is None or service_date_to is None:
                return 0, 1
            count = connection.execute(
                """SELECT count(*)
                   FROM travel_projection.taxi_fare_policy_fact
                   WHERE publication_id = %s AND vehicle_type = 'STANDARD'
                     AND effective_from <= %s
                     AND (effective_to IS NULL OR effective_to >= %s)""",
                (publication_id, service_date_to, service_date_from),
            ).fetchone()[0]
            return int(count), 1
        raise ValueError(f"COVERAGE_MEASUREMENT_UNSUPPORTED:{capability}")
