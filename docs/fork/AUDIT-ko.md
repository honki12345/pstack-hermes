# pstack-hermes 포크 감사 · 비교 · 고급 위임 기록

대상: `iap/pstack-hermes` master `e6beab0`. 패키지는 `cursor/plugins` pstack
`c47b128`(v0.15.5)에서 변환한 것입니다. 포크는 `honki12345/pstack-hermes`이고,
작업 브랜치는 `feature/hermes-delegation-routing`입니다.

## 1. 판정: 보안상 그대로 써도 되고, 기능상으로는 고쳐야 쓸 수 있다

악성 코드, 비밀값 유출 경로, 프롬프트 주입, 숨은 유니코드는 발견되지 않았습니다.
배포본(`dist`)은 고정된 업스트림 원본을 변환기로 다시 빌드한 결과와 바이트 단위로
같습니다. 출처 기록과 시각을 뜻하는 파일 3개만 다르며, 이는 실행으로 확인했습니다.
다만 포팅 과정에서 Hermes의 실제 동작과 맞지 않는 지시가 들어가, 핵심 기능이
**오류 없이 조용히 약해집니다**.

### 확인 방법

1. 업스트림 `c47b128:pstack` 158개 파일과 배포본을 전부 diff했습니다(60개 파일,
   +1071/−459). 바뀐 줄을 모두 읽었습니다.
2. 변환기 `tools/convert.py` 2,632줄을 끝까지 읽은 뒤 격리된 환경에서 다시 빌드했습니다.
   해시 `eddf739557d575bf`가 기록과 일치했고, 산출물도 배포본과 동일했습니다.
3. CI 워크플로 8개, 복합 액션, 패치 4개, 서명 키, 에이전트 지시 문서, 커밋 이력,
   액션 SHA 고정 여부를 공식 태그와 대조했습니다.
4. Hermes v0.23.0 소스(`tools/delegate_tool*.py`, `agent/delegation_provider.py`)와 대조하고,
   실제 `delegate_task` 호출로 동작을 확인했습니다.

### 보안 (치명적·높음 등급 없음)

| 등급 | 항목 | 근거 |
|---|---|---|
| 중간 | `publish-dist.yml`이 master에 push될 때마다 dist 브랜치에 자동 커밋·push함 | 포크에서는 워크플로가 등록되지 않은 상태라 사용자가 켜기 전에는 실행되지 않음(확인함) |
| 중간 | `patches/hermes-fork-scanner-plugin-root.patch`가 Hermes 스캐너의 파일 수 검사를 우회함 | 문서상 "참고용"이고 v0.23.0에는 적용되지 않음. 적용하지 말 것 |
| 낮음 | `keys/allowed_signers`의 키 출처를 외부에서 검증할 수 없음 | 기존 태그 7개는 모두 서명이 없음 |
| 낮음 | `model-drift-watch.yml`이 매주 openrouter를 조회하고 이슈를 작성함 | 포크에서는 기본으로 꺼져 있음 |
| 정보 | `pull_request_target`이 PR 코드를 checkout하지 않음, `run:` 블록에 `${{ }}` 직접 삽입 0건, 서드파티 액션 6종 모두 SHA 고정 | 안전함 |

### 기능상 이상한 점 (포트에서 생긴 문제)

| 항목 | 사실 | 영향 |
|---|---|---|
| `delegate_task`에 `model` 전달 | core 예약 키라 **오류 없이 무시**됨. `gpt-5.6-sol-max`를 지정해도 자식 에이전트는 `claude-opus-5-5`로 실행됨(실행 확인) | interrogate·arena·architect의 다중 모델 패널이 모두 단일 모델로 줄어듦 |
| `delegate_task`에 `readonly: true` 전달 | 선언되지 않은 키라 **작업이 거부**됨(실행 확인) | how, interrogate 검토자의 생성이 실패함 |
| "readonly는 파일 쓰기만 막고 MCP는 유지" | Hermes에 작업별 readonly·toolsets 기능이 아예 없음(소스 확인) | 사실과 다른 서술 |
| `/loop`를 `hermes cron`으로 치환하면서 "이 세션을 다시 깨운다"고 서술 | cron은 매번 **새 에이전트 실행**임(`--continuity`는 직전 출력만 주입) | 맥락 없는 반복 작업이 남을 위험. shipping/babysit의 반복 점검이 자동 병합과 연결될 수 있음 |
| orchestrate의 "Always local execution unless the task needs this machine" | `cloud`를 `local`로 기계적으로 바꾸면서 논리가 깨짐 | 지시 혼선 |
| autopilot의 "A local root arms … A local root uses the existing wake chain instead" | 같은 원인으로 문장이 자기모순이 됨 | 지시 혼선 |
| 배포되는 `config/models.json` | 이 파일을 읽는 스킬이 없음. 내용도 `qwen-2.5-7b-instruct`, `meta/muse-spark-1.2-contributor` 같은 이질적인 slug | 사용하지 않는 설정이 혼란을 줌 |
| 원칙 문구 변경(T8, G1) | `fix-root-causes`와 `test-behavior-not-implementation`의 금지 목록이 완화됨. feature 플레이북에 "스레드 안에서 직접 구현해도 된다"는 예외가 추가됨 | 플랫폼 적응이 아니라 원저자 원칙의 의미를 바꾼 것 |
| `github` 스택 공급자(T15) | `gh pr list --state all --limit 100`으로 저장소 전체 PR을 대상으로 루트가 정확히 하나이고 단절이 없어야 함 | PR 고정(`--prs`) 없이는 실제 저장소에서 대부분 실패할 것으로 보임(코드 판독 추론, 실행하지 않음) |
| 남는 참조 | `deslop`, `control-ui`, `control-cli`가 각각 5개 파일에서 참조되지만 배포본에 없음 | 해당 단계는 실행할 수 없음 |
| 업스트림과의 차이 | 고정 시점은 v0.15.5, 현재 업스트림은 v0.15.36(커밋 79개 차이). 신규 스킬 12개가 없음 | 최신 개선 사항이 빠짐 |

