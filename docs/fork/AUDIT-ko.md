# pstack-hermes 포크 감사 · 비교 · 고급 위임 기록

이 문서는 `honki12345/pstack-hermes` 포크의 감사 기록입니다. 현재 기준은 `develop`의
`dcdcf21`입니다. 패키지는 `cursor/plugins` pstack
`c742dd61706fbacc1b95f1a20b7b988cf096ce6c`(v0.15.36)에서 변환했습니다.
`uv run --frozen python tools/check_pins.py`는 `ci.yml`의 `UPSTREAM_PIN`으로 같은 SHA를
보고하고, `plugin.json`의 `version`은 `0.15.36`입니다.

원 감사는 원래 포트 `iap/pstack-hermes` master `e6beab0`과 그 포트가 고정한 업스트림
`c47b128`(v0.15.5)을 대상으로 했습니다. 그때의 판정과 표는 2장에 그대로 남겼습니다.
2026-11-08에 업스트림 `origin/main`을 다시 받았을 때 pin 이후 `pstack/` 아래 커밋은
0개였습니다(`git log c742dd6..origin/main -- pstack/`).

남은 일과 담당은 [BACKLOG-ko.md](BACKLOG-ko.md)에 있습니다.

## 1. 원 감사 항목의 현재 상태

원 감사의 지적과 권장은 17건입니다. 2장 보안 표에서 등급이 중간이나 낮음인 4행, 2장 기능 표
11행, 그리고 원 감사 때 비교 표(지금의 3장)에 "권장"으로 남긴 2행을 더한 수입니다
(`git show dcdcf21:docs/fork/AUDIT-ko.md`의 30-33행, 40-50행, 62행, 64행). 이 중 14건이
해소됐고 3건이 남았습니다. 해소 표는 같은 커밋으로 고친 항목을 한 행으로 묶어 10행입니다.
"원 감사 행" 열은 각 행이 묶은 원 감사 표의 행 수이고, 이 열을 더하면 보안 표 4행과 기능 표
10행으로 14입니다. 미해결 표 3행은 기능 표 1행과 비교 표 2행이고, 모두 백로그 항목이 담당을
정합니다. 명령은 저장소 루트에서 실행합니다. `<upstream>`은 `cursor/plugins` 클론 경로입니다.

### 해소된 항목

