# 보조 요인 CSV 슬롯

`scripts/preprocess_supplemental_factors.py` 로 아래 원본에서 25구 값을 생성합니다.

| 파일 | 요인 | 원본 |
|------|------|------|
| `single_person_household_ratio_pct.csv` | 1인 가구 비율(%) | 가구원수별 가구통계(시군구 합계, 2024) |
| `employment_rate_pct.csv` | 고용률(%) | 시군구 경제활동인구 총괄(서울 25구, 2025 Q2) |
| `park_area_total_m2.csv` | 공원 면적(㎡) | 서울시 공원 통계(2024, 천㎡×1000) |

재생성:

```bash
python scripts/preprocess_supplemental_factors.py \
  --employment /path/to/시군구_경제활동인구_총괄_*.csv \
  --parks /path/to/서울시\ 공원\ 통계.csv \
  --household /path/to/가구원수별+가구+...csv
```

유의사항 전문은 `data/factors/gu_factors_meta.json` 의 `supplemental_caveats` 및 각 요인 `notes` 를 참고하세요.
