CREATE OR REPLACE VIEW v_monthly_headcount AS
WITH month_end AS (
    SELECT DISTINCT fiscal_month, (fiscal_month + INTERVAL '1 month - 1 day')::date AS last_day
    FROM budget_plan
)
SELECT
    me.fiscal_month,
    r.site_id,
    r.cost_center_id,
    r.role_id,
    r.shift,
    COUNT(*)::integer AS headcount,
    ROUND(SUM(r.fte), 2) AS fte
FROM month_end me
JOIN headcount_roster r
  ON r.hire_date <= me.last_day
 AND (r.term_date IS NULL OR r.term_date > me.last_day)
GROUP BY me.fiscal_month, r.site_id, r.cost_center_id, r.role_id, r.shift;

CREATE OR REPLACE VIEW v_attrition AS
WITH month_end AS (
    SELECT DISTINCT fiscal_month, (fiscal_month + INTERVAL '1 month - 1 day')::date AS last_day
    FROM budget_plan
),
snapshots AS (
    SELECT
        me.fiscal_month,
        r.site_id,
        ro.job_family,
        COUNT(*) FILTER (
            WHERE r.hire_date <= me.fiscal_month
              AND (r.term_date IS NULL OR r.term_date >= me.fiscal_month)
        )::numeric AS hc_start,
        COUNT(*) FILTER (
            WHERE r.hire_date <= me.last_day
              AND (r.term_date IS NULL OR r.term_date > me.last_day)
        )::numeric AS hc_end,
        COUNT(*) FILTER (
            WHERE r.term_date IS NOT NULL
              AND r.term_date >= me.fiscal_month
              AND r.term_date <= me.last_day
        )::integer AS terminations
    FROM month_end me
    CROSS JOIN headcount_roster r
    JOIN roles ro ON ro.role_id = r.role_id
    GROUP BY me.fiscal_month, r.site_id, ro.job_family
),
rates AS (
    SELECT
        fiscal_month,
        site_id,
        job_family,
        ROUND((hc_start + hc_end) / 2.0, 1) AS avg_headcount,
        terminations,
        CASE WHEN (hc_start + hc_end) > 0 THEN
            ROUND(terminations / ((hc_start + hc_end) / 2.0) * 12.0, 4)
        ELSE NULL END AS annualized_rate
    FROM snapshots
)
SELECT
    fiscal_month,
    site_id,
    job_family,
    avg_headcount,
    terminations,
    annualized_rate,
    ROUND(AVG(annualized_rate) OVER (
        PARTITION BY site_id, job_family
        ORDER BY fiscal_month
        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    ), 4) AS annualized_rate_3mo_avg
FROM rates;

CREATE OR REPLACE VIEW v_req_aging AS
WITH as_of AS (
    SELECT (max(fiscal_month) + INTERVAL '1 month - 1 day')::date AS as_of_date
    FROM budget_plan
),
open_buckets AS (
    SELECT
        r.site_id,
        COUNT(*) FILTER (WHERE a.as_of_date - r.opened_date BETWEEN 0 AND 30)::integer AS open_0_30_days,
        COUNT(*) FILTER (WHERE a.as_of_date - r.opened_date BETWEEN 31 AND 60)::integer AS open_31_60_days,
        COUNT(*) FILTER (WHERE a.as_of_date - r.opened_date BETWEEN 61 AND 90)::integer AS open_61_90_days,
        COUNT(*) FILTER (WHERE a.as_of_date - r.opened_date >= 91)::integer AS open_90_plus_days,
        COUNT(*)::integer AS open_total
    FROM requisitions r
    CROSS JOIN as_of a
    WHERE r.status = 'open'
    GROUP BY r.site_id
),
fill_stats AS (
    SELECT
        r.site_id,
        COUNT(*)::integer AS closed_total,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.filled_date - r.opened_date)::numeric, 1)
            AS median_time_to_fill_days
    FROM requisitions r
    WHERE r.status = 'filled'
    GROUP BY r.site_id
)
SELECT
    s.site_id,
    COALESCE(o.open_0_30_days, 0) AS open_0_30_days,
    COALESCE(o.open_31_60_days, 0) AS open_31_60_days,
    COALESCE(o.open_61_90_days, 0) AS open_61_90_days,
    COALESCE(o.open_90_plus_days, 0) AS open_90_plus_days,
    COALESCE(o.open_total, 0) AS open_total,
    COALESCE(f.closed_total, 0) AS closed_total,
    f.median_time_to_fill_days
FROM sites s
LEFT JOIN open_buckets o ON o.site_id = s.site_id
LEFT JOIN fill_stats f ON f.site_id = s.site_id;

