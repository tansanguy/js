# Step 2 OSM 다운로드 및 SUMO Net 생성

## 목표

Step 2는 Step 1의 `analysis_area_meta.json`에 저장된 `bbox_wgs84`를 그대로 사용해 OSM 도로망을 다운로드하고, SUMO에서 열 수 있는 고정 `net.xml`을 생성하는 단계다.

타원형 clipping은 하지 않는다. `analysis_ellipse`는 연구권역 표시와 이후 edge 선택 기준이고, `osm_extract_bbox`는 OSM/SUMO 맵 생성용 범위다.

## 주요 원칙

- `bbox_wgs84`는 Step 1 산출물을 그대로 사용한다.
- Step 2에서 buffer를 다시 더하지 않는다.
- `data_raw/osm/jungbu_bbox.osm.xml`이 있으면 기본 재사용한다.
- `--force-download` 옵션을 쓸 때만 기존 OSM 파일을 백업하고 새로 다운로드한다.
- 모든 실험은 저장된 OSM 파일과 `net.xml`을 재사용한다.
- OSM 다운로드 실패, netconvert 실패, TLS 0개 같은 문제는 명확한 실패로 처리한다.
- 로컬 Python 인증서 저장소 문제로 HTTPS 검증이 실패하면 strict SSL을 먼저 실패로 기록한 뒤 해당 endpoint에 한해 SSL 검증 비활성 fallback을 시도한다.

## netconvert 옵션 정책

Step 2의 netconvert 옵션은 고정 확정값이 아니라 1차 초안이다.

서울 도심 신호망 보존을 우선으로 다음 옵션을 먼저 사용한다.

```bash
netconvert \
  --osm-files data_raw/osm/jungbu_bbox.osm.xml \
  --output-file archive/full_map_legacy/net/jungbu_area.net.xml \
  --tls.guess true \
  --tls.join true \
  --junctions.join true \
  --geometry.remove true \
  --remove-edges.isolated true \
  --no-turnarounds true
```

이 옵션은 `data_prepared/net/net_audit.json`, `data_prepared/net/map_manifest.json`, `data_prepared/net/netconvert_command.txt`에 기록한다.

`net_audit.json`과 `sumo-gui` 육안 확인 결과 문제가 있으면 Step 2를 재실행하면서 옵션을 조정할 수 있다. 예를 들어 TLS가 과도하게 합쳐지거나 사라지면 `--tls.join`, `--junctions.join`, `--geometry.remove` 조합을 재검토한다.

## 구현 파일

- `01_prepare/01_map/step02_build_map.py`
- `common/net_utils.py`
- `config/map_config.yaml`

## 입력

- `data_prepared/geojson/analysis_area_meta.json`
- `config/map_config.yaml`

## 산출물

- `data_raw/osm/jungbu_bbox.osm.xml`
- `archive/full_map_legacy/net/jungbu_area.net.xml`
- `data_prepared/net/netconvert_command.txt`
- `data_prepared/net/net_audit.json`
- `data_prepared/net/map_manifest.json`
- `outputs/logs/step02_build_map.log`

## 실제 수행 결과

실행한 명령:

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step02_build_map.py
```

실행 결과:

```text
Status: PASS
OSM file exists; reuse without download: data_raw/osm/jungbu_bbox.osm.xml
edge_count: 21599
junction_count: 13715
traffic_light_count: 356
lane_count: 64383
```

생성된 파일:

- `data_raw/osm/jungbu_bbox.osm.xml` 약 8.8 MB
- `archive/full_map_legacy/net/jungbu_area.net.xml` 약 37 MB
- `data_prepared/net/netconvert_command.txt` 약 832 B
- `data_prepared/net/net_audit.json` 약 3.5 KB
- `data_prepared/net/map_manifest.json` 약 1.2 KB
- `outputs/logs/step02_build_map.log`

다운로드/재사용 기록:

- 최초 실행 때 Overpass HTTPS 인증서 검증 문제가 발생해 strict SSL 실패 후 fallback 처리를 구현했다.
- OSM 파일 생성 후 재실행에서는 `auto_download_once_then_reuse` 정책에 따라 기존 `data_raw/osm/jungbu_bbox.osm.xml`을 재사용했다.
- 마지막 manifest 기준 `force_download_used`: `false`.

netconvert 실행 결과:

- `netconvert_option_status`: `first_pass_draft_adjust_after_net_audit_and_sumo_gui`
- `edge_count`: `21599`
- `junction_count`: `13715`
- `traffic_light_count`: `356`
- `lane_count`: `64383`
- warning 수: `115`

검증 결과:

- `net_audit.json` JSON valid.
- `map_manifest.json` JSON valid.
- `sumo -n archive/full_map_legacy/net/jungbu_area.net.xml --no-step-log true --duration-log.disable true -e 0` 실행 exit code `0`.
- 사용자가 `sumo-gui`에서 `archive/full_map_legacy/net/jungbu_area.net.xml` 로드 확인 완료.

## 검증 기준

- OSM 파일이 생성 또는 재사용되어야 한다.
- OSM 파일 크기가 0보다 커야 한다.
- OSM XML에 highway way가 1개 이상 있어야 한다.
- `jungbu_area.net.xml`이 생성되어야 한다.
- net.xml root가 `net`이어야 한다.
- edge, junction, lane 수가 1개 이상이어야 한다.
- traffic light 수가 1개 이상이어야 한다.
- `map_manifest.json`에 OSM/net SHA256과 netconvert command가 기록되어야 한다.
- `sumo-gui archive/full_map_legacy/net/jungbu_area.net.xml`로 열어 신호망을 확인해야 한다.

## 실행 명령

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step02_build_map.py
```

강제 재다운로드:

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step02_build_map.py --force-download
```

수동 확인:

```bash
cd /Users/junlee/Desktop/js
cat data_prepared/net/net_audit.json
cat data_prepared/net/map_manifest.json
sumo-gui archive/full_map_legacy/net/jungbu_area.net.xml
```

## 하지 않는 일

- SUMO edge GeoJSON 변환
- HTML 지도 생성
- 사고 edge 선택
- 소방차 route 생성
- 일반차량 수요 생성
- TraCI controller 구현
- 신호제어 구현
- batch simulation 구현
- Bayesian Optimization
