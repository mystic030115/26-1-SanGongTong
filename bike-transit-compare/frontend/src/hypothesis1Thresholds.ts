/**
 * 가설 1 전역 판정·차트 기준선 (단일 소스).
 * 정당화는 보수적·재현 가능한 운영 정의 + 민감도(임계 승률 탭)로 보완한다는 전제를 둔다.
 */
export const DEPTH_MEANINGFUL_PCT = 20;
/** Coverage는 “경로 절약률 임계”와 “25구 평균 하한” 두 층이 있다. */
export const COVERAGE_PATH_THRESHOLD_DEFAULT_PCT = 20;
export const COVERAGE_MEANINGFUL_AVG_PCT = 40;
export const F1_MEANINGFUL = 0.25;

export type ThresholdRationaleBlock = { title: string; paragraphs: string[] };

export const THRESHOLD_RATIONALE_BLOCKS: ThresholdRationaleBlock[] = [
  {
    title: `Depth 전역 평균 ≥ ${DEPTH_MEANINGFUL_PCT}%`,
    paragraphs: [
      "Depth는 고빈도 경로 가중으로 집계한 ‘대중교통 대비 따릉이 절약이 얼마나 큰가’의 전역 요약이다. 하한을 두지 않으면 소수의 극단 절약만으로도 평균이 부풀려질 수 있다.",
      `${DEPTH_MEANINGFUL_PCT}%는 ‘왕복·통근에서 체감할 만한 절약’에 가까운 보수적 컷이다. 10%는 노이즈·측정오차에 가깝고, 30%는 상대적으로 엄격해져 서울 전역에서 자주 미달할 수 있어, 본 분석에서는 ${DEPTH_MEANINGFUL_PCT}%를 ‘규모가 의미 있다’는 최소 운영 정의로 둔다.`,
      "외부 규범이 아니라 분석 팀의 운영 정의이므로, 보고서에는 위 논리와 함께 ‘다른 컷일 때 결론이 어떻게 바뀌는지’를 민감도로 제시하는 것이 정당화에 가깝다.",
    ],
  },
  {
    title: `Coverage: 경로 임계 ${COVERAGE_PATH_THRESHOLD_DEFAULT_PCT}% · 구 평균 ≥ ${COVERAGE_MEANINGFUL_AVG_PCT}%`,
    paragraphs: [
      `먼저 각 구에서 ‘절약률 ≥ ${COVERAGE_PATH_THRESHOLD_DEFAULT_PCT}%’인 경로 비중을 구하고, 25구 산술평균을 Coverage로 쓴다. 상단 입력으로 경로 임계는 바꿀 수 있으나, 가설 1 전역 판정의 하한 ${COVERAGE_MEANINGFUL_AVG_PCT}%는 ‘넓게 퍼진 절약’을 보기 위한 별도 기준이다.`,
      `${COVERAGE_MEANINGFUL_AVG_PCT}%는 구 평균이 과반에 가까운 수준에서 고빈도 경로에 절약이 나타나는지를 보려는 값이다. 절약 ‘규모’(Depth)만 크고 특정 OD에만 몰리면 정책·서비스 관점에서 일반화가 약하므로, Coverage로 범위를 함께 묶는다.`,
      "경로 임계와 구 평균 하한을 동시에 고정하면 보수적·낙관적 편향을 줄이려는 장치이며, 역시 민감도(다른 임계 조합)로 보완하는 것이 좋다.",
    ],
  },
  {
    title: `F1(25구 평균) ≥ ${F1_MEANINGFUL.toFixed(2)}`,
    paragraphs: [
      "F1은 Depth와 Coverage를 각각 0~1로 맞춘 뒤 조화평균으로 만든 ‘규모와 범위의 동시 양호’ 지표다. 한쪽만 매우 크면 F1은 그보다 낮아진다.",
      `d=Depth/100, c=Coverage/100일 때 d≈0.2·c≈0.4이면 F1≈0.27이다. 경로 임계 ${COVERAGE_PATH_THRESHOLD_DEFAULT_PCT}%·구 평균 Coverage ${COVERAGE_MEANINGFUL_AVG_PCT}% 근처에서 ‘둘 다 중간 이상’이면 F1이 대략 0.25~0.3 구간에 오므로, 전역 한 줄 판정용 하한으로 ${F1_MEANINGFUL.toFixed(2)}를 둔다.`,
      "즉 0.25는 외부 통계청 기준이 아니라, 위 Depth·Coverage 운영 정의와 수치적으로 맞물리게 잡은 내부 기준선이다.",
    ],
  },
];
