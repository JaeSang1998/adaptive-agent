# ADR-002: Defense-in-Depth 보안 계층 설계

**상태**: 채택  
**날짜**: 2026-04-12

---

## 문제

LLM이 생성한 코드를 실행할 때, 악의적이거나 결함 있는 코드로부터 호스트 시스템을 보호해야 한다.

## 검토한 대안

### A. sys.modules 런타임 차단

금지 모듈을 `sys.modules`에서 제거/교체하여 import를 런타임에 차단.

**기각 이유**:
- Python 3.x에서 `sys.modules` 조작은 brittle (Python issue #31642)
- 허용 모듈(`pathlib`, `tempfile`, `sqlite3`)이 내부적으로 `os`에 의존 → `os` 차단 시 연쇄 실패
- AST 수준 차단이 더 일찍, 더 안정적으로 동작

### B. resource.setrlimit (메모리/CPU 하드 제한)

subprocess에서 `setrlimit`으로 리소스를 제한.

**기각 이유**:
- macOS에서 비결정적 동작 (Python issue #34602)
- `timeout=30초`가 CPU 고갈에 대해 이미 충분
- Production 경로는 cgroup (Linux) 또는 Docker

### C. Container/nsjail/Bubblewrap

Claude Code 방식의 OS 수준 격리.

**기각 이유**:
- 프로젝트 범위를 초과하는 인프라 의존성
- 로컬 CLI 도구에서 Docker 필수는 UX 저하
- Production 경로로 문서화 (README 보안 모델)

## 결정

3계층 defense-in-depth 설계를 채택한다.

```
Layer 1: AST 정적 검증 (validator.py, 5 단계 — 전부 AST 기반)
  - 구문 검사 (ast.parse)
  - def run(input: dict) -> dict 존재 확인
  - import whitelist (20개 순수 연산 모듈)
  - 금지 함수 호출 탐지 (ast.Call 노드):
      open, eval, exec, compile, __import__,
      getattr, setattr, delattr, globals, locals, vars
  - 구조 검사 (class 정의 + dunder 속성 접근 차단)
  - import를 코드 실행 전에 차단 → stdlib 내부 의존성 문제 없음
  - 모든 검사는 AST 노드 기반이라 docstring/주석 false positive 없음

Layer 2: subprocess 프로세스 격리 (runner.py)
  - 별도 Python 인터프리터에서 실행
  - cwd=tmpdir (파일시스템 접근 제한)
  - timeout=30초 (CPU 고갈 방지)
  - 출력 30KB 제한 (context rot 방지)
  - Layer 1 우회(간접 호출 등)를 catch

Layer 3: Built-in 도구 승인 (builtin.py, registry.py)
  - 파일 쓰기, 셸 명령, 네트워크 접근에 사용자 승인
  - run_bash 3단계 위험도 분류 (normal/warn/danger)
  - web_fetch SSRF 방지 (사설 IP 차단)
  - 생성 코드가 아닌 built-in 도구 경유 접근도 통제
```

## 귀결: `suggested_file` 간접 파일 출력 계약

Layer 1 이 `open()` 을 AST 수준에서 차단하므로, 생성 코드는 **파일을 직접 쓸 수 없다.**
그러나 데이터 분석의 흔한 결과물 (CSV 정리, JSON 리포트, SQLite 스냅샷) 은 파일 저장이
필수. 두 요구사항을 양립시키기 위해 **간접 파일 출력 계약**을 둔다.

생성 코드가 파일 저장이 필요하면 `run()` 의 반환 dict 에:

```python
return {
    "result": ...,
    "suggested_file": {"path": "out.json", "content": "..."},
    # 또는
    "suggested_files": [{"path": "a.json", ...}, {"path": "b.json", ...}],
}
```

core 가 이 키를 감지하면 `write_file` built-in 을 통해 저장 — 따라서 Layer 3
approval flow 에 자동으로 걸린다 (사용자 승인 없으면 쓰지 않음). binary 파일은
`encoding: "base64"` 로 함께 반환.

이 계약은 AST whitelist 의 결과로서 **defense-in-depth 의 귀결**이고, 단순히 편의
기능이 아니다 — 이것이 없으면 "안전한 생성 코드" 와 "유용한 파일 출력" 이 상호
배타적이 된다.

## 알려진 한계 (의도적 수용)

- 간접 호출 (`f = getattr; f(...)`) → Layer 1 우회 가능, Layer 2가 catch
- subprocess 네트워크 격리 없음 → import 차단으로 대응, production은 네임스페이스 격리
- 메모리 무제한 → timeout이 대부분 catch, production은 cgroup

## 참고

- Checkmarx (2024): "The Glass Sandbox: Complexity of Python Sandboxing"
- Python issue #31642: sys.modules None blocking의 한계
- Python issue #34602: macOS setrlimit 비결정성
- Anthropic (2025): Claude Code Sandboxing (Seatbelt + Bubblewrap + proxy)
