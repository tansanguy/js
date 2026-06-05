# B4 `local_fill_100m` 쉬운 설명

## 1. 뭘 재는 값인가

`local_fill_100m`는 **정지선 뒤쪽 100m 안에 대기열이 얼마나 찼는지**를 나타내는 값이다.

```text
local_fill_100m = 정지선 뒤 대기열 길이 / 100m
```

예시:

```text
정지선 뒤로 70m까지 차가 기다림
=> local_fill_100m = 0.7
```

즉, 팀에서 말하는 `tau = 0.7`은 이렇게 해석하면 된다.

```text
정지선 뒤 100m 중 70m 정도가 대기열로 차 있으면
큐가 심하다고 보고 신호를 미리 열 후보로 본다.
```

## 2. 대기열 길이는 어떻게 구하나

대기열 길이, 즉 분자는 `queue_m_est`다.

```text
local_fill_100m = queue_m_est / 100m
```

`queue_m_est`는 두 가지를 보고 정한다.

```text
1. 기다리는 차량이 몇 대인지
2. 가장 뒤에 있는 대기 차량이 정지선에서 얼마나 떨어져 있는지
```

차량 수로 계산할 때는 차량 1대가 약 `7.5m`를 차지한다고 본다.

```text
대기 차량 8대
=> 8 * 7.5m = 60m
```

차량 위치로 계산할 때는 가장 뒤에 있는 정지/저속 차량까지의 거리를 본다.

```text
가장 뒤 대기 차량이 정지선 뒤 65m 지점에 있음
=> 대기열 길이 = 65m
```

둘 중 더 큰 값을 쓴다.

```text
차량 수로 본 길이 = 60m
차량 위치로 본 길이 = 65m

queue_m_est = 65m
local_fill_100m = 65 / 100 = 0.65
```

## 3. 어디에 쓰나

이 값은 신호를 미리 열지 판단하는 데 쓴다.

```text
local_fill_100m >= 0.7
=> 큐가 심함
=> EV가 오기 전에 신호를 열 후보
```

현재는 속도 조건도 같이 본다.

```text
local_fill_100m >= 0.7
또는 접근 속도 <= 15km/h
```

즉, 큐가 길거나 이미 느리게 막히고 있으면 신호 제어 후보가 된다.

## 4. 현실 CSV 보정은 뭔가

현실 CSV 보정은 지금 개념을 이해하는 데 핵심은 아니다.

간단히 말하면:

```text
SUMO가 큐를 계속 작게 보거나 크게 보면
나중에 현실 CSV를 참고해서 살짝 맞추는 장치
```

기본 개념은 이거 하나면 된다.

```text
local_fill_100m = 정지선 뒤쪽 대기열 길이 / 100m
```

## 참고문헌

1. SUMO Documentation, TraCI Lane Value Retrieval.
2. SUMO Documentation, Interfacing TraCI from Python.
3. SUMO Documentation, Lanearea Detectors (E2).
4. SUMO Documentation, TripInfo Output.
5. Zhao et al., Various Methods for Queue Length and Traffic Volume Estimation Using Probe Vehicle Trajectories.
6. Comert et al., A Combinatorial Approach for Nonparametric Short-Term Estimation of Queue Lengths Using Probe Vehicles.
7. Comert and Begashaw, Cycle-to-Cycle Queue Length Estimation from Connected Vehicles with Filtering on Primary Parameters.