| # | 원 감사 항목 | 원 감사 행 | 해소 커밋 | 다시 확인하는 방법과 결과 |
|---|---|---|---|---|
| 1 | 보안 표의 `publish-dist.yml` 자동 push, 스캐너 우회 패치, 출처를 검증할 수 없는 서명 키, `model-drift-watch.yml` 주간 이슈 | 보안 4 | `9521d12` | `ls .github/workflows`는 `ci.yml`과 `validate-gold.yml`만 보여 줍니다. `patches/`와 `keys/`는 없습니다. |
| 2 | `delegate_task`에 넘긴 `model`이 오류 없이 무시되어 다중 모델 패널이 한 모델로 줄어듦 | 기능 1 | `a703a0d`, `8458f13` | 스킬은 공식 advanced-delegation `model_map`의 `option`으로 경로를 고릅니다(`skills/poteto-mode/references/hermes-delegation.md:33-85`). 처음 해소한 `a703a0d`, `8458f13`은 `pstack_role`을 썼고, 4장 끝의 전환 절이 바뀐 경위를 적습니다. `` grep -rnE -e '^- `model`:' -e 'Set `model` to' skills ``는 0건입니다. 같은 패턴을 `git grep`으로 해소 전 `8458f13^`에서 찾으면 9건입니다. `uv run --frozen pytest -q tools/tests/test_pstack_delegation_routing.py`는 14개 통과입니다. |
| 3 | `readonly: true`를 넘기면 작업이 거부됨 | 기능 1 | `8458f13` | `` grep -rnF '`readonly`: `true`' skills ``는 0건입니다. 같은 패턴을 `git grep`으로 해소 전 `8458f13^`에서 찾으면 4건입니다. 변환기의 `H1_FORBIDDEN`(`tools/convert.py:1163-1174`)은 이 문자열이 다시 생기면 빌드를 실패시킵니다. |
| 4 | "readonly는 파일 쓰기만 막고 MCP는 유지"라는 사실과 다른 서술 | 기능 1 | `8458f13` | 계약 문서의 읽기 전용 절이 권고 지시와 `git status` 사후 검증으로 바뀌었습니다(`hermes-delegation.md:87-99`). `H1_FORBIDDEN`이 `MCP access is unaffected`를 금지합니다. |
| 5 | `/loop`를 cron으로 바꾸면서 "이 세션을 다시 깨운다"고 서술 | 기능 1 | `8458f13` | `grep -rn 're-wakes this session' skills`는 0건이고 `H1_FORBIDDEN`이 이 문구를 금지합니다. 계약 문서는 cron 틱을 새 실행으로 다룹니다(`hermes-delegation.md:101-116`). 스킬 문서에서 `/loop`는 `skills/poteto-help/SKILL.md:109`의 설명 한 곳뿐입니다(`grep -rn '/loop' skills --include=*.md`). |
| 6 | orchestrate의 "Always local execution ..."와 autopilot의 "A local root ..." 자기모순 | 기능 2 | `8458f13` | `git grep -l 'A local root' e6beab0 -- skills`는 2개 파일을, `Always local execution`은 1개 파일을 찾습니다. 현재 `skills/`에서는 둘 다 0건입니다. |
| 7 | 아무 스킬도 읽지 않는 `config/models.json`에 이질적인 slug가 있음 | 기능 1 | `63b83c1` | 모든 역할이 `inherit-parent`인 템플릿입니다. `grep -c -e qwen -e muse-spark config/models.json`은 0입니다. `docs/MODELS.md:43-46`이 템플릿이라고 밝힙니다. |
| 8 | 원칙 문구 완화(T8)와 feature 플레이북의 직접 구현 예외(G1) | 기능 1 | `63b83c1` | `principle-fix-root-causes`와 `principle-test-behavior-not-implementation`의 `SKILL.md`가 pin의 업스트림 원문과 바이트 단위로 같습니다(`cmp <(git -C <upstream> show c742dd6:pstack/skills/<이름>/SKILL.md) skills/<이름>/SKILL.md`). `feature.md`는 업스트림과 12행 하나만 다르고, 그 차이는 H1 계약에 등록한 standing-by 응답 적응 문장입니다. |
| 9 | `github` 스택 공급자(T15)가 저장소 전체 PR을 대상으로 함 | 기능 1 | `7d3d6e9`, `6194aec` | 업스트림 #1043이 `scripts/orch/`를 지웠고, `7d3d6e9`가 T15와 T16 패스를 뺐습니다(`tools/convert.py:320-322`). 재빌드 커밋 `6194aec`가 생성본의 `orch/`를 지웠습니다(`git log --diff-filter=D --oneline -- skills/poteto-mode/scripts/orch`). `ls skills/poteto-mode/scripts`에 `orch/`가 없습니다. |
| 10 | 고정 시점이 v0.15.5로 업스트림보다 79커밋 뒤처짐 | 기능 1 | `7d3d6e9`, `6194aec` | `check_pins.py` 출력과 `.build-provenance.txt`의 `source_commit`이 `c742dd6...`으로 같습니다. 앵커 감사는 147/147, 0 dead입니다(`.build-provenance.txt:31`). 스킬은 47개에서 58개로 늘었습니다. |

### 미해결 항목

| 원 감사 항목 | 원 감사 행 | 현재 근거 | 백로그 |
|---|---|---|---|
| `deslop`, `control-ui`, `control-cli`가 패키지 밖 스킬을 가리킴 | 기능 1 | `7d3d6e9`가 "설치된 경우" 조건을 붙였습니다(`skills/poteto-mode/SKILL.md:29`, `:31`). 조건이 없는 `/deslop` 줄이 두 개 남았습니다. `skills/poteto-mode/playbooks/multi-phase-plan.md:59`와 `skills/poteto-mode/playbooks/opening-a-pr.md:34`입니다. `opening-a-pr.md`는 9행에만 조건이 있습니다. | B15 |
| 업스트림 의미 변경을 파일별로 남기는 등록부가 없음 | 비교 1 | `tools/forks.json`이 없습니다. 의미 변경 결정은 이 문서 5장의 서술뿐입니다. | B13 |
| 세션 시작 라우팅 훅이 없음 | 비교 1 | `hermes-plugin/`에는 위임 공급자만 등록하는 `pstack-delegation` 하나뿐입니다(`hermes-plugin/pstack-delegation/__init__.py:104-105`). | B14 |

### 포크가 고칠 수 없는 Hermes 한계

Hermes v0.23.0에는 작업별 읽기 전용이 없고, cron 틱은 언제나 새 실행입니다. 둘 다
Hermes 본체 동작이라 위 표에 넣지 않았습니다. H1 계약
(`skills/poteto-mode/references/hermes-delegation.md:87-116`)이 두 한계에 맞춘 절차를
정합니다.

### 현재 head에서 다시 본 보안 사실

- 워크플로에 `pull_request_target`이 없습니다(`grep -rn pull_request_target .github`, 0건).
- 서드파티 액션은 `astral-sh/setup-uv`와 `oven-sh/setup-bun` 두 종류이고, 모두 커밋 SHA로
  고정했습니다.
