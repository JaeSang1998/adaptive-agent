# Adaptive AI Agent

자연어 요청을 받아 필요한 도구를 **스스로 생성·검증·실행·수정·저장**하는 CLI 기반 AI Agent.

Agent 추상화 라이브러리 없이 LLM API를 직접 호출하여 핵심 루프를 수동 구현했습니다.

### TL;DR

- **순수 tool loop** — Planner(LLM)가 도구 호출 또는 텍스트 응답을 결정하는 단일 루프. 프레임워크 없이 ~700줄 오케스트레이터.
- **`generate_code` 통합 파이프라인** — 코드 생성(LLM 1회) → 5-layer AST 정적 검증 → subprocess 격리 실행 → 자가 수정(최대 3회). `run(input: dict) -> dict` 고정 계약.
- **Defense-in-depth 보안** — AST import 화이트리스트 + AST 기반 금지 호출 탐지 + subprocess 격리(timeout 30s, cwd=tmpdir, 출력 30KB cap) + built-in 도구 승인 콜백.
- **51개 eval 시나리오** — 11종 verifier + failure_attribution(planner/builder/repairer 자동 귀인) + train/test split. 프롬프트 최적화 루프로 train 32/32 달성.

---

### 핵심 설계 결정 (요약)

대안을 검토한 뒤 의도적으로 선택한 결정 3개. 각 ADR 에 trade-off 와 인용 근거가 있다.

- **[ADR-001 · Observation Masking 컨텍스트 압축](docs/decisions/001-context-compaction.md)** — LLM 요약 호출 없이 오래된 도구 결과의 본문만 마스킹하는 2-stage 압축. JetBrains 2025 실측 (masking > LLM 요약: +2.6% solve, −52% cost) 인용.
- **[ADR-002 · Defense-in-Depth 보안](docs/decisions/002-defense-in-depth.md)** — AST 정적 검증 + subprocess 격리 + 도구 승인 콜백의 3계층. 단일 강한 boundary 대신 layered 방어를 선택한 이유와 production 경로 (Docker / seccomp) 명시.
- **[ADR-003 · 통합 `generate_code` 파이프라인](docs/decisions/003-unified-code-generation.md)** — `run_code` (일회성) + `create_tool` (재사용) 분리를 의도적으로 통합. CodeAct (Wang et al. ICML 2024), Voyager 선례 인용.

---

### 알려진 한계 (정직하게)

