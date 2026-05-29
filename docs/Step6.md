# Step 6 사고 후보와 route 준비

이 단계는 emergency route 생성을 위한 후보 edge와 목적지 후보를 준비한 기록이다.

## 목적

- SUMO에서 도달 가능한 목적지 edge를 선별한다.
- 중부소방서 출발 edge에서 각 목적지까지 route를 생성할 준비를 한다.
- route 생성 실패나 연결 불량 edge를 사전에 줄인다.

## 최종 기준

- 최종 검증 route set은 `b0_valid_18`이다.
- `ER_ACC_013`은 teleport 문제가 있어 제외한다.
- 파라미터 입력 route는 소방서→서울역 synthetic route를 사용한다.
