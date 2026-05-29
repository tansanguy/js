# Step 2 지도 입력 준비

이 단계는 분석 권역의 OSM 또는 SUMO 입력 자료를 준비하는 단계다.

## 목적

- 중부소방서 권역과 서울역 방향을 포함하는 분석 범위를 정한다.
- 이후 network 생성에 사용할 지도 입력을 확보한다.
- full map 계열과 reduced map 계열을 구분한다.

## 현재 기준

최종 실험의 active net은 아래 파일이다.

```text
data_prepared/net/jungbu_ellipse_passenger.net.xml
```

full map 관련 자료는 `archive/full_map_legacy/`에 보관하며 최종 실험 입력으로 사용하지 않는다.
