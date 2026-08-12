# primesim-dm-setup

PrimeSim 시뮬레이션 덱을 자동으로 셋업해주는 CLI.

모델 파일에서 `.subckt` 포트를 읽어와 → **네이밍 룰로 자동 결선** → **안 쓰는 노드 자동 터미네이션**
→ 덱(`.sp`) + 결선 리포트를 생성합니다. HBM / LPDDR / NAND처럼 포트가 수십~수백 개인 IO 모델을
손으로 연결하다 순서 하나 틀려서 며칠 날리는 걸 막는 게 목적입니다.

- **의존성 없음.** Python 3 표준 라이브러리만 사용 (3.6+). pip 설치 불필요.
- **파일 복사만으로 반입 가능.** `primesim-dm/` 디렉토리 통째로 복사하면 끝.
- 결과 파싱/리포트(`.mt0` 등)는 아직 범위 밖. 지금은 덱 생성 + 결선 검증까지.

---

## 1. 반입 & 동작 확인

사내 환경에 `primesim-dm/` 디렉토리를 복사한 뒤:

```bash
cd primesim-dm
python3 -m unittest discover -s tests     # 89개 테스트, 전부 통과해야 정상
python3 -m primesim_dm gen examples/hbm_tx_rx.jsonc
```

`examples/out/hbm_tx_rx.sp` 와 `.report.txt` 가 나오면 준비 완료입니다.
`examples/models/*.inc` 는 동작 확인용 가짜 모델이므로 실제 모델로 교체해서 쓰면 됩니다.

## 2. 기본 흐름

```bash
# (1) 모델에 뭐가 들어있는지 본다 - 포트 이름과 순서 확인
python3 -m primesim_dm scan /proj/models/hbm/io.inc

# (2) config 뼈대를 만든다 (인스턴스 stub 자동 생성)
python3 -m primesim_dm init /proj/models/hbm/io.inc \
        --subckt hbm_tx_drv --subckt hbm_rx_amp \
        -o my_deck.jsonc --deck out/my_deck.sp

# (3) my_deck.jsonc 에 네이밍 룰 / 안 쓰는 핀을 적는다  ← 여기가 사람이 하는 일

# (4) 덱 생성
python3 -m primesim_dm gen my_deck.jsonc

# (5) 룰만 고쳐가며 결선 확인 (덱은 안 쓰고 리포트만 출력)
python3 -m primesim_dm check my_deck.jsonc
```

명령어 4개가 전부입니다.

| 명령 | 하는 일 |
|---|---|
| `scan`  | `.subckt` 정의와 포트 목록/순서/파라미터 출력 (`--json` 지원) |
| `init`  | 모델 파일에서 config 뼈대 생성 |
| `gen`   | config → 덱 `.sp` + `.sp.report.txt` |
| `check` | 덱은 안 쓰고 결선 리포트만 출력 (룰 튜닝용) |
| `lint`  | **이미 있는 덱(.sp)을 읽어서 구조·연결성 검사.** config 불필요 |

`gen`/`check` 에 `--strict` 를 주면 경고가 하나라도 있을 때 exit code 1 → 회귀 스크립트에 물리기 좋습니다.

## 2-1. `lint` — 기존 덱 검사 (설정 없이)

`gen`/`check` 는 이 도구가 만든 config가 있어야 하지만, `lint` 는 **덱 파일만 있으면 됩니다.**
손으로 만든 덱, 남이 준 덱, 예전 프로젝트 덱에 그대로 돌아갑니다.

```bash
python3 -m primesim_dm lint /proj/sim/lpddr_write.sp
python3 -m primesim_dm lint deck.sp --search-dir /proj/models -v
```

```
primesim-dm-setup deck check
==================================================================
files    : 1
elements : 13 at the top level
nets     : 14
unparsed : 1 line(s)  <- checks below do not cover these

ERROR duplicate-name     element XIO1 is defined twice (first at deck.sp:13)
ERROR missing-include    cannot find included file: models.inc (from deck.sp:2)
ERROR port-count         XIO2 passes 4 node(s) to subckt io_cell, which declares 5 port(s)
ERROR undefined-subckt   XRX1 calls subckt 'mem_io_cell', which is not defined in any file read
WARN  floating-net       net en_rx is touched only by XRX1 (port 4)
INFO  merged-net         ball_dq0 and ball_dq1 are one node (Rshort = 0 ohm)

4 error(s), 4 warning(s), 2 note(s)
```

