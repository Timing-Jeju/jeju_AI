# 테스트 규칙

## First RED

공개 동작이나 불변조건을 설명하는 가장 작은 실패 테스트를 먼저 만든다. 테스트 함수명은 영문 snake_case, 첫 docstring은 한글로 쓴다. `pytest.mark.parametrize` case에는 `id="한글 시나리오"`를 둔다.

외부 API의 일반 테스트는 고정 fixture와 fake adapter를 사용한다. live smoke는 별도 marker와 명시적 환경변수로만 실행한다. API key, authorization header, raw body가 실패 출력에 나타나지 않아야 한다.

## 자동 검사

`scripts/check_korean_test_descriptions.py`는 동기·비동기 `test_` 함수 docstring의 한글 포함, 빈 값, TODO를 검사한다. 예외는 함수 바로 위의 `# korean-test-description-waiver: 이유`만 허용한다.

## 보고 형식

```text
상태: Pass | Fail | Partial
테스트: 한글 시나리오명
최초 RED:
예상 RED 실패:
최소 GREEN:
검증 근거:
검증 공백:
잔여 위험:
```