이 부분은 [§7](#7-한계--개선-방향) 에 더 자세히 있지만, 평가자가 먼저 알고 시작했으면 하는 것 3개:

- **도구 검색이 키워드 기반** — semantic search (embedding) 미구현. 도구 수백 개 수준까지 충분, 수천 개 이상에서는 부족. Voyager 식 embedding 검색은 개선 방향에 명시.
- **단일 회 eval (pass@k 미적용)** — 시나리오당 1회 실행. gemma 계열 nondeterminism 환경에서 pass rate variance 감지 안 됨. README 한계 섹션의 가장 큰 약점.
- **subprocess 격리는 secondary defense** — Codex CLI 수준 OS-native sandbox (Seatbelt / Bubblewrap+Landlock) 까지는 안 감. README 보안 섹션과 ADR-002 에 production 확장 경로 명시.

---

## 목차

1. [퀵스타트](#1-퀵스타트)
2. [사용 예시](#2-사용-예시)
3. [아키텍처 개요](#3-아키텍처-개요)
4. [레이어별 상세](#4-레이어별-상세)
   - [4.1 Agent Layer](#41-agent-layer)
   - [4.2 LLM Layer](#42-llm-layer)
   - [4.3 Tools Layer](#43-tools-layer)
   - [4.4 Prompt Layer](#44-prompt-layer)
   - [4.5 Eval Layer](#45-eval-layer)
5. [모듈 디렉토리](#5-모듈-디렉토리)
6. [보안 모델](#6-보안-모델)
7. [한계 · 개선 방향](#7-한계--개선-방향)

---

## 1. 퀵스타트

### 사전 요구사항

- Python 3.11+
- [Ollama](https://ollama.com/) 설치
- LLM 모델 (기본: `gemma4:26b`)

### 설치

```bash
uv sync                       # 권장 (uv.lock 잠금)
# 또는
pip install -e .
```

### LLM 설정

```bash
ollama pull gemma4:26b                            # 기본 모델 (256K context)

mkdir -p ~/.adaptive-agent
cp config.example.yaml ~/.adaptive-agent/config.yaml
# config.yaml 의 model 값만 바꾸면 다른 Ollama 모델도 사용 가능
```

설정 우선순위: `~/.adaptive-agent/config.yaml` → 환경변수 (`AGENT_LLM_*`) → CLI 인자.

### 실행

```bash
adaptive-agent                # REPL 모드 (CLI 진입점)
/tools                        # REPL 내에서 등록된 도구 목록
exit                          # 종료
```

### 테스트 · 평가

```bash
pytest                                  # 단위·통합 테스트
python -m eval                          # 전체 시나리오 실행
python -m eval --filter csv_analysis    # 단일 시나리오
```

---

## 2. 사용 예시

다음 4가지 행동 패턴을 모두 데모합니다.

### 2.1 데이터 분석 — 도구 자동 생성

```
> 아래 JSON에서 hp가 100 이상인 몬스터 이름과 평균 hp를 알려줘.
  [{"name":"Goblin","hp":80},{"name":"Orc","hp":150},{"name":"Dragon","hp":300}]

  ⏳ [1/2] 도구 스펙 생성 중...
  ⏳ [2/2] Python 코드 생성 중...
  🔧 도구 생성 중: hp_filter
  ✓ hp_filter 완료

조건을 만족하는 몬스터는 Orc, Dragon이며 평균 hp는 225.0입니다.
```

### 2.2 자가 수정 — 실패 → 진단 → 수정 → 재실행

```
> buggy_tool.py 를 수정하고 [1,2,3,4,5] 데이터로 통계를 계산해줘

  ▶ read_file 완료
  📝 코드 생성 중: 통계 계산
  ✗ stats_tool 실패: NameError: name 'statistics' is not defined
  🔄 도구 수정 중: stats_tool (시도 1/3)
  ✓ stats_tool 완료

수정 완료. 평균: 3.0, 중앙값: 3, 표준편차: 1.58
```

### 2.3 모호한 요청 — 재질문 (Human-in-the-loop)

```
> 데이터 정리해줘

  어떤 데이터인지, 어떤 방식으로 정리할지 알려주시겠어요?
    1. CSV 파일의 중복 제거
    2. JSON 데이터 필터링
    3. 기타
  > 1
  어떤 CSV 파일인가요?
  > sales_data.csv
  ...
```

### 2.4 도구 영구 저장 — 다음 세션에서 재사용

```
  도구 'hp_filter' 을 저장하면 다음 세션에서도 사용할 수 있습니다.
  저장할까요? (y/이름 입력/n): y
  ✅ 도구 'hp_filter' 저장 완료. (~/.adaptive-agent/tools/hp_filter/)
```

다음 세션에서 자동 로드:

```
> hp가 200 이상인 몬스터만 골라줘. [{"name":"Slime","hp":50},{"name":"Dragon","hp":300}]

  ▶ 기존 도구 실행 중: hp_filter   ← 이전 세션에서 저장한 도구 재사용
  ✓ hp_filter 완료

Dragon (hp: 300) 1마리입니다.
```

---

## 3. 아키텍처 개요

### 핵심 루프

```
사용자 입력
    │
    ▼
Planner (LLM) → JSON action 결정
    │
    ├─ ask_user ──────→ 재질문 → 답변 대기
    ├─ respond ───────→ 최종 응답 출력
    ├─ use_tool ──────→ 기존 도구 실행
    ├─ generate_code ─→ 코드 생성 → 검증 → 실행
    └─ repair_tool ───→ 코드 수정 → 재실행
                           │
    성공 → 결과 기반 응답 → 도구 저장 여부 확인
    실패 → 자가 수정 루프 (최대 3회)
```

### 코드 생성 파이프라인

`generate_code`는 하나의 통합된 코드 생성 액션입니다:

```
Builder (LLM) → run(input) -> dict Python 코드 생성
    ↓
Validator (AST) → 구문, run() 존재, import 화이트리스트, 금지 함수 호출, 구조 검증
    ↓
Registry → 세션 도구로 등록
    ↓
Runner (subprocess) → 격리 실행 (timeout 30s, cwd=tmpdir, 출력 30KB cap)
    ↓
실패 시 → Repairer (LLM) → 수정 → 재실행 (최대 3회)
    ↓
성공 시 → 저장 여부 확인 → AST에서 run() docstring 추출 → manifest 자동 생성
```

### 레이어 맵

| 레이어 | 책임 | 모듈 |
|---|---|---|
| **Agent** | 루프 오케스트레이션, 상태, 대화 컨텍스트 | [src/adaptive_agent/agent/](src/adaptive_agent/agent/) |
| **LLM** | LLM 호출, native tool calling, fallback 파싱 | [src/adaptive_agent/llm/](src/adaptive_agent/llm/) |
| **Tools** | 도구 등록·생성·검증·실행·수정·저장 | [src/adaptive_agent/tools/](src/adaptive_agent/tools/) |
| **Prompts** | 역할별 프롬프트, KV cache 최적화 | [src/adaptive_agent/llm/prompts.py](src/adaptive_agent/llm/prompts.py) |
| **Eval** | 비대화형 시나리오 검증, 프롬프트 최적화 | [eval/](eval/) |

---

## 4. 레이어별 상세

### 4.1 Agent Layer

[src/adaptive_agent/agent/](src/adaptive_agent/agent/) — [core.py](src/adaptive_agent/agent/core.py) · [planner.py](src/adaptive_agent/agent/planner.py) · [session.py](src/adaptive_agent/agent/session.py) · [compaction.py](src/adaptive_agent/agent/compaction.py) · [events.py](src/adaptive_agent/agent/events.py)

Agent loop이 LLM 결정 → 도구 실행 → 관찰 → 다음 결정을 안정적으로 반복해야 합니다. 무한루프·컨텍스트 폭발·반복 행동이 가장 흔한 실패 모드입니다.

<details>
<summary><b>선택</b></summary>

- **프레임워크 없이 순수 tool loop** — LangGraph/LangChain 배제. 모든 상태 전이가 [core.py](src/adaptive_agent/agent/core.py) 한 파일에서 추적 가능.
- **`max_steps=15`** — 무한루프 방지. step 내에서 generate_code는 턴당 최대 1회로 제한.
- **Observation Masking 컨텍스트 압축** — 오래된 도구 결과의 본문을 마스킹하고 헤더(성공/실패)만 보존. LLM 요약 호출 대비 비용 0, 정확도 +2.6% (JetBrains 2025). 근거: [docs/decisions/001-context-compaction.md](docs/decisions/001-context-compaction.md)
- **stuck loop 감지** — 동일 (tool, input) 3회 누적 시 Planner에 경고.
- **Human-in-the-loop 3가지 접점** — 모호한 요청(`ask_user`), 위험 작업(승인 콜백), 도구 저장(저장 제안). 과도한 승인 요청으로 UX가 망가지는 것을 방지하기 위해 이 3곳에만 한정.
- **`generate_code` explicit input contract** — Planner 는 데이터를 항상 explicit input key 로 전달. 파일은 `{"키": {"$ref": "<path>"}}` 로 참조 (core 가 `session.observations` 에서 dehydrate, JSON/CSV 는 자동 parse), 사용자 메시지에 inline 된 데이터는 literal 값. Planner 가 명시한 키만 builder 에 전달되므로, 멀티 파일 생성에서 "마지막 read 만 전달되는" 실패 모드가 원천 봉쇄됨.
- **Planner timeout fallback** — Ollama 로컬 지연 시 planner 호출이 timeout 되면 observation aggressive masking 후 1회 재시도.
</details>

<details>
<summary><b>결과</b></summary>

51개 eval 시나리오에서 step limit 또는 stuck loop으로 인한 무한루프 0건. observation masking으로 긴 세션에서도 컨텍스트 안정.
</details>

<details>
<summary><b>한계</b></summary>

복잡한 멀티스텝 작업의 성공률은 여전히 Planner LLM의 판단력에 의존. step 15 내에서 끝나지 않는 작업은 중단됨.
</details>

<details>
<summary>구성 상세</summary>

```
AgentCore
  ├─ Planner          → LLM → PlannerDecision (tool_call | text)
  ├─ Session          → messages, temp_tools, plan, recent_actions
  ├─ ToolBuilder      → 코드 생성 (tools layer 위임)
  ├─ ToolRunner       → subprocess 격리 실행
  ├─ ToolRepairer     → traceback 기반 자가 수정
  └─ Registry         → 도구 lookup
```
</details>

---

### 4.2 LLM Layer

[src/adaptive_agent/llm/](src/adaptive_agent/llm/) — [client.py](src/adaptive_agent/llm/client.py) · [schemas.py](src/adaptive_agent/llm/schemas.py) · [json_parser.py](src/adaptive_agent/llm/json_parser.py) · [code_extractor.py](src/adaptive_agent/llm/code_extractor.py)

Agent의 모든 의사결정이 LLM 호출에 의존합니다. 호출 안정성(파싱 실패율, timeout)이 곧 Agent 안정성입니다.

<details>
<summary><b>선택</b></summary>

- **단일 provider (Ollama)** — 현 범위에서 multi-provider 추상화는 복잡성만 추가. 전환 지점은 `LLMClientProtocol` 인터페이스 한 곳에 격리.
- **Native tool calling + prompt-based fallback 이중 경로** — Ollama v0.20.3+의 native tool calling을 우선 사용하되, 미지원 모델에서 자동 fallback. 첫 호출에서 capability auto-detection.
- **Phase별 token/timeout 분리** — planner(4096 tok / 90s), code(8192 tok / 120s), repair(8192 tok / 120s). 단일 설정 대비 P99 latency 단축.
- **다단계 JSON 파싱** — 전체 파싱 → 코드블록 추출 → 중첩 JSON 추출 → `json_repair` healing. fallback 경로에서 다양한 모델 출력을 수용.
</details>

<details>
<summary><b>결과</b></summary>

native 모드에서 도구 결과가 `role: "tool"` + `tool_name`으로 전달, 사용자 메시지와 혼동 0. capability detection은 세션 시작 시 1회만 수행.
</details>

<details>
<summary><b>한계</b></summary>

fallback 모드에서는 도구 결과를 `role: "user"`로 보내고 접두사 `[도구 ...]`로만 구분합니다. 이는 **구조적 role 오염**으로, 도구 결과에 악의적 텍스트가 포함되면 사용자 메시지로 오인될 수 있습니다. native tool calling 모드에서는 role이 완전 분리되어 이 문제가 없으므로, 가능하면 native 모드 사용을 권장합니다. `json_repair` 외부 의존성은 이 fallback 안정성을 위한 유일한 비순정 의존성.
</details>

<details>
<summary>구성 상세</summary>

```
OllamaClient
  ├─ chat(messages, tools?, format?, phase)
  │     ├─ 시도 1: native tool calling (tools 파라미터 포함)
  │     │            → 4xx → capability=False 캐시
  │     └─ 시도 2: prompt-based fallback
  │                 → JSON 추출 → 코드블록 파싱 → json_repair healing
  ├─ phase별 token / timeout
  │     ├─ planner   : 4096 tok / 90s
  │     ├─ code      : 8192 tok / 120s
  │     └─ repair    : 4096 tok / 60s
  └─ LLMResponse(content, thinking?, usage, tool_calls)
```
</details>

---

### 4.3 Tools Layer

[src/adaptive_agent/tools/](src/adaptive_agent/tools/) — [registry.py](src/adaptive_agent/tools/registry.py) · [builder.py](src/adaptive_agent/tools/builder.py) · [validator.py](src/adaptive_agent/tools/validator.py) · [runner.py](src/adaptive_agent/tools/runner.py) · [repair.py](src/adaptive_agent/tools/repair.py) · [persistence.py](src/adaptive_agent/tools/persistence.py) · [builtin.py](src/adaptive_agent/tools/builtin.py) · [errors.py](src/adaptive_agent/tools/errors.py)

Agent가 LLM으로 생성한 임의의 Python 코드를 실행합니다. 안전한 격리, 일관된 인터페이스, 실패 시 복구 경로가 핵심입니다.

<details>
<summary><b>선택 — 핵심</b></summary>

- **`run(input: dict) -> dict` 고정 계약** — 모든 생성 도구가 동일 인터페이스. Runner(stdin JSON → run → stdout JSON)가 단순해지고, 어떤 도구든 동일 방식으로 실행·테스트·저장 가능.
- **통합 코드 생성 + AST 기반 manifest** — Builder가 Python 코드만 생성 (LLM 1회). 저장 시 `run()` docstring을 AST로 추출하여 manifest 자동 생성. 초기에는 2단계(manifest → code) 파이프라인이었으나, production 시스템(Claude Code, CodeAct, Voyager) 분석 후 통합. 근거: [docs/decisions/003-unified-code-generation.md](docs/decisions/003-unified-code-generation.md)
- **subprocess 격리 실행** — tempdir 안에서 실행, timeout 30s, 출력 30KB cap. 크래시·무한루프가 Agent 프로세스를 죽이지 않음. ~100ms 오버헤드 vs 안전성.
</details>

<details>
<summary><b>선택 — 보조</b></summary>

- **5-layer 정적 검증** — (1) AST 구문 (2) `def run()` 존재 (3) import 화이트리스트 (4) AST 기반 금지 호출 (`eval`/`exec`/`open`/`compile`/`__import__`/`getattr`/`setattr`/`delattr`/`globals`/`locals`/`vars`) (5) 구조 검사 (class/dunder 차단). defense-in-depth이며 subprocess 격리가 2차 방어선. 모든 검사는 AST 노드 기반이라 docstring/주석 false positive 없음.
- **Repair (최대 3회)** — traceback + 직전 시도 에러를 누적 전달. 같은 실수 반복 차단.
- **3계층 레지스트리 (builtin > persistent > session)** — persistent 도구는 context windowing 최대 20개만 노출, 초과분은 `list_tools`로 탐색.
- **`python_exec` 의도적 제외** — built-in에 임의 Python 실행을 두면 Agent가 도구 생성을 하지 않음. **Built-in = 인프라**, **Generated = 로직**. 이 경계가 있어야 도구 "설계" 능력이 발휘됨.
</details>

<details>
<summary><b>결과</b></summary>

`generate_code` 하나의 통합 액션으로 모든 코드 생성을 처리. 실행 성공 후 사용자 승인 시 저장. manifest는 코드의 `run()` docstring에서 AST로 자동 추출. Production 시스템(Claude Code, CodeAct, Voyager)과 동일한 "실행 먼저, persistence는 나중에" 패턴. 도구 저장소는 `~/.adaptive-agent/tools/{name}/`에 manifest.json + tool.py로 영구 보존.
</details>

<details>
<summary><b>한계</b></summary>

AST 기반 검사는 변수에 함수를 할당하는 간접 호출(예: `f = getattr; f(...)`)을 탐지 불가 — subprocess 격리에 의존. 문자열 패턴 매칭은 주석/문자열 내부도 매칭하여 false positive 가능 (의도적으로 보수적 수용).
</details>

<details>
<summary>Built-in 도구 8종</summary>

| 도구 | 설명 | 승인 필요 |
|------|------|-----------|
| `read_file` | 파일 읽기 (offset/limit) | - |
| `write_file` | 파일 쓰기 (mkdir parents) | ✓ |
| `list_directory` | 디렉토리 목록 | - |
| `edit_file` | 파일 부분 편집 (find-replace) | ✓ |
| `glob_search` | 패턴 파일 검색 | - |
| `grep_search` | 내용 정규식 검색 | - |
| `run_bash` | 셸 명령 실행 | ✓ (세션 캐시: 한 번 승인 시 동일 도구 자동 허용) |
| `web_fetch` | URL → text/JSON | ✓ |

부작용이 있는 도구만 승인 요구. 코드 생성·실행은 subprocess 격리이므로 사전 승인 없이 진행.
</details>

<details>
<summary>구성 상세</summary>

```
ToolsLayer
  ├─ Registry (3계층)
  │     ├─ Built-in    : 항상 사용 가능, 8개
  │     ├─ Persistent  : ~/.adaptive-agent/tools/ 에서 세션 시작 시 자동 로드
  │     └─ Session     : 현재 세션 한정, 저장 안 하면 종료 시 폐기
  │     · context windowing: persistent ≤20개만 full description 노출
  │
  ├─ Builder (단일 단계)
  │     └─ Python 코드 생성          (free-text + ```python 블록, LLM 1회)
  │        → 저장 시 AST 기반 manifest 자동 생성 (extract_manifest_from_code)
  │        → input_schema 도 last_success_input 타입 + run() docstring Args 로 자동 추론
  │
  ├─ Validator (5-layer, 전부 AST 기반)
  │     ├─ 1. AST 구문 검사
  │     ├─ 2. def run() 존재 확인
  │     ├─ 3. import 화이트리스트 (20개 순수 연산 모듈)
  │     ├─ 4. 금지 함수 호출 (open/eval/exec/compile/__import__/getattr/setattr/delattr/globals/locals/vars)
  │     └─ 5. 구조 검사 (class 정의, dunder 속성 접근 차단)
  │
  ├─ Runner (subprocess)
  │     ├─ stdin JSON → wrapper → tool.run() → stdout JSON
  │     ├─ timeout 30s, cwd=tmpdir, 출력 30KB cap
  │     └─ 에러 코드 enum (TIMEOUT, EXECUTION_FAILED 등)
  │
  ├─ Repairer
  │     └─ traceback + previous_errors 누적 → 수정 코드 → Validator → Runner
  │
  └─ Persistence
        └─ ~/.adaptive-agent/tools/{name}/ (tool.py + manifest.json)
```
</details>

---

### 4.4 Prompt Layer

[src/adaptive_agent/llm/prompts.py](src/adaptive_agent/llm/prompts.py) (단일 파일, ~200 lines) — LLM Layer 내부 모듈이지만, 프로젝트에서 **가장 많은 시행착오를 거친 부분**이므로 별도 섹션으로 분리합니다.

Planner 프롬프트가 Agent의 모든 행동을 결정합니다. 규칙이 부족하면 반복 호출·누락·무의미한 도구 선택, 과하면 context 압박. 특히 로컬 모델(gemma4:26b)은 규칙 준수가 불안정하여 **어떤 규칙을 어떤 형태로 넣느냐**가 성공률을 좌우합니다.

<details>
<summary><b>선택</b></summary>

- **XML 태그 구획화** — `<identity>`, `<rules>`, `<examples>`, `<output_format>` 등으로 섹션 분리. LLM이 위계를 인식하고, 프로그래밍적 파싱/압축도 가능.
- **KV cache 최적화 — 정적 상단, 동적 하단** — 불변 콘텐츠(identity, rules, examples)를 상단, 동적 조립(available_tools, current_plan, workspace)을 하단에 배치.
- **공유 상수 단일 출처** — `ALLOWED_IMPORTS`, `FORBIDDEN_OPS`가 [validator.py](src/adaptive_agent/tools/validator.py) 에서 파생. 프롬프트와 검증 규칙이 영원히 동기화.
- **Negative/Positive example 쌍** — "하지 마"만 쓰면 모델이 대안을 못 찾아 멈추거나 다른 위반을 생성. 금지 + 올바른 대안을 항상 쌍으로 제시.
- **Repair에 previous_errors 누적** — 시도 회차(1/2/3)와 직전 에러를 모두 전달하여 같은 실수 반복 방지.
</details>

<details>
<summary><b>실제 프롬프트 이터레이션 사례</b></summary>

규칙 19개와 example 8+개는 한 번에 설계된 것이 아니라, eval 시나리오 실패를 관찰하고 **가설 → 수정 → 검증** 루프를 반복하여 도달한 결과입니다.

**사례 1 — `deprecated_scan`: grep 패턴 오해**
- **관찰**: Planner가 `grep_search`에 `def .*deprecated` 패턴을 사용. "deprecated 함수"를 이름에 `deprecated`가 포함된 함수 정의로 오해하여 `@deprecated` 데코레이터를 놓침.
- **가설**: "deprecated 함수"라는 자연어 표현이 데코레이터가 아니라 함수명으로 해석됨.
- **수정**: Planner few-shot에 `@deprecated` + `context_after=1` negative/positive example 추가. `grep_search` description에 "마커(@deprecated 등)는 데코레이터 패턴으로 검색" 규칙 추가.
- **결과**: targeted eval PASS.

**사례 2 — `read_file` 후 조기 종료 패턴**
- **관찰**: `tool_repair`, `think_first`, `repair_no_repeat` 시나리오에서 Planner가 `read_file`로 코드를 읽은 뒤 파일 내용을 설명만 하고 `generate_code`로 이어가지 않음.
- **가설**: Planner가 "파일 내용을 알았으니 작업 완료"로 판단. 규칙에 "읽기 → 실행" 연결이 명시되지 않았음.
- **수정**: 규칙 추가 — "코드/스크립트 파일의 버그 수정, 계산 실행, 결과 저장 요청은 `read_file` 설명으로 끝내지 말고 이어서 `generate_code`로 실제 처리하세요."
- **결과**: 3개 시나리오 모두 PASS. full suite 회귀 없음.

**사례 3 — `cross_session`: Builder 입력 우선순위 혼동**
- **관찰**: 코드 생성 후 도구가 `filtered_count=0`을 반환. Builder가 structured input보다 raw `_data`를 우선하여, 실제 입력 데이터가 도구에 전달되지 않음.
- **가설**: Builder prompt에 입력 우선순위가 미명시.
- **수정**: Builder prompt에 "비즈니스 키 우선, `_data`는 보조/폴백" 계약 명시.
- **결과**: targeted eval PASS.

</details>

<details>
<summary><b>결과</b></summary>

반복 이터레이션으로 규칙 19개 + example 8+개에 도달. 초기 train 시나리오 29/32 → 32/32 달성.
</details>

<details>
<summary><b>한계</b></summary>

gemma4:26b에서는 프롬프트 총량이 커질수록 규칙 후반부 무시 경향이 관찰됨. 규칙·예시를 추가할 때마다 기존 규칙 준수가 흔들릴 수 있어, 추가와 제거를 동시에 고려해야 합니다. 상용 모델(GPT-4, Claude)에서는 이 문제가 완화되지만 로컬 모델에서는 구조적 한계.
</details>

<details>
<summary>구성 상세</summary>

```
prompts.py
  ├─ 공유 상수 (validator.py에서 파생)
  │     ├─ ALLOWED_IMPORTS
  │     └─ FORBIDDEN_OPS
  ├─ Planner Prompt (_PLANNER_STATIC + dynamic 조립)
  │     ├─ <identity>  — 역할 정의
  │     ├─ <rules>     — 19개 규칙 (핵심/도구 선택/응답 판단/실행 제약)
  │     ├─ <output_format> — JSON tool call 또는 plain text
  │     ├─ <examples>  — 8+개 worked example (negative/positive 쌍)
  │     ├─ <message_conventions> — [도구 ...] 접두사 규약
  │     └─ (dynamic) <available_tools>, <current_plan>, <workspace>
  ├─ Builder Manifest Prompt — constrained decoding
  ├─ Builder Code Prompt — free-text + constraints
  └─ Repair Prompt — traceback + previous_errors + attempt#
```
</details>

---

### 4.5 Eval Layer

[eval/](eval/) — [harness.py](eval/harness.py) · [scenario.py](eval/scenario.py) · [runner.py](eval/runner.py) · [verifiers.py](eval/verifiers.py) · [metrics.py](eval/metrics.py) · [scenarios/](eval/scenarios/)

프롬프트 변경이 Agent 행동에 어떤 영향을 미치는지 재현 가능하게 측정해야 합니다. 수동 테스트만으로는 회귀를 잡을 수 없습니다.

<details>
<summary><b>선택</b></summary>

- **비대화형 시나리오 실행** — YAML로 정의된 시나리오(inputs, setup_files, verify, ask_user_responses)를 Harness가 자동 실행. CI에서 결정론적 재현 가능 (LLM 비결정성은 별도 영역).
- **51개 시나리오, 11종 verifier** — `response_contains`, `response_not_contains`, `file_exists`, `file_content_contains`, `event_occurred`, `final_success`, `tool_called`, `tool_not_called`, `no_declare_only`, `plan_progress`, `json_schema`. 카테고리: 데이터 분석 / 자가 수정 / 세션 간 재사용 / HITL / 엣지 케이스 / 인지 행동 / OOD.
- **failure_attribution** — 실패 원인을 `planner`(잘못된 도구 선택, stuck, step 초과) / `builder`(repair 없이 실패) / `repairer`(repair 후에도 실패) 중 하나로 자동 귀인. **어느 프롬프트를 고쳐야 할지** 즉시 식별.
- **train / test split** — 시나리오에 split 필드를 두어 프롬프트 튜닝용과 최종 평가용 분리.
- **multiprocess 격리** — 한 시나리오 크래시가 다른 시나리오에 영향 없음. 워커별 로깅 디렉토리 분리.
</details>

<details>
<summary><b>프롬프트 최적화 루프</b></summary>

프롬프트를 모델에 fitting시키기 위해 **Coding Agent (Claude Code 등)과 반복 루프**를 돌렸습니다.

```
프롬프트 최적화 루프

 ┌──────────────────────────────────────────────┐
 │ 1. targeted eval 실행 (실패 시나리오 필터)     │
 │    python -m eval --filter <failing_scenarios> │
 │                                                │
 │ 2. failure_attribution으로 원인 귀인            │
 │    planner? builder? repairer?                 │
 │                                                │
 │ 3. 해당 프롬프트 수정                           │
 │    few-shot 추가, 규칙 추가/수정, 예시 보강      │
 │                                                │
 │ 4. targeted eval 재실행 → PASS 확인             │
 │                                                │
 │ 5. full suite 회귀 검증                         │
 │    python -m eval                              │
 │                                                │
 │ 6. 새로운 실패 발견 시 → 1로 돌아감              │
 └──────────────────────────────────────────────┘
```

각 수정 단계별로 이전 스냅샷 대비 pass-rate 차이를 추적하여, 어떤 프롬프트 변경이 어떤 시나리오에 영향을 미쳤는지 체계적으로 검증했습니다.
</details>

<details>
<summary><b>결과</b></summary>

반복 최적화로 32개 train 시나리오 기준 29/32 → 32/32 달성. 이후 OOD + 난이도 확장으로 51개까지 증가. failure_attribution 덕분에 "어디를 고칠지" 판단에 드는 시간이 크게 줄었습니다 — 실패 시나리오를 열어보기 전에 planner/builder/repairer 중 어느 프롬프트를 봐야 하는지 바로 알 수 있음.
</details>

<details>
<summary><b>한계</b></summary>

- **LLM 비결정성** — 동일 프롬프트도 실행마다 결과가 달라질 수 있음. 현재 시나리오당 1회 실행으로, 통계적 신뢰를 위해서는 N회 반복 + pass@k 측정이 필요.
- **eval이 프롬프트와 동시 진화** — 시나리오를 추가하면서 프롬프트를 수정하면, 개선이 프롬프트 덕인지 시나리오가 쉬워진 덕인지 구분이 어려워짐. train/test split으로 완화하고 있으나 완전한 분리는 아님.
</details>

<details>
<summary>구성 상세</summary>

```
EvalLayer
  ├─ Scenario (YAML)
  │     ├─ id, name, category, difficulty, split (train/test)
  │     ├─ inputs              — 사용자 요청 시퀀스
  │     ├─ setup_files         — 작업 디렉토리에 복사할 파일
  │     ├─ setup_tools         — 사전 등록 도구
  │     ├─ ask_user_responses  — HITL 자동 답변 큐
  │     ├─ config_overrides    — agent config 오버라이드
  │     └─ verify              — VerifyCheck 리스트
  │
  ├─ Verifiers (11종)
  │     ├─ response_contains / response_not_contains
  │     ├─ file_exists / file_content_contains
  │     ├─ event_occurred / final_success
  │     ├─ tool_called / tool_not_called
  │     └─ no_declare_only / plan_progress / json_schema
  │
  ├─ Metrics (ScenarioMetrics)
  │     ├─ passed, total_steps, latency_seconds, llm_calls
  │     ├─ tools_created / tools_reused / builtin_tools_used
  │     ├─ repair_attempts, builder_errors, repair_history
  │     └─ failure_attribution: planner | builder | repairer
  │
  └─ Runner → multiprocess worker 풀 (격리 + 로깅 + timeout)
```

시나리오 카테고리:

| 카테고리 | 예시 | 검증 목표 |
|---|---|---|
| 데이터 분석 | csv_analysis, json_to_sqlite, statistics | 도구 생성·실행 정확도 |
| 자가 수정 | tool_repair, repair_keyerror, repair_off_by_one | Repairer 효과 |
| 세션 간 재사용 | cross_session, persistent_tool_reuse | Persistence 동작 |
| HITL | edge_ambiguous, ask_user_missing_data | ask_user 타이밍 |
| 엣지 케이스 | edge_empty, edge_impossible, contradictory | 견고성 |
| 인지 행동 | think_first, multi_step_plan, result_summary | 메타 도구 사용 |
| OOD | ood_read_first_orders, ood_unicode_books | 일반화 |
</details>

---

## 5. 모듈 디렉토리

<details>
<summary>전체 디렉토리 트리</summary>

```
adaptive-agent/
├── src/
│   ├── main.py               # CLI REPL + 이벤트 로깅 + 저장 제안
│   ├── config.py             # 설정 로딩 (YAML + 환경변수 + CLI)
│   ├── agent/
│   │   ├── core.py           # 상태기계 오케스트레이터
│   │   ├── planner.py        # LLM 행동 결정
│   │   ├── session.py        # 대화 히스토리 + 도구 성공 추적
│   │   ├── compaction.py     # Observation masking 컨텍스트 압축
│   │   └── events.py         # JSONL 이벤트 로깅
│   ├── llm/
│   │   ├── client.py         # LLMClientProtocol + OllamaClient
│   │   ├── prompts.py        # 역할별 프롬프트 템플릿
│   │   ├── schemas.py        # Pydantic 스키마
│   │   ├── json_parser.py    # JSON 추출 + healing
│   │   └── code_extractor.py # 코드 추출 + AST 검증
│   └── tools/
│       ├── registry.py       # 3계층 도구 레지스트리
│       ├── builder.py        # 코드 생성 (LLM 1회)
│       ├── runner.py         # subprocess 격리 실행
│       ├── repair.py         # 자가 수정
│       ├── validator.py      # 5-layer AST 기반 정적 검증
│       ├── persistence.py    # 영구 저장/로딩
│       ├── builtin.py        # 내장 도구 8종
│       └── errors.py         # 에러 코드
├── eval/
│   ├── harness.py            # 비대화형 실행 wrapper
│   ├── scenario.py           # YAML 스키마 + 로더
│   ├── runner.py             # multiprocess 시나리오 러너
│   ├── verifiers.py          # 11종 검증 함수
│   ├── metrics.py            # 이벤트 기반 메트릭
│   ├── scenarios/            # 51개 YAML 시나리오
│   └── fixtures/             # 테스트 데이터
├── tests/                    # pytest 단위·통합 테스트
└── docs/decisions/           # ADR (Architecture Decision Records)
```
</details>

<details>
<summary>의존성 방향 (단방향, 순환 없음)</summary>

```
main.py → agent/core.py
             ├→ agent/planner.py → llm/
             ├→ agent/session.py
             ├→ agent/compaction.py
             ├→ tools/builder.py  → llm/, tools/validator.py
             ├→ tools/runner.py
             ├→ tools/repair.py   → llm/, tools/validator.py, tools/runner.py
             ├→ tools/registry.py → tools/persistence.py
             └→ tools/builtin.py  → llm/
```
</details>

---

## 6. 보안 모델

**신뢰 경계**: LLM이 생성한 코드 ↔ 호스트 시스템.

| 위협 | 방어 | 잔여 위험 | Production 경로 |
|------|------|-----------|-----------------|
| 코드 인젝션 (eval/exec/import) | 5-layer AST 정적 검증 + subprocess 격리 | 간접 호출 우회 | seccomp 시스템콜 필터링 |
| 네트워크 접근 | 네트워크 모듈 import 차단 + `web_fetch` 강제 | subprocess에 네트워크 격리 없음 | 네트워크 네임스페이스 |
| 리소스 고갈 | 30s timeout + 30KB 출력 cap | 메모리 무제한 | cgroup 제한 |
| 파일시스템 탈출 | cwd=tmpdir + open() AST 차단 | built-in 경유 접근 | nsjail/gVisor |
| 프롬프트 인젝션 | native: role 분리, fallback: 메시지 규약 | LLM 판단 의존 | input validation |
| 컨텍스트 폭발 | observation masking + 출력 cap + windowing | 극단적 세션 | token budget 강제 |

---

## 7. 한계 · 개선 방향

### 현재 한계

- **도구 검색이 키워드 기반** — semantic search (embedding) 미구현. 도구 수백 개 수준까지는 충분하나 수천 개 이상에서는 부족.
- **Planner 품질 의존** — 복잡한 멀티스텝의 성공률은 LLM 판단력에 좌우됨.
- **JSON 파싱 불안정 (fallback 모드)** — 모델에 따라 간헐적 실패. native 모드에서는 해당 없음.
- **프롬프트 최적화의 통계적 신뢰** — 시나리오당 1회 실행. pass@k 측정으로 확장 필요.

### 개선 방향

임팩트 순:

1. **Container 기반 실행** — Docker/nsjail로 cgroup + seccomp 완전 격리. 보안 모델의 잔여 위험 대부분을 해소하는 가장 높은 임팩트 개선.
2. **pass@k 측정** — 시나리오당 N회 반복 실행으로 통계적 신뢰 확보. 현재 최적화 루프의 가장 큰 약점.
3. **Builder/Repair에도 native tool calling** — 현재 Planner만 native. Builder/Repair도 전환하면 코드 생성 파싱 실패율 추가 감소.
4. **Embedding 기반 도구 검색** — 도구 수천 개 이상 시 필요. 현재 수백 개 규모에서는 키워드 매칭으로 충분.
5. **도구 unit test 자동 생성** — `last_success_input/output`으로 regression test 자동 생성.
6. **Multimodal 입력 / Prompt caching** — 기능 확장 + 비용 최적화. nice-to-have.

### 의도적으로 포함하지 않은 것

- **Multi-provider LLM** — `LLMClientProtocol` 인터페이스에 전환 지점 격리로 충분.
- **비동기 (async)** — 단일 사용자 CLI에서 동기가 더 간결. async 전환 시 상용 API prompt caching, streaming 토큰 출력, 독립 I/O 도구 병렬 실행 등을 도입할 수 있으나, 현재 범위에서 refactor 비용 대비 가치 낮음. 전환 지점은 `LLMClientProtocol`에 격리.
- **플러그인 아키텍처** — 프레임워크가 아닌 하나의 에이전트. 확장성보다 응집도 우선.
- **컨테이너 샌드박스** — subprocess 격리가 현 범위에 적합. [6. 보안 모델](#6-보안-모델) 에 Production 경로로 문서화.
