# Step 8 preflight와 emergency-only smoke

이 단계는 배경 차량 없이 emergency route가 SUMO에서 주행 가능한지 확인한 기록이다.

## 목적

- route 연결 오류를 조기에 찾는다.
- 응급차 단독 주행에서 도착 가능 여부를 확인한다.
- 최종 B00 개념의 참고 자료를 만든다.

## 현재 기준

최종 CSV의 B00 값은 Step8 산출물이 아니라 `run_b0_b1_b2_experiment.py`의 `B00` mode에서 새로 측정한다.

Step8 결과는 과거 preflight 참고용으로만 사용한다.
