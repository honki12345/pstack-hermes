# pstack-hermes 개선 백로그

이 문서는 포크에 남은 개선 항목과 담당을 한곳에 모은 목록입니다. 목표는 poteto-mode에서
에이전트가 플레이북의 위임 결정을 건너뛰지 않게 하고, 개선마다 실제 에이전트 행동으로
효과를 재는 것입니다. 감사 결과와 해소된 항목은 [AUDIT-ko.md](AUDIT-ko.md)에 있습니다.

`AGENTS.md`의 Planning 정책은 계획을 GitHub 이슈에 두고, 이슈가 꺼진 동안에는 이 문서가
백로그를 맡는다고 정합니다. 이 포크는 이슈가 꺼져 있습니다(`gh repo view
honki12345/pstack-hermes --json hasIssuesEnabled`가 `false`). 이슈를 켜면 항목을 이슈로 옮깁니다.

줄 번호는 `develop`의 `dcdcf21` 기준입니다. 저장소 밖에서 본 사실은 "로컬 관찰"로,
재현하지 않은 판단은 "추정"으로 적었습니다. Hermes 소스 경로는 Hermes v0.23.0 저장소
기준이고, 로컬 설치본에서 읽었습니다.

## 진행 상태

개선 프로그램의 PR과 결과입니다. 상태는 `develop`의 `acb12c5` 기준입니다.

