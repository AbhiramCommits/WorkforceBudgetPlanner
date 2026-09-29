DROP TABLE IF EXISTS actual_spend, budget_plan, requisitions, headcount_roster, cost_centers, roles, sites CASCADE;
DROP TYPE IF EXISTS shift_enum, req_status_enum CASCADE;

CREATE TYPE shift_enum AS ENUM ('A', 'B', 'C', 'Days');
CREATE TYPE req_status_enum AS ENUM ('open', 'filled', 'cancelled', 'on_hold');

CREATE TABLE sites (
    site_id   INTEGER PRIMARY KEY,
    site_name TEXT NOT NULL UNIQUE,
    region    TEXT NOT NULL,
    country   CHAR(2) NOT NULL
);

CREATE TABLE cost_centers (
    cost_center_id INTEGER PRIMARY KEY,
    site_id        INTEGER NOT NULL REFERENCES sites(site_id),
    cc_code        TEXT NOT NULL UNIQUE,
    cc_name        TEXT NOT NULL,
    function       TEXT NOT NULL
);
CREATE INDEX idx_cost_centers_site_id ON cost_centers (site_id);
CREATE INDEX idx_cost_centers_function ON cost_centers (function);

CREATE TABLE roles (
    role_id         INTEGER PRIMARY KEY,
    role_name       TEXT NOT NULL UNIQUE,
    job_family      TEXT NOT NULL,
    salary_band     TEXT NOT NULL,
    base_annual_usd NUMERIC(10, 2) NOT NULL
);
CREATE INDEX idx_roles_job_family ON roles (job_family);

CREATE TABLE headcount_roster (
    employee_id      INTEGER PRIMARY KEY,
    site_id          INTEGER NOT NULL REFERENCES sites(site_id),
    cost_center_id   INTEGER NOT NULL REFERENCES cost_centers(cost_center_id),
    role_id          INTEGER NOT NULL REFERENCES roles(role_id),
    shift            shift_enum NOT NULL,
    hire_date        DATE NOT NULL,
    term_date        DATE,
    fte              NUMERIC(3, 2) NOT NULL CHECK (fte > 0 AND fte <= 1),
    annual_salary_usd NUMERIC(12, 2) NOT NULL
);
CREATE INDEX idx_roster_site_id ON headcount_roster (site_id);
CREATE INDEX idx_roster_cost_center_id ON headcount_roster (cost_center_id);
CREATE INDEX idx_roster_role_id ON headcount_roster (role_id);
CREATE INDEX idx_roster_hire_date ON headcount_roster (hire_date);
CREATE INDEX idx_roster_term_date ON headcount_roster (term_date);
CREATE INDEX idx_roster_site_cc ON headcount_roster (site_id, cost_center_id);
CREATE INDEX idx_roster_active_cc ON headcount_roster (cost_center_id, hire_date) WHERE term_date IS NULL;

CREATE TABLE requisitions (
    req_id            INTEGER PRIMARY KEY,
    site_id           INTEGER NOT NULL REFERENCES sites(site_id),
    cost_center_id    INTEGER NOT NULL REFERENCES cost_centers(cost_center_id),
    role_id           INTEGER NOT NULL REFERENCES roles(role_id),
    opened_date       DATE NOT NULL,
    target_start_date DATE NOT NULL,
    filled_date       DATE,
    status            req_status_enum NOT NULL
);
CREATE INDEX idx_reqs_site_id ON requisitions (site_id);
CREATE INDEX idx_reqs_cost_center_id ON requisitions (cost_center_id);
CREATE INDEX idx_reqs_status ON requisitions (status);
CREATE INDEX idx_reqs_opened_date ON requisitions (opened_date);

CREATE TABLE budget_plan (
    cost_center_id      INTEGER NOT NULL REFERENCES cost_centers(cost_center_id),
    fiscal_month        DATE NOT NULL,
    planned_headcount   NUMERIC(6, 1) NOT NULL,
    planned_labor_usd   NUMERIC(14, 2) NOT NULL,
    planned_nonlabor_usd NUMERIC(14, 2) NOT NULL,
    PRIMARY KEY (cost_center_id, fiscal_month)
);
CREATE INDEX idx_budget_plan_month ON budget_plan (fiscal_month);

CREATE TABLE actual_spend (
    cost_center_id      INTEGER NOT NULL REFERENCES cost_centers(cost_center_id),
    fiscal_month        DATE NOT NULL,
    actual_labor_usd    NUMERIC(14, 2) NOT NULL,
    actual_overtime_usd NUMERIC(14, 2) NOT NULL,
    actual_nonlabor_usd NUMERIC(14, 2) NOT NULL,
    PRIMARY KEY (cost_center_id, fiscal_month)
);
CREATE INDEX idx_actual_spend_month ON actual_spend (fiscal_month);