## 2.1 `pstack-claude` 등과 비교한 보완점

| 보완점 | pstack-claude | 포트 원본 | 이 포크 |
|---|---|---|---|
| 역할별로 실제 다른 모델로 라우팅 | `Agent` 도구의 `model`과 effort별 agent | 무시됨 | **완료**: `pstack-delegation` 백엔드 |
| 역할별 reasoning effort | `opus @xhigh` | 없음 | **완료**: `<model>@<effort>`, 객체 경로 |
| 모델 시트 검증기 | `check-sheet.sh` | 없음 | **완료**: `routing.py --check` |
| 런타임별 도구 대응표 | `references/*-tools.md` | 본문을 그 자리에서 치환 | **완료**: `poteto-mode/references/hermes-delegation.md` |
| 버려진 에이전트를 멈춘 뒤 교체 | poteto-mode 규칙 | 없음 | **완료**: 계약 문서의 `stop`/`steer` |
| 업스트림 추적 | 최신 `c742dd6`에 동기화 | `c47b128` | 권장: 고정 시점 갱신. 앵커 감사에서 남는 표현 정리 필요 |
| 포크 정책 변경의 파일별 기록 | `tools/forks.json`(why/since/upstream) | 변환 단계 설명뿐 | 권장: T8, G1 등 의미 변경을 등록부로 분리 |
| cursor-team-kit 포함 | deslop, fix-ci, fix-merge-conflicts 등 | 참조만 남음 | 권장: deslop 포함, 또는 참조를 조건부로 처리 |
| 세션 시작 라우팅 훅 | SessionStart hook | 없음 | 권장: Hermes 플러그인 hook으로 poteto-mode 안내 주입 |
| 네이티브 에이전트 등록(poteto-agent) | `.claude/agents` | 없음 | 부분 완료: `profile:<name>` 경로로 페르소나 프로필 지정 가능 |

## 2.2 고급 위임 시스템 설계

Hermes의 `delegation.backend` 공급자 플러그인 경계를 사용합니다. core의 수명주기
(격리, 깊이, 동시 실행, 백그라운드 완료, `stop`/`steer`, `output_schema`)는 그대로
두고, 공급자는 "이 작업을 어떤 경로로 보낼지"만 결정합니다.

- 작업별 인자로 `pstack_role`(18개 역할 열거), `seat`(패널 순번), `access`를 선언합니다.
- 경로 결정 순서는 `pstack-models.json` → `aliases` → 업스트림 기본값입니다. 별칭이 없는
  기본값은 부모 모델로 보내고, 그 사실을 라벨에 표시합니다.
- 경로 결과는 model, provider, reasoning_effort, service_tier, 그리고 profile 실행 중 하나입니다.
  결과의 `route.label`이 실제 실행 모델의 기록이 됩니다.
- 실패는 작업 단위로 닫힙니다. 범위를 벗어난 seat, 알 수 없는 키·effort, 잘못된
  JSON은 그 작업만 거부하고, 고칠 방법을 메시지로 안내합니다.
- 읽기 전용은 Hermes가 강제할 수 없으므로 "권고 지시 + 부모의 `git status` 사후
  검증"이라는 계약으로 명시합니다.
- 스킬 쪽은 변환 단계 `H1`이 맡습니다. `H1_MAP`의 앵커 21개를 감사하고, 금지 문자열
  5개가 다시 생기면 빌드를 실패시킵니다.

### 검증

| 범위 | 결과 |
|---|---|
| 순수 라우팅 단위 테스트(저장소 venv) | 14개 통과 |
| 실제 Hermes v0.23.0 로더 통합 테스트 | 3개 통과: 스키마 인자와 바이트 안정성, 좌석 3개가 모델 3개로 빌드됨, 4번째 좌석만 거부, `readonly` 거부 |
| 저장소 전체 pytest, ruff, check_docs, validate.py(static·gold manifest·gold load) | 모두 통과 |
| `hermes plugins doctor`(패키지, 플러그인) | OK |
| 실제 프로필에 설치해 실제 모델 호출 | **미실행**. 사용자 프로필 설정 변경이 필요해 승인 대상 |