CREATE OR REPLACE VIEW v_budget_variance AS
WITH joined AS (
    SELECT
        b.cost_center_id,
        b.fiscal_month,
        cc.site_id,
        b.planned_headcount,
        b.planned_labor_usd,
        0::numeric(14, 2) AS planned_overtime_usd,
        b.planned_nonlabor_usd,
        b.planned_labor_usd + b.planned_nonlabor_usd AS planned_total_usd,
        a.actual_labor_usd,
        a.actual_overtime_usd,
        a.actual_nonlabor_usd,
        a.actual_labor_usd + a.actual_overtime_usd + a.actual_nonlabor_usd AS actual_total_usd
    FROM budget_plan b
    JOIN actual_spend a ON a.cost_center_id = b.cost_center_id AND a.fiscal_month = b.fiscal_month
    JOIN cost_centers cc ON cc.cost_center_id = b.cost_center_id
)
SELECT
    cost_center_id,
    fiscal_month,
    site_id,
    planned_headcount,
    planned_labor_usd,
    planned_overtime_usd,
    planned_nonlabor_usd,
    planned_total_usd,
    actual_labor_usd,
    actual_overtime_usd,
    actual_nonlabor_usd,
    actual_total_usd,
    ROUND(actual_labor_usd - planned_labor_usd, 2) AS labor_variance_usd,
    ROUND((actual_labor_usd - planned_labor_usd) / NULLIF(planned_labor_usd, 0) * 100.0, 2)
        AS labor_variance_pct,
    ROUND(actual_overtime_usd - planned_overtime_usd, 2) AS overtime_variance_usd,
    ROUND((actual_overtime_usd - planned_overtime_usd) / NULLIF(planned_overtime_usd, 0) * 100.0, 2)
        AS overtime_variance_pct,
    ROUND(actual_nonlabor_usd - planned_nonlabor_usd, 2) AS nonlabor_variance_usd,
    ROUND((actual_nonlabor_usd - planned_nonlabor_usd) / NULLIF(planned_nonlabor_usd, 0) * 100.0, 2)
        AS nonlabor_variance_pct,
    ROUND(actual_total_usd - planned_total_usd, 2) AS total_variance_usd,
    ROUND((actual_total_usd - planned_total_usd) / NULLIF(planned_total_usd, 0) * 100.0, 2)
        AS total_variance_pct,
    ROUND(SUM(actual_total_usd - planned_total_usd) OVER (
        PARTITION BY cost_center_id, EXTRACT(YEAR FROM fiscal_month)
        ORDER BY fiscal_month
    ), 2) AS ytd_variance_usd,
    ROUND(
        SUM(actual_total_usd - planned_total_usd) OVER (
            PARTITION BY cost_center_id, EXTRACT(YEAR FROM fiscal_month)
            ORDER BY fiscal_month
        )
        / NULLIF(SUM(planned_total_usd) OVER (
            PARTITION BY cost_center_id, EXTRACT(YEAR FROM fiscal_month)
            ORDER BY fiscal_month
        ), 0) * 100.0, 2
    ) AS ytd_variance_pct
FROM joined;

CREATE OR REPLACE VIEW v_cost_per_head AS
WITH month_end AS (
    SELECT DISTINCT fiscal_month, (fiscal_month + INTERVAL '1 month - 1 day')::date AS last_day
    FROM budget_plan
),
headcount AS (
    SELECT
        me.fiscal_month,
        r.site_id,
        COUNT(*) FILTER (
            WHERE r.hire_date <= me.fiscal_month
              AND (r.term_date IS NULL OR r.term_date >= me.fiscal_month)
        )::numeric AS hc_start,
        COUNT(*) FILTER (
            WHERE r.hire_date <= me.last_day
              AND (r.term_date IS NULL OR r.term_date > me.last_day)
        )::numeric AS hc_end
    FROM month_end me
    CROSS JOIN headcount_roster r
    GROUP BY me.fiscal_month, r.site_id
),
spend AS (
    SELECT
        a.fiscal_month,
        cc.site_id,
        SUM(a.actual_labor_usd + a.actual_overtime_usd + a.actual_nonlabor_usd) AS total_spend_usd
    FROM actual_spend a
    JOIN cost_centers cc ON cc.cost_center_id = a.cost_center_id
    GROUP BY a.fiscal_month, cc.site_id
)
SELECT
    s.fiscal_month,
    s.site_id,
    ROUND((h.hc_start + h.hc_end) / 2.0, 1) AS avg_headcount,
    ROUND(s.total_spend_usd, 2) AS total_spend_usd,
    ROUND(s.total_spend_usd / NULLIF((h.hc_start + h.hc_end) / 2.0, 0), 2) AS cost_per_head_usd
FROM spend s
JOIN headcount h ON h.fiscal_month = s.fiscal_month AND h.site_id = s.site_id;