- 워크플로와 복합 액션의 `run:` 블록 25개에 `${{ }}` 직접 삽입이 없습니다(YAML을 읽어
  `run:` 값을 검사).
- CI 재현성 검사는 매 실행에서 pin으로 패키지를 다시 빌드해 커밋된 트리와 비교합니다
  (`.github/workflows/ci.yml:112-121`).

원 감사의 나머지 보안 검토(변환기 전체 판독, 재빌드 해시 대조, 액션 SHA와 공식 태그 대조)는
현재 head에서 다시 하지 않았습니다.

## 2. 원 감사 기록 (`e6beab0` 기준)

이 장은 원래 포트를 처음 감사했을 때의 판정과 표입니다. 각 항목의 현재 상태는 1장에
있습니다.

### 보안상 그대로 써도 되지만 기능은 고쳐야 쓸 수 있었다

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

## 3. `pstack-claude` 등과 비교한 보완점

| 보완점 | pstack-claude | 포트 원본 | 이 포크 |
|---|---|---|---|
| 역할별로 실제 다른 모델로 라우팅 | `Agent` 도구의 `model`과 effort별 agent | 무시됨 | 완료. 공식 advanced-delegation `model_map` 경로(처음에는 `pstack-delegation` 백엔드, 지금은 퇴역 예정) |
| 역할별 reasoning effort | `opus @xhigh` | 없음 | 완료. `<model>@<effort>`, 객체 경로 |
| 모델 시트 검증기 | `check-sheet.sh` | 없음 | 완료. `tools/model_map_options.py`가 역할 패널을 경로표로 바꾸며 어긋난 역할을 거부(이전 `routing.py --check`) |
| 런타임별 도구 대응표 | `references/*-tools.md` | 본문을 그 자리에서 치환 | 완료. `poteto-mode/references/hermes-delegation.md` |
| 버려진 에이전트를 멈춘 뒤 교체 | poteto-mode 규칙 | 없음 | 완료. 계약 문서의 `stop`/`steer` |
| 업스트림 추적 | 최신 `c742dd6`에 동기화 | `c47b128` | 완료. `c742dd6`(v0.15.36)로 재고정, 앵커 147/147, 신규 스킬 11개 적응 |
| 포크 정책 변경의 파일별 기록 | `tools/forks.json`(why/since/upstream) | 변환 단계 설명뿐 | 없음. 백로그 B13 |
| cursor-team-kit 포함 | deslop, fix-ci, fix-merge-conflicts 등 | 참조만 남음 | 조건부 완료. 참조에 "설치된 경우"를 붙였고 외부 코드는 들여오지 않음. 조건 없는 줄은 백로그 B15 |
| 세션 시작 라우팅 훅 | SessionStart hook | 없음 | 없음. 백로그 B14 |
| 네이티브 에이전트 등록(poteto-agent) | `.claude/agents` | 없음 | 부분 완료. `profile:<name>` 경로로 페르소나 프로필 지정 가능 |

## 4. 고급 위임 시스템 설계

Hermes의 `delegation.backend` 공급자 플러그인 경계를 사용합니다. core의 수명주기
(격리, 깊이, 동시 실행, 백그라운드 완료, `stop`/`steer`, `output_schema`)는 그대로
두고, 공급자는 "이 작업을 어떤 경로로 보낼지"만 결정합니다.

- 작업별 인자로 `pstack_role`(18개 역할 열거), `seat`(패널 순번), `access`를 선언합니다.
- 경로 결정 순서는 `pstack-models.json`, `aliases`, 업스트림 기본값입니다. 별칭이 없는
  기본값은 부모 모델로 보내고, 그 사실을 라벨에 표시합니다.
- 경로 결과는 model, provider, reasoning_effort, service_tier, 그리고 profile 실행 중 하나입니다.
  결과의 `route.label`이 실제 실행 모델의 기록이 됩니다.
- 실패는 작업 단위로 닫힙니다. 범위를 벗어난 seat, 알 수 없는 키·effort, 잘못된
  JSON은 그 작업만 거부하고, 고칠 방법을 메시지로 안내합니다.
- 읽기 전용은 Hermes가 강제할 수 없으므로 "권고 지시 + 부모의 `git status` 사후
  검증"이라는 계약으로 명시합니다.
- 스킬 쪽은 변환 단계 `H1`이 맡습니다. `H1_MAP`의 앵커를 감사하고, 금지 문자열
  9개(`H1_FORBIDDEN`)가 다시 생기면 빌드를 실패시킵니다. 앵커는 `8458f13`에서 21개였고
  현재는 23개입니다(`tools/convert.py:1067`).

`uv run --frozen python -c "import sys; sys.path.insert(0, 'tools'); import convert; print(len(convert.H1_MAP), len(convert.H1_FORBIDDEN))"`는
`23 9`를 출력합니다.

