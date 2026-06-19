"""
연관 요인(소가설2) 단변량 적합 + Lasso 다중회귀 + CCA.

- 단변량: OLS 직선 vs 2차 — ΔR²·AIC로 선형/비선형 자동 선택 → 산점도 식·곡선
- 다중 F1 추정: LassoCV (연관 요인 동시)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from sklearn.linear_model import LassoCV
from sklearn.preprocessing import StandardScaler


def _zscore_columns(a: np.ndarray) -> np.ndarray:
    mu = np.nanmean(a, axis=0)
    sd = np.nanstd(a, axis=0, ddof=1)
    sd = np.where(sd < 1e-12, 1.0, sd)
    return (a - mu) / sd


def _fmt_beta(b: float) -> str:
    if not np.isfinite(b):
        return "0"
    if abs(b) >= 1e-4 or b == 0:
        return f"{b:.6f}".rstrip("0").rstrip(".")
    return f"{b:.4e}"


def _lasso_back_transform_1d(
    model: LassoCV, scaler: StandardScaler, x_raw: np.ndarray
) -> tuple[float, float]:
    """표준화 1변수 Lasso → 원 단위 F1 = b0 + b1·x."""
    b1_s = float(model.coef_[0])
    mu = float(scaler.mean_[0])
    sd = float(scaler.scale_[0])
    b1 = b1_s / sd if sd > 1e-15 else 0.0
    b0 = float(model.intercept_) - b1_s * mu / sd if sd > 1e-15 else float(model.intercept_)
    return b0, b1


def _aic(n: int, sse: float, k: int) -> float:
    if n <= 0 or sse <= 1e-15:
        return float("inf")
    return float(n * np.log(sse / n) + 2 * k)


def simple_ols_f1(
    x: np.ndarray,
    y: np.ndarray,
    x_name: str = "X",
) -> dict[str, Any]:
    """OLS: F1 = β0 + β1·X (산점도·선형 판정용)."""
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask].astype(float)
    y = y[mask].astype(float)
    n = int(x.size)
    if n < 4:
        return {"n": n, "error": "n too small"}
    X = np.column_stack([np.ones(n), x])
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank < 2:
        return {"n": n, "error": "rank deficient"}
    b0, b1 = float(beta[0]), float(beta[1])
    yhat = X @ beta
    resid = y - yhat
    sse = float(np.sum(resid**2))
    sst = float(np.sum((y - float(np.mean(y))) ** 2))
    r2 = float(1.0 - sse / sst) if sst > 1e-15 else 0.0
    dof = n - 2
    mse = sse / dof if dof > 0 else np.nan
    try:
        cov_b = mse * np.linalg.inv(X.T @ X)
        se_b1 = float(np.sqrt(max(cov_b[1, 1], 0.0)))
    except Exception:
        se_b1 = np.nan
    t_stat = float(b1 / se_b1) if se_b1 and np.isfinite(se_b1) and se_b1 > 0 else None
    p_val = None
    if t_stat is not None and dof > 0:
        p_val = float(2.0 * (1.0 - scipy_stats.t.cdf(abs(t_stat), dof)))
    pr = float(np.corrcoef(x, y)[0, 1]) if n >= 2 else None
    s1 = "+" if b1 >= 0 else "−"
    return {
        "n": n,
        "x_name": x_name,
        "model_type": "linear_ols",
        "equation": f"F1 = {b0:.4f} {s1} ({abs(b1):.6f})·{x_name}",
        "beta0": b0,
        "beta1": b1,
        "r2": r2,
        "sse": sse,
        "aic": _aic(n, sse, 2),
        "pearson_r": pr,
        "t_stat": t_stat,
        "p_value_slope": p_val,
        "se_slope": se_b1 if np.isfinite(se_b1) else None,
    }


def _nonlinear_alternatives_ko(
    fac: str,
    *,
    delta_r2: float,
    pearson_r: float | None,
    spearman_r: float | None,
) -> str:
    """비선형 요인에 쓸 수 있는 분석 옵션 (n=25 기준)."""
    tips: list[str] = []
    if delta_r2 >= 0.05:
        tips.append("① 2차 다항(차트 보라 곡선): 곡률이 있을 때 n=25에서 쓸 수 있는 가장 단순한 비선형 식.")
    if (
        pearson_r is not None
        and spearman_r is not None
        and abs(spearman_r) > abs(pearson_r) + 0.05
    ):
        tips.append("② Spearman·순위 상관: 직선보다 단조(순위) 관계가 강할 때.")
    tips.append("③ CCA(아래 블록): 이 요인만 직선/곡선으로 약해도 요인 묶음과 F1은 함께 움직일 수 있음.")
    if fac == "park_area_total_m2":
        tips.append("④ log(면적) 선형: 면적 스케일이 크면 로그 변환 후 직선 재적합.")
    if fac in ("elderly_65plus_ratio_pct", "single_person_household_ratio_pct"):
        tips.append("⑤ 잔차·이상 구: 1~2개 구가 r을 깎는지 산점도 툴팁으로 확인.")
    tips.append("⑥ GAM·고차 다항: n=25에서는 과적합 위험 → 보고서엔 2차·CCA 위주.")
    return " ".join(tips)


def fit_univariate_factor(
    x: np.ndarray,
    y: np.ndarray,
    fac: str,
    label_ko: str,
    *,
    pearson_r: float | None = None,
    spearman_r: float | None = None,
    alpha: float = 0.05,
    r2_quad_min_gain: float = 0.05,
) -> dict[str, Any]:
    """
    요인 하나에 대해 선형(OLS) vs 2차 중 선택.
    선형 적합: ΔR² < 임계 또는 AIC가 직선 우세.
    """
    lin = simple_ols_f1(x, y, x_name=fac)
    if "error" in lin:
        return {"factor": fac, "label_ko": label_ko, **lin}

    quad = quadratic_f1(x, y, x_name=fac)
    use_quad = False
    reason = "직선 OLS가 2차 대비 설명력·AIC 모두 우수 → 선형 산점도."
    delta_r2 = 0.0

    if "error" not in quad:
        delta_r2 = float(quad["r2"]) - float(lin["r2"])
        aic_lin = float(lin["aic"])
        aic_quad = float(quad["aic"])
        if delta_r2 >= r2_quad_min_gain and aic_quad < aic_lin + 2:
            use_quad = True
            reason = (
                f"2차 R²가 직선보다 {delta_r2:.3f} 높고 AIC 유리 "
                f"(직선={aic_lin:.1f}, 2차={aic_quad:.1f}) → 비선형(2차) 산점도."
            )
        else:
            reason = (
                f"직선 적합 유지 (ΔR²={delta_r2:.3f}, AIC 직선={aic_lin:.1f}, 2차={aic_quad:.1f})."
            )

    fit_kind = "quadratic" if use_quad else "linear"
    chosen = quad if use_quad else lin
    p_slope = lin.get("p_value_slope")
    linear_p_sig = p_slope is not None and np.isfinite(p_slope) and float(p_slope) < alpha

    row: dict[str, Any] = {
        "factor": fac,
        "label_ko": label_ko,
        "fit_kind": fit_kind,
        "recommended_model_type": fit_kind,
        "model_selection_reason_ko": reason,
        "significant_at_alpha": fit_kind == "linear" and linear_p_sig,
        "linear_model": lin,
        "r2_linear": lin.get("r2"),
        "r2_quadratic": quad.get("r2") if "error" not in quad else None,
        "delta_r2_quad_vs_linear": delta_r2,
        "equation": chosen.get("equation"),
        "recommended_equation": chosen.get("equation"),
        "beta0": chosen.get("beta0"),
        "beta1": chosen.get("beta1"),
        "beta2": chosen.get("beta2") if use_quad else None,
        "r2": chosen.get("r2"),
        "pearson_r": pearson_r if pearson_r is not None else lin.get("pearson_r"),
        "p_value_slope": p_slope,
        "n": lin.get("n"),
        "analysis_path": "linear_ols" if fit_kind == "linear" else "quadratic",
    }
    if use_quad:
        row["nonlinear_model"] = quad
        row["nonlinear_alternatives_ko"] = _nonlinear_alternatives_ko(
            fac, delta_r2=delta_r2, pearson_r=pearson_r, spearman_r=spearman_r
        )
        row["interpretation_ko"] = reason + " " + row["nonlinear_alternatives_ko"]
    else:
        row["interpretation_ko"] = (
            f"{reason} p(β₁)={p_slope:.4f}." if p_slope is not None else reason
        )
    return row


def simple_lasso_f1(
    x: np.ndarray,
    y: np.ndarray,
    x_name: str = "X",
) -> dict[str, Any]:
    """LassoCV: F1 = beta0 + beta1*x (원 단위). 유의 = Lasso 계수 ≠ 0."""
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask].astype(float)
    y = y[mask].astype(float)
    n = int(x.size)
    if n < 4:
        return {"n": n, "error": "n too small"}
    scaler = StandardScaler()
    xs = scaler.fit_transform(x.reshape(-1, 1))
    cv = max(2, min(5, n - 1))
    model = LassoCV(cv=cv, fit_intercept=True, random_state=0, n_alphas=40, max_iter=8000)
    model.fit(xs, y)
    b0, b1 = _lasso_back_transform_1d(model, scaler, x)
    yhat = b0 + b1 * x
    resid = y - yhat
    sse = float(np.sum(resid**2))
    sst = float(np.sum((y - float(np.mean(y))) ** 2))
    r2 = float(1.0 - sse / sst) if sst > 1e-15 else 0.0
    pr = float(np.corrcoef(x, y)[0, 1]) if n >= 2 else None
    nonzero = abs(float(model.coef_[0])) > 1e-9
    return {
        "n": n,
        "x_name": x_name,
        "model_type": "lasso",
        "lasso_alpha": float(model.alpha_),
        "equation": f"F1 = {b0:.4f} + ({_fmt_beta(b1)})·{x_name}",
        "beta0": b0,
        "beta1": b1,
        "r2": r2,
        "pearson_r": pr,
        "lasso_selected": nonzero,
        "p_value_slope": None,
        "se_slope": None,
    }


def quadratic_f1(
    x: np.ndarray,
    y: np.ndarray,
    x_name: str = "X",
) -> dict[str, Any]:
    """F1 = b0 + b1·x + b2·x² (OLS, 비선형 예측식)."""
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask].astype(float)
    y = y[mask].astype(float)
    n = int(x.size)
    if n < 5:
        return {"n": n, "error": "n too small"}
    X = np.column_stack([np.ones(n), x, x * x])
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank < 3:
        return {"n": n, "error": "rank deficient"}
    b0, b1, b2 = float(beta[0]), float(beta[1]), float(beta[2])
    yhat = X @ beta
    resid = y - yhat
    sse = float(np.sum(resid**2))
    sst = float(np.sum((y - float(np.mean(y))) ** 2))
    r2 = float(1.0 - sse / sst) if sst > 1e-15 else 0.0
    s1 = "+" if b1 >= 0 else "−"
    s2 = "+" if b2 >= 0 else "−"
    eq = (
        f"F1 = {b0:.4f} {s1} ({abs(b1):.6f})·{x_name} "
        f"{s2} ({abs(b2):.6f})·{x_name}²"
    )
    return {
        "n": n,
        "x_name": x_name,
        "model_type": "quadratic",
        "equation": eq,
        "beta0": b0,
        "beta1": b1,
        "beta2": b2,
        "r2": r2,
        "sse": sse,
        "aic": _aic(n, sse, 3),
    }


def canonical_correlation(
    X: np.ndarray,
    Y: np.ndarray,
    x_names: list[str],
    y_names: list[str],
) -> dict[str, Any]:
    """
    정준상관(CCA). X: n×p, Y: n×q (행=구).
    반환: ρ_k, X·Y 가중치(정준변수 계수), Rao F 근사 p (전체 H0: 모든 ρ=0).
    """
    mask = np.all(np.isfinite(X), axis=1) & np.all(np.isfinite(Y), axis=1)
    X = X[mask].astype(float)
    Y = Y[mask].astype(float)
    n, p = X.shape
    q = Y.shape[1]
    if n < max(p + 2, q + 2):
        return {"n": n, "error": "n too small for CCA"}

    Xz = _zscore_columns(X)
    Yz = _zscore_columns(Y)
    c11 = (Xz.T @ Xz) / (n - 1) + 1e-6 * np.eye(p)
    c22 = (Yz.T @ Yz) / (n - 1) + 1e-6 * np.eye(q)
    c12 = (Xz.T @ Yz) / (n - 1)
    c11i = np.linalg.pinv(c11)
    c22i = np.linalg.pinv(c22)
    m = c11i @ c12 @ c22i @ c12.T
    evals, evecs_x = np.linalg.eigh(m)
    order = np.argsort(evals)[::-1]
    evals = np.clip(evals[order], 0.0, 1.0)
    evecs_x = evecs_x[:, order]
    rhos = [float(np.sqrt(e)) for e in evals[: min(p, q)]]

    x_weights: list[dict[str, Any]] = []
    y_weights: list[dict[str, Any]] = []
    for k in range(min(p, q)):
        a = evecs_x[:, k]
        norm_a = float(np.linalg.norm(a))
        if norm_a < 1e-12:
            continue
        a = a / norm_a
        b = c22i @ c12.T @ a
        norm_b = float(np.linalg.norm(b))
        if norm_b > 1e-12:
            b = b / norm_b
        x_weights.append({"pair": k + 1, "weights": {x_names[i]: float(a[i]) for i in range(p)}})
        y_weights.append({"pair": k + 1, "weights": {y_names[j]: float(b[j]) for j in range(q)}})

    # Wilks Λ = ∏(1 - ρ²); Rao F-approx for H0 (Barlett-type, simplified)
    lam = float(np.prod([1.0 - r**2 for r in rhos if r < 1.0])) if rhos else 1.0
    lam = max(min(lam, 1.0), 1e-15)
    s = min(p, q)
    t = n - 0.5 * (p + q + 1) - 1
    df1 = p * q
    df2 = t * s - 0.5 * s * (s + 1) + 1
    if df2 > 0 and lam < 1.0:
        chi2 = -(t - (p + q - 1) / 2.0) * np.log(lam)
        p_overall = float(1.0 - scipy_stats.chi2.cdf(chi2, df1)) if df1 > 0 else None
    else:
        p_overall = None

    return {
        "n": n,
        "x_names": x_names,
        "y_names": y_names,
        "canonical_correlations": rhos,
        "x_weights": x_weights,
        "y_weights": y_weights,
        "wilks_lambda": lam,
        "p_overall_approx": p_overall,
        "note": "p_overall은 Wilks Λ→χ² 근사(표본 n=25에서 보수적 해석 권장).",
    }


def _label_ko(fac: str, factor_meta: dict[str, Any]) -> str:
    m = factor_meta.get(fac) if isinstance(factor_meta, dict) else None
    if isinstance(m, dict) and m.get("label_ko"):
        return str(m["label_ko"])
    return fac


def lasso_multiple(
    X: np.ndarray,
    y: np.ndarray,
    names: list[str],
    *,
    factor_meta: dict[str, Any] | None = None,
    gu_labels: list[str] | None = None,
    subset_label: str = "all_associated",
) -> dict[str, Any]:
    """LassoCV: F1 = b0 + sum b_j X_j (원 단위)."""
    factor_meta = factor_meta or {}
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    X = X[mask].astype(float)
    y = y[mask].astype(float)
    gu_arr = np.array(gu_labels, dtype=object) if gu_labels else None
    gu_use = gu_arr[mask].tolist() if gu_arr is not None and len(gu_arr) == len(mask) else None
    n, p = X.shape
    if n < max(4, p + 1):
        return {"n": n, "error": "n too small", "predictors": names, "subset": subset_label}
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    cv = max(2, min(5, n - 1))
    model = LassoCV(cv=cv, fit_intercept=True, random_state=0, n_alphas=40, max_iter=8000)
    model.fit(Xs, y)
    b0 = float(model.intercept_)
    coefs = {"intercept": b0}
    coef_rows: list[dict[str, Any]] = [
        {"term": "intercept", "factor": None, "label_ko": "절편", "beta": b0}
    ]
    active: list[str] = []
    for j, name in enumerate(names):
        bj_s = float(model.coef_[j])
        sd = float(scaler.scale_[j])
        mu = float(scaler.mean_[j])
        bj = bj_s / sd if sd > 1e-15 else 0.0
        b0 -= bj_s * mu / sd if sd > 1e-15 else 0.0
        coefs[name] = bj
        if abs(bj_s) > 1e-9:
            active.append(name)
        coef_rows.append(
            {
                "term": name,
                "factor": name,
                "label_ko": _label_ko(name, factor_meta),
                "beta": bj,
                "lasso_selected": abs(bj_s) > 1e-9,
            }
        )
    coefs["intercept"] = b0
    coef_rows[0]["beta"] = b0
    yhat = b0 + X @ np.array([coefs[n] for n in names], dtype=float)
    resid = y - yhat
    sse = float(np.sum(resid**2))
    sst = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - sse / sst if sst > 1e-15 else 0.0
    n_eff = max(1, len(active) + 1)
    r2_adj = 1.0 - (1.0 - r2) * (n - 1) / (n - n_eff) if n > n_eff else None
    rmse = float(np.sqrt(sse / n))
    mae = float(np.mean(np.abs(resid)))
    f_stat = None
    f_p = None

    parts = [f"{b0:.4f}"] + [f"({_fmt_beta(coefs[n])})·{n}" for n in names if n in active]
    eq = "F1 = " + " + ".join(parts) if active else f"F1 = {b0:.4f}"
    parts_ko = [f"{b0:.4f}"] + [
        f"({_fmt_beta(coefs[n])})·{_label_ko(n, factor_meta)}" for n in names if n in active
    ]
    eq_ko = "F1 = " + " + ".join(parts_ko) if active else f"F1 = {b0:.4f}"
    by_gu: list[dict[str, Any]] = []
    if gu_use and len(gu_use) == n:
        for i, gu in enumerate(gu_use):
            by_gu.append(
                {
                    "gu": str(gu),
                    "f1_actual": float(y[i]),
                    "f1_predicted": float(yhat[i]),
                    "residual": float(resid[i]),
                }
            )
    return {
        "n": n,
        "predictors": names,
        "subset": subset_label,
        "model_type": "lasso",
        "lasso_alpha": float(model.alpha_),
        "lasso_active_predictors": active,
        "equation": eq,
        "equation_ko": eq_ko,
        "coefficients": coefs,
        "coefficient_rows": coef_rows,
        "r2": float(r2),
        "r2_adj": float(r2_adj) if r2_adj is not None else None,
        "rmse": rmse,
        "mae": mae,
        "f_stat": f_stat,
        "p_value_model": f_p,
        "by_gu": by_gu,
    }


def run_associated_factor_analysis(
    merged: pd.DataFrame,
    factor_meta: dict[str, Any],
    *,
    abs_r_threshold: float = 0.2,
    min_n: int = 15,
    alpha: float = 0.05,
    corr_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    merged columns: gu, f1, depth_pct, coverage_pct, + factors.
    """
    if corr_rows is None:
        corr_rows = []

    assoc: list[str] = []
    seen: set[str] = set()
    for r in corr_rows:
        if r.get("target") != "f1":
            continue
        fac = str(r.get("factor") or "").strip()
        if not fac or fac in seen:
            continue
        pr = r.get("pearson_r")
        n = r.get("n")
        if pr is None or n is None:
            continue
        if int(n) >= min_n and abs(float(pr)) >= abs_r_threshold:
            seen.add(fac)
            assoc.append(fac)

    if not assoc:
        return {
            "empty": True,
            "error": f"No associated factors (|r|>={abs_r_threshold}, n>={min_n}).",
        }

    y = merged["f1"].to_numpy(dtype=float)
    simple_rows: list[dict[str, Any]] = []
    nonsig: list[str] = []

    corr_by_fac: dict[str, dict[str, Any]] = {}
    for r in corr_rows or []:
        if r.get("target") == "f1" and r.get("factor"):
            corr_by_fac[str(r["factor"])] = r

    for fac in assoc:
        if fac not in merged.columns:
            continue
        x = merged[fac].to_numpy(dtype=float)
        m = factor_meta.get(fac) if isinstance(factor_meta, dict) else {}
        label_ko = (m.get("label_ko") if isinstance(m, dict) else None) or fac
        cr = corr_by_fac.get(fac, {})
        pr = cr.get("pearson_r")
        sr = cr.get("spearman_r")
        row = fit_univariate_factor(
            x,
            y,
            fac,
            label_ko,
            pearson_r=float(pr) if pr is not None else None,
            spearman_r=float(sr) if sr is not None else None,
            alpha=alpha,
        )
        if "error" in row:
            simple_rows.append(row)
            continue
        simple_rows.append(row)
        if row.get("fit_kind") == "quadratic":
            nonsig.append(fac)

    linear_fit_factors = [
        str(r["factor"])
        for r in simple_rows
        if r.get("fit_kind") == "linear" and r.get("factor") and "error" not in r
    ]
    sig_factors = linear_fit_factors

    cols = (["gu"] if "gu" in merged.columns else []) + assoc + ["f1", "depth_pct", "coverage_pct"]
    complete = merged[cols].dropna()
    cano_f1 = None
    cano_dcf = None
    multi = None
    multi_sig = None
    if len(complete) >= len(assoc) + 3:
        gu_list = complete["gu"].astype(str).tolist() if "gu" in complete.columns else None
        X = complete[assoc].to_numpy(dtype=float)
        multi = lasso_multiple(
            X,
            complete["f1"].to_numpy(dtype=float),
            assoc,
            factor_meta=factor_meta,
            gu_labels=gu_list,
            subset_label="all_associated",
        )
        if len(sig_factors) >= 1 and len(complete) >= len(sig_factors) + 2:
            Xs = complete[sig_factors].to_numpy(dtype=float)
            multi_sig = lasso_multiple(
                Xs,
                complete["f1"].to_numpy(dtype=float),
                sig_factors,
                factor_meta=factor_meta,
                gu_labels=gu_list,
                subset_label="significant_only",
            )
        cano_f1 = canonical_correlation(
            X,
            complete[["f1"]].to_numpy(dtype=float),
            assoc,
            ["f1"],
        )
        cano_dcf = canonical_correlation(
            X,
            complete[["depth_pct", "coverage_pct", "f1"]].to_numpy(dtype=float),
            assoc,
            ["depth_pct", "coverage_pct", "f1"],
        )

    methodology = {
        "step1_ko": "단변량: 각 요인마다 OLS 직선 vs 2차를 ΔR²(≥0.05)·AIC로 비교 → 선형이면 빨간 직선·식.",
        "step2_ko": "비선형 판정 요인: 2차식 + 보라 곡선. 보고·추가 분석은 CCA·Spearman·이상 구 등 가이드 참고.",
        "step3_ko": "다중 F1 추정: 선형 적합 요인만 LassoCV 동시 투입(또는 연관 6개 전체 Lasso 보조).",
        "step4_ko": "CCA: X블록(6요인)과 F1(또는 Depth·Coverage·F1) 다변량 연관.",
        "caution_ko": [
            "n=25로 검정력이 낮아 p가 크게 나와도 |r|이 크면 실질 연관은 있을 수 있음.",
            "F1은 Depth·Coverage 조합이라 Y에 F1만 쓰면 정보 중복; 보조로 Y=[Depth,Coverage,F1] CCA 권장.",
            "요인 연도·정의가 다르면(가구2024, 고용2025Q2 등) 인과 해석 금지, 연관만.",
        ],
    }

    return {
        "empty": False,
        "alpha": alpha,
        "abs_r_threshold": abs_r_threshold,
        "associated_factors": assoc,
        "methodology": methodology,
        "simple_linear": simple_rows,
        "significant_factors": sig_factors,
        "linear_fit_factors": linear_fit_factors,
        "nonlinear_fit_factors": nonsig,
        "nonsignificant_factors": nonsig,
        "multiple_regression_f1": multi,
        "f1_regression": {
            "recommended": "significant_only",
            "description_ko": (
                "F1 Score 추정용 Lasso 다중회귀. 단변량 선형 적합 요인만 넣은 모형을 기본 추천."
                if multi_sig
                else "선형 적합 요인이 없어 연관 요인 전체 Lasso 모형만 제공."
            ),
            "significant_only": multi_sig,
            "all_associated": multi,
        },
        "cca": {
            "trigger": "always_for_block" if nonsig else "supplementary",
            "trigger_reason_ko": (
                f"단순 회귀 비유의 요인: {', '.join(nonsig)}"
                if nonsig
                else "모든 연관 요인이 단순 회귀에서 유의하나, 다변량 공선·공유 변동 확인용 CCA 병행."
            ),
            "y_block_f1": cano_f1,
            "y_block_depth_coverage_f1": cano_dcf,
        },
    }