검사 항목:

| 코드 | 등급 | 내용 |
|---|---|---|
| `missing-include` | ERROR | `.include`/`.lib` 경로에 파일이 없음 |
| `undefined-subckt` | ERROR | X가 부르는 subckt이 어디에도 정의 안 됨 |
| `port-count` | ERROR | X의 노드 개수 ≠ `.subckt` 포트 개수 |
| `duplicate-name` | ERROR | 같은 이름 소자가 두 번 |
| `isolated-instance` | ERROR | 인스턴스의 모든 노드가 다른 데 안 붙음 |
| `floating-net` | WARN | 한 곳만 붙은 net (`--keep-net` 로 제외 가능) |
| `unparsed-line` | WARN | 노드를 확정 못 한 줄 — **검사 범위 밖임을 명시** |
| `merged-net` | INFO | 0옴 저항이나 `.connect` 로 두 net이 사실상 한 노드 |

에러가 있으면 exit 1, `--strict` 면 경고에도 exit 1입니다.

**파싱 못 한 줄은 추측하지 않고 신고합니다.** 소자 종류마다 노드 개수 규칙이 달라서
(`M`은 4개, `Q`는 3~4개, `W`는 `N=`에 따라, `S`/`B`는 파라미터 직전까지) 전부 맞히는 파서는
없습니다. 확신이 안 서는 줄은 `unparsed`로 빼고 개수를 헤더에 찍습니다 —
"이 검사 결과가 덱의 몇 %를 봤는지" 모르면 통과했다는 말에 의미가 없어서입니다.

net 이름은 SPICE 규칙대로 대소문자를 구분하지 않습니다 (`PAD_DQ0` = `pad_dq0`).

## 3. 결선 규칙 — 우선순위

포트 하나가 어떤 net에 붙을지는 **위에서부터 먼저 걸리는 것**이 이깁니다.

1. `instances[].connect` — 포트명 → net명 직접 지정
2. `instances[].unused` — 정규식에 걸리면 터미네이션
3. `instances[].rules` — 인스턴스 전용 룰 (위에서부터 첫 매치)
4. `naming.rules` — 전역 룰 (위에서부터 첫 매치)
5. `naming.default` — 아무것도 안 걸렸을 때의 기본 정책

기본 정책(`naming.default`):

| 값 | 동작 |
|---|---|
| `same_name` | net 이름 = 포트 이름 (기본값) |
| `prefix` | net 이름 = `<인스턴스명>_<포트명>` |
| `terminate` | 매칭 안 된 포트는 전부 터미네이션 |
| `error` | 매칭 안 된 포트가 있으면 에러로 중단 (룰을 빠짐없이 쓰게 강제) |

### 룰 문법

```jsonc
{ "match": "^DQ<(\\d+)>$", "net": "ctrl_pad<{1}>" }
```

- `match` 는 파이썬 정규식. JSON 안이라 백슬래시는 `\\d` 처럼 두 번 씁니다.
- `net` 템플릿에서 쓸 수 있는 필드:
  - `{1}`, `{2}` … 정규식 캡처 그룹 / `{0}` 전체 매치
  - `{port}` 포트명, `{inst}` 인스턴스명, `{subckt}` 서브서킷명, `{index}` 포트 순번
- 변환 붙이기: `{1|lower}`, `{1|upper}`, `{1|int}`(0 제거), `{1|zfill:3}`
  → 예: `{ "match": "^V(DD|SS)Q$", "net": "v{1|lower}q" }` → `VDDQ` → `vddq`
- `"action": "terminate"` 를 주면 연결 대신 터미네이션, `"skip"` 은 이 룰을 건너뛰고 다음 룰로.
- net 이름 앞에 `!` 를 붙이면 (`"!VDD_ANALOG"`) 아래 정규화를 건너뛰고 그대로 씁니다.

