# Step 3 SUMO 네트워크 생성

이 단계는 지도 입력에서 SUMO network를 생성하는 준비 단계다.

## 목적

- 차량 통행 가능한 edge와 lane 정보를 생성한다.
- 신호 교차로와 connection 정보를 SUMO 형식으로 확보한다.
- 이후 route 생성과 TraCI 제어가 참조할 `net.xml`을 만든다.

## 최종 입력

최종 실험은 다음 network를 사용한다.

```text
data_prepared/net/jungbu_ellipse_passenger.net.xml
```

다른 network는 진단 또는 과거 실험 참고용으로만 사용한다.