### 검증

| 범위 | 결과 |
|---|---|
| 순수 라우팅 단위 테스트(저장소 venv) | 14개 통과 |
| 실제 Hermes v0.23.0 로더 통합 테스트 | 3개 통과. 스키마 인자와 바이트 안정성, 좌석 3개가 모델 3개로 빌드됨, 4번째 좌석만 거부, `readonly` 거부 |
| 저장소 전체 pytest, ruff, check_docs, validate.py(static·gold manifest·gold load) | 모두 통과. `dcdcf21`의 `uv run --frozen pytest -q`는 193개 통과 |
| `hermes plugins doctor`(패키지, 플러그인) | OK |
| 실제 모델 호출(검증 전용 프로필 `pstacklab`, `3cf3d43`) | 통과. 1회 호출의 seat 0/1/2가 `claude-opus-5-5`, `gpt-6-astra-900k`(`profile:reviewer`), `claude-sonnet-5-5[1m]`(부모)로 실행됨. 1회성 실행은 자식 총량 한도 `delegation.oneshot_max_children`(기본 2)를 패널 좌석 수 이상으로 올려야 3석이 실행됨. 이 한도가 동시 실행 수가 아니라 실행 전체의 총량이라는 정정은 `0cecf88` |

### 공식 `model_map`으로 전환

Hermes 공식 플러그인 `advanced-delegation`(카탈로그 pin `e404b68`)이 같은 공급자 경계 위에
`delegation.backend: model_map`을 제공합니다. 경로표 하나(`config.yaml`의
`delegation.backends.model_map.options`)를 pstack 작업과 일반 위임이 함께 쓰도록 이 포크도 그쪽으로
옮겼습니다.

- 작업별 인자는 `option` 하나입니다. 이름과 한 줄 설명만 모델에 보이고, 목록 밖 이름은 core가
  자식을 만들기 전에 거부합니다. `pstack_role`, `seat`, `access`는 선언되지 않은 키라 거부됩니다.
- 역할 18개는 경로 `implement`, `explore`, `quick`, `deep`과 `panel-*`(좌석마다 하나)로 묶입니다.
  일반 작업용으로 `research`와 기본 경로 `general`이 있습니다. 대응표는
  `tools/model_map_options.py`의 `ROLE_ROUTES`가 정본이고, 테스트가 계약 문서의 표와 대조합니다.
- `setup-pstack`은 `pstack-models.json` 대신 `config.yaml`에 경로를 씁니다. 생성기는 역할 패널을
  경로 블록으로 바꾸고, 프로필이 직접 추가한 경로는 남깁니다.
- `H1_FORBIDDEN`에 `pstack_role`, `seat: n`, `access: read-only`, `pstack-models.json`을 더했고,
  `validate.py`도 앞의 셋을 생성 스킬에서 찾으면 실패합니다.
- `pstack-delegation`은 저장소에 남겨 두고 퇴역 예정으로 표시했습니다. 되돌릴 때
  `delegation.backend: pstack`으로 바꾸면 스킬은 `option`을 보내지 않고 부모 경로로 위임합니다.

## 5. 원칙 변경(T8·G1) 결정과 업스트림 원문 복원

- **경위.** T8 원칙 완화 4건은 원래 포트의 PR #70에서 리뷰 봇 `macroscopeapp[bot]`의
  Medium 지적 3건을 그대로 반영한 것으로, 업스트림 이력에 없는 문구다. G1은 최초
  커밋부터 있었고 사유 문서가 없다.
- **독립 판단.** Claude Agent SDK로 `claude-fable-5-1`에게 근거만 주고 판단을 맡겼다(실행 모델 검증, 비용 $0.45).
  - T8-a·d는 기술적으로는 타당하지만, 업스트림 c742dd6(#633)이 같은 결론을 더 정확한 문구로 담고 있다.
  - T8-b·c는 원 의도를 훼손한다. 남겨 둔 `toBeDefined`와도 모순된다.
  - G1은 "예외 없음" 문장 바로 뒤에 예외를 붙인 자기모순이고, 작성자가 예외 여부를 스스로 판정하는 구조다.
    Hermes 고유의 사정도 아니다.
- **결정(사용자 승인).** 6건을 모두 제거하고 업스트림 원문을 따른다. 재고정 후에는 최신 문구를 따른다.
  진짜 플랫폼 차이인 "standing by 응답 금지"만 H1 계약에 Hermes 적응으로 등록했다.
- **운영 원칙.** 플랫폼 적응(도구·경로·메커니즘)만 허용하고, 원저자 원칙의 의미 변경은 하지 않는다.
  의미 변경이 필요하면 이 문서에 사유·대안·승인을 기록한 뒤에만 도입한다.