### 버스 표기 정규화 — `<>` / `[]` / `()` 섞임 문제

모델마다 `DQ[0]`, `DQ<0>`, `DQ(0)` 이 제각각이라 룰이 안 먹는 게 제일 흔한 사고입니다.

- `naming.bus_style` 을 `angle` 로 두면 **생성되는 net 이름**이 전부 `DQ<0>` 로 통일되고,
- `naming.match_normalized`(기본 `true`) 덕분에 **룰 매칭도 정규화된 이름**으로 이뤄집니다.
  즉 모델이 `DQ[0]` 이라고 써놨어도 룰은 `^DQ<(\d+)>$` 하나로 충분합니다.
- 모델의 `.subckt` 선언이 `DQ[7:0]` 처럼 묶여 있으면 `models.expand_buses: true` 로 펼칩니다.

`naming.case` 는 `keep`/`upper`/`lower` — 생성 net 이름의 대소문자를 강제합니다.
(포트 매칭 시 대소문자는 건드리지 않습니다. 룰은 모델에 적힌 철자대로 쓰세요.)

## 4. 터미네이션

안 쓰는 핀을 지정하는 방법은 세 가지입니다.

```jsonc
"instances": [{
  "unused": ["^ODT_EN<", "^DRV_STR<"]          // (1) 인스턴스 단위
}],
"naming": { "rules": [
  { "match": "^TM_", "action": "terminate", "type": "tie", "to": "vss" }   // (2) 룰로
]},
"termination": { "auto_terminate_floating": true }   // (3) 뜬 노드 자동 처리
```

(3)은 **연결된 게 하나뿐인 net**(= 한쪽 끝이 뜬 노드)을 찾아 자동으로 부하를 답니다.
실수로 빠뜨린 출력 핀을 잡아주는 안전망입니다. 이때 `tie` 는 실신호를 레일에 쇼트시키므로
자동 처리 시에는 `rload` 로 낮춰서 적용합니다.

여기서 중요한 건 **안전망이 진짜 실수를 덮지 않는 것**입니다. 자동 터미네이션이 걸렸는데
`termination.overrides` 나 인스턴스 `termination` 으로 "이 핀은 부하가 없는 게 정상"이라고
미리 밝혀두지 않은 포트는 **경고로 올라옵니다**. 룰의 net 이름을 잘못 써서 두 블록이 안 이어진
경우가 정확히 여기 걸립니다 (`--strict` 면 exit 1).

```
! XTX.PAD<0> -> txpad<0> was floating and got a rload; no rule or override
  expected that - is the net name right?
! XCH.IN<0>  -> txp<0>   was floating and got a rload; ...
```

양쪽 다 뜬 걸로 잡히므로 `txpad<n>` 과 `txp<n>` 이 어긋났다는 게 바로 보입니다.

터미네이션 타입:

| `type` | 생성되는 것 |
|---|---|
| `tie` | 포트를 `to` net(예: `vss`)에 직접 연결. 소자 없음 |
| `rload` | `Rterm_N <net> <to> <value>` (기본 `1T`) |
| `cload` | `Cterm_N <net> <to> <value>` — 부하 없는 디지털 출력용 |
| `rc` | R(`value`) + C(`value2`) 둘 다 |
| `vsource` | `Vterm_N <net> <to> DC <value>` — 코드/모드 핀을 특정 전압으로 |
| `isource` | `Iterm_N <net> <to> DC <value>` |
| `open` | 아무것도 안 붙이고 고유 net 이름만 부여 (의도적 open, 경고 안 뜸) |

우선순위: 룰에 적힌 값 > `instances[].termination` > `termination.overrides`(포트명 정규식) > `termination.default`.

`termination.keep_nets` 에 정규식을 넣으면 그 net은 뜬 노드 검사에서 제외됩니다
(외부에서 자극을 주는 net 등).

## 5. 여러 제품 덱 공유 — `extends`