| PR | 내용 | 결과 | 백로그 |
|---|---|---|---|
| PR-1 | 평가 하네스(`tools/eval/`). 고정 프롬프트, 실행기, 판정기 | 병합(#4) | 측정 기반. B10, B11을 우회합니다 |
| PR-2 | 관문 플러그인을 `hermes-plugin/poteto-delegation-gate/`로 이관 | 병합(#6) | B1 |
| PR-3 | todolist에 위임 결정 항목이 있는지 검사 | 병합(#8) | B2 |
| PR-4 | 마커 없는 후속 턴에도 관문 유지 | 병합(#10) | B5 |
| PR-5 | H1 위임 계약에 위임 결정 기록 문장 추가 | 닫음(#7). 효과가 측정되지 않았습니다 | B4 |
| PR-6 | 이 문서와 감사 문서 갱신 | 이 PR | B12 |
| PR-7 | 조사형 프롬프트 `p5-ops-errors`와 `fulfilment-api` fixture | 별도 PR(#9) | B16 |

## 효과를 재는 지표

평가 하네스는 poteto-mode 마커를 붙인 프롬프트를 격리 프로필에서 실행하고, 관문 로그,
`state.db`, 실행 기록 `run.json`을 읽어 지표 G1부터 G6을 JSON으로 냅니다. 모델 판정은 쓰지
않습니다. 지표 정의의 정본은 [`tools/eval/README.md`](../../tools/eval/README.md)이고, 위임 결정
항목의 기준은 관문과 판정기가 같습니다. 모델 출력이 비결정적이므로 각 지표는 여러 번 실행한
통과 비율로 판정합니다.

측정에서 확인한 것은 다음과 같습니다. 숫자는 `claude-sonnet-5-5`로 실행한 결과입니다.

- G6(후속 턴)은 관문 PR로 바뀌었습니다. 이전 관문에서는 0/3, PR-4 관문에서는 3/3과 재검증
  3/3이었습니다. 후속 턴은 trunk 15.5초에서 head 24.0초로 길어졌습니다.
- 결정 항목의 질이 바뀌었습니다. 기준선의 `skip: not requested` 같은 형식적 사유가 PR-3 뒤에는
  "저장소가 작고 함수 하나를 고친다" 같은 구체적 사유와 playbook 서문 문장으로 바뀌었습니다.
  PR-4의 후속 턴 차단 문구 뒤에는 에이전트가 `Playbook: none fits`와 위임 결정을 적었습니다.
- G1(선결정)은 관문 PR로 바뀌지 않았습니다. 관문은 시도된 작업 호출을 막을 뿐 시도 자체를
  막지 못하고, 판정기는 막힌 첫 시도를 감점합니다. 지금까지 모든 실행에서 G1은 false였습니다.
- G3(위임 실행)은 0이었습니다. p1은 함수 하나짜리 버그이고, p5에서도 에이전트가 두 결함을 1~2분에
  혼자 고쳤습니다. 이 크기에서는 위임 생략이 맞는 판단일 수 있어 G3 false를 결함으로 보지 않습니다.
- PR-5의 문장은 매 턴 들어가는 `SKILL.md`에 넣어 PR-3 유무로 각각 2회씩 실행했지만 차단 횟수,
  G1, G3, 비용 어느 것도 나아지지 않았고 결정 항목이 todolist 앞으로 옮겨지기만 했습니다.

## 분류

각 항목은 대상, 근거, 검사, 담당 순서로 적고 담당으로 끝납니다.

대상은 다음 다섯 가지입니다.

- 포크 확장. `hermes-plugin/` 아래 손으로 쓴 Hermes 플러그인과 포크 문서.
- 변환 적응. `tools/convert.py`와 `tools/assets/`. 업스트림 원칙과 플레이북의 의미는 바꾸지 않습니다.
- 업스트림 보고. `cursor/plugins`의 pstack에 올리는 보고.
- Hermes 본체. `NousResearch/hermes-agent`에 올리는 보고. 포크는 Hermes를 패치하지 않습니다(`CONTRIBUTING.md:96-98`).
- 사용자 로컬. 패키지를 설치한 사용자의 설정.

담당은 다음 세 가지 중 하나입니다.

- 계획의 PR id(PR-1부터 PR-7).
- 운영자 항목. 운영자가 결정하거나 직접 처리합니다. Hermes 본체 보고와 사용자 로컬 설정이 여기에 들어갑니다.
- 업스트림 보고. `cursor/plugins`에 보고합니다. 보고를 올리는 일도 운영자가 합니다.

## 항목

### B1. 위임 관문이 포크 밖에만 있다

poteto-mode 턴에서 todolist를 쓰기 전 작업 도구를 막는 관문(`poteto-delegation-gate`)은
사용자의 Hermes 홈에만 설치돼 있습니다. 다시 설치하거나 다른 기기로 옮기면 사라지고,
버전 관리와 CI가 없습니다.

- 대상. 포크 확장.
- 근거. 로컬 관찰. 관문은 `__init__.py` 155줄과 테스트 42개(`pytest --collect-only`)로 되어 있습니다. 저장소의 `hermes-plugin/`에는 `pstack-delegation`만 있습니다(`ls hermes-plugin`, `docs/agent/layout.md:16`).
- 검사. 이관 뒤 `hermes plugins doctor hermes-plugin/poteto-delegation-gate --ci`가 훅 2개를 보고하고, `uv run --frozen pytest -q`의 통과 수가 193에서 관문 테스트 수만큼 늘어납니다. 평가 하네스의 G1과 G2가 이관 전후로 같습니다.
- 담당. PR-2.
- 상태. 해소. PR #6이 관문을 옮겼고 설치본과 바이트 단위로 같습니다.

### B2. 관문이 todolist 내용을 보지 않는다

관문은 `todo_list` 호출 여부만 확인합니다. 항목에 위임 결정이 없어도 통과하므로, bug-fix
서문의 조사 위임을 목록에서 빠뜨려도 막지 않습니다.

- 대상. 포크 확장.
- 근거. 로컬 관찰. 관문의 `_decide`(`__init__.py:117-128`)는 호출 이름에 `todo_list`가 있으면 `todo_written`을 세우고 인자는 읽지 않습니다. `BLOCK_MESSAGE`(`__init__.py:33-37`)는 위임 결정 항목을 적으라고 안내하지만 그 항목을 검사하지 않습니다. PR-2가 파일을 그대로 옮기므로 이관 뒤에는 `hermes-plugin/poteto-delegation-gate/__init__.py`의 같은 줄에서 확인합니다.
- 검사. 결정 항목 없는 todolist 뒤의 작업 도구 호출에 관문 로그 `{"event": "block", "reason": "todo_without_decision"}`이 찍힙니다. 평가 하네스의 G1 통과 비율이 변경 전 `develop`보다 낮지 않습니다.
- 담당. PR-3.
- 상태. 해소. PR #8. 결정 항목은 `delegate_task`를 쓰거나, 위임을 가리키는 말과 같은 줄 사유가 있는 `skip:`입니다. 단계 생략 `skip:`만으로는 풀리지 않습니다.

### B3. bug-fix의 조사 위임 지시가 번호 단계에 없다

`bug-fix.md`는 서문에서 조사와 수정을 서브에이전트에 맡기라고 합니다. 번호 단계 6개 중
조사 단계인 2단계에는 위임 문장이 없고, 구현 위임만 3단계에 있습니다. 에이전트가 번호
단계만 todolist로 옮기면 조사 위임이 빠집니다.

- 대상. 업스트림 보고. 포크 정책상 플레이북 본문은 바꾸지 않습니다. 관문 안내 보강은 B2(PR-3)가, H1 계약 문구는 B4(PR-5)가 맡습니다.
- 근거. `skills/poteto-mode/playbooks/bug-fix.md:3`(서문), `:8`(2단계, `how`로 가설을 시드하라고만 함), `:9`(3단계, 구현 위임). 포크 생성본은 pin의 업스트림 원문과 8행 하나만 다르고, 그 차이는 `/loop`를 hermes cron으로 바꾼 치환입니다(`diff <(git -C <upstream> show c742dd6:pstack/skills/poteto-mode/playbooks/bug-fix.md) skills/poteto-mode/playbooks/bug-fix.md`). `skills/poteto-mode/playbooks/feature.md:3`과 `skills/poteto-mode/playbooks/hillclimb.md:3`의 서문에도 위임 지시가 있지만, 두 플레이북은 12행의 단계 본문에도 위임 지시가 있습니다.
- 검사. 업스트림이 번호 단계에 조사 위임을 넣으면 재고정 뒤 같은 `diff`와 업스트림 커밋에서 보입니다. 평가 하네스의 버그 수정 프롬프트에서 G3이 오릅니다.
- 담당. 업스트림 보고.

### B4. H1 계약에 위임 결정 기록 규칙이 없다

`poteto-mode`는 플레이북 단계를 todolist로 옮기라고 합니다. Hermes 위임 계약은 서문의
위임 지시를 todolist의 `delegate_task` 또는 `skip: <사유>` 항목으로 남기라고 하지 않습니다.

- 대상. 변환 적응. 계약 문서는 `tools/assets/hermes-delegation.md`에서 생성합니다.
- 근거. `skills/poteto-mode/SKILL.md:124`가 todolist 규칙을 정합니다. `skills/poteto-mode/references/hermes-delegation.md`에는 todolist 언급이 없습니다(`grep -n -i todo`, 0건).
- 검사. `tools/tests/test_hermes_adaptation_contract.py`가 새 문장을 단언하고, 재빌드 출력의 앵커 감사가 0 dead입니다. 관문을 끈 프로필에서도 todolist에 위임 결정 항목이 생기는지 평가 하네스로 봅니다.
- 담당. PR-5.
- 상태. 미해소. PR #7을 닫았습니다. 문장을 참고 문서에 둔 실행에서는 에이전트가 문서를 읽지 않았고, `SKILL.md`로 옮긴 실행에서는 행동 차이가 없었습니다. 관문의 차단 문구가 같은 내용을 더 직접 전합니다.

### B5. 후속 턴에서 관문이 꺼진다

같은 세션에서 poteto-mode 마커가 없는 다음 턴이 오면 관문이 꺼집니다. 첫 턴 뒤의
"테스트도 붙여 줘" 같은 후속 요청은 todolist 없이 진행됩니다.

- 대상. 포크 확장.
- 근거. 로컬 관찰. 관문의 `_on_pre_llm_call`(`__init__.py:109-111`)은 마커 없는 턴에서 `active`를 끄고 `turn_disarmed`를 기록합니다.
- 검사. 같은 세션의 두 번째 턴 관문 로그가 `turn_disarmed`가 아니라 `turn_continued`이고 G6이 참입니다. poteto-mode를 쓰지 않은 세션의 일반 턴에서는 관문 로그가 늘지 않습니다.
- 담당. PR-4.
- 상태. 해소. PR #10. 남은 한계는 B17에 있습니다.

### B6. `todo_list`가 기본으로 지연 도구다

Hermes v0.23.0은 `todo_list`를 기본 지연 도구로 둡니다. 에이전트는 todolist를 `tool_call`로
부르기 전에 보통 `tool_search`나 `tool_describe`로 스키마를 먼저 찾습니다. 이 추가 단계가
todolist를 건너뛰게 만드는지는 추정입니다.

- 대상. 사용자 로컬. 기본값 자체를 바꾸는 일은 Hermes 본체 대상입니다.
- 근거. Hermes 소스 `hermes_cli/config_defaults.py:2001-2003`의 `tools.tool_search.defer` 기본 목록에 `todo_list`가 있습니다. Hermes 소스 `tools/tool_search.py:53`은 명시한 `defer` 목록이 기본 목록을 통째로 대신한다고 적습니다. 로컬 관찰. 기본 프로필 관문 로그의 두 poteto-mode 턴과 검증 프로필 관문 로그의 한 턴 모두 `todo_list` 허용 앞에 `tool_describe` 허용이 찍혀 있습니다.
- 검사. `defer`에서 `todo_list`를 뺀 프로필과 기본 프로필로 평가 하네스를 돌려, 첫 `todo_list`까지의 도구 호출 수와 G1을 비교합니다.
- 담당. 운영자 항목.

### B7. 최상위 위임은 백그라운드로만 돈다

최상위 `delegate_task`는 언제나 백그라운드로 돌고, 결과는 턴이 끝난 뒤 새 메시지로
옵니다. TUI 사용자는 위임이 도는 동안 진행 상황을 보기 어렵습니다.

- 대상. Hermes 본체.
- 근거. `skills/poteto-mode/references/hermes-delegation.md:18-20`. 진행 상황이 보이지 않는다는 불편은 로컬 관찰입니다.
- 검사. 포크 안에서 검사할 것은 없습니다. Hermes 새 버전에서 TUI로 위임을 실행해 진행 표시가 보이는지 직접 확인합니다.
- 담당. 운영자 항목.

### B8. 로컬 지침이 위임을 줄이라고 한다

사용자 로컬 환경의 다른 에이전트 지침이 `delegate_task`를 필요한 작업에만 쓰라고 합니다.
poteto-mode는 위임을 기본으로 하므로 두 지시가 부딪힙니다.

- 대상. 사용자 로컬.
- 근거. 로컬 관찰. 위임 누락의 원인을 분석할 때 저장한 시스템 프롬프트에 그 문장이 있었습니다.
- 검사. 그 지침이 있는 프로필과 없는 프로필로 평가 하네스를 돌려 G3과 G4를 비교합니다.
- 담당. 운영자 항목.

### B9. 세션 목록 CLI가 서브에이전트 세션을 보여 주지 않는다

`hermes sessions list --source subagent`는 `No sessions found.`를 출력합니다. 세션 DB에는
`source`가 `subagent`인 행이 있습니다.

- 대상. Hermes 본체.
- 근거. 로컬 관찰(2026-11-08). CLI 출력과 `sqlite3 -readonly state.db "select source, count(*) from sessions group by source;"`의 `subagent|65`를 비교했습니다. Hermes 소스 `hermes_state_sessions.py:1303-1310`의 `list_sessions_rich`는 `include_children`과 `include_subagents`의 기본값이 `False`이고, `hermes sessions list --help`에는 이 값을 켜는 옵션이 없습니다. 이 기본값이 원인이라는 것은 추정입니다.
- 검사. Hermes 새 버전에서 같은 명령이 서브에이전트 세션을 보여 주는지 확인합니다. 그 전까지 평가 하네스의 판정기는 CLI 대신 `state.db`를 읽기 전용으로 직접 엽니다.
- 담당. 운영자 항목.

### B10. 서브에이전트가 export한 `HERMES_HOME`이 부모 셸에 남는다고 추정한다

관문 운영 메모에는 서브에이전트가 export한 `HERMES_HOME`이 부모 터미널 셸에 남아 뒤이은
명령이 다른 프로필을 가리킬 수 있다고 적혀 있습니다. 재현하지 않았으므로 추정입니다.

- 대상. Hermes 본체. 사용자 셸 위생 문제이기도 합니다.
- 근거. 로컬 관찰. 관문 운영 메모의 함정 항목에 적혀 있지만 재현 절차와 기록은 없습니다.
- 검사. 부모 셸에 `HERMES_HOME`을 둔 채 평가 하네스를 `env -i`로 실행하고, 기본 프로필의 관문 로그와 세션 DB 행 수가 실행 전후로 같은지 봅니다(PR-1의 환경 격리 lane).
- 담당. 운영자 항목.

### B11. `hermes -z` 실행에서 막 설치한 관문이 로드되지 않은 것을 관찰했다

`hermes -p <profile> -z`로 실행했을 때 그 프로필에 막 설치한 관문이 로드되지 않았습니다.
관문 검증은 `hermes_cli.oneshot.run_oneshot`을 프로세스 안에서 직접 불러 했습니다.

- 대상. Hermes 본체.
- 근거. 로컬 관찰. Hermes 소스 `hermes_cli/main.py:2996-3002`의 `_run_oneshot_from_args`는 `launch_gateway_chat`으로 실행을 넘깁니다. 상주 게이트웨이가 플러그인을 다시 읽지 않은 것이 원인이라는 판단은 관문 검증 때 내린 것이고, 이 문서를 쓰면서 다시 재현하지 않았습니다. 추정입니다.
- 검사. 평가 하네스는 실행 첫 단계에서 관문 로그 `turn_armed`를 확인하고, 없으면 `run_oneshot` 직접 호출로 바꿉니다(PR-1). Hermes 새 버전에서 `-z` 실행에 `turn_armed`가 찍히면 해소입니다.
- 담당. 운영자 항목.

### B12. 감사 문서가 낡았다

`AUDIT-ko.md`의 머리말이 원래 포트 `e6beab0`과 v0.15.5를 기준으로 적혀 있었고, 해소된
항목이 현재 문제처럼 남아 있었습니다.

- 대상. 포크 확장(문서).
- 근거. `git show dcdcf21:docs/fork/AUDIT-ko.md`의 3-5행(머리말)과 40-50행(기능 표). 기능 표 11행 중 10행은 이미 해소된 상태였습니다.
- 검사. [AUDIT-ko.md](AUDIT-ko.md) 머리말의 pin이 `uv run --frozen python tools/check_pins.py` 출력과 같고, 해소 표의 커밋이 모두 `git show`로 보입니다.
- 담당. PR-6.
- 상태. 이 PR이 진행 상태와 측정 결과를 반영합니다.

### B13. 의미 변경 등록부가 없다

업스트림 의미를 바꾸는 변경을 파일별 사유, 시작 시점, 업스트림 상태와 함께 남기는
등록부(`pstack-claude`의 `tools/forks.json` 방식)가 없습니다. 지금 기록은 감사 문서 5장의
서술뿐입니다.

- 대상. 변환 적응.
- 근거. `tools/forks.json`이 없습니다(`ls tools`). `git show dcdcf21:docs/fork/AUDIT-ko.md`의 62행이 권장으로 남겼습니다.
- 검사. 등록부를 만들면 등록부 항목과 `tools/convert.py`의 변환 맵이 일치하는지 단위 테스트로 확인합니다. 계획에 이 일을 맡은 PR이 없어 별도 PR을 열지 먼저 정합니다.
- 담당. 운영자 항목.

### B14. 세션 시작 라우팅 훅이 없다

`pstack-claude`의 SessionStart 훅처럼 세션이 시작될 때 poteto-mode 안내를 넣는 Hermes
플러그인 훅이 없습니다.

- 대상. 포크 확장.
- 근거. `hermes-plugin/`에는 `pstack-delegation`만 있고, 그 플러그인은 위임 공급자만 등록합니다(`hermes-plugin/pstack-delegation/__init__.py:104-105`). `git show dcdcf21:docs/fork/AUDIT-ko.md`의 64행이 권장으로 남겼습니다.
- 검사. 훅을 만들면 `hermes plugins doctor`가 그 훅을 보고하고, 평가 하네스의 G1이 훅 없는 프로필보다 높은지 봅니다. B1의 관문과 역할이 겹치므로 PR-2 뒤에 관문에 흡수할지 먼저 정합니다.
- 담당. 운영자 항목.

### B15. 패키지 밖 스킬 참조에 조건이 빠진 줄이 있다

`deslop`, `control-ui`, `control-cli`는 Cursor의 `cursor-team-kit`에 있고 이 패키지에는
없습니다. 변환은 이 참조에 "설치된 경우" 조건을 붙였지만 두 줄이 빠졌습니다. 설치하지 않은
사용자에게는 그 단계를 실행할 방법이 없습니다.

- 대상. 변환 적응. 업스트림이 `cursor-team-kit`을 전제하는 설계라 업스트림 보고 대상은 아닙니다.
- 근거. `skills/poteto-mode/SKILL.md:29`, `:31`, `skills/poteto-mode/playbooks/multi-phase-plan.md:15`, `:71`, `skills/poteto-mode/playbooks/opening-a-pr.md:9`, `skills/poteto-help/SKILL.md:108`에 조건이 있습니다. 조건이 없는 줄은 `skills/poteto-mode/playbooks/multi-phase-plan.md:59`와 `skills/poteto-mode/playbooks/opening-a-pr.md:34`이고, 둘 다 `/deslop`을 실행하라고 합니다. `multi-phase-plan.md`의 다른 줄에는 `deslop` 조건 문구가 없고, `opening-a-pr.md`는 9행에만 조건이 있습니다.
- 검사. `grep -rnE 'deslop|control-ui|control-cli' skills | grep -vi installed`는 같은 줄에 "installed"가 없는 참조를 출력합니다. 지금은 위 두 줄이 나오고, 고친 뒤에는 0줄입니다. 조건 없는 줄을 변환 규칙으로 고칠지 먼저 정합니다.
- 담당. 운영자 항목.

### B16. 위임 실행 지표가 개선을 드러내지 못한다

G3은 `delegate_task`가 실행됐는지만 봅니다. 고정 프롬프트는 혼자 처리해도 1~2분에 끝나는
크기라, 에이전트가 위임을 생략해도 틀린 판단이 아닐 수 있습니다. 원래 사고처럼 넓은 조사를
혼자 한 경우를 재려면 위임이 분명히 이득인 크기의 과제나, 위임 여부 대신 결정의 질을 보는
지표가 필요합니다.

- 대상. 포크 확장.
- 근거. PR-1 기준선과 PR-3, PR-4, PR-7 실행에서 G3은 모두 false였고 skip 사유는 저장소 크기였습니다. p5 실행 하나는 증상이 둘인데 "원인은 하나일 것"이라고 미리 단정했습니다.
- 검사. 새 지표나 프롬프트를 더하고 기준선과 관문 PR의 차이가 반복 실행에서 드러나는지 봅니다.
- 담당. 운영자 항목. 과제를 키울지 지표를 바꿀지 정해야 합니다.

### B17. 후속 턴 관문의 남은 한계

후속 턴의 읽기 허용은 마찰을 줄이는 장치이고 보안 경계가 아닙니다. 애매한 명령은 거절합니다.

- 대상. 포크 확장.
- 근거. 이 기기의 터미널 기록에서 읽기 전용 명령의 26%를 막습니다. kanban 계속 프롬프트와 `/loop` 깨움은 머리글 목록에 없어, 게이트웨이 푸터가 없는 CLI 경로에서는 일반 후속 턴으로 시작합니다. 세션 상태는 프로세스 메모리에만 있어 재시작하면 사라집니다.
- 검사. 거짓 차단 비율과 알림 인식을 `hermes-plugin/poteto-delegation-gate/tests/test_gate.py`와 같은 방식으로 다시 잽니다.
- 담당. 운영자 항목.

## 운영자 항목과 업스트림 보고 모음

위 항목 중 PR이 맡지 않는 것은 다음과 같습니다.

- 업스트림 보고는 B3입니다.
- Hermes 본체 보고는 B7, B9, B10, B11입니다.
- 사용자 로컬 설정은 B6과 B8입니다.
- 포크에서 별도 PR을 열지 결정할 항목은 B4, B13, B14, B15, B16, B17입니다.

계획에는 항목 밖의 운영자 항목도 있습니다.

- `develop`에서 `main`으로 가는 릴리즈 PR.
- 설치된 관문을 저장소 경로의 심볼릭 링크로 바꾸는 일. PR-3과 PR-4는 저장소에만 있고, 설치본은 PR-2 상태입니다.
- 주간 업스트림 재고정 자동화에 평가 하네스 단계를 넣는 일.
