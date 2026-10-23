-- The store applies this file on every write-mode open, so each statement
-- is idempotent. Timestamps are UTC without a zone.
--
-- A step is keyed by (stage, scope, unit). The scope is the cruncher name
-- for crunch and carve steps, the jurisdiction for derive steps, and the
-- dataset for reference steps.

CREATE TABLE IF NOT EXISTS records (
    source          VARCHAR NOT NULL,
    record          VARCHAR NOT NULL,
    feed            VARCHAR NOT NULL,
    jurisdiction    VARCHAR NOT NULL,
    code            VARCHAR NOT NULL,      -- '' when the record carries every code
    session         VARCHAR,               -- null when the source has no sessions
    completeness    VARCHAR NOT NULL,      -- full | partial
    fetched_at      TIMESTAMP NOT NULL,
    last_modified   TIMESTAMP,
    sha256          VARCHAR NOT NULL,      -- the file's hash, or the manifest's for a tree
    crunchable      BOOLEAN NOT NULL,      -- false for a record with nothing to crunch
    code_version    VARCHAR NOT NULL,      -- the source's version
    registered_at   TIMESTAMP NOT NULL,
    item_count      BIGINT,
    size            BIGINT,                -- unwrapped bytes
    PRIMARY KEY (source, record)
);

CREATE TABLE IF NOT EXISTS steps (
    stage             VARCHAR NOT NULL,    -- crunch | carve | derive | reference
    scope             VARCHAR NOT NULL,
    unit              VARCHAR NOT NULL,    -- a record id, or '' for a scope-wide step
    input_fingerprint VARCHAR NOT NULL,
    code_version      VARCHAR NOT NULL,
    fingerprint       VARCHAR NOT NULL,    -- sha256(input_fingerprint || '\n' || code_version)
    ran_at            TIMESTAMP NOT NULL,
    details           VARCHAR,             -- json diagnostics, not used for planning
    PRIMARY KEY (stage, scope, unit)
);

CREATE TABLE IF NOT EXISTS step_inputs (
    stage       VARCHAR NOT NULL,
    scope       VARCHAR NOT NULL,
    unit        VARCHAR NOT NULL,
    ordinal     INTEGER NOT NULL,          -- consumption order
    input_stage VARCHAR NOT NULL,          -- 'record', 'config', or a stage
    input_scope VARCHAR NOT NULL,          -- for a record, its source
    input_unit  VARCHAR NOT NULL,
    fingerprint VARCHAR NOT NULL,          -- the input's fingerprint when this step ran
    PRIMARY KEY (stage, scope, unit, input_stage, input_scope, input_unit)
);

CREATE TABLE IF NOT EXISTS tables (
    stage               VARCHAR NOT NULL,
    scope               VARCHAR NOT NULL,
    unit                VARCHAR NOT NULL,
    name                VARCHAR NOT NULL,
    path                VARCHAR NOT NULL,  -- relative to data_root
    rows                BIGINT NOT NULL,
    columns_fingerprint VARCHAR NOT NULL,  -- of the written frame's schema
    sha256              VARCHAR,           -- set by derive only
    bytes               BIGINT NOT NULL,
    PRIMARY KEY (stage, scope, unit, name)
);

-- Inputs whose current fingerprint differs from the one a step consumed.
-- Config and package inputs are left out: their hashes are not stored.
CREATE OR REPLACE VIEW changed_inputs AS
SELECT i.stage, i.scope, i.unit, i.input_stage, i.input_scope, i.input_unit,
       i.fingerprint AS consumed,
       COALESCE(r.sha256, s.fingerprint) AS current
FROM step_inputs i
LEFT JOIN records r
       ON i.input_stage = 'record' AND r.source = i.input_scope AND r.record = i.input_unit
LEFT JOIN steps s
       ON i.input_stage <> 'record' AND s.stage = i.input_stage
      AND s.scope = i.input_scope AND s.unit = i.input_unit
WHERE i.input_stage NOT IN ('config', 'package')
  AND COALESCE(r.sha256, s.fingerprint) IS DISTINCT FROM i.fingerprint;