사내 공통 룰(테스트핀 처리, 전원 이름 규칙 등)은 베이스 config 하나에 몰아두고,
제품별 덱은 그걸 상속합니다.

```jsonc
{ "extends": "base_common.jsonc",
  "deck": { "title": "LPDDR DQ lane", "output": "out/lpddr.sp" },
  "instances": [ ... ] }
```

- 오브젝트는 **깊은 병합**, 리스트는 **통째로 교체**입니다.
  (자식이 `naming.rules` 를 쓰면 부모 룰 목록은 사라집니다. 덧붙이는 게 아닙니다.)
- 부모에 적힌 모델 경로는 **부모 파일 기준**으로 해석됩니다.
- `"extends": ["a.jsonc", "b.jsonc"]` 처럼 여러 개도 가능 (뒤가 우선).

`examples/base_common.jsonc` + `examples/lpddr_dq.jsonc` 가 이 형태의 예시입니다.

## 6. config 전체 레퍼런스

```jsonc
{
  "extends": "base.jsonc",              // 선택

  "deck": {
    "title":   "덱 제목",
    "output":  "out/deck.sp",           // config 파일 기준 상대경로
    "temp":    [25, 125],               // .temp
    "options": ["post", "accurate"],    // 각각 .option 한 줄
    "globals": ["vdd", "vss"],          // .global (뜬 노드 검사에서도 제외됨)
    "width":   88                       // 줄바꿈 폭 ('+' 연속행)
  },

  "models": {
    // 파일 / 디렉토리 / 글롭 다 됨. 매치 결과는 정렬되므로
    // models/0_soc_io, 1_soc_rdl ... 처럼 번호를 붙여두면 stage 순서대로 나옴
    "files": ["models/**/*.inc", "models/3_board", {"path": "b.lib", "section": "tt"}],
    "extensions": [".inc", ".sp", ".spi", ".lib", ".cir", ".mod", ".net"],
    "search_dirs": ["/proj/models"],    // include 따라갈 때 탐색 경로
    "follow_includes": false,           // .include/.lib 를 재귀적으로 따라감
    "expand_buses": false,              // .subckt 의 DQ[7:0] 을 비트로 펼침
    "emit_includes": true               // 덱에 .include/.lib 줄을 쓸지
  },

  "naming": {
    "case": "keep",                     // keep | upper | lower
    "bus_style": "keep",                // keep | angle | bracket | paren | underscore
    "default": "same_name",             // same_name | prefix | terminate | error
    "match_normalized": true,
    "prefix_sep": "_",
    "rules": [ { "match": "^TM_", "action": "terminate", "type": "tie", "to": "vss" } ]
  },

  "supplies": [
    { "net": "vdd", "value": 1.1 },     // Vvdd vdd 0 DC 1.1
    { "net": "vddq", "value": "PWL 0 0 1n 1.1", "ref": "0", "name": "Vq" }
  ],

  "termination": {
    "auto_terminate_floating": true,
    "keep_nets": ["^probe_"],
    "net_prefix": "n_",
    "default":   { "type": "rload", "to": "vss", "value": "1T" },
    "overrides": [ { "match": "^DOUT", "type": "cload", "to": "vss", "value": "5f" } ]
  },

  "instances": [
    {
      "name": "XTX",                    // 덱에 나갈 인스턴스 이름
      "subckt": "hbm_tx_drv",
      "source": "io.inc",               // 같은 이름 subckt이 여러 파일에 있을 때 지정
      "comment": "출력 드라이버",
      "params": { "seg": 4 },
      "default": "prefix",              // 이 인스턴스만 다른 기본 정책
      "connect": { "EN_TX": "en_tx" },
      "rules":   [ { "match": "^DIN<(\\d+)>$", "net": "txd<{1}>" } ],
      "unused":  ["^SCAN_"],
      "termination": { "type": "tie", "to": "vss" }   // 이 인스턴스 터미네이션 기본값
    }
  ],

  "stimulus": ["Vclk clk 0 PULSE 0 1.1 0 20p 20p 230p 500p"],   // 그대로 출력
  "analysis": {
    "op": true,
    "tran": { "step": "1p", "stop": "20n", "start": "0", "extra": "" },
    "dc":   { "source": "vin", "start": 0, "stop": 1.1, "step": 0.01 },
    "ac":   { "mode": "dec", "points": 100, "start": "1k", "stop": "10G" },
    "raw":  [".tran 1p 20n sweep temp 25 125 50"]   // 위 형식으로 안 되는 건 여기에
  },
  "probes":  ["v(dq<0>) v(dqs_t)"],
  "measures": [".meas tran pp1 pp v(dq<0>) from=2n to=20n"],
  "raw_prepend": [],                    // 헤더 직후에 그대로 삽입
  "raw_append":  []                     // .end 직전에 그대로 삽입
}
```

