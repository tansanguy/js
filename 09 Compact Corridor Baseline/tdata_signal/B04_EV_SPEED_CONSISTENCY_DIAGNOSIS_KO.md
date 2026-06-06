# B04 EV 속도/통행시간 일관성 진단

## 결론

이전 요약의 `B04 평균 구간속도 25.682 km/h`는 EV 경로 평균속도가 아니다. 해당 값은 44개 segment-direction을 단순 평균한 값이라, EV가 실제로 통과한 정체 방향의 속도를 대표하지 못한다.

B04의 EV 통행시간 3172초는 tripinfo 기준으로는 다음처럼 설명된다.

- EV route length: 4072.77 m
- EV duration: 3172.0 s
- EV total average speed: 4.622 km/h
- EV waiting time: 2565.0 s
- EV waiting ratio: 80.864%
- EV moving-only average speed: 24.155 km/h

즉 EV가 움직일 때의 속도는 약 24 km/h로 구간 평균과 크게 어긋나지 않는다. 총 평균속도가 4.6 km/h까지 떨어진 이유는 대부분 정지/대기 시간 때문이다.

## B04 구간속도 재해석

B04 upbound는 EV가 지나가는 주요 정체 방향으로 보이며, 초중반 구간은 매우 낮다.

- S1: 1.588 km/h
- S2: 1.757 km/h
- S3: 4.660 km/h
- S4: 4.404 km/h
- S5: 4.106 km/h
- S6: 3.255 km/h
- S7: 2.525 km/h
- S8: 1.718 km/h
- S9: 1.363 km/h
- S10: 29.818 km/h
- S11: 11.035 km/h
- S12: 3.045 km/h
- S13: 2.527 km/h
- S14: 2.284 km/h
- S15: 1.502 km/h

B04 upbound 전체 22개 구간의 단순 평균은 22.095 km/h지만, median은 4.255 km/h다. 뒤쪽 일부 자유류 구간이 평균을 끌어올린다.

## B4 비교

B4에서는 같은 EV route length 4072.77 m에 대해:

- EV duration: 621.0 s
- EV total average speed: 23.614 km/h
- EV waiting time: 162.0 s
- EV waiting ratio: 26.087%
- EV moving-only average speed: 31.940 km/h

B4 개선의 본질은 최고속도 증가가 아니라, B04에서 2565초였던 정지/대기를 162초로 줄인 것이다.

## 수정해야 할 해석

앞으로 B04/B4 성능 비교에는 전역 segment-direction 단순 평균 대신 아래 지표를 함께 써야 한다.

- EV route total average speed
- EV moving-only average speed
- EV waiting time / waiting ratio
- EV route 방향의 segment weighted speed
- EV route early/mid/late 구간별 speed