`stimulus` / `raw_*` 에 적은 줄은 그대로 덱에 나가고, 그 안에 등장하는 net 이름은
"연결된 것"으로 세어지므로 뜬 노드 경고에서 빠집니다.

## 7. 리포트 읽는 법

`gen` 은 덱 옆에 `<덱이름>.sp.report.txt` 를 같이 씁니다. 인스턴스별로 **포트 → net,
그리고 그 결정을 내린 근거**가 한 줄씩 찍힙니다.

```
XTX  (subckt hbm_tx_drv, 19 ports)  [hbm_io.inc]
  DIN<0>     -> txd<0>    inst-rule   instances[XTX].rules[0] ^DIN<(\d+)>$
  EN_TX      -> en_tx     explicit    connect[EN_TX]
  TM_DRV<0>  -> vssq      rule        naming.rules[0] (terminate) ^TM_   <TERM tie>
```

맨 아래 `single-connection nets` 목록이 **한쪽만 붙은 net** 입니다.
`[open by design]` 표시가 없는데 여기 올라온 net은 대부분 룰이 빠진 것이니 확인하세요.

## 8. 실제 모델에 적용할 때

1. `scan` 으로 포트 이름 규칙을 눈으로 확인 (버스 표기, 테스트핀 접두사 등)
2. 사내 공통 규칙을 `base_common.jsonc` 로 한 번 정리 — 이게 자산입니다
3. 참고용으로 쓰던 기존 덱과 생성 덱을 나란히 diff 해서 결선이 같은지 확인
4. 맞으면 `--strict` 로 회귀 스크립트에 물리기

## 9. 한계 / 주의

- `.subckt` 포트 **순서**대로 결선하므로, 모델이 바뀌면 덱을 다시 생성해야 합니다.
  (생성 덱을 손으로 고치지 마세요. 헤더에도 그렇게 적힙니다.)
- SPICE 문법 전체를 파싱하지 않습니다. `.subckt`/`.ends`/`.include`/`.lib` 와
  주석·연속행만 해석합니다. 전처리기 매크로(`.if` 등)가 섞인 모델은 `scan` 결과를 먼저 확인하세요.
- 뜬 노드 검사는 이 덱 안에서만 셉니다. 서브서킷 내부에서 실제로 어떻게 쓰이는지는 모릅니다.
- 시뮬레이터를 실행하지 않습니다. 덱 생성까지가 범위입니다.

## 10. 구조

```
primesim_dm/
  spice.py    .subckt 파싱, 주석/연속행 처리, 버스 표기, 줄바꿈
  config.py   JSONC 로딩, extends 병합, 스키마 검증
  netlist.py  포트→net 해석, 터미네이션, 뜬 노드 검사   ← 자동화의 핵심
  emit.py     덱/리포트 렌더링
  cli.py      scan / init / gen / check
examples/     동작 확인용 가짜 모델 + config 예시
tests/        unittest (표준 라이브러리)
```

라이브러리로도 쓸 수 있습니다:

```python
from primesim_dm import config, netlist, emit, spice

cfg = config.load("my_deck.jsonc")
subs, _files = spice.scan_files([f["path"] for f in cfg["models"]["files"]])
nl = netlist.Resolver(cfg, netlist.build_index(subs)).build()
print(emit.render_deck(cfg, nl))
```
